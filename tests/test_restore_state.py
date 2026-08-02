"""Tests for restoring entity state across a Home Assistant restart.

These entities are RestoreEntity subclasses: after a restart they have no way to
ask the device what it is doing, so they adopt whatever they wrote last. A
restored value that the device file cannot express, or that Home Assistant
rejects, leaves the entity broken until the user touches it.
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache

from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.components.fan import ATTR_PERCENTAGE, DOMAIN as FAN_DOMAIN
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State

from .conftest import (
    CLIMATE_DEVICE_DATA,
    CLIMATE_PRESET_DEVICE_DATA,
    FAN_DEVICE_DATA,
    LIGHT_DEVICE_DATA,
    MEDIA_PLAYER_DEVICE_DATA,
    get_entity,
    payloads,
)

CLIMATE_CONFIG = {
    "name": "Test AC",
    "unique_id": "test_ac",
    "device_code": 9000,
    "controller_data": "remote.broadlink",
}


async def test_climate_restores_a_valid_state(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Mode, fan mode and target temperature all come back."""
    mock_restore_cache(
        hass,
        (
            State(
                "climate.test_ac",
                HVACMode.HEAT,
                {"fan_mode": "low", ATTR_TEMPERATURE: 17, "last_on_operation": "heat"},
            ),
        ),
    )
    write_device_file("climate", 9000, CLIMATE_DEVICE_DATA)
    await setup_platform(CLIMATE_DOMAIN, CLIMATE_CONFIG)

    state = hass.states.get("climate.test_ac")
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 17
    assert state.attributes["fan_mode"] == "low"
    assert state.attributes["last_on_operation"] == "heat"

    # The restored state must be usable straight away, without a mode change.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {"entity_id": "climate.test_ac", ATTR_TEMPERATURE: 16},
        blocking=True,
    )
    assert payloads(sent_commands) == [["b64:aGVhdDE2"]]


