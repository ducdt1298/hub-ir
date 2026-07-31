"""Broadlink IR fan platform."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    PLATFORM_SCHEMA as FAN_PLATFORM_SCHEMA,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.const import CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from . import Helper
from .controller import get_controller

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Broadlink IR Fan"
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_POWER_SENSOR = "power_sensor"

SPEED_OFF = "off"

PLATFORM_SCHEMA = FAN_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): cv.string,
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
    """Set up the IR Fan platform."""
    device_code = config[CONF_DEVICE_CODE]
    device_data = await Helper.load_device_data(hass, "fan", device_code)

    async_add_entities([BroadlinkIRFan(hass, config, device_data)])


class BroadlinkIRFan(FanEntity, RestoreEntity):
    """A fan entity driven by IR/RF codes from a device file."""

    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, config: ConfigType, device_data: dict[str, Any]
    ) -> None:
        """Set the entity up from its YAML config and its device file."""
        self.hass = hass
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._delay = config.get(CONF_DELAY)
        self._power_sensor = config.get(CONF_POWER_SENSOR)

        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]
        self._speed_list = device_data["speed"]
        self._commands = device_data["commands"]

        if not self._speed_list:
            raise HomeAssistantError(
                f"Device code {self._device_code} lists no fan speeds"
            )

        self._speed = SPEED_OFF
        self._direction = None
        self._last_on_speed = None
        self._oscillating = None
        self._support_flags = (
            FanEntityFeature.SET_SPEED
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.TURN_ON
        )

        if DIRECTION_REVERSE in self._commands and DIRECTION_FORWARD in self._commands:
            self._direction = DIRECTION_REVERSE
            self._support_flags = self._support_flags | FanEntityFeature.DIRECTION
        if "oscillate" in self._commands:
            self._oscillating = False
            self._support_flags = self._support_flags | FanEntityFeature.OSCILLATE

        self._temp_lock = asyncio.Lock()
        self._on_by_remote = False

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
            if (speed := last_state.attributes.get("speed")) in self._speed_list:
                self._speed = speed

            # If _direction has a value the direction controls appear in the UI
            # even when DIRECTION is not in the supported features.
            if (
                direction := last_state.attributes.get("direction")
            ) and self._support_flags & FanEntityFeature.DIRECTION:
                self._direction = direction

            if (
                last_on_speed := last_state.attributes.get("last_on_speed")
            ) in self._speed_list:
                self._last_on_speed = last_on_speed

        # Registered outside the restore block: a fan with no previous state
        # still needs its power sensor tracked.
        if self._power_sensor:
            async_track_state_change_event(
                self.hass, self._power_sensor, self._async_power_sensor_changed
            )

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the display name of the fan."""
        return self._name

    @property
    def is_on(self) -> bool:
        """Return true when the fan is running."""
        return self._on_by_remote or self._speed != SPEED_OFF

    @property
    def percentage(self) -> int:
        """Return speed percentage of the fan."""
        if self._speed == SPEED_OFF:
            return 0

        return ordered_list_item_to_percentage(self._speed_list, self._speed)

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        return len(self._speed_list)

    @property
    def oscillating(self) -> bool | None:
        """Return the oscillation state."""
        return self._oscillating

    @property
    def current_direction(self) -> str | None:
        """Return the direction state."""
        return self._direction

    @property
    def last_on_speed(self) -> str | None:
        """Return the last non-idle speed."""
        return self._last_on_speed

    @property
    def supported_features(self) -> FanEntityFeature:
        """Return the list of supported features."""
        return self._support_flags

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Platform specific attributes."""
        return {
            "last_on_speed": self._last_on_speed,
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
            "on_by_remote": self._on_by_remote,
        }

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the desired speed for the fan."""
        if percentage == 0:
            self._speed = SPEED_OFF
        else:
            self._speed = percentage_to_ordered_list_item(self._speed_list, percentage)

        if self._speed != SPEED_OFF:
            self._last_on_speed = self._speed

        await self.send_command()
        self.async_write_ha_state()

    async def async_oscillate(self, oscillating: bool) -> None:
        """Set oscillation of the fan."""
        self._oscillating = oscillating

        await self.send_command()
        self.async_write_ha_state()

    async def async_set_direction(self, direction: str) -> None:
        """Set the direction of the fan."""
        self._direction = direction

        if self._speed != SPEED_OFF:
            await self.send_command()

        self.async_write_ha_state()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if percentage is None:
            percentage = ordered_list_item_to_percentage(
                self._speed_list, self._last_on_speed or self._speed_list[0]
            )

        await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self.async_set_percentage(0)

    async def send_command(self) -> None:
        """Send the code matching the current speed/direction/oscillation."""
        async with self._temp_lock:
            self._on_by_remote = False
            speed = self._speed
            direction = self._direction or "default"
            oscillating = self._oscillating

            try:
                if speed == SPEED_OFF:
                    command = self._commands["off"]
                elif oscillating:
                    command = self._commands["oscillate"]
                else:
                    command = self._commands[direction][speed]
            except KeyError:
                _LOGGER.error(
                    "Device code %s has no command for speed '%s', direction "
                    "'%s', oscillating '%s'",
                    self._device_code,
                    speed,
                    direction,
                    oscillating,
                )
                return

            try:
                await self._controller.send(command)
            except Exception:
                _LOGGER.exception("Error sending command to %s", self._controller_data)

    async def _async_power_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Track the fan being switched on or off by its own remote."""
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None:
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        if new_state.state == STATE_ON and self._speed == SPEED_OFF:
            # Keep _speed a real speed: percentage and send_command both index
            # the speed list with it.
            self._on_by_remote = True
            self.async_write_ha_state()

        if new_state.state == STATE_OFF:
            self._on_by_remote = False
            self._speed = SPEED_OFF
            self.async_write_ha_state()
