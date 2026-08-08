"""The services that reach codes the four entity models cannot express.

A device file holds far more than a climate, fan, light or media player entity
can offer as a control: a television's arrow keys, an air conditioner's LED
toggle, a source the platform does not model, a preset. Without a service those
codes exist on disk and nothing can ever send them.

Two services, because there are two situations:

* ``hub_ir.send_command`` targets a HubIR entity and names a code by its path in
  that entity's own device file. It reuses the entity's controller, remote and
  delay, so an automation repeats no configuration.
* ``hub_ir.send_code`` targets no entity at all and takes a raw code. It is for a
  code that is not in any device file yet — something just learned, or copied
  out of a forum post.
"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv

from .controller import (
    BROADLINK_COMMANDS_ENCODING,
    BROADLINK_CONTROLLER,
    ENC_BASE64,
    get_controller,
)
from .device_file import (
    COMMAND_PATH_SEPARATOR,
    command_paths,
    is_recorded,
    resolve_command,
)

# Spelled out rather than imported from __init__.py, which imports this module's
# neighbours and would make the import a cycle. const.py deliberately holds no
# DOMAIN, so there is nowhere else to take it from.
DOMAIN = "hub_ir"

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_COMMAND = "send_command"
SERVICE_SEND_CODE = "send_code"

ATTR_COMMAND = "command"
ATTR_REPEAT = "repeat"
ATTR_DELAY = "delay"
ATTR_CODE = "code"
ATTR_ENCODING = "encoding"
ATTR_REMOTE_ENTITY_ID = "remote_entity_id"

# A typo in an automation must not hold the remote for an hour.
MAX_REPEAT = 100

# Between repeats of the same code. Long enough that a television registers two
# presses rather than one, short enough for a volume ramp to feel like one.
DEFAULT_REPEAT_DELAY = 0.4

DEFAULT_SEND_CODE_DELAY = 0.5

# How many paths an unresolved-command error lists before giving up on being
# helpful; a full climate file has 180 of them.
MAX_SUGGESTIONS = 15

SEND_COMMAND_SCHEMA = {
    vol.Required(ATTR_COMMAND): cv.string,
    vol.Optional(ATTR_REPEAT, default=1): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=MAX_REPEAT)
    ),
    vol.Optional(ATTR_DELAY, default=DEFAULT_REPEAT_DELAY): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=10)
    ),
}


def _send_code_schema() -> vol.Schema:
    """Build the send_code schema.

    remote_entity_id is validated here rather than imported at module scope,
    because the validator lives in __init__.py and importing it from there while
    this module is first loaded would be a cycle.
    """
    from . import remote_entity_id  # noqa: PLC0415

    return vol.Schema(
        {
            vol.Required(ATTR_REMOTE_ENTITY_ID): remote_entity_id,
            vol.Required(ATTR_CODE): vol.Any(cv.string, [cv.string]),
            vol.Optional(ATTR_ENCODING, default=ENC_BASE64): vol.In(
                BROADLINK_COMMANDS_ENCODING
            ),
            vol.Optional(ATTR_DELAY, default=DEFAULT_SEND_CODE_DELAY): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=10)
            ),
        }
    )


class HubIRCommandMixin:
    """Gives an entity hub_ir.send_command.

    Adds no state: every HubIR entity already carries _commands, _controller,
    _temp_lock and _device_code, so this is the send path they already have,
    reached by name instead of through a control.
    """

    async def async_send_named_command(
        self,
        command: str,
        repeat: int = 1,
        delay: float = DEFAULT_REPEAT_DELAY,
    ) -> None:
        """Send the code a path names, without changing the entity's state.

        Deliberately does not update the entity. Sending 'extras/menu' says
        nothing about whether the device is on, which mode it is in, or which
        source it shows. Use the platform's own services for state the entity
        models.
        """
        code = resolve_command(self._commands, command)
        if not is_recorded(code):
            raise HomeAssistantError(self._unknown_command_message(command))

        async with self._temp_lock:
            for index in range(repeat):
                if index:
                    await asyncio.sleep(delay)
                await self._controller.send(code)

    def _unknown_command_message(self, command: str) -> str:
        """Report an unresolved path, listing the paths that do exist."""
        available = command_paths(self._commands)
        shown = ", ".join(available[:MAX_SUGGESTIONS])
        more = (
            f" (+{len(available) - MAX_SUGGESTIONS} more)"
            if len(available) > MAX_SUGGESTIONS
            else ""
        )
        return (
            f"Device code {self._device_code} has no code recorded at "
            f"{command!r}. Paths are separated by "
            f"{COMMAND_PATH_SEPARATOR!r}, for example 'extras/menu' or "
            f"'presets/turbo'. This device file offers: {shown}{more}"
        )


async def async_send_raw_code(
    hass: HomeAssistant,
    remote_entity_id_: str,
    code: str | list[str],
    encoding: str = ENC_BASE64,
    delay: float = DEFAULT_SEND_CODE_DELAY,
) -> None:
    """Send a code through a remote with no entity involved.

    Shared with the learning panel's Test button, so what the panel proves works
    and what an automation sends cannot drift apart.
    """
    controller = get_controller(
        hass, BROADLINK_CONTROLLER, encoding, remote_entity_id_, delay
    )
    await controller.send(code)


@callback
def async_register_entity_services() -> None:
    """Register hub_ir.send_command on the platform currently being set up.

    Called from both async_setup_entry and async_setup_platform: both run inside
    an EntityPlatform context, so a YAML entity gets the service exactly as a
    config-entry one does. Home Assistant dedupes the service across platforms
    and dispatches to whichever entities the call targeted, which is also where
    device, area and label targeting comes from for free.
    """
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_SEND_COMMAND,
        SEND_COMMAND_SCHEMA,
        "async_send_named_command",
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the services that need no entity, once per Home Assistant."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_CODE):
        return

    async def _async_send_code(call: ServiceCall) -> None:
        """Handle hub_ir.send_code."""
        await async_send_raw_code(
            hass,
            call.data[ATTR_REMOTE_ENTITY_ID],
            call.data[ATTR_CODE],
            call.data[ATTR_ENCODING],
            call.data[ATTR_DELAY],
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_CODE, _async_send_code, schema=_send_code_schema()
    )
