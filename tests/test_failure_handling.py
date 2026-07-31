"""Tests for what happens when a command cannot be delivered.

IR is open-loop, so these entities assume their command took effect — there is
no feedback that could confirm it. That is the right model, and it is what
`iot_class: assumed_state` declares.

A send that *fails* is a different thing entirely. If the remote is unavailable,
misconfigured, or the code is corrupt, nothing reached the device and we know it.
Upstream logged those and published the requested state anyway, so with the
Broadlink unplugged the UI showed an air conditioner cooling an untouched room.
These tests pin the opposite: the service call fails and the entity keeps
reporting the last state it actually transmitted.
"""

from __future__ import annotations

from typing import Any

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
from homeassistant.components.fan import ATTR_PERCENTAGE, DOMAIN as FAN_DOMAIN
from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOURCE,
    MediaType,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from .conftest import (
    CLIMATE_DEVICE_DATA,
    FAN_DEVICE_DATA,
    LIGHT_DEVICE_DATA,
    MEDIA_PLAYER_DEVICE_DATA,
    REMOTE_ENTITY_IDS,
    payloads,
)


@pytest.fixture
async def breakable_remote(hass: HomeAssistant):
    """Return a remote whose send_command can be made to start failing.

    The handle carries the captured calls and a `break_it()` that makes every
    later send raise, so a test can succeed first and fail second — that is what
    proves the entity rolled back rather than never having advanced.
    """

    class Remote:
        def __init__(self) -> None:
            self.calls: list[ServiceCall] = []
            self.failing = False

        def break_it(self) -> None:
            self.failing = True

        def clear(self) -> None:
            self.calls.clear()

    remote = Remote()

    assert await async_setup_component(hass, "remote", {})
    await hass.async_block_till_done()
    for entity_id in REMOTE_ENTITY_IDS:
        hass.states.async_set(entity_id, STATE_ON)

    @callback
    def handle(call: ServiceCall) -> None:
        if remote.failing:
            raise HomeAssistantError("the Broadlink device did not respond")
        remote.calls.append(call)

    hass.services.async_register("remote", "send_command", handle)
    return remote


async def _setup(hass: HomeAssistant, domain: str, config: dict[str, Any]) -> None:
    """Set a platform up from YAML."""
    assert await async_setup_component(
        hass, domain, {domain: {"platform": "broadlink_ir", **config}}
    )
    await hass.async_block_till_done()


# --------------------------------------------------------------------------
# A misconfigured or missing remote
# --------------------------------------------------------------------------


