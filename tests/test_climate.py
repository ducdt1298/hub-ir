"""Tests for the HubIR climate platform."""

from __future__ import annotations

import pytest

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import CLIMATE_DEVICE_DATA, get_entity, payloads

ENTITY_ID = "climate.test_ac"
CONFIG = {
    "name": "Test AC",
    "unique_id": "test_ac",
    "device_code": 9000,
    "controller_data": "remote.broadlink",
}


@pytest.fixture
async def climate(hass, write_device_file, sent_commands, setup_platform):
    """Set up a climate entity backed by the test device file."""
    write_device_file("climate", 9000, CLIMATE_DEVICE_DATA)
    await setup_platform(CLIMATE_DOMAIN, CONFIG)
    return sent_commands


async def test_entity_created_from_device_file(hass: HomeAssistant, climate) -> None:
    """The entity exposes what the device file declares."""
    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes["hvac_modes"] == [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
    ]
    assert state.attributes["fan_modes"] == ["low"]
    assert state.attributes["min_temp"] == 16
    assert state.attributes["max_temp"] == 17
    assert state.attributes["manufacturer"] == "Test"
    assert state.attributes["device_code"] == 9000


async def test_set_hvac_mode_sends_matching_code(hass: HomeAssistant, climate) -> None:
    """Selecting a mode sends the code for that mode/fan/temperature."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    # min temperature is the startup target, so cool/low/16
    assert payloads(climate) == [["b64:Y29vbDE2"]]
    assert hass.states.get(ENTITY_ID).state == HVACMode.COOL


async def test_set_temperature_sends_matching_code(
    hass: HomeAssistant, climate
) -> None:
    """Changing the target temperature re-sends the full state."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    climate.clear()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 17},
        blocking=True,
    )

    assert payloads(climate) == [["b64:aGVhdDE3"]]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_TEMPERATURE] == 17


async def test_set_temperature_while_off_sends_nothing(
    hass: HomeAssistant, climate
) -> None:
    """A target change while off is remembered but not transmitted."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 17},
        blocking=True,
    )

    assert payloads(climate) == []
    assert hass.states.get(ENTITY_ID).attributes[ATTR_TEMPERATURE] == 17


async def test_turn_off_sends_off_code(hass: HomeAssistant, climate) -> None:
    """Turning off sends the device file's off code."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    climate.clear()

    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert payloads(climate) == [["b64:b2Zm"]]
    assert hass.states.get(ENTITY_ID).state == HVACMode.OFF


async def test_turn_on_restores_last_mode(hass: HomeAssistant, climate) -> None:
    """turn_on returns to the last mode that was on."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    climate.clear()

    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert hass.states.get(ENTITY_ID).state == HVACMode.HEAT
    assert payloads(climate) == [["b64:aGVhdDE2"]]


async def test_set_fan_mode_while_off_sends_nothing(
    hass: HomeAssistant, climate
) -> None:
    """A fan change while off is remembered but not transmitted."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_FAN_MODE: "low"},
        blocking=True,
    )

    assert payloads(climate) == []


async def test_celsius_device_file_declares_celsius(
    hass: HomeAssistant, climate
) -> None:
    """A 16-17 range is read as Celsius."""
    assert get_entity(hass, ENTITY_ID).temperature_unit == UnitOfTemperature.CELSIUS


async def test_fahrenheit_device_file_declares_fahrenheit(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A 60-86 range is read as Fahrenheit, not as the HA display unit.

    Upstream reported hass.config.units.temperature_unit for every device file,
    so a Fahrenheit file on a Celsius system advertised 60-86 degrees Celsius.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "minTemperature": 60,
        "maxTemperature": 86,
        "commands": {
            "off": "b2Zm",
            "cool": {"low": {"60": "YQ==", "86": "Yg=="}},
            "heat": {"low": {"60": "Yw==", "86": "ZA=="}},
        },
    }
    write_device_file("climate", 9001, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "F AC", "unique_id": "f_ac", "device_code": 9001},
    )

    entity = get_entity(hass, "climate.f_ac")
    assert entity.temperature_unit == UnitOfTemperature.FAHRENHEIT

    # HA converts for display: 60-86 F is 15.6-30 C, not 60-86 C.
    state = hass.states.get("climate.f_ac")
    assert state.attributes["min_temp"] == 15.6
    assert state.attributes["max_temp"] == 30.0


