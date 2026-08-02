"""Tests for the HubIR switch platform.

The interesting case is a remote with a single power button, where the same code
alternates. That entity has to believe a state, because sending the code when the
device already matches would do the opposite of what was asked.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import SWITCH_DEVICE_DATA, SWITCH_TOGGLE_DEVICE_DATA, payloads

ENTITY_ID = "switch.test_amp"
CONFIG = {
    "name": "Test Amp",
    "unique_id": "test_amp",
    "device_code": 9200,
    "controller_data": "remote.broadlink",
}


@pytest.fixture
async def switch(hass, write_device_file, sent_commands, setup_platform):
    """Set up a switch with separate on and off codes."""
    write_device_file("switch", 9200, SWITCH_DEVICE_DATA)
    await setup_platform(SWITCH_DOMAIN, CONFIG)
    return sent_commands


@pytest.fixture
async def toggle_switch(hass, write_device_file, sent_commands, setup_platform):
    """Set up a switch whose remote has one power button."""
    write_device_file("switch", 9201, SWITCH_TOGGLE_DEVICE_DATA)
    await setup_platform(SWITCH_DOMAIN, {**CONFIG, "device_code": 9201})
    return sent_commands


async def _call(hass: HomeAssistant, service: str) -> None:
    """Call a switch service against the test entity."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )


async def test_entity_created_from_device_file(hass: HomeAssistant, switch) -> None:
    """The entity reports what the device file declares."""
    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["manufacturer"] == "Test"
    assert state.attributes["supported_models"] == ["TEST-AMP"]
    assert state.attributes["device_code"] == 9200


async def test_turn_on_and_off_send_their_codes(hass: HomeAssistant, switch) -> None:
    """The easy case: two buttons, two codes."""
    await _call(hass, SERVICE_TURN_ON)
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    await _call(hass, SERVICE_TURN_OFF)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    assert payloads(switch) == [["b64:b24="], ["b64:b2Zm"]]


async def test_a_dedicated_code_is_idempotent(hass: HomeAssistant, switch) -> None:
    """Turning on something already on re-sends 'on', which changes nothing.

    Unlike a toggle code, an absolute one is safe to repeat, and repeating it is
    useful when the first transmission was missed.
    """
    await _call(hass, SERVICE_TURN_ON)
    await _call(hass, SERVICE_TURN_ON)

    assert payloads(switch) == [["b64:b24="]] * 2
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_a_switch_without_a_power_sensor_assumes_its_state(
    hass: HomeAssistant, switch
) -> None:
    """IR is open-loop, so the UI should offer two buttons rather than a toggle."""
    assert hass.states.get(ENTITY_ID).attributes["assumed_state"] is True


async def test_a_power_sensor_makes_the_state_observed(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """With a sensor the state is a reading, so it is not assumed."""
    write_device_file("switch", 9202, SWITCH_DEVICE_DATA)
    hass.states.async_set("binary_sensor.amp_power", STATE_ON)
    await setup_platform(
        SWITCH_DOMAIN,
        {
            **CONFIG,
            "name": "P Amp",
            "unique_id": "p_amp",
            "device_code": 9202,
            "power_sensor": "binary_sensor.amp_power",
        },
    )

    state = hass.states.get("switch.p_amp")
    # Adopted at startup rather than waiting for the sensor to change.
    assert state.state == STATE_ON
    assert state.attributes.get("assumed_state") is not True

    hass.states.async_set("binary_sensor.amp_power", STATE_OFF)
    await hass.async_block_till_done()
    assert hass.states.get("switch.p_amp").state == STATE_OFF


# ---------------------------------------------------------------------------
# A remote with one power button
# ---------------------------------------------------------------------------


async def test_a_toggle_only_switch_sends_the_toggle_code(
    hass: HomeAssistant, toggle_switch
) -> None:
    """Nothing else is recorded, so the toggle code is what moves it."""
    await _call(hass, SERVICE_TURN_ON)

    assert payloads(toggle_switch) == [["b64:dG9nZ2xl"]]
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_a_toggle_only_switch_does_not_re_send_when_it_already_matches(
    hass: HomeAssistant, toggle_switch
) -> None:
    """Sending the code again would switch the device off.

    This is the whole reason a toggle-only device needs the entity to believe a
    state rather than transmitting on every request.
    """
    await _call(hass, SERVICE_TURN_ON)
    await _call(hass, SERVICE_TURN_ON)

    assert payloads(toggle_switch) == [["b64:dG9nZ2xl"]]
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_a_toggle_only_switch_toggles_back(
    hass: HomeAssistant, toggle_switch
) -> None:
    """On then off sends the same code twice, which is what the hardware wants."""
    await _call(hass, SERVICE_TURN_ON)
    await _call(hass, SERVICE_TURN_OFF)

    assert payloads(toggle_switch) == [["b64:dG9nZ2xl"]] * 2
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_the_toggle_service_works_on_a_toggle_only_switch(
    hass: HomeAssistant, toggle_switch
) -> None:
    """switch.toggle routes through turn_on/turn_off, so it needs no special case."""
    await _call(hass, SERVICE_TOGGLE)
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    await _call(hass, SERVICE_TOGGLE)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    assert payloads(toggle_switch) == [["b64:dG9nZ2xl"]] * 2


# ---------------------------------------------------------------------------
# Files that cannot do what is asked
# ---------------------------------------------------------------------------


async def test_a_file_with_no_power_codes_is_refused(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A switch with none of the three codes could never do anything."""
    data = {**SWITCH_DEVICE_DATA, "commands": {"extras": {"menu": "bWVudQ=="}}}
    write_device_file("switch", 9203, data)

    await setup_platform(
        SWITCH_DOMAIN,
        {**CONFIG, "name": "Bad", "unique_id": "bad", "device_code": 9203},
    )

    assert hass.states.get("switch.bad") is None


async def test_turning_off_a_one_way_switch_names_the_code_it_wanted(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """The error should name 'off', not a toggle the user has never heard of."""
    data = {**SWITCH_DEVICE_DATA, "commands": {"on": "b24="}}
    write_device_file("switch", 9204, data)
    await setup_platform(
        SWITCH_DOMAIN,
        {**CONFIG, "name": "One Way", "unique_id": "one_way", "device_code": 9204},
    )

    with pytest.raises(HomeAssistantError, match="'off'"):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "switch.one_way"},
            blocking=True,
        )

    assert payloads(sent_commands) == []


async def test_a_failed_send_rolls_back_the_state(hass: HomeAssistant, switch) -> None:
    """A state that was never transmitted must not be published."""
    with (
        patch(
            "custom_components.hub_ir.controller.BroadlinkController.send",
            side_effect=HomeAssistantError("no remote"),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await _call(hass, SERVICE_TURN_ON)

    assert hass.states.get(ENTITY_ID).state == STATE_OFF
