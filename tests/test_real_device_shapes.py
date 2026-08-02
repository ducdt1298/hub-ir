"""End-to-end tests over the shapes that really occur in codes/.

The hand-written fixtures elsewhere cover one shape each. These drive actual
files from the shipped database, so the combinations that only exist in real
data — swing modes, Pronto encoding, a fan with forward/reverse instead of
default, a light with no colour temperature, a media player with no sources —
are exercised against Home Assistant rather than assumed to work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_SWING_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_MODE,
)
from homeassistant.components.fan import (
    ATTR_DIRECTION,
    ATTR_PERCENTAGE,
    DOMAIN as FAN_DOMAIN,
    SERVICE_SET_DIRECTION,
)
from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_UP,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import payloads

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def real_codes(monkeypatch: pytest.MonkeyPatch):
    """Serve device files straight out of the repository's codes/ directory."""
    from custom_components import hub_ir

    monkeypatch.setattr(hub_ir, "COMPONENT_ABS_DIR", str(REPO_ROOT))


def device_file(platform: str, code: int) -> dict:
    """Return a shipped device file's contents."""
    return json.loads(
        (REPO_ROOT / "codes" / platform / f"{code}.json").read_text(encoding="utf-8")
    )


def pick_climate_code_with_swing() -> int:
    """Return a shipped climate code that declares swing modes."""
    for path in sorted((REPO_ROOT / "codes" / "climate").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("swingModes"):
            return int(path.stem)
    raise AssertionError("no climate device file declares swingModes")


async def test_real_swing_mode_file_sends_per_swing_codes(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform
) -> None:
    """A real four-level command tree (mode/fan/swing/temperature) works."""
    code = pick_climate_code_with_swing()
    data = device_file("climate", code)
    swing_modes = data["swingModes"]

    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Swing AC",
            "unique_id": "swing_ac",
            "device_code": code,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("climate.swing_ac")
    assert state is not None
    assert state.attributes["swing_modes"] == swing_modes
    features = state.attributes["supported_features"]
    assert features & 32  # ClimateEntityFeature.SWING_MODE

    mode = next(m for m in data["operationModes"] if m in ("cool", "heat"))
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.swing_ac", ATTR_HVAC_MODE: mode},
        blocking=True,
    )
    first = payloads(sent_commands)
    assert first and all(code_.startswith("b64:") for code_ in first[-1])
    sent_commands.clear()

    # A different swing mode must produce a different code.
    other = (
        swing_modes[-1]
        if state.attributes["swing_mode"] != swing_modes[-1]
        else swing_modes[0]
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_SWING_MODE,
        {ATTR_ENTITY_ID: "climate.swing_ac", ATTR_SWING_MODE: other},
        blocking=True,
    )
    second = payloads(sent_commands)
    assert second
    assert hass.states.get("climate.swing_ac").attributes["swing_mode"] == other
    assert second[-1] != first[-1]


async def test_real_pronto_file_is_converted_and_sent(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform
) -> None:
    """The one Pronto-encoded file in the database converts to a Broadlink packet."""
    from base64 import b64decode

    code = next(
        int(path.stem)
        for path in sorted((REPO_ROOT / "codes" / "media_player").glob("*.json"))
        if json.loads(path.read_text(encoding="utf-8"))["commandsEncoding"] == "Pronto"
    )

    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Pronto TV",
            "unique_id": "pronto_tv",
            "device_code": code,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "media_player.pronto_tv"},
        blocking=True,
    )

    (payload,) = payloads(sent_commands)
    packet = b64decode(payload[0].removeprefix("b64:"))
    assert packet[0] == 0x26  # a Broadlink IR packet, not the Pronto text
    assert len(packet) > 8


