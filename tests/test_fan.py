"""Tests for the Broadlink IR fan platform."""

from __future__ import annotations

import pytest

from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    DOMAIN as FAN_DOMAIN,
    SERVICE_SET_PERCENTAGE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant

from .conftest import FAN_DEVICE_DATA, payloads

ENTITY_ID = "fan.test_fan"
CONFIG = {
    "name": "Test Fan",
    "unique_id": "test_fan",
    "device_code": 9100,
    "controller_data": "remote.broadlink",
}


@pytest.fixture
async def fan(hass, write_device_file, sent_commands, setup_platform):
    """Set up a fan entity backed by the test device file."""
    write_device_file("fan", 9100, FAN_DEVICE_DATA)
    await setup_platform(FAN_DOMAIN, CONFIG)
    return sent_commands


async def test_entity_created_from_device_file(hass: HomeAssistant, fan) -> None:
    """The entity starts off with one step per declared speed."""
    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["percentage_step"] == 50


async def test_turn_on_uses_first_speed(hass: HomeAssistant, fan) -> None:
    """Turning on with no percentage picks the first speed."""
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert payloads(fan) == [["b64:bG93"]]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_PERCENTAGE] == 50


async def test_set_percentage_picks_matching_speed(hass: HomeAssistant, fan) -> None:
    """A 100% request maps onto the highest declared speed."""
    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PERCENTAGE: 100},
        blocking=True,
    )

    assert payloads(fan) == [["b64:aGlnaA=="]]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_PERCENTAGE] == 100


async def test_turn_off_sends_off_code(hass: HomeAssistant, fan) -> None:
    """Turning off sends the off code and reports 0%."""
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    fan.clear()

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert payloads(fan) == [["b64:b2Zm"]]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_PERCENTAGE] == 0


async def test_turn_on_returns_to_last_speed(hass: HomeAssistant, fan) -> None:
    """After off, turn_on goes back to the speed that was last used."""
    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PERCENTAGE: 100},
        blocking=True,
    )
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    fan.clear()

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert payloads(fan) == [["b64:aGlnaA=="]]


async def test_power_sensor_is_tracked_without_a_restored_state(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """The power sensor is tracked on a first-ever start.

    Upstream registered the listener inside the restore-state branch, so a fan
    with no previous state never followed its power sensor.
    """
    write_device_file("fan", 9101, FAN_DEVICE_DATA)
    hass.states.async_set("binary_sensor.fan_power", STATE_OFF)
    await setup_platform(
        FAN_DOMAIN,
        {
            **CONFIG,
            "name": "P Fan",
            "unique_id": "p_fan",
            "device_code": 9101,
            "power_sensor": "binary_sensor.fan_power",
        },
    )

    hass.states.async_set("binary_sensor.fan_power", STATE_ON)
    await hass.async_block_till_done()

    state = hass.states.get("fan.p_fan")
    assert state.state == STATE_ON
    assert state.attributes["on_by_remote"] is True


async def test_power_sensor_on_keeps_percentage_usable(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Turning off after the remote turned it on must not crash.

    Upstream set the speed to None here, which broke the percentage property
    and the next send.
    """
    write_device_file("fan", 9102, FAN_DEVICE_DATA)
    hass.states.async_set("binary_sensor.fan2_power", STATE_OFF)
    await setup_platform(
        FAN_DOMAIN,
        {
            **CONFIG,
            "name": "Q Fan",
            "unique_id": "q_fan",
            "device_code": 9102,
            "power_sensor": "binary_sensor.fan2_power",
        },
    )

    hass.states.async_set("binary_sensor.fan2_power", STATE_ON)
    await hass.async_block_till_done()

    # Reading the percentage must not raise.
    assert hass.states.get("fan.q_fan").attributes[ATTR_PERCENTAGE] is not None

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "fan.q_fan"}, blocking=True
    )

    assert payloads(sent_commands) == [["b64:b2Zm"]]
    assert hass.states.get("fan.q_fan").state == STATE_OFF
