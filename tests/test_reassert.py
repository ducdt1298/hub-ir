"""Tests for re-asserting a state a power sensor contradicts.

This is the most dangerous option in the integration, so most of what follows
pins down what it must *not* do. The envelope in one place:

* off by default, and then behaviour is exactly what it always was;
* a contradiction within a minute of our own transmission is the sensor catching
  up, and is ignored;
* three attempts, then it gives up and follows the sensor until someone touches
  the thermostat;
* anything that is not on or off is ignored entirely, and so is the first reading
  after the sensor returns from unavailable;
* a sensor saying *on* while the entity wants *off* is never re-asserted, because
  that is almost always a person holding the original remote.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    HVACMode,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .conftest import (
    CLIMATE_DEVICE_DATA,
    CLIMATE_PRESET_DEVICE_DATA,
    SWITCH_DEVICE_DATA,
    SWITCH_TOGGLE_DEVICE_DATA,
    payloads,
)

ENTITY_ID = "climate.reassert_ac"
SENSOR = "binary_sensor.ac_power"

# Comfortably past REASSERT_SETTLE_SECONDS.
PAST_SETTLE = timedelta(seconds=90)


@pytest.fixture
async def reassert(hass, write_device_file, sent_commands, setup_platform):
    """Return a helper that sets the entity up with the options under test."""

    async def _setup(
        *, device_data: dict[str, Any] = CLIMATE_DEVICE_DATA, **options: Any
    ):
        write_device_file("climate", 9300, device_data)
        hass.states.async_set(SENSOR, STATE_OFF)
        await setup_platform(
            CLIMATE_DOMAIN,
            {
                "name": "Reassert AC",
                "unique_id": "reassert_ac",
                "device_code": 9300,
                "controller_data": "remote.broadlink",
                "power_sensor": SENSOR,
                **options,
            },
        )
        return sent_commands

    return _setup


async def _run(hass: HomeAssistant, mode: HVACMode = HVACMode.HEAT) -> None:
    """Put the entity into a running mode."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, "hvac_mode": mode},
        blocking=True,
    )


async def _sensor(hass: HomeAssistant, state: str) -> None:
    """Change the power sensor and let the listener run."""
    hass.states.async_set(SENSOR, state)
    await hass.async_block_till_done()


async def _advance(hass: HomeAssistant, freezer, delta: timedelta) -> None:
    """Move the clock forward and let anything due run.

    The clock has to move for real: the settle window is measured with
    dt_util.utcnow(), which async_fire_time_changed on its own does not touch.
    """
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _contradict(hass: HomeAssistant, freezer) -> None:
    """Report on, then off, outside the settle window.

    The sensor has to pass through on to produce an off *transition* at all,
    which is also what happens in the real failure: the unit was running, then
    the sensor noticed it was not.
    """
    await _sensor(hass, STATE_ON)
    await _advance(hass, freezer, PAST_SETTLE)
    await _sensor(hass, STATE_OFF)


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


