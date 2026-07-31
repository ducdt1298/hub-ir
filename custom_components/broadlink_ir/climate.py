"""Broadlink IR climate platform."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import ATTR_HVAC_MODE, HVAC_MODES
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_TEMPERATURE_UNIT,
    PRECISION_WHOLE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.unit_conversion import TemperatureConverter

from . import Helper, is_recorded
from .controller import get_controller

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Broadlink IR Climate"
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_POWER_SENSOR = "power_sensor"
CONF_POWER_SENSOR_RESTORE_STATE = "power_sensor_restore_state"

# Above this value a device file's min/max temperatures cannot plausibly be
# Celsius, so they are read as Fahrenheit.
_FAHRENHEIT_THRESHOLD = 40

# Keys used inside command trees for documentation rather than for a command.
_ANNOTATION_PREFIXES = ("_", "$")

SUPPORT_FLAGS = (
    ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
)

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): cv.string,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Optional(CONF_TEMPERATURE_UNIT): vol.In(
            [UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT]
        ),
        vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Optional(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
        vol.Optional(CONF_POWER_SENSOR_RESTORE_STATE, default=False): cv.boolean,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IR Climate platform."""
    device_code = config[CONF_DEVICE_CODE]
    device_data = await Helper.load_device_data(hass, "climate", device_code)

    async_add_entities([BroadlinkIRClimate(hass, config, device_data)])