async def test_a_remote_that_does_not_exist_is_reported(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A typo in controller_data must not look like a successful send.

    remote.send_command targets an entity, and Home Assistant does not raise for
    an entity_id that matches nothing — it logs that the reference is missing and
    carries on. Upstream therefore reported success forever.
    """
    write_device_file("climate", 9500, CLIMATE_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Typo AC",
            "unique_id": "typo_ac",
            "device_code": 9500,
            # A valid entity_id that no entity has.
            "controller_data": "remote.does_not_exist",
        },
    )

    with pytest.raises(HomeAssistantError, match="does not exist"):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: "climate.typo_ac", ATTR_HVAC_MODE: HVACMode.COOL},
            blocking=True,
        )

    assert payloads(sent_commands) == []
    assert hass.states.get("climate.typo_ac").state == HVACMode.OFF


async def test_an_unavailable_remote_is_reported(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """An unplugged Broadlink fails the call instead of being ignored."""
    write_device_file("climate", 9501, CLIMATE_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Gone AC",
            "unique_id": "gone_ac",
            "device_code": 9501,
            "controller_data": "remote.broadlink",
        },
    )

    hass.states.async_set("remote.broadlink", STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="is unavailable"):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: "climate.gone_ac", ATTR_HVAC_MODE: HVACMode.COOL},
            blocking=True,
        )

    assert payloads(sent_commands) == []
    assert hass.states.get("climate.gone_ac").state == HVACMode.OFF


@pytest.mark.parametrize(
    "controller_data",
    ["switch.not_a_remote", "sensor.temperature", "not_an_entity_id", "remote"],
)
async def test_controller_data_must_name_a_remote(
    hass: HomeAssistant, write_device_file, sent_commands, controller_data
) -> None:
    """Anything that is not a remote entity_id is refused at config validation.

    The wrong domain is silent at runtime, so it has to be caught here.
    """
    write_device_file("climate", 9502, CLIMATE_DEVICE_DATA)

    await async_setup_component(
        hass,
        CLIMATE_DOMAIN,
        {
            CLIMATE_DOMAIN: {
                "platform": "broadlink_ir",
                "name": "Bad Controller",
                "unique_id": "bad_controller",
                "device_code": 9502,
                "controller_data": controller_data,
            }
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get("climate.bad_controller") is None


# --------------------------------------------------------------------------
# Climate
# --------------------------------------------------------------------------


async def test_climate_keeps_the_mode_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed mode change leaves the entity on the mode that did transmit."""
    write_device_file("climate", 9510, CLIMATE_DEVICE_DATA)
    await _setup(
        hass,
        CLIMATE_DOMAIN,
        {
            "name": "Roll AC",
            "unique_id": "roll_ac",
            "device_code": 9510,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.roll_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    assert hass.states.get("climate.roll_ac").state == HVACMode.COOL

    breakable_remote.break_it()
    breakable_remote.clear()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: "climate.roll_ac", ATTR_HVAC_MODE: HVACMode.HEAT},
            blocking=True,
        )

    # Not 'heat': that code never left Home Assistant.
    assert hass.states.get("climate.roll_ac").state == HVACMode.COOL


async def test_climate_keeps_the_temperature_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed setpoint change does not move the advertised target."""
    write_device_file("climate", 9511, CLIMATE_DEVICE_DATA)
    await _setup(
        hass,
        CLIMATE_DOMAIN,
        {
            "name": "Temp AC",
            "unique_id": "temp_ac",
            "device_code": 9511,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: "climate.temp_ac",
            ATTR_TEMPERATURE: 17,
            ATTR_HVAC_MODE: HVACMode.COOL,
        },
        blocking=True,
    )
    assert hass.states.get("climate.temp_ac").attributes[ATTR_TEMPERATURE] == 17

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: "climate.temp_ac", ATTR_TEMPERATURE: 16},
            blocking=True,
        )

    assert hass.states.get("climate.temp_ac").attributes[ATTR_TEMPERATURE] == 17
    assert hass.states.get("climate.temp_ac").state == HVACMode.COOL


async def test_climate_keeps_the_fan_mode_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed fan change does not move the advertised fan mode."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "fanModes": ["low", "high"],
        "commands": {
            "off": "b2Zm",
            "cool": {
                "low": {"16": "Y29vbDE2", "17": "Y29vbDE3"},
                "high": {"16": "Y29vbEgxNg==", "17": "Y29vbEgxNw=="},
            },
            "heat": {
                "low": {"16": "aGVhdDE2", "17": "aGVhdDE3"},
                "high": {"16": "aGVhdEgxNg==", "17": "aGVhdEgxNw=="},
            },
        },
    }
    write_device_file("climate", 9512, data)
    await _setup(
        hass,
        CLIMATE_DOMAIN,
        {
            "name": "Fanmode AC",
            "unique_id": "fanmode_ac",
            "device_code": 9512,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.fanmode_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    assert hass.states.get("climate.fanmode_ac").attributes[ATTR_FAN_MODE] == "low"

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: "climate.fanmode_ac", ATTR_FAN_MODE: "high"},
            blocking=True,
        )

    assert hass.states.get("climate.fanmode_ac").attributes[ATTR_FAN_MODE] == "low"


async def test_climate_without_an_off_code_says_so(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A device file with no 'off' code cannot turn the unit off.

    climate/1801 in the database is exactly this. Upstream raised a bare KeyError
    into the log and showed the entity as off regardless.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "commands": {
            "off": "",
            "cool": {"low": {"16": "Y29vbDE2", "17": "Y29vbDE3"}},
            "heat": {"low": {"16": "aGVhdDE2", "17": "aGVhdDE3"}},
        },
    }
    write_device_file("climate", 9513, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Nooff AC",
            "unique_id": "nooff_ac",
            "device_code": 9513,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.nooff_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    sent_commands.clear()

    with pytest.raises(HomeAssistantError, match="no 'off' code"):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "climate.nooff_ac"},
            blocking=True,
        )

    assert payloads(sent_commands) == []
    # Still cooling, because that is what the device was last told to do.
    assert hass.states.get("climate.nooff_ac").state == HVACMode.COOL


# --------------------------------------------------------------------------
# Fan
# --------------------------------------------------------------------------


async def test_fan_keeps_the_speed_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed speed change leaves the fan reporting the speed that worked."""
    write_device_file("fan", 9520, FAN_DEVICE_DATA)
    await _setup(
        hass,
        FAN_DOMAIN,
        {
            "name": "Roll Fan",
            "unique_id": "roll_fan",
            "device_code": 9520,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        FAN_DOMAIN,
        "set_percentage",
        {ATTR_ENTITY_ID: "fan.roll_fan", ATTR_PERCENTAGE: 50},
        blocking=True,
    )
    was = hass.states.get("fan.roll_fan").attributes[ATTR_PERCENTAGE]

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            FAN_DOMAIN,
            "set_percentage",
            {ATTR_ENTITY_ID: "fan.roll_fan", ATTR_PERCENTAGE: 100},
            blocking=True,
        )

    state = hass.states.get("fan.roll_fan")
    assert state.state == STATE_ON
    assert state.attributes[ATTR_PERCENTAGE] == was


async def test_fan_that_cannot_be_turned_off_reports_it(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed turn_off does not leave the fan looking switched off."""
    write_device_file("fan", 9521, FAN_DEVICE_DATA)
    await _setup(
        hass,
        FAN_DOMAIN,
        {
            "name": "Stuck Fan",
            "unique_id": "stuck_fan",
            "device_code": 9521,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "fan.stuck_fan"}, blocking=True
    )
    assert hass.states.get("fan.stuck_fan").state == STATE_ON

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            FAN_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "fan.stuck_fan"},
            blocking=True,
        )

    assert hass.states.get("fan.stuck_fan").state == STATE_ON


async def test_fan_missing_a_speed_command_says_so(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A speed the device file does not record fails loudly."""
    data = {
        **FAN_DEVICE_DATA,
        "speed": ["low", "high"],
        "commands": {"off": "b2Zm", "default": {"low": "bG93"}},
    }
    write_device_file("fan", 9522, data)
    await setup_platform(
        FAN_DOMAIN,
        {
            "name": "Gap Fan",
            "unique_id": "gap_fan",
            "device_code": 9522,
            "controller_data": "remote.broadlink",
        },
    )

    with pytest.raises(HomeAssistantError, match="no command for speed 'high'"):
        await hass.services.async_call(
            FAN_DOMAIN,
            "set_percentage",
            {ATTR_ENTITY_ID: "fan.gap_fan", ATTR_PERCENTAGE: 100},
            blocking=True,
        )

    assert payloads(sent_commands) == []
    assert hass.states.get("fan.gap_fan").state == STATE_OFF


# --------------------------------------------------------------------------
# Light
# --------------------------------------------------------------------------


async def test_light_keeps_the_state_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed turn_on leaves the light reporting off."""
    write_device_file("light", 9530, LIGHT_DEVICE_DATA)
    await _setup(
        hass,
        LIGHT_DOMAIN,
        {
            "name": "Roll Light",
            "unique_id": "roll_light",
            "device_code": 9530,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "light.roll_light"},
        blocking=True,
    )
    assert hass.states.get("light.roll_light").state == STATE_OFF

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "light.roll_light"},
            blocking=True,
        )

    assert hass.states.get("light.roll_light").state == STATE_OFF


async def test_light_keeps_the_brightness_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed brightness change does not move the advertised brightness."""
    write_device_file("light", 9531, LIGHT_DEVICE_DATA)
    await _setup(
        hass,
        LIGHT_DOMAIN,
        {
            "name": "Dim Light",
            "unique_id": "dim_light",
            "device_code": 9531,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.dim_light", ATTR_BRIGHTNESS: 10},
        blocking=True,
    )
    was = hass.states.get("light.dim_light").attributes[ATTR_BRIGHTNESS]

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "light.dim_light", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    assert hass.states.get("light.dim_light").attributes[ATTR_BRIGHTNESS] == was


async def test_light_missing_a_command_says_so(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A light whose 'off' code was never captured cannot be turned off."""
    data = {
        **LIGHT_DEVICE_DATA,
        "commands": {**LIGHT_DEVICE_DATA["commands"], "off": ""},
    }
    write_device_file("light", 9532, data)
    await setup_platform(
        LIGHT_DOMAIN,
        {
            "name": "Nooff Light",
            "unique_id": "nooff_light",
            "device_code": 9532,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.nooff_light"},
        blocking=True,
    )
    sent_commands.clear()

    with pytest.raises(HomeAssistantError, match="no code recorded for 'off'"):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "light.nooff_light"},
            blocking=True,
        )

    assert payloads(sent_commands) == []
    assert hass.states.get("light.nooff_light").state == STATE_ON


# --------------------------------------------------------------------------
# Media player
# --------------------------------------------------------------------------


async def test_media_player_keeps_the_state_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed turn_on leaves the player reporting off."""
    write_device_file("media_player", 9540, MEDIA_PLAYER_DEVICE_DATA)
    await _setup(
        hass,
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Roll TV",
            "unique_id": "roll_tv",
            "device_code": 9540,
            "controller_data": "remote.broadlink",
        },
    )

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "media_player.roll_tv"},
            blocking=True,
        )

    assert hass.states.get("media_player.roll_tv").state == STATE_OFF


async def test_media_player_keeps_the_source_it_last_sent(
    hass: HomeAssistant, write_device_file, breakable_remote
) -> None:
    """A failed source change does not move the advertised source."""
    write_device_file("media_player", 9541, MEDIA_PLAYER_DEVICE_DATA)
    await _setup(
        hass,
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Src TV",
            "unique_id": "src_tv",
            "device_code": 9541,
            "controller_data": "remote.broadlink",
        },
    )

    # Home Assistant only publishes 'source' while the player is on.
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "media_player.src_tv"},
        blocking=True,
    )
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_SELECT_SOURCE,
        {ATTR_ENTITY_ID: "media_player.src_tv", ATTR_INPUT_SOURCE: "HDMI1"},
        blocking=True,
    )
    assert hass.states.get("media_player.src_tv").attributes["source"] == "HDMI1"

    breakable_remote.break_it()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_SELECT_SOURCE,
            {ATTR_ENTITY_ID: "media_player.src_tv", ATTR_INPUT_SOURCE: "Channel 1"},
            blocking=True,
        )

    assert hass.states.get("media_player.src_tv").attributes["source"] == "HDMI1"