async def test_explicit_temperature_unit_overrides_inference(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """The temperature_unit option wins over the inferred unit."""
    write_device_file("climate", 9002, CLIMATE_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CONFIG,
            "name": "U AC",
            "unique_id": "u_ac",
            "device_code": 9002,
            "temperature_unit": UnitOfTemperature.FAHRENHEIT,
        },
    )

    entity = get_entity(hass, "climate.u_ac")
    assert entity.temperature_unit == UnitOfTemperature.FAHRENHEIT


async def test_power_sensor_on_never_yields_invalid_state(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A power sensor turning on must leave hvac_mode a valid mode.

    Upstream set the state to literal 'on', which HA rejects because 'on' is
    not in hvac_modes.
    """
    write_device_file("climate", 9003, CLIMATE_DEVICE_DATA)
    hass.states.async_set("binary_sensor.ac_power", STATE_OFF)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CONFIG,
            "name": "P AC",
            "unique_id": "p_ac",
            "device_code": 9003,
            "power_sensor": "binary_sensor.ac_power",
        },
    )

    hass.states.async_set("binary_sensor.ac_power", STATE_ON)
    await hass.async_block_till_done()

    state = hass.states.get("climate.p_ac")
    assert state.state in state.attributes["hvac_modes"]
    assert state.state != STATE_ON
    assert state.attributes["on_by_remote"] is True

    hass.states.async_set("binary_sensor.ac_power", STATE_OFF)
    await hass.async_block_till_done()

    state = hass.states.get("climate.p_ac")
    assert state.state == HVACMode.OFF
    assert state.attributes["on_by_remote"] is False


@pytest.mark.parametrize(
    ("restore_state", "expected"),
    [(True, HVACMode.HEAT), (False, HVACMode.COOL)],
)
async def test_power_sensor_restore_state_decides_the_mode_guessed(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    restore_state: bool,
    expected: HVACMode,
) -> None:
    """The option has to change the guess, or it is not an option.

    Both arms of this branch used to compute the same value, so the documented
    setting did nothing at all. 'cool' is the first mode the file offers and
    'heat' is the one the unit last ran in, so the two answers differ.
    """
    write_device_file("climate", 9013, CLIMATE_DEVICE_DATA)
    hass.states.async_set("binary_sensor.r_power", STATE_OFF)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CONFIG,
            "name": "R AC",
            "unique_id": "r_ac",
            "device_code": 9013,
            "power_sensor": "binary_sensor.r_power",
            "power_sensor_restore_state": restore_state,
        },
    )

    # Run in heat, then switch off, so last_on_operation is not the first mode.
    for mode in (HVACMode.HEAT, HVACMode.OFF):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: "climate.r_ac", ATTR_HVAC_MODE: mode},
            blocking=True,
        )

    hass.states.async_set("binary_sensor.r_power", STATE_ON)
    await hass.async_block_till_done()

    state = hass.states.get("climate.r_ac")
    assert state.state == expected
    assert state.attributes["on_by_remote"] is True


async def test_a_missing_mode_never_substitutes_a_reserved_key(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Falling back to 'off' would switch the unit off while reporting cool.

    _select substitutes a sibling when the mode it wants is absent, and 'on'
    and 'off' sit in the same dict as the modes. hvac_modes here is
    [off, cool, heat], so a request for 'cool' is one step from both 'off' and
    'heat': _nearest_key breaks that tie on position, and 'off' is written
    first in every device file. Excluding the power codes is what keeps the
    substitution among actual modes.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "commands": {
            "off": "b2Zm",
            "heat": {"low": {"16": "aGVhdDE2", "17": "aGVhdDE3"}},
        },
    }
    write_device_file("climate", 9014, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "S AC", "unique_id": "s_ac", "device_code": 9014},
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.s_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    assert payloads(sent_commands) == [["b64:aGVhdDE2"]]
    assert hass.states.get("climate.s_ac").state == HVACMode.COOL


async def test_a_missing_mode_never_substitutes_a_command_group(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """An 'extras' dict picked as a mode would be walked as the fan-mode level.

    That is worse than substituting the off code: the entity would transmit
    whichever extra button happened to be recorded first, with no clue why.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "commands": {
            "off": "b2Zm",
            "extras": {"led": "bGVk"},
            "heat": {"low": {"16": "aGVhdDE2", "17": "aGVhdDE3"}},
        },
    }
    write_device_file("climate", 9016, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "X AC", "unique_id": "x_ac", "device_code": 9016},
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.x_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    assert payloads(sent_commands) == [["b64:aGVhdDE2"]]