async def test_real_forward_reverse_fan_file(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform
) -> None:
    """A fan file with forward/reverse instead of a default group works."""
    code = next(
        int(path.stem)
        for path in sorted((REPO_ROOT / "codes" / "fan").glob("*.json"))
        if "default" not in json.loads(path.read_text(encoding="utf-8"))["commands"]
    )
    data = device_file("fan", code)
    assert {"forward", "reverse"} <= set(data["commands"])

    await setup_platform(
        FAN_DOMAIN,
        {
            "name": "Dir Fan",
            "unique_id": "dir_fan",
            "device_code": code,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("fan.dir_fan")
    assert state is not None
    assert state.attributes[ATTR_DIRECTION] in ("forward", "reverse")

    # Full speed, not turn_on: this file's slowest reverse code is one of the
    # corrupt ones, which the integration refuses to transmit (covered below).
    await hass.services.async_call(
        FAN_DOMAIN,
        "set_percentage",
        {ATTR_ENTITY_ID: "fan.dir_fan", ATTR_PERCENTAGE: 100},
        blocking=True,
    )
    reverse_payload = payloads(sent_commands)[-1]
    sent_commands.clear()

    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_DIRECTION,
        {ATTR_ENTITY_ID: "fan.dir_fan", ATTR_DIRECTION: "forward"},
        blocking=True,
    )

    forward_payload = payloads(sent_commands)[-1]
    assert hass.states.get("fan.dir_fan").attributes[ATTR_DIRECTION] == "forward"
    assert forward_payload != reverse_payload


async def test_real_light_without_colour_temperature(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform
) -> None:
    """A brightness-only light file reports BRIGHTNESS, never UNKNOWN."""
    code = next(
        int(path.stem)
        for path in sorted((REPO_ROOT / "codes" / "light").glob("*.json"))
        if not json.loads(path.read_text(encoding="utf-8")).get("colorTemperature")
    )

    await setup_platform(
        LIGHT_DOMAIN,
        {
            "name": "Plain Light",
            "unique_id": "plain_light",
            "device_code": code,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("light.plain_light")
    assert state is not None
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]
    assert ColorMode.UNKNOWN not in state.attributes[ATTR_SUPPORTED_COLOR_MODES]

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.plain_light"},
        blocking=True,
    )
    assert hass.states.get("light.plain_light").state == STATE_ON
    assert payloads(sent_commands)


async def test_real_media_player_without_sources(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform
) -> None:
    """A file with no sources must not advertise SELECT_SOURCE."""
    code = next(
        int(path.stem)
        for path in sorted((REPO_ROOT / "codes" / "media_player").glob("*.json"))
        if not json.loads(path.read_text(encoding="utf-8"))["commands"].get("sources")
    )

    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "Basic TV",
            "unique_id": "basic_tv",
            "device_code": code,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("media_player.basic_tv")
    assert state is not None
    features = MediaPlayerEntityFeature(state.attributes["supported_features"])
    assert not features & MediaPlayerEntityFeature.SELECT_SOURCE
    assert not features & MediaPlayerEntityFeature.PLAY_MEDIA

    if features & MediaPlayerEntityFeature.VOLUME_STEP:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_VOLUME_UP,
            {ATTR_ENTITY_ID: "media_player.basic_tv"},
            blocking=True,
        )
        assert payloads(sent_commands)


async def test_the_empty_light_file_is_refused(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform
) -> None:
    """light/1040 ships with no codes at all, so no entity is created.

    The docs list it as a Toshiba FRC-199T, but every command in the file is an
    empty placeholder.
    """
    data = device_file("light", 1040)
    assert all(
        not entry.strip()
        for value in data["commands"].values()
        for entry in (value if isinstance(value, list) else [value])
    ), "light/1040 now has codes; drop this test and the docs note"

    from homeassistant.setup import async_setup_component

    await async_setup_component(
        hass,
        LIGHT_DOMAIN,
        {
            LIGHT_DOMAIN: {
                "platform": "hub_ir",
                "name": "Dead Light",
                "unique_id": "dead_light",
                "device_code": 1040,
                "controller_data": "remote.broadlink",
            }
        },
    )
    await hass.async_block_till_done()

    assert hass.states.get("light.dead_light") is None


async def test_a_corrupt_code_in_a_real_file_is_refused_not_transmitted(
    hass: HomeAssistant, real_codes, sent_commands, setup_platform, caplog
) -> None:
    """Selecting a corrupt code fails the call and sends nothing.

    fan/1000's reverse/lowest entry cannot be decoded even after re-padding.
    Upstream handed it to the Broadlink integration, which raised a bare
    binascii error; here the entity refuses it, says why, and does not pretend
    the speed changed.
    """
    await setup_platform(
        FAN_DOMAIN,
        {
            "name": "Corrupt Fan",
            "unique_id": "corrupt_fan",
            "device_code": 1000,
            "controller_data": "remote.broadlink",
        },
    )
    before = hass.states.get("fan.corrupt_fan")

    # The slowest speed maps onto the corrupt reverse/lowest code.
    with pytest.raises(HomeAssistantError, match="not valid base64"):
        await hass.services.async_call(
            FAN_DOMAIN,
            "set_percentage",
            {ATTR_ENTITY_ID: "fan.corrupt_fan", ATTR_PERCENTAGE: 1},
            blocking=True,
        )

    assert payloads(sent_commands) == []

    # Nothing was transmitted, so the entity still reports what it last sent.
    after = hass.states.get("fan.corrupt_fan")
    assert after.state == before.state
    assert after.attributes[ATTR_PERCENTAGE] == before.attributes[ATTR_PERCENTAGE]

    # The entity stays usable: a good speed still transmits.
    await hass.services.async_call(
        FAN_DOMAIN,
        "set_percentage",
        {ATTR_ENTITY_ID: "fan.corrupt_fan", ATTR_PERCENTAGE: 100},
        blocking=True,
    )
    assert payloads(sent_commands)
