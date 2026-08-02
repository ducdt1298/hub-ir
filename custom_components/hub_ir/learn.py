"""Learn one IR code from a Broadlink remote, without leaving the browser.

This is the step that used to mean SSH. ``remote.learn_command`` puts the
Broadlink into learning mode and, when a code arrives, writes it into the
Broadlink integration's own storage — the file people open over SSH to copy the
base64 out of. Everything here is about reading it back in process instead.

Two details of the Broadlink integration shape this module:

* The code it stores is ``b64encode(packet).decode("utf8")``, which is exactly
  the Base64 our device files hold. Nothing needs converting.
* ``async_learn_command`` catches its own failures and logs them. A timeout, an
  authorization error, a remote that is switched off — none of them reach the
  caller, so the service call returning tells us nothing. Success has to be
  detected by looking for a code that was not there before.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import ulid as ulid_util

_LOGGER = logging.getLogger(__name__)

BROADLINK_DOMAIN = "broadlink"

# Mirrors homeassistant.components.broadlink.remote.
CODE_STORAGE_VERSION = 1

# The subdevice every scratch code is filed under, so the entries this module
# creates are easy to recognise and clean up.
SCRATCH_DEVICE = "hub_ir_learning"


def broadlink_unique_id(hass: HomeAssistant, remote_entity_id: str) -> str:
    """Return the Broadlink config entry unique_id behind a remote entity.

    The code store is keyed by it. Getting there goes entity registry → device
    registry → the identifier the Broadlink integration registered its device
    with, all public helpers.
    """
    entity_entry = er.async_get(hass).async_get(remote_entity_id)
    if entity_entry is None:
        raise HomeAssistantError(
            f"{remote_entity_id} is not in the entity registry, so it cannot be "
            "a Broadlink remote"
        )

    if entity_entry.platform != BROADLINK_DOMAIN:
        raise HomeAssistantError(
            f"{remote_entity_id} belongs to the '{entity_entry.platform}' "
            "integration, but learning codes needs a Broadlink remote"
        )

    if entity_entry.device_id is None:
        raise HomeAssistantError(
            f"{remote_entity_id} is not attached to a device, so its Broadlink "
            "identifier cannot be found"
        )

    device = dr.async_get(hass).async_get(entity_entry.device_id)
    if device is None:
        raise HomeAssistantError(f"The device behind {remote_entity_id} is gone")

    for domain, identifier in device.identifiers:
        if domain == BROADLINK_DOMAIN:
            return identifier

    raise HomeAssistantError(
        f"The device behind {remote_entity_id} carries no Broadlink identifier"
    )


async def _load_codes(hass: HomeAssistant, unique_id: str) -> dict[str, Any]:
    """Return the Broadlink integration's stored codes.

    A fresh Store every call. Store.async_load reads through to the file unless
    that same instance has a write pending, which ours never does, so keeping
    one around would buy nothing and would couple us to whatever state it had
    accumulated. Home Assistant's shared cache is not a risk either: Store
    invalidates a key before saving it, so once Broadlink has written a code
    every later read comes from disk.
    """
    store = Store[dict[str, Any]](
        hass, CODE_STORAGE_VERSION, f"broadlink_remote_{unique_id}_codes"
    )
    return await store.async_load() or {}


def _assert_remote_ready(hass: HomeAssistant, remote_entity_id: str) -> None:
    """Fail with a usable message when the remote cannot learn right now."""
    state = hass.states.get(remote_entity_id)
    if state is None:
        raise HomeAssistantError(f"The remote entity {remote_entity_id} does not exist")
    if state.state == STATE_UNAVAILABLE:
        raise HomeAssistantError(
            f"The remote entity {remote_entity_id} is unavailable. Check that the "
            "Broadlink device is powered on and reachable"
        )
    if state.state == "off":
        # async_learn_command logs a warning and returns without learning.
        raise HomeAssistantError(
            f"The remote entity {remote_entity_id} is turned off, and a remote "
            "that is off cannot learn. Turn it on and try again"
        )


async def async_learn_ir_code(
    hass: HomeAssistant, remote_entity_id: str, *, toggle: bool = False
) -> str | list[str]:
    """Put the remote into learning mode and return the code it captured.

    Set ``toggle`` for a device whose remote alternates between two codes for
    the same button; the result is then the pair, which the device files and
    the controller already accept as a list.

    Raises HomeAssistantError if nothing was captured.
    """
    unique_id = broadlink_unique_id(hass, remote_entity_id)
    _assert_remote_ready(hass, remote_entity_id)

    # A unique name per attempt: an earlier code left behind under the same name
    # would otherwise be mistaken for a fresh capture.
    command = f"code_{ulid_util.ulid_now()}"

    before = await _load_codes(hass, unique_id)
    if command in (before.get(SCRATCH_DEVICE) or {}):
        raise HomeAssistantError("Learning scratch name collided; try again")

    await hass.services.async_call(
        "remote",
        "learn_command",
        {
            ATTR_ENTITY_ID: remote_entity_id,
            "device": SCRATCH_DEVICE,
            "command": command,
            "command_type": "ir",
            "alternative": toggle,
        },
        blocking=True,
    )

    after = await _load_codes(hass, unique_id)
    code = (after.get(SCRATCH_DEVICE) or {}).get(command)

    await _forget_scratch_code(hass, remote_entity_id, command)

    if not code:
        raise HomeAssistantError(
            "No infrared code arrived within 30 seconds. Point the remote at the "
            "Broadlink, hold it close, and press the button once"
        )

    return code


async def _forget_scratch_code(
    hass: HomeAssistant, remote_entity_id: str, command: str
) -> None:
    """Drop the scratch entry so a long session leaves no litter behind.

    Best effort: the code is already in hand, and failing to tidy up is not a
    reason to fail the capture.
    """
    try:
        await hass.services.async_call(
            "remote",
            "delete_command",
            {
                ATTR_ENTITY_ID: remote_entity_id,
                "device": SCRATCH_DEVICE,
                "command": command,
            },
            blocking=True,
        )
    except Exception as err:
        _LOGGER.debug("Could not delete the scratch code %s: %s", command, err)
