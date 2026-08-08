"""HubIR switch platform.

For the IR devices that are only ever on or off: an amplifier, a projector, a
bathroom heat lamp, a fan heater. Before this platform they had to be squeezed
into a media player, which advertised volume and sources the device does not
have.

Two shapes of remote are supported, because both are common:

* separate **on** and **off** buttons, which is the easy case; and
* a single **toggle** button, where the same code alternates. That one needs the
  entity to believe a state, because sending the code when the device already
  matches would do the opposite of what was asked.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.switch import (
    PLATFORM_SCHEMA as SWITCH_PLATFORM_SCHEMA,
    SwitchEntity,
)
from homeassistant.const import CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
import homeassistant.util.dt as dt_util

from . import (
    Helper,
    HubIRConfigEntry,
    entry_config,
    is_recorded,
    optimistic_state,
    remote_entity_id,
    warn_if_no_unique_id,
)
from .const import (
    CONF_DEVICE_INFO,
    CONF_POWER_SENSOR_REASSERT,
    CONF_REASSERT_INTERVAL,
    REASSERT_MAX_ATTEMPTS,
    REASSERT_SETTLE_SECONDS,
)
from .controller import get_controller
from .services import HubIRCommandMixin, async_register_entity_services

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "HubIR Switch"
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_POWER_SENSOR = "power_sensor"

CMD_ON = "on"
CMD_OFF = "off"
CMD_TOGGLE = "toggle"

# Which command a requested state asks for. Spelled out rather than relying on
# STATE_ON happening to equal the command name.
COMMAND_FOR_STATE = {STATE_ON: CMD_ON, STATE_OFF: CMD_OFF}

PLATFORM_SCHEMA = SWITCH_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): remote_entity_id,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
        vol.Optional(CONF_POWER_SENSOR_REASSERT, default=False): cv.boolean,
        vol.Optional(CONF_REASSERT_INTERVAL, default=0): cv.positive_int,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IR switch platform."""
    device_code = config[CONF_DEVICE_CODE]
    device_data = await Helper.load_device_data(hass, "switch", device_code)

    warn_if_no_unique_id("switch", config)

    async_register_entity_services()
    async_add_entities([HubIRSwitch(hass, config, device_data)])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubIRConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a HubIR switch from a config entry."""
    device_data = entry.runtime_data
    config = entry_config(entry, device_data)

    try:
        entity = HubIRSwitch(hass, config, device_data)
    except HomeAssistantError as err:
        # async_validate_device already rejected everything it can see coming,
        # so anything left is permanent: retrying would only repeat it.
        raise ConfigEntryError(str(err)) from err

    async_register_entity_services()
    async_add_entities([entity])


class HubIRSwitch(HubIRCommandMixin, SwitchEntity, RestoreEntity):
    """A switch entity driven by IR/RF codes from a device file."""

    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, config: ConfigType, device_data: dict[str, Any]
    ) -> None:
        """Set the entity up from its YAML config and its device file."""
        self.hass = hass
        self._unique_id = config.get(CONF_UNIQUE_ID)
        # Only a config entry supplies this; a YAML entity leaves device_info
        # None and stays out of the device registry.
        self._attr_device_info = config.get(CONF_DEVICE_INFO)
        self._name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._delay = config.get(CONF_DELAY)
        self._power_sensor = config.get(CONF_POWER_SENSOR)

        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]
        self._commands = device_data["commands"]

        if not any(self._has_code(name) for name in (CMD_ON, CMD_OFF, CMD_TOGGLE)):
            raise HomeAssistantError(
                f"Device code {self._device_code} records no 'on', 'off' or "
                "'toggle' code, so the switch has no usable commands"
            )

        self._state = STATE_OFF
        self._read_reassert_options(config)
        self._temp_lock = asyncio.Lock()

        # Init the IR/RF controller
        self._controller = get_controller(
            self.hass,
            self._supported_controller,
            self._commands_encoding,
            self._controller_data,
            self._delay,
        )

    def _read_reassert_options(self, config: ConfigType) -> None:
        """Set up re-asserting a state the power sensor contradicts.

        Refused outright for a remote with only a toggle button. Re-sending that
        code when the sensor and the entity disagree is a coin flip that can
        oscillate for ever, because nothing in the pair is absolute.
        """
        self._reassert = bool(config.get(CONF_POWER_SENSOR_REASSERT))
        self._reassert_interval = int(config.get(CONF_REASSERT_INTERVAL) or 0)
        self._reassert_attempts = 0
        self._reassert_suspended = False
        self._last_transmit = None

        absolute = self._has_code(CMD_ON) and self._has_code(CMD_OFF)
        if absolute or not (self._reassert or self._reassert_interval):
            return

        _LOGGER.warning(
            "Device code %s records only a toggle code, so %s and %s are ignored: "
            "re-sending a toggle when the sensor disagrees could switch the "
            "device either way",
            self._device_code,
            CONF_POWER_SENSOR_REASSERT,
            CONF_REASSERT_INTERVAL,
        )
        self._reassert = False
        self._reassert_interval = 0

    def _has_code(self, command: str) -> bool:
        """Return whether the device file records a usable code for a command."""
        return is_recorded(self._commands.get(command))

    def _command(self, name: str) -> str | list[str]:
        """Return a recorded command, or explain why it cannot be sent."""
        command = self._commands.get(name)
        if not is_recorded(command):
            raise HomeAssistantError(
                f"Device code {self._device_code} has no code recorded for "
                f"'{name}'; it cannot be controlled"
            )
        return command

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and start watching the power sensor."""
        await super().async_added_to_hass()

        # A restored state can be 'unavailable'/'unknown', neither of which is
        # something this entity can be.
        if (last_state := await self.async_get_last_state()) is not None and (
            last_state.state in (STATE_ON, STATE_OFF)
        ):
            self._state = last_state.state

        # Tracked through async_on_remove so a reload does not leave the old
        # listener answering as well.
        if self._power_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._power_sensor, self._async_power_sensor_changed
                )
            )

            # The sensor is the source of truth, so adopt its current reading
            # rather than waiting for it to change.
            if power_state := self.hass.states.get(self._power_sensor):
                self._update_from_power_state(power_state.state)

        # Cancelled on unload and on every options-flow reload, the same way the
        # sensor listener above is.
        if self._reassert_interval:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_reassert_tick,
                    timedelta(minutes=self._reassert_interval),
                    cancel_on_shutdown=True,
                )
            )

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return self._name

    @property
    def is_on(self) -> bool:
        """Return whether the switch is believed to be on."""
        return self._state == STATE_ON

    @property
    def assumed_state(self) -> bool:
        """Return whether the state is a belief rather than an observation.

        IR is open-loop: without a power sensor nothing reports back, and the UI
        should offer separate on and off buttons rather than one toggle that can
        get out of step with the device.
        """
        return self._power_sensor is None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Platform specific attributes."""
        return {
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
            "reassert_attempts": self._reassert_attempts,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_apply(STATE_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_apply(STATE_OFF)

    def _power_command(self, state: str) -> str | None:
        """Return the command that moves the device to a state.

        None means the device is believed to be there already and the remote has
        only a toggle button, so sending anything would move it the wrong way.
        Refusing is the whole reason a toggle-only device needs the entity to
        believe a state at all.
        """
        dedicated = COMMAND_FOR_STATE[state]

        if self._has_code(dedicated):
            return dedicated

        if not self._has_code(CMD_TOGGLE):
            # Raises naming the command that was asked for, rather than the
            # fallback the user has never heard of.
            self._command(dedicated)

        return None if self._state == state else CMD_TOGGLE

    async def _async_apply(self, state: str) -> None:
        """Send whichever code moves the device to a state, if one is needed."""
        command = self._power_command(state)
        if command is None:
            _LOGGER.debug(
                "Device code %s is already %s and its remote has only a toggle "
                "button, so nothing is sent",
                self._device_code,
                state,
            )
            return

        async with optimistic_state(self, "_state"):
            self._state = state
            # A command from the user is a fresh start for the re-assert budget.
            self._reassert_attempts = 0
            self._reassert_suspended = False
            async with self._temp_lock:
                await self._controller.send(self._command(command))
                self._last_transmit = dt_util.utcnow()

    async def _async_power_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Track the device being switched on or off by something else.

        With power_sensor_reassert off — the default — this adopts the reading and
        nothing else, exactly as it always has.
        """
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None or new_state.state not in (STATE_ON, STATE_OFF):
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        first_reading = old_state is None or old_state.state not in (
            STATE_ON,
            STATE_OFF,
        )

        # Only one direction is ever re-asserted: sensor off while we believe on.
        # A sensor saying on while we believe off is almost always somebody with
        # the original remote, and turning their amplifier off over it would be
        # the worst thing this could do.
        if (
            self._reassert
            and not first_reading
            and new_state.state == STATE_OFF
            and self._state == STATE_ON
        ):
            await self._async_handle_contradiction()
            return

        self._update_from_power_state(new_state.state)
        self.async_write_ha_state()

    async def _async_handle_contradiction(self) -> None:
        """Re-send 'on' when the sensor says the device is off."""
        if self._within_settle_window():
            # The sensor is catching up with what we just sent.
            return

        if self._reassert_attempts >= REASSERT_MAX_ATTEMPTS:
            if not self._reassert_suspended:
                self._reassert_suspended = True
                _LOGGER.warning(
                    "Device code %s: %s still reports off after %s attempts to "
                    "re-send 'on', so %s is giving up and following the sensor. "
                    "Check the emitter, or turn off %s",
                    self._device_code,
                    self._power_sensor,
                    REASSERT_MAX_ATTEMPTS,
                    self.entity_id,
                    CONF_POWER_SENSOR_REASSERT,
                )
            self._state = STATE_OFF
            self.async_write_ha_state()
            return

        self._reassert_attempts += 1
        await self._async_reassert()
        self.async_write_ha_state()

    def _within_settle_window(self) -> bool:
        """Return whether we transmitted too recently to trust a contradiction."""
        if self._last_transmit is None:
            return False
        elapsed = dt_util.utcnow() - self._last_transmit
        return elapsed.total_seconds() < REASSERT_SETTLE_SECONDS

    async def _async_reassert(self) -> None:
        """Re-send the code for the believed state.

        Swallows failures: both callers are background, and neither has anywhere
        to report to. Not wrapped in optimistic_state either, because no state is
        changing and a failure has nothing to roll back.
        """
        command = COMMAND_FOR_STATE[self._state]
        try:
            async with self._temp_lock:
                await self._controller.send(self._command(command))
                self._last_transmit = dt_util.utcnow()
        except HomeAssistantError as err:
            _LOGGER.debug(
                "Device code %s: re-assert failed: %s", self._device_code, err
            )

    async def _async_reassert_tick(self, _now) -> None:
        """Re-send the current state on a timer, for a device that drifts."""
        if self._state != STATE_ON or self._reassert_suspended:
            # Nothing is re-sent while off: transmitting at a device somebody
            # just switched on by hand is the same fight as above.
            return

        if self._within_settle_window():
            return

        await self._async_reassert()

    @callback
    def _update_from_power_state(self, power_state: str) -> None:
        """Adopt the power sensor's reading as the switch's state."""
        if power_state in (STATE_ON, STATE_OFF):
            self._state = power_state
