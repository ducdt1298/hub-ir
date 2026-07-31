"""Tests for the Broadlink IR light platform."""

from __future__ import annotations

import pytest

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant

from .conftest import LIGHT_DEVICE_DATA, payloads

ENTITY_ID = "light.test_light"
CONFIG = {
    "name": "Test Light",
    "unique_id": "test_light",
    "device_code": 9200,
    "controller_data": "remote.broadlink",
}


@pytest.fixture
async def light(hass, write_device_file, sent_commands, setup_platform):
    """Set up a light entity backed by the test device file."""
    write_device_file("light", 9200, LIGHT_DEVICE_DATA)
    await setup_platform(LIGHT_DOMAIN, CONFIG)
    return sent_commands


async def test_entity_created_from_device_file(hass: HomeAssistant, light) -> None:
    """A device file with colder/warmer codes advertises color temp support."""
    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.COLOR_TEMP]
    assert state.attributes["min_color_temp_kelvin"] == 2700
    assert state.attributes["max_color_temp_kelvin"] == 6500


async def test_turn_on_and_off(hass: HomeAssistant, light) -> None:
    """Plain on/off sends the on and off codes."""
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert payloads(light) == [["b64:b24="]]
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    light.clear()

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert payloads(light) == [["b64:b2Zm"]]
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_brightness_step_count_uses_the_brightness_list(
    hass: HomeAssistant, light
) -> None:
    """Stepping to the top of the brightness list resyncs over its own length.

    Upstream sized this resync from the colorTemperature list, which is a
    different length in most device files.
    """
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_BRIGHTNESS: 255},
        blocking=True,
    )

    brighten = [call for call in payloads(light) if call == ["b64:YnJpZ2h0ZW4="]]
    # 3 entries in the brightness list -> 3 resync steps.
    assert len(brighten) == 3
    assert hass.states.get(ENTITY_ID).attributes[ATTR_BRIGHTNESS] == 255


async def test_color_temp_change_sends_steps(hass: HomeAssistant, light) -> None:
    """Lowering the color temperature sends warmer commands."""
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_COLOR_TEMP_KELVIN: 2700},
        blocking=True,
    )

    warmer = [call for call in payloads(light) if call == ["b64:d2FybWVy"]]
    assert len(warmer) == 3  # resync across the full 3-entry list
    assert hass.states.get(ENTITY_ID).attributes[ATTR_COLOR_TEMP_KELVIN] == 2700


async def test_onoff_only_device_file(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A device file with only on/off advertises ONOFF, never UNKNOWN.

    HA raises on a supported_color_modes of UNKNOWN, which is what upstream
    reported for an on/off-only device file that also had no brightness list.
    """
    write_device_file(
        "light",
        9201,
        {
            "manufacturer": "Test",
            "supportedModels": ["TEST-ONOFF"],
            "supportedController": "Broadlink",
            "commandsEncoding": "Base64",
            "commands": {"on": "b24=", "off": "b2Zm"},
        },
    )
    await setup_platform(
        LIGHT_DOMAIN,
        {**CONFIG, "name": "OnOff Light", "unique_id": "onoff_light", "device_code": 9201},
    )

    state = hass.states.get("light.onoff_light")
    assert state is not None
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.ONOFF]


async def test_power_sensor_off_clears_on_by_remote(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """The power sensor drives is_on, and a first event with no old state works."""
    write_device_file("light", 9202, LIGHT_DEVICE_DATA)
    await setup_platform(
        LIGHT_DOMAIN,
        {
            **CONFIG,
            "name": "P Light",
            "unique_id": "p_light",
            "device_code": 9202,
            "power_sensor": "binary_sensor.light_power",
        },
    )

    # First ever state for the sensor: old_state is None here.
    hass.states.async_set("binary_sensor.light_power", STATE_ON)
    await hass.async_block_till_done()

    assert hass.states.get("light.p_light").state == STATE_ON

    hass.states.async_set("binary_sensor.light_power", STATE_OFF)
    await hass.async_block_till_done()

    assert hass.states.get("light.p_light").state == STATE_OFF