async def test_reassert_is_off_by_default(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """Every configuration that exists today must behave exactly as before."""
    sent = await reassert()
    await _run(hass)
    sent.clear()

    await _contradict(hass, freezer)

    assert payloads(sent) == []
    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.OFF
    assert state.attributes["reassert_attempts"] == 0


# ---------------------------------------------------------------------------
# The asymmetry, which is the heart of the feature
# ---------------------------------------------------------------------------


async def test_a_sensor_that_says_on_while_we_want_off_never_re_asserts(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """The guarantee that matters most.

    A sensor reporting on while Home Assistant thinks the unit is off is almost
    always somebody who has just picked up the original remote. Switching their
    air conditioner off over it is the worst thing this feature could do.
    """
    sent = await reassert(power_sensor_reassert=True)
    assert hass.states.get(ENTITY_ID).state == HVACMode.OFF

    await _advance(hass, freezer, PAST_SETTLE)
    await _sensor(hass, STATE_ON)

    assert payloads(sent) == []
    state = hass.states.get(ENTITY_ID)
    assert state.state != HVACMode.OFF
    assert state.attributes["on_by_remote"] is True


async def test_a_sensor_that_says_off_after_the_settle_window_re_sends_the_state(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """The failure this exists for: a dropped frame, or a hand in the beam."""
    sent = await reassert(power_sensor_reassert=True)
    await _run(hass)
    sent.clear()

    await _contradict(hass, freezer)

    assert payloads(sent) == [["b64:aGVhdDE2"]]
    state = hass.states.get(ENTITY_ID)
    # Still believed to be running: the point is to make that true again, not to
    # give up on it.
    assert state.state == HVACMode.HEAT
    assert state.attributes["reassert_attempts"] == 1


# ---------------------------------------------------------------------------
# The settle window
# ---------------------------------------------------------------------------


async def test_a_contradiction_inside_the_settle_window_is_ignored(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """A compressor takes seconds to draw current; a plug reports when it likes.

    Neither adopt nor re-assert: the sensor is catching up with what we just sent.
    """
    sent = await reassert(power_sensor_reassert=True)
    await _sensor(hass, STATE_ON)
    await _run(hass)
    sent.clear()

    await _sensor(hass, STATE_OFF)

    assert payloads(sent) == []
    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.HEAT
    assert state.attributes["reassert_attempts"] == 0


# ---------------------------------------------------------------------------
# The attempt cap
# ---------------------------------------------------------------------------


async def test_re_assert_gives_up_after_the_cap_and_says_so(
    hass: HomeAssistant, reassert, freezer, caplog
) -> None:
    """A blocked emitter must not become an endless stream of transmissions."""
    sent = await reassert(power_sensor_reassert=True)
    await _run(hass)
    sent.clear()

    for _ in range(5):
        await _contradict(hass, freezer)

    assert len(payloads(sent)) == 3, "the cap did not hold"
    assert hass.states.get(ENTITY_ID).state == HVACMode.OFF
    assert "giving up" in caplog.text
    # One warning, not one per attempt.
    assert caplog.text.count("giving up") == 1


async def test_a_user_command_restores_the_budget(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """They have asked for something, so the feature gets a fresh start.

    The subtlety here: a re-assert goes through the same send path as a command,
    so resetting the counter there would make the cap unreachable.
    """
    sent = await reassert(power_sensor_reassert=True)
    await _run(hass)

    for _ in range(5):
        await _contradict(hass, freezer)
    assert hass.states.get(ENTITY_ID).attributes["reassert_attempts"] == 3

    await _run(hass, HVACMode.COOL)
    assert hass.states.get(ENTITY_ID).attributes["reassert_attempts"] == 0

    sent.clear()
    await _contradict(hass, freezer)
    assert payloads(sent) == [["b64:Y29vbDE2"]]


# ---------------------------------------------------------------------------
# Sensor states that mean nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unusable", [STATE_UNAVAILABLE, STATE_UNKNOWN, ""])
async def test_an_unusable_sensor_state_changes_nothing(
    hass: HomeAssistant, reassert, freezer, unusable: str
) -> None:
    """A sensor dropping out says nothing at all about the device."""
    sent = await reassert(power_sensor_reassert=True)
    await _run(hass)
    sent.clear()

    await _advance(hass, freezer, PAST_SETTLE)
    await _sensor(hass, unusable)

    assert payloads(sent) == []
    assert hass.states.get(ENTITY_ID).state == HVACMode.HEAT


async def test_a_sensor_returning_from_unavailable_adopts_but_does_not_re_assert(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """A restart artefact must not put a code on the air."""
    sent = await reassert(power_sensor_reassert=True)
    await _run(hass)
    sent.clear()

    await _advance(hass, freezer, PAST_SETTLE)
    await _sensor(hass, STATE_UNAVAILABLE)
    await _sensor(hass, STATE_OFF)

    assert payloads(sent) == []
    assert hass.states.get(ENTITY_ID).state == HVACMode.OFF


# ---------------------------------------------------------------------------
# The periodic refresh
# ---------------------------------------------------------------------------


async def test_the_periodic_refresh_re_sends_the_current_state(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """For a unit that forgets its setpoint after a power blip."""
    sent = await reassert(reassert_interval=5)
    await _run(hass)
    await _sensor(hass, STATE_ON)
    sent.clear()

    await _advance(hass, freezer, timedelta(minutes=6))

    assert payloads(sent) == [["b64:aGVhdDE2"]]


async def test_the_periodic_refresh_sends_nothing_while_off(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """Transmitting 'off' at a unit somebody just switched on is the same fight."""
    sent = await reassert(reassert_interval=5)
    assert hass.states.get(ENTITY_ID).state == HVACMode.OFF

    await _advance(hass, freezer, timedelta(minutes=12))

    assert payloads(sent) == []


async def test_the_periodic_refresh_re_sends_the_preset_not_the_state(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """The ordinary state frame is exactly what clears a preset.

    Refreshing with it would quietly cancel the Turbo the entity is claiming to
    be in, which is the one way these two features could contradict each other.
    """
    sent = await reassert(device_data=CLIMATE_PRESET_DEVICE_DATA, reassert_interval=5)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: "turbo"},
        blocking=True,
    )
    await _sensor(hass, STATE_ON)
    sent.clear()

    await _advance(hass, freezer, timedelta(minutes=6))

    assert payloads(sent) == [["b64:dHVyYm8="]]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_PRESET_MODE] == "turbo"


async def test_the_periodic_refresh_survives_a_remote_that_is_gone(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """A background timer must never raise into the event loop."""
    sent = await reassert(reassert_interval=5)
    await _run(hass)
    await _sensor(hass, STATE_ON)
    sent.clear()

    hass.states.async_remove("remote.broadlink")
    await _advance(hass, freezer, timedelta(minutes=6))
    assert payloads(sent) == []

    # Still alive: the next tick fires once the remote is back.
    hass.states.async_set("remote.broadlink", STATE_ON)
    await _advance(hass, freezer, timedelta(minutes=6))
    assert payloads(sent) == [["b64:aGVhdDE2"]]


# ---------------------------------------------------------------------------
# The switch platform, where a toggle-only remote must never re-assert
# ---------------------------------------------------------------------------

SWITCH_ENTITY = "switch.reassert_amp"


@pytest.fixture
async def reassert_switch(hass, write_device_file, sent_commands, setup_platform):
    """Return a helper that sets a switch up with the options under test."""

    async def _setup(*, device_data: dict[str, Any], **options: Any):
        write_device_file("switch", 9301, device_data)
        hass.states.async_set(SENSOR, STATE_OFF)
        await setup_platform(
            SWITCH_DOMAIN,
            {
                "name": "Reassert Amp",
                "unique_id": "reassert_amp",
                "device_code": 9301,
                "controller_data": "remote.broadlink",
                "power_sensor": SENSOR,
                **options,
            },
        )
        return sent_commands

    return _setup


async def test_a_switch_re_sends_on_when_the_sensor_says_off(
    hass: HomeAssistant, reassert_switch, freezer
) -> None:
    """The same dropped frame, on an amplifier rather than an air conditioner."""
    sent = await reassert_switch(
        device_data=SWITCH_DEVICE_DATA, power_sensor_reassert=True
    )
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: SWITCH_ENTITY},
        blocking=True,
    )
    sent.clear()

    await _sensor(hass, STATE_ON)
    await _advance(hass, freezer, PAST_SETTLE)
    await _sensor(hass, STATE_OFF)

    assert payloads(sent) == [["b64:b24="]]
    assert hass.states.get(SWITCH_ENTITY).state == STATE_ON


async def test_a_toggle_only_switch_never_re_asserts(
    hass: HomeAssistant, reassert_switch, freezer, caplog
) -> None:
    """Nothing in a toggle pair is absolute, so a nudge is a coin flip.

    Re-sending it when the sensor disagrees could switch the device either way,
    and could oscillate for ever. The option is refused at construction.
    """
    sent = await reassert_switch(
        device_data=SWITCH_TOGGLE_DEVICE_DATA,
        power_sensor_reassert=True,
        reassert_interval=5,
    )
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: SWITCH_ENTITY},
        blocking=True,
    )
    sent.clear()

    await _sensor(hass, STATE_ON)
    await _advance(hass, freezer, PAST_SETTLE)
    await _sensor(hass, STATE_OFF)
    await _advance(hass, freezer, timedelta(minutes=12))

    assert payloads(sent) == []
    assert "only a toggle code" in caplog.text


async def test_the_timer_stops_when_the_entity_goes_away(
    hass: HomeAssistant, reassert, freezer
) -> None:
    """A leaked timer would keep transmitting for an entity nobody can see."""
    sent = await reassert(reassert_interval=5)
    await _run(hass)
    await _sensor(hass, STATE_ON)
    sent.clear()

    entity = hass.data[DATA_INSTANCES][CLIMATE_DOMAIN].get_entity(ENTITY_ID)
    await entity.async_remove()
    await hass.async_block_till_done()

    await _advance(hass, freezer, timedelta(minutes=30))
    assert payloads(sent) == []