class BroadlinkIRClimate(ClimateEntity, RestoreEntity):
    """A climate entity driven by IR/RF codes from a device file."""

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
        self._temperature_sensor = config.get(CONF_TEMPERATURE_SENSOR)
        self._humidity_sensor = config.get(CONF_HUMIDITY_SENSOR)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._power_sensor_restore_state = config.get(CONF_POWER_SENSOR_RESTORE_STATE)

        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]
        self._min_temperature = device_data["minTemperature"]
        self._max_temperature = device_data["maxTemperature"]
        self._precision = device_data["precision"]

        # The device file's temperatures are in the unit the remote codes were
        # recorded in; HA converts to whatever the user displays.
        self._unit = config.get(CONF_TEMPERATURE_UNIT) or _device_temperature_unit(
            device_data, self._max_temperature
        )

        valid_hvac_modes = [
            mode for mode in device_data["operationModes"] if mode in HVAC_MODES
        ]
        if not valid_hvac_modes:
            raise HomeAssistantError(
                f"Device code {self._device_code} lists no usable operationModes"
            )

        self._operation_modes = [HVACMode.OFF, *valid_hvac_modes]
        self._fan_modes = device_data["fanModes"]
        self._swing_modes = device_data.get("swingModes")
        self._commands = device_data["commands"]

        self._target_temperature = self._min_temperature
        self._hvac_mode = HVACMode.OFF
        self._current_fan_mode = self._fan_modes[0]
        self._current_swing_mode = None
        self._last_on_operation = None

        self._current_temperature = None
        self._current_humidity = None

        # Supported features
        self._support_flags = SUPPORT_FLAGS
        self._support_swing = False

        if self._swing_modes:
            self._support_flags = self._support_flags | ClimateEntityFeature.SWING_MODE
            self._current_swing_mode = self._swing_modes[0]
            self._support_swing = True

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
        """Restore the previous state and start watching the linked sensors."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            # A restored state can be 'unavailable'/'unknown' or a mode the
            # device file no longer offers; both would make HA reject the state.
            if last_state.state in self._operation_modes:
                self._hvac_mode = last_state.state

            if (fan_mode := last_state.attributes.get("fan_mode")) in self._fan_modes:
                self._current_fan_mode = fan_mode

            if self._swing_modes and (
                (swing_mode := last_state.attributes.get("swing_mode"))
                in self._swing_modes
            ):
                self._current_swing_mode = swing_mode

            if (temperature := last_state.attributes.get("temperature")) is not None:
                self._target_temperature = self._restore_temperature(temperature)

            if (
                last_on_operation := last_state.attributes.get("last_on_operation")
            ) in self._operation_modes:
                self._last_on_operation = last_on_operation

        if self._temperature_sensor:
            async_track_state_change_event(
                self.hass, self._temperature_sensor, self._async_temp_sensor_changed
            )

            temp_sensor_state = self.hass.states.get(self._temperature_sensor)
            if temp_sensor_state and temp_sensor_state.state != STATE_UNKNOWN:
                self._async_update_temp(temp_sensor_state)

        if self._humidity_sensor:
            async_track_state_change_event(
                self.hass, self._humidity_sensor, self._async_humidity_sensor_changed
            )

            humidity_sensor_state = self.hass.states.get(self._humidity_sensor)
            if humidity_sensor_state and humidity_sensor_state.state != STATE_UNKNOWN:
                self._async_update_humidity(humidity_sensor_state)

        if self._power_sensor:
            async_track_state_change_event(
                self.hass, self._power_sensor, self._async_power_sensor_changed
            )

    def _restore_temperature(self, temperature: float) -> float:
        """Convert a restored target temperature back into the device's unit.

        Home Assistant writes the 'temperature' attribute in the unit the user
        displays, so a Fahrenheit device file on a Celsius system comes back as
        Celsius and has to be converted before it can index the command tree.
        """
        try:
            converted = TemperatureConverter.convert(
                float(temperature), self.hass.config.units.temperature_unit, self._unit
            )
        except (TypeError, ValueError):
            return self._target_temperature

        if not self._min_temperature <= converted <= self._max_temperature:
            _LOGGER.debug(
                "Restored target temperature %s is outside the %s-%s range of "
                "device code %s, keeping %s",
                converted,
                self._min_temperature,
                self._max_temperature,
                self._device_code,
                self._target_temperature,
            )
            return self._target_temperature

        if self._precision == PRECISION_WHOLE:
            return round(converted)
        return round(converted, 1)

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the name of the climate device."""
        return self._name

    @property
    def temperature_unit(self) -> str:
        """Return the unit the device file's temperatures are expressed in."""
        return self._unit

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature the device accepts."""
        return self._min_temperature

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature the device accepts."""
        return self._max_temperature

    @property
    def target_temperature(self) -> float:
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        return self._precision

    @property
    def hvac_modes(self) -> list[str]:
        """Return the list of available operation modes."""
        return self._operation_modes

    @property
    def hvac_mode(self) -> str:
        """Return hvac mode ie. heat, cool."""
        return self._hvac_mode

    @property
    def last_on_operation(self) -> str | None:
        """Return the last non-idle operation ie. heat, cool."""
        return self._last_on_operation

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes."""
        return self._fan_modes

    @property
    def fan_mode(self) -> str:
        """Return the fan setting."""
        return self._current_fan_mode

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the swing modes currently supported for this device."""
        return self._swing_modes

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode."""
        return self._current_swing_mode

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._current_temperature

    @property
    def current_humidity(self) -> float | None:
        """Return the current humidity."""
        return self._current_humidity

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        return self._support_flags

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Platform specific attributes."""
        return {
            "last_on_operation": self._last_on_operation,
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
            "on_by_remote": self._on_by_remote,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperatures."""
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            return

        if temperature < self._min_temperature or temperature > self._max_temperature:
            _LOGGER.warning("The temperature value is out of min/max range")
            return

        if self._precision == PRECISION_WHOLE:
            self._target_temperature = round(temperature)
        else:
            self._target_temperature = round(temperature, 1)

        if hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
            return

        if self._hvac_mode != HVACMode.OFF:
            await self.send_command()

        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set operation mode."""
        self._hvac_mode = hvac_mode

        if hvac_mode != HVACMode.OFF:
            self._last_on_operation = hvac_mode

        await self.send_command()
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        self._current_fan_mode = fan_mode

        if self._hvac_mode != HVACMode.OFF:
            await self.send_command()
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set swing mode."""
        self._current_swing_mode = swing_mode

        if self._hvac_mode != HVACMode.OFF:
            await self.send_command()
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn on."""
        if self._last_on_operation is not None:
            await self.async_set_hvac_mode(self._last_on_operation)
        else:
            await self.async_set_hvac_mode(self._operation_modes[1])

    async def send_command(self) -> None:
        """Send the code matching the current mode/fan/swing/temperature."""
        async with self._temp_lock:
            try:
                self._on_by_remote = False

                if self._hvac_mode == HVACMode.OFF:
                    await self._controller.send(self._commands["off"])
                    return

                # Resolved before the 'on' code is sent, so a device file that
                # cannot express this state does not leave the unit switched on.
                command = self._resolve_command()

                if "on" in self._commands:
                    await self._controller.send(self._commands["on"])
                    await asyncio.sleep(self._delay)

                await self._controller.send(command)
            except Exception:
                _LOGGER.exception("Error sending command to %s", self._controller_data)

    def _resolve_command(self) -> Any:
        """Return the code for the current state, tolerating sparse files.

        Device files are not uniformly deep. Under 'dry' and 'fan_only' many
        record only one fan mode, or stop at a bare code because the unit
        ignores fan speed and temperature there. Walk down as far as the file
        goes and use the first code found.
        """
        node = _select(
            self._commands, self._hvac_mode, "operation mode", self._device_code
        )
        if not isinstance(node, dict):
            return node

        node = _select(node, self._current_fan_mode, "fan mode", self._device_code)
        if not isinstance(node, dict):
            return node

        if self._support_swing:
            node = _select(
                node, self._current_swing_mode, "swing mode", self._device_code
            )
            if not isinstance(node, dict):
                return node

        return _select_temperature(node, self._target_temperature, self._device_code)

    async def _async_temp_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle temperature sensor changes."""
        new_state = event.data["new_state"]

        if new_state is None:
            return

        self._async_update_temp(new_state)
        self.async_write_ha_state()

    async def _async_humidity_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle humidity sensor changes."""
        new_state = event.data["new_state"]

        if new_state is None:
            return

        self._async_update_humidity(new_state)
        self.async_write_ha_state()

    async def _async_power_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Track the device being switched on or off by its own remote."""
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None:
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        if new_state.state == STATE_ON and self._hvac_mode == HVACMode.OFF:
            self._on_by_remote = True
            # 'on' is not a valid hvac_mode, so guess the real one: HA rejects
            # any state that is not in hvac_modes.
            if self._power_sensor_restore_state and self._last_on_operation:
                self._hvac_mode = self._last_on_operation
            else:
                self._hvac_mode = self._last_on_operation or self._operation_modes[1]
            self.async_write_ha_state()

        if new_state.state == STATE_OFF:
            self._on_by_remote = False
            self._hvac_mode = HVACMode.OFF
            self.async_write_ha_state()

    @callback
    def _async_update_temp(self, state) -> None:
        """Update thermostat with latest state from temperature sensor."""
        try:
            if state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._current_temperature = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from temperature sensor: %s", ex)

    @callback
    def _async_update_humidity(self, state) -> None:
        """Update thermostat with latest state from humidity sensor."""
        try:
            if state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._current_humidity = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from humidity sensor: %s", ex)


def _select(node: Any, key: str, label: str, device_code: int) -> Any:
    """Return ``node[key]``, substituting another entry when the key is absent.

    Device files routinely omit fan and swing modes under 'dry' and 'fan_only'
    even though they list them globally, because the unit ignores them there.
    Sending the nearest recorded code beats sending nothing at all.
    """
    if not isinstance(node, dict) or not node:
        raise HomeAssistantError(
            f"Device code {device_code} has no {label} level in its commands"
        )

    if key in node:
        return node[key]

    # Device files carry '_comment'/'$comment'/'_note' documentation keys inside
    # the command tree. Substituting one would transmit its prose as a code.
    # Note that '' is a real, selectable fan mode in some files, so only the
    # annotation prefixes are excluded.
    candidates = {
        name: value
        for name, value in node.items()
        if not name.startswith(_ANNOTATION_PREFIXES)
    }
    if not candidates:
        raise HomeAssistantError(
            f"Device code {device_code} has no {label} to select {key!r} from"
        )

    substitute, value = next(iter(candidates.items()))

    if len(candidates) == 1:
        _LOGGER.debug(
            "Device code %s has no %s %r, using the only one it defines (%r)",
            device_code,
            label,
            key,
            substitute,
        )
    else:
        _LOGGER.warning(
            "Device code %s has no %s %r under this mode, falling back to %r. "
            "Defined: %s",
            device_code,
            label,
            key,
            substitute,
            ", ".join(repr(name) for name in node),
        )

    return value


def _select_temperature(node: Any, target: float, device_code: int) -> Any:
    """Return the code for ``target``, or for the closest one recorded.

    Parts of the database leave a temperature's code as an empty string where it
    was never captured, so those are skipped in favour of a neighbour that has
    one.
    """
    if not isinstance(node, dict):
        raise HomeAssistantError(
            f"Device code {device_code} has no temperature level in its commands"
        )

    key = f"{target:g}"
    if is_recorded(node.get(key)):
        return node[key]

    numeric = {}
    for candidate, value in node.items():
        if not is_recorded(value):
            continue
        try:
            numeric[float(candidate)] = candidate
        except (TypeError, ValueError):
            continue

    if not numeric:
        raise HomeAssistantError(
            f"Device code {device_code} records no temperature codes for this "
            "combination of mode, fan speed and swing"
        )

    closest = min(numeric, key=lambda value: abs(value - target))
    _LOGGER.debug(
        "Device code %s has no code for %s degrees in this mode, using %s",
        device_code,
        key,
        numeric[closest],
    )
    return node[numeric[closest]]


def _device_temperature_unit(
    device_data: dict[str, Any], max_temperature: float
) -> str:
    """Return the temperature unit a device file's codes were recorded in."""
    declared = str(device_data.get("temperatureUnit", "")).upper()
    if declared in ("F", "°F", UnitOfTemperature.FAHRENHEIT):
        return UnitOfTemperature.FAHRENHEIT
    if declared in ("C", "°C", UnitOfTemperature.CELSIUS):
        return UnitOfTemperature.CELSIUS

    if max_temperature > _FAHRENHEIT_THRESHOLD:
        return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS
