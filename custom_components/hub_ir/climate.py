"""HubIR climate platform."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    HVAC_MODES,
    PRESET_NONE,
)
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
from homeassistant.util.unit_conversion import TemperatureConverter

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
from .device_file import ANNOTATION_PREFIXES, PRESETS_KEY, RESERVED_COMMAND_KEYS
from .services import HubIRCommandMixin, async_register_entity_services

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "HubIR Climate"
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
_ANNOTATION_PREFIXES = ANNOTATION_PREFIXES

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
        vol.Required(CONF_CONTROLLER_DATA): remote_entity_id,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Optional(CONF_TEMPERATURE_UNIT): vol.In(
            [UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT]
        ),
        vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Optional(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
        vol.Optional(CONF_POWER_SENSOR_RESTORE_STATE, default=False): cv.boolean,
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
    """Set up the IR Climate platform."""
    device_code = config[CONF_DEVICE_CODE]
    device_data = await Helper.load_device_data(hass, "climate", device_code)

    warn_if_no_unique_id("climate", config)

    async_register_entity_services()
    async_add_entities([HubIRClimate(hass, config, device_data)])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubIRConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a HubIR climate entity from a config entry."""
    device_data = entry.runtime_data
    try:
        entity = HubIRClimate(hass, entry_config(entry, device_data), device_data)
    except HomeAssistantError as err:
        # async_validate_device already rejected everything it can see coming,
        # so anything left is permanent: retrying would only repeat it.
        raise ConfigEntryError(str(err)) from err

    async_register_entity_services()
    async_add_entities([entity])