@pytest.mark.parametrize(
    ("media_type", "media_id", "match"),
    [
        ("music", "1", "Invalid media type"),
        (MediaType.CHANNEL, "BBC One", "must be a channel number"),
        (MediaType.CHANNEL, "1.5", "must be a channel number"),
    ],
)
async def test_play_media_rejects_what_it_cannot_tune(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    media_type,
    media_id,
    match,
) -> None:
    """play_media only enters channel numbers, so anything else fails the call."""
    write_device_file("media_player", 9543, MEDIA_PLAYER_DEVICE_DATA)
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Tune TV",
            "unique_id": "tune_tv",
            "device_code": 9543,
            "controller_data": "remote.broadlink",
        },
    )

    with pytest.raises(HomeAssistantError, match=match):
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: "media_player.tune_tv",
                ATTR_MEDIA_CONTENT_TYPE: media_type,
                ATTR_MEDIA_CONTENT_ID: media_id,
            },
            blocking=True,
        )

    assert payloads(sent_commands) == []


async def test_media_player_source_without_a_code_says_so(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A source whose code is an empty placeholder is refused."""
    data = {
        **MEDIA_PLAYER_DEVICE_DATA,
        "commands": {
            **MEDIA_PLAYER_DEVICE_DATA["commands"],
            "sources": {"HDMI1": "aGRtaTE=", "HDMI2": ""},
        },
    }
    write_device_file("media_player", 9542, data)
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Gap TV",
            "unique_id": "gap_tv",
            "device_code": 9542,
            "controller_data": "remote.broadlink",
        },
    )

    # Not offered in the first place.
    assert hass.states.get("media_player.gap_tv").attributes["source_list"] == ["HDMI1"]

    with pytest.raises(HomeAssistantError, match="no code recorded for the source"):
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_SELECT_SOURCE,
            {ATTR_ENTITY_ID: "media_player.gap_tv", ATTR_INPUT_SOURCE: "HDMI2"},
            blocking=True,
        )

    assert payloads(sent_commands) == []