async def test_a_mode_with_nothing_to_fall_back_on_raises(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Sending the power code would be worse than admitting the file is short."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "operationModes": ["cool"],
        "commands": {"off": "b2Zm", "on": "b24="},
    }
    write_device_file("climate", 9015, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "T AC", "unique_id": "t_ac", "device_code": 9015},
    )

    with pytest.raises(HomeAssistantError, match="no operation mode to select"):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: "climate.t_ac", ATTR_HVAC_MODE: HVACMode.COOL},
            blocking=True,
        )

    assert payloads(sent_commands) == []
    assert hass.states.get("climate.t_ac").state == HVACMode.OFF


async def test_temperature_sensor_updates_current_temperature(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A linked temperature sensor feeds current_temperature."""
    write_device_file("climate", 9004, CLIMATE_DEVICE_DATA)
    hass.states.async_set("sensor.room_temp", "21.5")
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CONFIG,
            "name": "T AC",
            "unique_id": "t_ac",
            "device_code": 9004,
            "temperature_sensor": "sensor.room_temp",
        },
    )

    assert hass.states.get("climate.t_ac").attributes["current_temperature"] == 21.5

    hass.states.async_set("sensor.room_temp", "22.5")
    await hass.async_block_till_done()

    assert hass.states.get("climate.t_ac").attributes["current_temperature"] == 22.5

    # An unavailable sensor must not crash or clobber the last reading.
    hass.states.async_set("sensor.room_temp", "unavailable")
    await hass.async_block_till_done()

    assert hass.states.get("climate.t_ac").attributes["current_temperature"] == 22.5


async def test_on_command_is_sent_before_the_state_code(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A device file with a separate 'on' code sends it first."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "commands": {**CLIMATE_DEVICE_DATA["commands"], "on": "b24="},
    }
    write_device_file("climate", 9005, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CONFIG,
            "name": "On AC",
            "unique_id": "on_ac",
            "device_code": 9005,
            "delay": 0,
        },
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.on_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    assert payloads(sent_commands) == [["b64:b24="], ["b64:Y29vbDE2"]]


# Device files are not uniformly deep: 'dry' and 'fan_only' commonly record a
# single fan mode, a single temperature, or a bare code, because the unit
# ignores those settings there. Upstream raised KeyError and sent nothing.
SPARSE_DEVICE_DATA = {
    **CLIMATE_DEVICE_DATA,
    "operationModes": ["cool", "dry", "fan_only", "heat"],
    "fanModes": ["low", "high"],
    "commands": {
        "off": "b2Zm",
        "cool": {
            "low": {"16": "Y29vbDE2", "17": "Y29vbDE3"},
            "high": {"16": "Y2gxNg==", "17": "Y2gxNw=="},
        },
        # One fan mode only, and only one temperature under it.
        "dry": {"auto": {"16": "ZHJ5"}},
        # A bare code for the whole mode.
        "fan_only": "ZmFuT25seQ==",
        # Both fan modes, but a gap at 17.
        "heat": {"low": {"16": "aGVhdDE2"}, "high": {"16": "aGgxNg=="}},
    },
}


@pytest.fixture
async def sparse(hass, write_device_file, sent_commands, setup_platform):
    """Set up a climate entity backed by a sparse device file."""
    write_device_file("climate", 9010, SPARSE_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "Sparse", "unique_id": "sparse", "device_code": 9010},
    )
    return sent_commands


async def test_sparse_mode_substitutes_its_only_fan_mode(
    hass: HomeAssistant, sparse
) -> None:
    """'dry' records only 'auto', so the selected fan mode is substituted."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.sparse", ATTR_HVAC_MODE: HVACMode.DRY},
        blocking=True,
    )

    assert payloads(sparse) == [["b64:ZHJ5"]]
    assert hass.states.get("climate.sparse").state == HVACMode.DRY


