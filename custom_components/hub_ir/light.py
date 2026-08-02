"""HubIR light platform."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    PLATFORM_SCHEMA as LIGHT_PLATFORM_SCHEMA,
    ColorMode,
    LightEntity,
)
from homeassistant.const import CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import (
    Helper,
    HubIRConfigEntry,
    entry_config,
    is_recorded,
    optimistic_state,
    remote_entity_id,
    warn_if_no_unique_id,
)
from .const import CONF_DEVICE_INFO
from .controller import get_controller

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "HubIR Light"
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_POWER_SENSOR = "power_sensor"

CMD_BRIGHTNESS_INCREASE = "brighten"
CMD_BRIGHTNESS_DECREASE = "dim"
CMD_COLORMODE_COLDER = "colder"
CMD_COLORMODE_WARMER = "warmer"
CMD_POWER_ON = "on"
CMD_POWER_OFF = "off"
CMD_NIGHTLIGHT = "night"

PLATFORM_SCHEMA = LIGHT_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): remote_entity_id,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IR Light platform."""
    device_code = config[CONF_DEVICE_CODE]
    device_data = await Helper.load_device_data(hass, "light", device_code)

    warn_if_no_unique_id("light", config)

    async_add_entities([HubIRLight(hass, config, device_data)])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubIRConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a HubIR light from a config entry."""
    device_data = entry.runtime_data
    try:
        entity = HubIRLight(hass, entry_config(entry, device_data), device_data)
    except HomeAssistantError as err:
        # async_validate_device already rejected everything it can see coming,
        # so anything left is permanent: retrying would only repeat it.
        raise ConfigEntryError(str(err)) from err

    async_add_entities([entity])


def closest_match(value: float | None, values: list[float]) -> int:
    """Return the index in the sorted list ``values`` closest to ``value``."""
    value = value or 0
    prev_val = None
    for index, entry in enumerate(values):
        if entry > value:
            if prev_val is None:
                return index
            if value - prev_val < entry - value:
                return index - 1
            return index
        prev_val = entry

    return len(values) - 1


class HubIRLight(LightEntity, RestoreEntity):
    """A light entity driven by IR/RF codes from a device file."""

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
        self._brightnesses = device_data.get("brightness") or []
        self._colortemps = device_data.get("colorTemperature") or []
        self._commands = device_data["commands"]

        self._power = STATE_OFF
        self._brightness = None
        self._colortemp = None

        self._temp_lock = asyncio.Lock()
        self._on_by_remote = False
        self._support_color_mode = ColorMode.ONOFF

        if (
            self._colortemps
            and CMD_COLORMODE_COLDER in self._commands
            and CMD_COLORMODE_WARMER in self._commands
        ):
            self._colortemp = self.max_color_temp_kelvin
            self._support_color_mode = ColorMode.COLOR_TEMP

        self._support_brightness = CMD_NIGHTLIGHT in self._commands or (
            CMD_BRIGHTNESS_INCREASE in self._commands
            and CMD_BRIGHTNESS_DECREASE in self._commands
        )
        if self._support_brightness:
            self._brightness = 100
            if self._support_color_mode == ColorMode.ONOFF:
                self._support_color_mode = ColorMode.BRIGHTNESS

        # Init the IR/RF controller
        self._controller = get_controller(
            self.hass,
            self._supported_controller,
            self._commands_encoding,
            self._controller_data,
            self._delay,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and start watching the power sensor."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in (STATE_ON, STATE_OFF):
                self._power = last_state.state
            if (brightness := last_state.attributes.get(ATTR_BRIGHTNESS)) is not None:
                self._brightness = brightness
            if (
                colortemp := last_state.attributes.get(ATTR_COLOR_TEMP_KELVIN)
            ) is not None:
                self._colortemp = colortemp

        # Tracked through async_on_remove so a reload does not leave the old
        # listener answering as well.
        if self._power_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._power_sensor, self._async_power_sensor_changed
                )
            )

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the display name of the light."""
        return self._name

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return the list of supported color modes."""
        return {self._support_color_mode}

    @property
    def color_mode(self) -> ColorMode:
        """Return the active color mode."""
        return self._support_color_mode

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        return self._colortemp

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the coldest color temperature the device supports."""
        if self._colortemps:
            return self._colortemps[0]
        return super().min_color_temp_kelvin

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the warmest color temperature the device supports."""
        if self._colortemps:
            return self._colortemps[-1]
        return super().max_color_temp_kelvin

    @property
    def is_on(self) -> bool:
        """Return true when the light is on."""
        return self._power == STATE_ON or self._on_by_remote

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        return self._brightness

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Platform specific attributes."""
        return {
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
            "on_by_remote": self._on_by_remote,
        }

    async def async_turn_on(self, **params: Any) -> None:
        """Turn the light on, optionally changing brightness/color temp."""
        async with optimistic_state(
            self, "_power", "_brightness", "_colortemp", "_on_by_remote"
        ):
            await self._async_turn_on(**params)

    async def _async_turn_on(self, **params: Any) -> None:
        """Do the work of turning on, so the caller can roll back on failure."""
        did_something = False
        # Turn the light on if off
        if self._power != STATE_ON and not self._on_by_remote:
            self._power = STATE_ON
            did_something = True
            await self.send_command(CMD_POWER_ON)

        if (
            ATTR_COLOR_TEMP_KELVIN in params
            and self._support_color_mode == ColorMode.COLOR_TEMP
        ):
            target = params.get(ATTR_COLOR_TEMP_KELVIN)
            old_color_temp = closest_match(self._colortemp, self._colortemps)
            new_color_temp = closest_match(target, self._colortemps)
            _LOGGER.debug(
                "Changing color temp from %sK step %s to %sK step %s",
                self._colortemp,
                old_color_temp,
                target,
                new_color_temp,
            )

            steps = new_color_temp - old_color_temp
            did_something = True
            if steps < 0:
                cmd = CMD_COLORMODE_WARMER
                steps = abs(steps)
            else:
                cmd = CMD_COLORMODE_COLDER

            if steps > 0:
                # If we are heading for the highest or lowest value, take the
                # opportunity to resync by issuing enough commands to go the
                # full range.
                if new_color_temp in (0, len(self._colortemps) - 1):
                    steps = len(self._colortemps)
                self._colortemp = self._colortemps[new_color_temp]
                await self.send_command(cmd, steps)

        if ATTR_BRIGHTNESS in params and self._support_brightness:
            # Before checking the supported brightnesses, make a special case
            # when a nightlight is fitted for brightness of 1.
            if params.get(ATTR_BRIGHTNESS) == 1 and CMD_NIGHTLIGHT in self._commands:
                self._brightness = 1
                self._power = STATE_ON
                did_something = True
                await self.send_command(CMD_NIGHTLIGHT)

            elif self._brightnesses:
                target = params.get(ATTR_BRIGHTNESS)
                old_brightness = closest_match(self._brightness, self._brightnesses)
                new_brightness = closest_match(target, self._brightnesses)
                did_something = True
                _LOGGER.debug(
                    "Changing brightness from %s step %s to %s step %s",
                    self._brightness,
                    old_brightness,
                    target,
                    new_brightness,
                )
                steps = new_brightness - old_brightness
                if steps < 0:
                    cmd = CMD_BRIGHTNESS_DECREASE
                    steps = abs(steps)
                else:
                    cmd = CMD_BRIGHTNESS_INCREASE

                if steps > 0:
                    # Resync at the ends of the range, as above.
                    if new_brightness in (0, len(self._brightnesses) - 1):
                        steps = len(self._brightnesses)
                    self._brightness = self._brightnesses[new_brightness]
                    await self.send_command(cmd, steps)

        # If we did nothing above, and the light is not detected as on already,
        # issue the on command even though we think the light is on. This is
        # because we may be out of sync due to use of the remote when we don't
        # have anything to detect it. If we do have such monitoring, avoid
        # issuing the command in case on and off are the same remote code.
        if not did_something and not self._on_by_remote:
            self._power = STATE_ON
            await self.send_command(CMD_POWER_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        async with optimistic_state(self, "_power", "_on_by_remote"):
            self._power = STATE_OFF
            await self.send_command(CMD_POWER_OFF)

    async def send_command(self, cmd: str, count: int = 1) -> None:
        """Send a named command from the device file ``count`` times.

        Raises HomeAssistantError if the command cannot be delivered, so the
        caller does not publish a state that was never transmitted.
        """
        if not is_recorded(self._commands.get(cmd)):
            raise HomeAssistantError(
                f"Device code {self._device_code} has no code recorded for "
                f"'{cmd}', so that cannot be controlled from Home Assistant"
            )
        _LOGGER.debug("Sending %s remote command %s times", cmd, count)
        remote_cmd = self._commands.get(cmd)
        async with self._temp_lock:
            self._on_by_remote = False
            for _ in range(count):
                await self._controller.send(remote_cmd)

    async def _async_power_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Track the light being switched on or off by its own remote."""
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None:
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        if new_state.state == STATE_ON:
            self._on_by_remote = True
            self.async_write_ha_state()

        if new_state.state == STATE_OFF:
            self._on_by_remote = False
            self._power = STATE_OFF
            self.async_write_ha_state()