async def test_climate_restores_a_preset(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A preset survives a restart, because the unit is still running in it."""
    mock_restore_cache(
        hass,
        (
            State(
                "climate.preset_ac",
                HVACMode.HEAT,
                {"fan_mode": "low", ATTR_TEMPERATURE: 17, ATTR_PRESET_MODE: "turbo"},
            ),
        ),
    )
    write_device_file("climate", 9020, CLIMATE_PRESET_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CLIMATE_CONFIG,
            "name": "Preset AC",
            "unique_id": "preset_ac",
            "device_code": 9020,
        },
    )

    state = hass.states.get("climate.preset_ac")
    assert state.attributes[ATTR_PRESET_MODE] == "turbo"


@pytest.mark.parametrize("restored", ["quiet", "sleep", None])
async def test_climate_rejects_a_preset_the_file_does_not_offer(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform, restored
) -> None:
    """'quiet' is in the file but unrecorded; 'sleep' is not there at all.

    Home Assistant rejects a preset_mode outside preset_modes, so an entity that
    adopted one would be unusable until someone touched it.
    """
    mock_restore_cache(
        hass,
        (State("climate.preset_ac", HVACMode.OFF, {ATTR_PRESET_MODE: restored}),),
    )
    write_device_file("climate", 9020, CLIMATE_PRESET_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            **CLIMATE_CONFIG,
            "name": "Preset AC",
            "unique_id": "preset_ac",
            "device_code": 9020,
        },
    )

    state = hass.states.get("climate.preset_ac")
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_NONE


@pytest.mark.parametrize("restored", [STATE_UNAVAILABLE, STATE_UNKNOWN, "dry", "on"])
async def test_climate_rejects_an_unusable_restored_mode(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform, restored
) -> None:
    """A mode the device file cannot do must not become the entity's state.

    'dry' is a real HVAC mode this device file does not list; 'on' is not a mode
    at all. Home Assistant raises while writing a state outside hvac_modes.
    """
    mock_restore_cache(
        hass,
        (
            State(
                "climate.test_ac", restored, {"fan_mode": "low", ATTR_TEMPERATURE: 16}
            ),
        ),
    )
    write_device_file("climate", 9000, CLIMATE_DEVICE_DATA)
    await setup_platform(CLIMATE_DOMAIN, CLIMATE_CONFIG)

    state = hass.states.get("climate.test_ac")
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.state in state.attributes["hvac_modes"]


async def test_climate_rejects_a_fan_mode_the_file_dropped(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A fan mode removed from the device file falls back to a valid one."""
    mock_restore_cache(
        hass,
        (
            State(
                "climate.test_ac",
                HVACMode.COOL,
                {"fan_mode": "turbo", ATTR_TEMPERATURE: 16},
            ),
        ),
    )
    write_device_file("climate", 9000, CLIMATE_DEVICE_DATA)
    await setup_platform(CLIMATE_DOMAIN, CLIMATE_CONFIG)

    state = hass.states.get("climate.test_ac")
    assert state.attributes["fan_mode"] == "low"
    assert state.attributes["fan_mode"] in state.attributes["fan_modes"]


async def test_climate_restores_a_fahrenheit_setpoint_as_fahrenheit(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A Fahrenheit device file on a Celsius system round-trips its setpoint.

    Home Assistant stores the 'temperature' attribute in the user's unit, so the
    restored number is Celsius and has to be converted back before it can index
    the command tree.
    """
    assert hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS

    data = {
        **CLIMATE_DEVICE_DATA,
        "minTemperature": 60,
        "maxTemperature": 86,
        "commands": {
            "off": "b2Zm",
            "cool": {"low": {"68": "Njg=", "86": "ODY="}},
            "heat": {"low": {"68": "aDY4", "86": "aDg2"}},
        },
    }
    write_device_file("climate", 9020, data)

    # 68 F written while displaying Celsius is stored as 20 C.
    mock_restore_cache(
        hass,
        (
            State(
                "climate.f_ac",
                HVACMode.COOL,
                {"fan_mode": "low", ATTR_TEMPERATURE: 20},
            ),
        ),
    )
    await setup_platform(
        CLIMATE_DOMAIN,
        {**CLIMATE_CONFIG, "name": "F AC", "unique_id": "f_ac", "device_code": 9020},
    )

    entity = get_entity(hass, "climate.f_ac")
    assert entity.temperature_unit == UnitOfTemperature.FAHRENHEIT
    # Internally Fahrenheit again, so the 68 code is the one that gets sent.
    assert entity.target_temperature == 68

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_fan_mode",
        {"entity_id": "climate.f_ac", "fan_mode": "low"},
        blocking=True,
    )
    assert payloads(sent_commands) == [["b64:Njg="]]

    # And the user still sees 20 C.
    assert hass.states.get("climate.f_ac").attributes[ATTR_TEMPERATURE] == 20


async def test_climate_ignores_an_out_of_range_restored_setpoint(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A setpoint the device cannot reach is discarded, not advertised."""
    mock_restore_cache(
        hass,
        (
            State(
                "climate.test_ac",
                HVACMode.COOL,
                {"fan_mode": "low", ATTR_TEMPERATURE: 30},
            ),
        ),
    )
    write_device_file("climate", 9000, CLIMATE_DEVICE_DATA)
    await setup_platform(CLIMATE_DOMAIN, CLIMATE_CONFIG)

    state = hass.states.get("climate.test_ac")
    assert state.attributes[ATTR_TEMPERATURE] == 16  # the device minimum
    assert state.attributes["min_temp"] <= state.attributes[ATTR_TEMPERATURE]
    assert state.attributes[ATTR_TEMPERATURE] <= state.attributes["max_temp"]


async def test_fan_restores_speed(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A restored speed is reported and is what turn_on returns to."""
    mock_restore_cache(
        hass,
        (State("fan.test_fan", STATE_ON, {"speed": "high", "last_on_speed": "high"}),),
    )
    write_device_file("fan", 9100, FAN_DEVICE_DATA)
    await setup_platform(
        FAN_DOMAIN,
        {
            "name": "Test Fan",
            "unique_id": "test_fan",
            "device_code": 9100,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("fan.test_fan")
    assert state.state == STATE_ON
    assert state.attributes[ATTR_PERCENTAGE] == 100

    await hass.services.async_call(
        FAN_DOMAIN, "turn_off", {"entity_id": "fan.test_fan"}, blocking=True
    )
    assert payloads(sent_commands) == [["b64:b2Zm"]]


async def test_fan_rejects_a_speed_the_file_dropped(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A restored speed the device file no longer lists must not be adopted.

    percentage and send_command both index the speed list with it.
    """
    mock_restore_cache(hass, (State("fan.test_fan", STATE_ON, {"speed": "turbo"}),))
    write_device_file("fan", 9100, FAN_DEVICE_DATA)
    await setup_platform(
        FAN_DOMAIN,
        {
            "name": "Test Fan",
            "unique_id": "test_fan",
            "device_code": 9100,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("fan.test_fan")
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_PERCENTAGE] == 0

    # Must still be drivable.
    await hass.services.async_call(
        FAN_DOMAIN, "turn_on", {"entity_id": "fan.test_fan"}, blocking=True
    )
    assert payloads(sent_commands) == [["b64:bG93"]]


async def test_light_restores_brightness_and_color_temp(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Brightness and colour temperature survive a restart."""
    mock_restore_cache(
        hass,
        (
            State(
                "light.test_light",
                STATE_ON,
                {ATTR_BRIGHTNESS: 128, ATTR_COLOR_TEMP_KELVIN: 4000},
            ),
        ),
    )
    write_device_file("light", 9200, LIGHT_DEVICE_DATA)
    await setup_platform(
        LIGHT_DOMAIN,
        {
            "name": "Test Light",
            "unique_id": "test_light",
            "device_code": 9200,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("light.test_light")
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 128
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 4000


@pytest.mark.parametrize("restored", [STATE_UNAVAILABLE, STATE_UNKNOWN])
async def test_light_ignores_an_unusable_restored_state(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform, restored
) -> None:
    """'unavailable' must not be adopted as the light's power state."""
    mock_restore_cache(hass, (State("light.test_light", restored, {}),))
    write_device_file("light", 9200, LIGHT_DEVICE_DATA)
    await setup_platform(
        LIGHT_DOMAIN,
        {
            "name": "Test Light",
            "unique_id": "test_light",
            "device_code": 9200,
            "controller_data": "remote.broadlink",
        },
    )

    assert hass.states.get("light.test_light").state == STATE_OFF


@pytest.mark.parametrize(
    ("restored", "expected"),
    [
        (STATE_ON, STATE_ON),
        (STATE_OFF, STATE_OFF),
        (STATE_UNAVAILABLE, STATE_OFF),
        (STATE_UNKNOWN, STATE_OFF),
        ("playing", STATE_OFF),
    ],
)
async def test_media_player_restores_only_a_valid_state(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    restored,
    expected,
) -> None:
    """Only on/off round-trip; anything else falls back to off."""
    mock_restore_cache(hass, (State("media_player.test_tv", restored, {}),))
    write_device_file("media_player", 9300, MEDIA_PLAYER_DEVICE_DATA)
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Test TV",
            "unique_id": "test_tv",
            "device_code": 9300,
            "controller_data": "remote.broadlink",
        },
    )

    assert hass.states.get("media_player.test_tv").state == expected