async def test_bare_mode_code_is_sent_as_is(hass: HomeAssistant, sparse) -> None:
    """'fan_only' is a single code with no fan or temperature level."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.sparse", ATTR_HVAC_MODE: HVACMode.FAN_ONLY},
        blocking=True,
    )

    assert payloads(sparse) == [["b64:ZmFuT25seQ=="]]


async def test_missing_temperature_uses_the_closest_recorded_one(
    hass: HomeAssistant, sparse
) -> None:
    """'heat' has no code for 17, so 16's code is sent."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: "climate.sparse",
            ATTR_TEMPERATURE: 17,
            ATTR_HVAC_MODE: HVACMode.HEAT,
        },
        blocking=True,
    )

    assert payloads(sparse) == [["b64:aGVhdDE2"]]


async def test_present_fan_mode_is_not_substituted(hass: HomeAssistant, sparse) -> None:
    """A fan mode the file does record is used, not substituted."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.sparse", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    sparse.clear()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: "climate.sparse", ATTR_FAN_MODE: "high"},
        blocking=True,
    )

    assert payloads(sparse) == [["b64:Y2gxNg=="]]


async def test_unusable_operation_modes_are_dropped(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A vendor mode HA does not know is not offered to the user."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "operationModes": ["cool", "money_saver"],
        "commands": {**CLIMATE_DEVICE_DATA["commands"], "money_saver": "bXM="},
    }
    write_device_file("climate", 9011, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "Vendor", "unique_id": "vendor", "device_code": 9011},
    )

    hvac_modes = hass.states.get("climate.vendor").attributes["hvac_modes"]
    assert hvac_modes == [HVACMode.OFF, HVACMode.COOL]


async def test_comment_keys_are_never_sent_as_commands(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Documentation keys in the command tree must not be substituted.

    Real device files carry '_comment', '$comment' and '_note' keys inside the
    command tree, sometimes as the first key of a level. Substituting one would
    transmit its prose to the remote.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "operationModes": ["cool", "dry"],
        "fanModes": ["low", "high"],
        "commands": {
            "off": "b2Zm",
            "cool": {
                "_comment": "captured from the AR-RCE1E remote",
                "low": {"_note": "16 only", "16": "Y29vbDE2"},
                "high": {"16": "Y2gxNg=="},
            },
            # 'dry' has no 'high' fan mode, and its first key is an annotation.
            "dry": {
                "$comment": "dry ignores the fan speed",
                "low": {"16": "ZHJ5"},
            },
        },
    }
    write_device_file("climate", 9030, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CONFIG, "name": "C AC", "unique_id": "c_ac", "device_code": 9030},
    )

    # dry + high: 'high' is absent, so a fan mode must be substituted. The
    # '$comment' string must not be the one chosen.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.c_ac", ATTR_HVAC_MODE: HVACMode.DRY},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: "climate.c_ac", ATTR_FAN_MODE: "high"},
        blocking=True,
    )

    sent = payloads(sent_commands)
    assert sent == [["b64:ZHJ5"], ["b64:ZHJ5"]]
    for call in sent:
        for code in call:
            assert "comment" not in code
            assert "ignores" not in code

    # And an annotation next to real temperatures is skipped too.
    sent_commands.clear()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.c_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    assert payloads(sent_commands) == [["b64:Y2gxNg=="]]
