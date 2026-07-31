"""Tests for the Broadlink IR media player platform."""

from __future__ import annotations

import pytest

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOURCE,
    MediaPlayerDeviceClass,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_UP,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant

from .conftest import MEDIA_PLAYER_DEVICE_DATA, payloads

ENTITY_ID = "media_player.test_tv"
CONFIG = {
    "name": "Test TV",
    "unique_id": "test_tv",
    "device_code": 9300,
    "controller_data": "remote.broadlink",
}


@pytest.fixture
async def tv(hass, write_device_file, sent_commands, setup_platform):
    """Set up a media player entity backed by the test device file."""
    write_device_file("media_player", 9300, MEDIA_PLAYER_DEVICE_DATA)
    await setup_platform(MEDIA_PLAYER_DOMAIN, CONFIG)
    return sent_commands


async def test_features_derived_from_device_file(hass: HomeAssistant, tv) -> None:
    """Only the commands present in the device file are advertised."""
    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["device_class"] == MediaPlayerDeviceClass.TV

    features = MediaPlayerEntityFeature(state.attributes["supported_features"])
    assert features & MediaPlayerEntityFeature.TURN_ON
    assert features & MediaPlayerEntityFeature.TURN_OFF
    assert features & MediaPlayerEntityFeature.VOLUME_STEP
    assert features & MediaPlayerEntityFeature.VOLUME_MUTE
    assert features & MediaPlayerEntityFeature.SELECT_SOURCE
    # No volumeSet code in the device file.
    assert not features & MediaPlayerEntityFeature.VOLUME_SET


async def test_turn_on_off_and_volume(hass: HomeAssistant, tv) -> None:
    """The basic commands map onto the device file's codes."""
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    assert payloads(tv) == [["b64:b24="]]
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    tv.clear()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_VOLUME_UP,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    assert payloads(tv) == [["b64:dnUp"]]
    tv.clear()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    assert payloads(tv) == [["b64:b2Zm"]]
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_select_source(hass: HomeAssistant, tv) -> None:
    """Selecting a source sends that source's code."""
    assert set(hass.states.get(ENTITY_ID).attributes["source_list"]) == {
        "HDMI1",
        "Channel 1",
        "Channel 2",
    }

    # HA reports no state attributes at all while a media player is off.
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    tv.clear()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_SELECT_SOURCE,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_INPUT_SOURCE: "HDMI1"},
        blocking=True,
    )

    assert payloads(tv) == [["b64:aGRtaTE="]]
    assert hass.states.get(ENTITY_ID).attributes["source"] == "HDMI1"


async def test_play_media_tunes_digit_by_digit(hass: HomeAssistant, tv) -> None:
    """A channel number is sent as one code per digit."""
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    tv.clear()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: ENTITY_ID,
            ATTR_MEDIA_CONTENT_TYPE: MediaType.CHANNEL,
            ATTR_MEDIA_CONTENT_ID: "12",
        },
        blocking=True,
    )

    assert payloads(tv) == [["b64:Y2gx"], ["b64:Y2gy"]]
    assert hass.states.get(ENTITY_ID).attributes["source"] == "Channel 12"


async def test_play_media_with_an_unknown_digit_sends_nothing(
    hass: HomeAssistant, tv
) -> None:
    """A channel the device file cannot express is refused before any code."""
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    tv.clear()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: ENTITY_ID,
            ATTR_MEDIA_CONTENT_TYPE: MediaType.CHANNEL,
            ATTR_MEDIA_CONTENT_ID: "39",
        },
        blocking=True,
    )

    assert payloads(tv) == []


async def test_source_names_renames_sources(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """source_names replaces a source's label but keeps its code."""
    write_device_file("media_player", 9301, MEDIA_PLAYER_DEVICE_DATA)
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            **CONFIG,
            "name": "R TV",
            "unique_id": "r_tv",
            "device_code": 9301,
            "source_names": {"HDMI1": "Chromecast"},
        },
    )

    sources = hass.states.get("media_player.r_tv").attributes["source_list"]
    assert "Chromecast" in sources
    assert "HDMI1" not in sources

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_SELECT_SOURCE,
        {ATTR_ENTITY_ID: "media_player.r_tv", ATTR_INPUT_SOURCE: "Chromecast"},
        blocking=True,
    )

    assert payloads(sent_commands) == [["b64:aGRtaTE="]]


async def test_power_sensor_drives_state(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """With a power sensor configured, the sensor is the source of truth."""
    write_device_file("media_player", 9302, MEDIA_PLAYER_DEVICE_DATA)
    hass.states.async_set("binary_sensor.tv_power", STATE_ON)
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            **CONFIG,
            "name": "P TV",
            "unique_id": "p_tv",
            "device_code": 9302,
            "power_sensor": "binary_sensor.tv_power",
        },
    )

    assert hass.states.get("media_player.p_tv").state == STATE_ON

    hass.states.async_set("binary_sensor.tv_power", STATE_OFF)
    await hass.async_block_till_done()

    assert hass.states.get("media_player.p_tv").state == STATE_OFF

    hass.states.async_set("binary_sensor.tv_power", STATE_ON)
    await hass.async_block_till_done()

    assert hass.states.get("media_player.p_tv").state == STATE_ON