class HubIRClimate(HubIRCommandMixin, ClimateEntity, RestoreEntity):
    """A climate entity driven by IR/RF codes from a device file."""

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
        self._temperature_sensor = config.get(CONF_TEMPERATURE_SENSOR)
        self._humidity_sensor = config.get(CONF_HUMIDITY_SENSOR)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._power_sensor_restore_state = config.get(CONF_POWER_SENSOR_RESTORE_STATE)
        self._read_reassert_options(config)

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
            device_data, self._max_temperature, self._device_code
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

        self._preset_mode = PRESET_NONE
        self._preset_baseline = self._parse_preset_baseline(device_data)
        self._preset_modes = self._recorded_presets()
        if self._preset_modes:
            self._support_flags = self._support_flags | ClimateEntityFeature.PRESET_MODE

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

    def _read_reassert_options(self, config: ConfigType) -> None:
        """Set up re-asserting a state the power sensor contradicts.

        Off unless asked for, so every configuration that already exists keeps
        the behaviour it has: adopt whatever the sensor says and transmit nothing.
        """
        self._reassert = bool(config.get(CONF_POWER_SENSOR_REASSERT))
        self._reassert_interval = int(config.get(CONF_REASSERT_INTERVAL) or 0)
        self._reassert_attempts = 0
        self._reassert_suspended = False
        # When we last put a code on the air, so a contradiction that is really
        # the sensor catching up can be told apart from a command that was missed.
        self._last_transmit = None

    def _recorded_presets(self) -> list[str]:
        """Return the preset modes to offer, PRESET_NONE first, or nothing.

        Derived from the codes the file actually records rather than from a list
        beside them. A second source of truth is a second thing that can
        disagree with the tree, which is why media_player's source_list is built
        the same way, and a placeholder must never be advertised as sendable.
        """
        presets = self._commands.get(PRESETS_KEY) or {}
        recorded = [
            name
            for name, command in presets.items()
            if not str(name).startswith(_ANNOTATION_PREFIXES) and is_recorded(command)
        ]

        if skipped := len(presets) - len(recorded):
            _LOGGER.warning(
                "Device code %s leaves %s of its %s preset(s) unrecorded, so they "
                "are not offered",
                self._device_code,
                skipped,
                len(presets),
            )

        return [PRESET_NONE, *recorded] if recorded else []

    def _parse_preset_baseline(self, device_data: dict[str, Any]) -> dict[str, Any]:
        """Return the parts of the file's preset baseline this entity can honour.

        validate() checks the same things, but it never runs when an entity is
        constructed: a hand-written or hand-edited file reaches this code without
        having passed it. Anything unusable is dropped with a warning rather than
        allowed to become a state Home Assistant will reject.
        """
        declared = device_data.get("presetBaseline")
        if not isinstance(declared, dict):
            return {}

        baseline: dict[str, Any] = {}
        for key, allowed in (
            ("operationMode", self._operation_modes),
            ("fanMode", self._fan_modes),
            ("swingMode", self._swing_modes or []),
        ):
            if (value := declared.get(key)) is None:
                continue
            if value in allowed:
                baseline[key] = value
            else:
                _LOGGER.warning(
                    "Device code %s has presetBaseline.%s %r, which it does not "
                    "offer; ignoring it",
                    self._device_code,
                    key,
                    value,
                )

        temperature = declared.get("temperature")
        if isinstance(temperature, int | float) and not isinstance(temperature, bool):
            if self._min_temperature <= temperature <= self._max_temperature:
                baseline["temperature"] = self._round_to_precision(temperature)
            else:
                _LOGGER.warning(
                    "Device code %s has presetBaseline.temperature %g, outside its "
                    "%g-%g range; ignoring it",
                    self._device_code,
                    temperature,
                    self._min_temperature,
                    self._max_temperature,
                )

        return baseline

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

            # PRESET_NONE is in _preset_modes, so restoring 'none' works too.
            if self._preset_modes and (
                (preset := last_state.attributes.get(ATTR_PRESET_MODE))
                in self._preset_modes
            ):
                self._preset_mode = preset

        # Tracked through async_on_remove: the options flow reloads the entry on
        # every Save, and a listener left behind would keep answering afterwards.
        if self._temperature_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._temperature_sensor, self._async_temp_sensor_changed
                )
            )

            temp_sensor_state = self.hass.states.get(self._temperature_sensor)
            if temp_sensor_state and temp_sensor_state.state != STATE_UNKNOWN:
                self._async_update_temp(temp_sensor_state)

        if self._humidity_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._humidity_sensor,
                    self._async_humidity_sensor_changed,
                )
            )

            humidity_sensor_state = self.hass.states.get(self._humidity_sensor)
            if humidity_sensor_state and humidity_sensor_state.state != STATE_UNKNOWN:
                self._async_update_humidity(humidity_sensor_state)

        if self._power_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._power_sensor, self._async_power_sensor_changed
                )
            )

        # Cancelled on unload and on every options-flow reload, the same way the
        # sensor listeners above are.
        if self._reassert_interval:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_reassert_tick,
                    timedelta(minutes=self._reassert_interval),
                    # Nothing about a refresh is worth delaying a shutdown for.
                    cancel_on_shutdown=True,
                )
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
    def preset_modes(self) -> list[str] | None:
        """Return the one-touch buttons the device file records, if any.

        None rather than an empty list: the climate component only accepts a
        preset_mode when PRESET_MODE is among the supported features.
        """
        return self._preset_modes or None

    @property
    def preset_mode(self) -> str | None:
        """Return the preset believed to be active."""
        return self._preset_mode if self._preset_modes else None

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
            # Always present, 0 when re-assert is off. Makes "is it fighting the
            # device?" answerable from a dashboard rather than from the log.
            "reassert_attempts": self._reassert_attempts,
        }

    def _round_to_precision(self, temperature: float) -> float:
        """Return a temperature rounded to the step the device accepts."""
        if self._precision == PRECISION_WHOLE:
            return round(temperature)
        return round(temperature, 1)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperatures."""
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            return

        if temperature < self._min_temperature or temperature > self._max_temperature:
            _LOGGER.warning("The temperature value is out of min/max range")
            return

        if hvac_mode:
            async with optimistic_state(
                self,
                "_target_temperature",
                "_hvac_mode",
                "_last_on_operation",
                "_on_by_remote",
                "_preset_mode",
            ):
                self._target_temperature = self._round_to_precision(temperature)
                self._hvac_mode = hvac_mode
                if hvac_mode != HVACMode.OFF:
                    self._last_on_operation = hvac_mode
                await self.send_command()
            return

        async with optimistic_state(
            self, "_target_temperature", "_on_by_remote", "_preset_mode"
        ):
            self._target_temperature = self._round_to_precision(temperature)
            if self._hvac_mode != HVACMode.OFF:
                await self.send_command()

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set operation mode."""
        async with optimistic_state(
            self, "_hvac_mode", "_last_on_operation", "_on_by_remote", "_preset_mode"
        ):
            self._hvac_mode = hvac_mode
            if hvac_mode != HVACMode.OFF:
                self._last_on_operation = hvac_mode
            await self.send_command()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        async with optimistic_state(
            self, "_current_fan_mode", "_on_by_remote", "_preset_mode"
        ):
            self._current_fan_mode = fan_mode
            if self._hvac_mode != HVACMode.OFF:
                await self.send_command()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set swing mode."""
        async with optimistic_state(
            self, "_current_swing_mode", "_on_by_remote", "_preset_mode"
        ):
            self._current_swing_mode = swing_mode
            if self._hvac_mode != HVACMode.OFF:
                await self.send_command()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Send a one-touch button, or re-send the ordinary state to clear one.

        Clearing is not a separate code. The preset bit lives in the state frame,
        so transmitting the ordinary state again is what physically turns it off —
        which is also why every other mutator clears the preset.
        """
        if preset_mode == PRESET_NONE:
            async with optimistic_state(self, "_preset_mode", "_on_by_remote"):
                self._preset_mode = PRESET_NONE
                if self._hvac_mode != HVACMode.OFF:
                    await self.send_command()
            return

        if preset_mode not in self._preset_modes:
            raise HomeAssistantError(
                f"Device code {self._device_code} has no code recorded for the "
                f"preset {preset_mode!r}"
            )

        # Everything the baseline can touch is snapshotted, so a failed transmit
        # rolls back the whole move rather than leaving a state that never went.
        async with optimistic_state(
            self,
            "_preset_mode",
            "_hvac_mode",
            "_last_on_operation",
            "_current_fan_mode",
            "_current_swing_mode",
            "_target_temperature",
            "_on_by_remote",
        ):
            self._preset_mode = preset_mode
            self._adopt_preset_baseline()
            await self.send_preset_command(preset_mode)

    def _adopt_preset_baseline(self) -> None:
        """Report the state the preset's own packet commands.

        The captured code carries a whole state frame, so after sending it the
        unit really is at that mode, fan speed and temperature. Reporting the
        state we had before would be a lie with a visible consequence: the next
        temperature change would be computed from the wrong starting point.
        """
        baseline = self._preset_baseline

        if mode := baseline.get("operationMode"):
            self._hvac_mode = mode
        elif self._hvac_mode == HVACMode.OFF:
            # A preset frame carries the power bit, so the unit is running now
            # even though the file did not say in which mode. Same guess the
            # power sensor makes, so the two agree.
            self._hvac_mode = self._last_on_operation or self._operation_modes[1]

        if fan_mode := baseline.get("fanMode"):
            self._current_fan_mode = fan_mode
        if (swing_mode := baseline.get("swingMode")) and self._support_swing:
            self._current_swing_mode = swing_mode
        if (temperature := baseline.get("temperature")) is not None:
            self._target_temperature = temperature

        if self._hvac_mode != HVACMode.OFF:
            self._last_on_operation = self._hvac_mode
        self._on_by_remote = False

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn on."""
        if self._last_on_operation is not None:
            await self.async_set_hvac_mode(self._last_on_operation)
        else:
            await self.async_set_hvac_mode(self._operation_modes[1])

    async def send_command(self, *, from_user: bool = True) -> None:
        """Send the code matching the current mode/fan/swing/temperature.

        Raises HomeAssistantError if the command cannot be delivered, so the
        caller does not publish a state that was never transmitted.

        ``from_user`` is False when a re-assert is replaying the current state
        rather than a person asking for something new.
        """
        async with self._temp_lock:
            self._on_by_remote = False
            # The ordinary state frame carries the preset bit off, so sending one
            # clears whatever preset was active. Set here rather than in each
            # mutator: this is the single funnel they all pass through, so a
            # mutator added later cannot forget.
            self._preset_mode = PRESET_NONE
            # A command from the user is a fresh start for the re-assert budget:
            # whatever was failing before, they have asked for something now.
            # Guarded, because a re-assert comes through here too and resetting
            # the budget it is spending would make the cap unreachable.
            if from_user:
                self._reassert_attempts = 0
                self._reassert_suspended = False

            if self._hvac_mode == HVACMode.OFF:
                off_command = self._commands.get("off")
                if not is_recorded(off_command):
                    raise HomeAssistantError(
                        f"Device code {self._device_code} has no 'off' code "
                        "recorded, so this device cannot be turned off through "
                        "Home Assistant"
                    )
                await self._controller.send(off_command)
                return

            # Resolved before the 'on' code is sent, so a device file that
            # cannot express this state does not leave the unit switched on.
            command = self._resolve_command()
            await self._send_state_code(command)

    async def send_preset_command(self, preset_mode: str) -> None:
        """Send one preset's code, the same way an ordinary state code is sent."""
        async with self._temp_lock:
            command = (self._commands.get(PRESETS_KEY) or {}).get(preset_mode)
            if not is_recorded(command):
                raise HomeAssistantError(
                    f"Device code {self._device_code} has no code recorded for the "
                    f"preset {preset_mode!r}"
                )
            await self._send_state_code(command)

    async def _send_state_code(self, command: Any) -> None:
        """Send the optional power-on code, then a state frame.

        Shared by the ordinary state and by a preset, so the dozen files that
        need a separate 'on' code first behave the same either way.
        """
        if "on" in self._commands:
            await self._controller.send(self._commands["on"])
            await asyncio.sleep(self._delay)

        await self._controller.send(command)
        # Recorded after a successful send, so the settle window starts from a
        # transmission that actually happened.
        self._last_transmit = dt_util.utcnow()

    def _resolve_command(self) -> Any:
        """Return the code for the current state, tolerating sparse files.

        Device files are not uniformly deep. Under 'dry' and 'fan_only' many
        record only one fan mode, or stop at a bare code because the unit
        ignores fan speed and temperature there. Walk down as far as the file
        goes and use the first code found.
        """
        node = _select_operation_mode(
            self._commands,
            self._hvac_mode,
            self._device_code,
            self._operation_modes,
        )
        if not isinstance(node, dict):
            return node

        node = _select(
            node,
            self._current_fan_mode,
            "fan mode",
            self._device_code,
            self._fan_modes,
        )
        if not isinstance(node, dict):
            return node

        if self._support_swing:
            node = _select(
                node,
                self._current_swing_mode,
                "swing mode",
                self._device_code,
                self._swing_modes,
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
        """Track the device being switched on or off by its own remote.

        With power_sensor_reassert off — the default, and therefore every
        configuration that exists today — this adopts the sensor's reading and
        nothing else, exactly as it always has.

        With it on, one direction gains a transmission and the other deliberately
        does not. See _async_handle_contradiction.
        """
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None or new_state.state not in (STATE_ON, STATE_OFF):
            # unavailable, unknown, or anything else: no adopt, no re-assert, no
            # counting. A sensor dropping out says nothing about the device.
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        # The sensor coming back from unavailable, or its first reading after a
        # restart, is an artefact of Home Assistant rather than a missed command.
        first_reading = old_state is None or old_state.state not in (
            STATE_ON,
            STATE_OFF,
        )

        if new_state.state == STATE_ON and self._hvac_mode == HVACMode.OFF:
            self._adopt_sensor_on()
            self.async_write_ha_state()
            return

        if new_state.state == STATE_OFF and self._hvac_mode != HVACMode.OFF:
            if self._reassert and not first_reading:
                await self._async_handle_contradiction()
                return
            self._on_by_remote = False
            self._hvac_mode = HVACMode.OFF
            self.async_write_ha_state()

        # Note what does *not* happen here: the sensor agreeing does not reset
        # the attempt counter. Every second 'off' event needs an intervening
        # 'on', so resetting on agreement would make the cap unreachable and the
        # warning below dead code. Only a command from the user clears it —
        # three nudges with nobody touching the thermostat in between means
        # something is wrong with the emitter, not with the last frame.

    def _adopt_sensor_on(self) -> None:
        """Believe the sensor when it says the device is running.

        'on' is not a valid hvac_mode, so the real one has to be guessed: Home
        Assistant rejects any state that is not in hvac_modes. With
        power_sensor_restore_state the guess is the mode the unit last ran in;
        without it the entity reports the first mode the file offers and claims
        to know no more.
        """
        self._on_by_remote = True
        if self._power_sensor_restore_state:
            self._hvac_mode = self._last_on_operation or self._operation_modes[1]
        else:
            self._hvac_mode = self._operation_modes[1]

    async def _async_handle_contradiction(self) -> None:
        """Re-send the current state when the sensor says the device is off.

        The asymmetry here is the heart of the feature, and it is deliberate.

        Sensor *on* while the entity wants *off* is never re-asserted — that case
        is overwhelmingly a person who has just picked up the original remote, and
        switching their air conditioner off because a sensor flickered is the
        worst thing this could do. That direction keeps adopting, and
        _on_by_remote keeps its full existing meaning.

        Sensor *off* while the entity wants a running mode is the failure this
        exists for: a dropped IR frame, a hand in the beam, a unit that ignored
        the packet. Because _on_by_remote is only ever set in the direction
        re-assert refuses to act on, the two features cannot contradict.
        """
        if self._within_settle_window():
            # The sensor is catching up with something we just sent.
            return

        if self._reassert_attempts >= REASSERT_MAX_ATTEMPTS:
            if not self._reassert_suspended:
                self._reassert_suspended = True
                _LOGGER.warning(
                    "Device code %s: %s still reports off after %s attempts to "
                    "re-send the state, so %s is giving up and following the "
                    "sensor. Check the emitter, or turn off %s",
                    self._device_code,
                    self._power_sensor,
                    REASSERT_MAX_ATTEMPTS,
                    self.entity_id,
                    CONF_POWER_SENSOR_REASSERT,
                )
            self._on_by_remote = False
            self._hvac_mode = HVACMode.OFF
            self.async_write_ha_state()
            return

        self._reassert_attempts += 1
        await self._async_reassert("the power sensor reports off")
        # The believed state has not changed, but reassert_attempts has, and it
        # is published so that "is it fighting the device?" is answerable without
        # reading the log.
        self.async_write_ha_state()

    def _within_settle_window(self) -> bool:
        """Return whether we transmitted too recently to trust a contradiction."""
        if self._last_transmit is None:
            return False
        elapsed = dt_util.utcnow() - self._last_transmit
        return elapsed.total_seconds() < REASSERT_SETTLE_SECONDS

    async def _async_reassert(self, reason: str) -> None:
        """Re-send whatever the entity currently believes the device is doing.

        Deliberately not wrapped in optimistic_state: no state is changing, so a
        failure has nothing to roll back. It is swallowed rather than raised
        because both callers are background — a state-change listener and a
        timer — and neither has anywhere to report to.
        """
        try:
            if self._preset_mode != PRESET_NONE:
                # The ordinary state frame is exactly what clears a preset, so
                # re-sending it here would quietly cancel the Turbo the entity
                # is claiming to be in.
                await self.send_preset_command(self._preset_mode)
            else:
                await self.send_command(from_user=False)
        except HomeAssistantError as err:
            _LOGGER.debug(
                "Device code %s: re-assert (%s) failed: %s",
                self._device_code,
                reason,
                err,
            )

    async def _async_reassert_tick(self, _now) -> None:
        """Re-send the current state on a timer, for a unit that drifts.

        Independent of the power sensor: this exists for an air conditioner that
        forgets its setpoint after a power blip, which no sensor would reveal.
        """
        if self._hvac_mode == HVACMode.OFF:
            # Transmitting 'off' at a unit somebody has just switched on by hand
            # is the same fight the contradiction path refuses to pick.
            return

        if self._reassert_suspended or self._within_settle_window():
            return

        # Without the re-assert option the sensor is authoritative, and it says
        # there is nothing running to refresh.
        if (
            self._power_sensor
            and not self._reassert
            and (state := self.hass.states.get(self._power_sensor))
            and state.state == STATE_OFF
        ):
            return

        await self._async_reassert("the periodic refresh")

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


def _select(
    node: Any,
    key: str,
    label: str,
    device_code: int,
    order: list[str] | None = None,
) -> Any:
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

    substitute = _nearest_key(candidates, key, order)
    value = candidates[substitute]

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


def _select_operation_mode(
    commands: dict[str, Any],
    hvac_mode: str,
    device_code: int,
    order: list[str],
) -> Any:
    """Return the command subtree for an operation mode.

    The power codes live in the same dict as the modes, so they are removed
    before _select is allowed to substitute: hvac_modes puts 'off' first, which
    makes it one step from whichever mode is listed next, and _nearest_key
    breaks that tie on position. Falling back to it would switch the unit off
    while the entity reported a running mode. Command groups that are not modes
    either — 'presets', 'extras' — would be worse still, because a whole dict
    would then be walked as if it were the fan-mode level.
    """
    modes = {
        name: value
        for name, value in commands.items()
        if name not in RESERVED_COMMAND_KEYS
    }
    if not modes:
        raise HomeAssistantError(
            f"Device code {device_code} has no operation mode to select "
            f"{hvac_mode!r} from"
        )

    return _select(modes, hvac_mode, "operation mode", device_code, order)


def _nearest_key(candidates: dict[str, Any], key: str, order: list[str] | None) -> str:
    """Return the candidate closest to ``key`` in the device file's own ordering.

    'fanModes' and 'swingModes' are ordered lists, so when the requested one is
    missing the neighbouring entry is the best stand-in — substituting whichever
    key happened to be written first could swap the lowest fan speed for the
    highest.
    """
    if order and key in order:
        target = order.index(key)
        ranked = [
            (abs(order.index(name) - target), position, name)
            for position, name in enumerate(candidates)
            if name in order
        ]
        if ranked:
            return min(ranked)[2]

    return next(iter(candidates))


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
    device_data: dict[str, Any], max_temperature: float, device_code: int
) -> str:
    """Return the temperature unit a device file's codes were recorded in.

    Every shipped file that records Fahrenheit says so in 'temperatureUnit', and
    the validator enforces that. The range check below is only reached by a
    hand-written file, so it warns rather than deciding quietly.
    """
    declared = str(device_data.get("temperatureUnit", "")).upper()
    if declared in ("F", "°F", UnitOfTemperature.FAHRENHEIT):
        return UnitOfTemperature.FAHRENHEIT
    if declared in ("C", "°C", UnitOfTemperature.CELSIUS):
        return UnitOfTemperature.CELSIUS

    if max_temperature > _FAHRENHEIT_THRESHOLD:
        _LOGGER.warning(
            "Device code %s declares no temperatureUnit and its maximum "
            "temperature is %s, which is too high to be Celsius, so it is read "
            'as Fahrenheit. Add "temperatureUnit": "C" to the device file, or '
            "set the temperature_unit option, if that is wrong",
            device_code,
            max_temperature,
        )
        return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS
