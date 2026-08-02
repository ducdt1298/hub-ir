"""Tests for the services that reach codes no entity control can express.

A device file holds far more than the four entity models offer: a television's
arrow keys, an air conditioner's LED toggle, every temperature in the matrix.
Before these services existed those codes sat on disk and nothing could send
them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol

from custom_components.hub_ir import DOMAIN
from custom_components.hub_ir.device_file import (
    build_device_file,
    capture_plan,
    resolve_command,
)
from custom_components.hub_ir.services import SERVICE_SEND_CODE, SERVICE_SEND_COMMAND
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from .conftest import (
    CLIMATE_DEVICE_DATA,
    CLIMATE_PRESET_DEVICE_DATA,
    FAN_DEVICE_DATA,
    LIGHT_DEVICE_DATA,
    MEDIA_PLAYER_DEVICE_DATA,
    payloads,
)

REMOTE = "remote.broadlink"

# The extras every fixture below gets, so a path exists to point the service at.
EXTRAS = {"menu": "bWVudQ==", "_note": "not a code"}

# (platform, entity domain, device code, base device data)
PLATFORM_CASES = [
    ("climate", CLIMATE_DOMAIN, 9500, CLIMATE_DEVICE_DATA),
    ("fan", FAN_DOMAIN, 9501, FAN_DEVICE_DATA),
    ("light", LIGHT_DOMAIN, 9502, LIGHT_DEVICE_DATA),
    ("media_player", MEDIA_PLAYER_DOMAIN, 9503, MEDIA_PLAYER_DEVICE_DATA),
]


def _with_extras(data: dict[str, Any]) -> dict[str, Any]:
    """Return a device file with the shared extras group added."""
    return {**data, "commands": {**data["commands"], "extras": EXTRAS}}


@pytest.fixture
async def component(hass: HomeAssistant, codes_dir, sent_commands):
    """Load hub_ir itself, with no entity at all, the way send_code needs it."""
    with patch("custom_components.hub_ir.frontend.async_register_panel"):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    return codes_dir


@pytest.fixture
async def media_player(hass, write_device_file, sent_commands, setup_platform):
    """Set up a television whose device file carries extras and sources."""
    write_device_file("media_player", 9503, _with_extras(MEDIA_PLAYER_DEVICE_DATA))
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "TV",
            "unique_id": "tv",
            "device_code": 9503,
            "controller_data": REMOTE,
        },
    )
    return sent_commands


async def _send_command(hass: HomeAssistant, entity_id: str, **data: Any) -> None:
    """Call hub_ir.send_command against one entity."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("platform", "domain", "code", "data"), PLATFORM_CASES)
async def test_every_platform_registers_send_command(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    platform: str,
    domain: str,
    code: int,
    data: dict[str, Any],
) -> None:
    """A platform that forgets to register has the service silently missing.

    This is the test that catches a new platform being added without it.
    """
    write_device_file(platform, code, _with_extras(data))
    await setup_platform(
        domain,
        {
            "name": "Thing",
            "unique_id": "thing",
            "device_code": code,
            "controller_data": REMOTE,
        },
    )

    assert hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND)

    await _send_command(hass, f"{domain}.thing", command="extras/menu")
    assert payloads(sent_commands) == [["b64:bWVudQ=="]]


async def test_send_code_is_registered_without_any_entity(
    hass: HomeAssistant, component, sent_commands
) -> None:
    """It exists for a code that is in no device file yet, so it needs no entity."""
    assert hass.services.has_service(DOMAIN, SERVICE_SEND_CODE)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_CODE,
        {"remote_entity_id": REMOTE, "code": "bWVudQ=="},
        blocking=True,
    )

    assert payloads(sent_commands) == [["b64:bWVudQ=="]]


async def test_send_code_refuses_something_that_is_not_a_remote(
    hass: HomeAssistant, component, sent_commands
) -> None:
    """A typo naming a switch would otherwise fail deep inside the controller."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_CODE,
            {"remote_entity_id": "switch.amplifier", "code": "bWVudQ=="},
            blocking=True,
        )

    assert payloads(sent_commands) == []


# ---------------------------------------------------------------------------
# Resolving a command path
# ---------------------------------------------------------------------------


async def test_send_command_reaches_a_source_and_a_nested_code(
    hass: HomeAssistant, media_player
) -> None:
    """Both a group entry and, for climate, a full mode/fan/temperature path."""
    await _send_command(hass, "media_player.tv", command="sources/HDMI1")
    assert payloads(media_player) == [["b64:aGRtaTE="]]


async def test_send_command_reaches_a_preset_and_a_nested_climate_code(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A preset and a matrix leaf are both just paths."""
    write_device_file("climate", 9504, _with_extras(CLIMATE_PRESET_DEVICE_DATA))
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "AC",
            "unique_id": "ac",
            "device_code": 9504,
            "controller_data": REMOTE,
        },
    )

    await _send_command(hass, "climate.ac", command="presets/turbo")
    await _send_command(hass, "climate.ac", command="cool/low/16")

    assert payloads(sent_commands) == [["b64:dHVyYm8="], ["b64:Y29vbDE2"]]


@pytest.mark.parametrize(
    ("source", "code"),
    [("Channel 1.2", "Y2gx"), ("A/B", "YWI="), ("Plain", "cGxhaW4=")],
)
async def test_a_source_name_the_separator_would_split_still_resolves(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    source: str,
    code: str,
) -> None:
    """An exact key wins before the path is split.

    A dot is not a separator at all, and a name containing a slash matches
    exactly once the walk is inside the group. Neither should force someone to
    rename their sources to use the service.
    """
    data = {
        **MEDIA_PLAYER_DEVICE_DATA,
        "commands": {**MEDIA_PLAYER_DEVICE_DATA["commands"], "sources": {source: code}},
    }
    write_device_file("media_player", 9505, data)
    await setup_platform(
        MEDIA_PLAYER_DOMAIN,
        {
            "name": "TV",
            "unique_id": "tv",
            "device_code": 9505,
            "controller_data": REMOTE,
        },
    )

    await _send_command(hass, "media_player.tv", command=f"sources/{source}")
    assert payloads(sent_commands) == [[f"b64:{code}"]]


@pytest.mark.parametrize("command", ["extras/_note", "_note", "extras/$doc"])
async def test_an_annotation_key_is_never_transmitted(
    hass: HomeAssistant, media_player, command: str
) -> None:
    """Sending a '_note' would transmit its prose as though it were a code."""
    with pytest.raises(HomeAssistantError, match="no code recorded"):
        await _send_command(hass, "media_player.tv", command=command)

    assert payloads(media_player) == []


@pytest.mark.parametrize("command", ["extras/nope", "sources", "", "a/b/c/d"])
async def test_an_unknown_path_fails_loudly_and_sends_nothing(
    hass: HomeAssistant, media_player, command: str
) -> None:
    """'sources' names a group, not a code, so it must not resolve either."""
    with pytest.raises(HomeAssistantError, match="no code recorded"):
        await _send_command(hass, "media_player.tv", command=command)

    assert payloads(media_player) == []


async def test_an_unknown_path_says_what_would_have_worked(
    hass: HomeAssistant, media_player
) -> None:
    """A typo should cost a minute, not an afternoon."""
    with pytest.raises(HomeAssistantError) as error:
        await _send_command(hass, "media_player.tv", command="extras/mneu")

    message = str(error.value)
    assert "extras/menu" in message
    assert "sources/HDMI1" in message
    # The annotation is not a path anyone can use, so it is not suggested.
    assert "_note" not in message


# ---------------------------------------------------------------------------
# Repeats, and leaving state alone
# ---------------------------------------------------------------------------


async def test_repeat_sends_the_code_that_many_times(
    hass: HomeAssistant, media_player
) -> None:
    """A volume ramp is the one thing a script does badly."""
    await _send_command(hass, "media_player.tv", command="volumeUp", repeat=3, delay=0)

    assert payloads(media_player) == [["b64:dnUp"]] * 3


@pytest.mark.parametrize("repeat", [0, 101])
async def test_an_absurd_repeat_is_refused(
    hass: HomeAssistant, media_player, repeat: int
) -> None:
    """A typo in an automation must not hold the remote for an hour."""
    with pytest.raises(vol.Invalid):
        await _send_command(hass, "media_player.tv", command="volumeUp", repeat=repeat)

    assert payloads(media_player) == []


async def test_send_command_does_not_change_the_entity_state(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Pressing 'menu' says nothing about what the device is doing.

    Guessing would be worse than staying quiet, so every attribute is left
    exactly as it was.
    """
    write_device_file("climate", 9506, _with_extras(CLIMATE_DEVICE_DATA))
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "AC",
            "unique_id": "ac",
            "device_code": 9506,
            "controller_data": REMOTE,
        },
    )
    before = hass.states.get("climate.ac")

    await _send_command(hass, "climate.ac", command="extras/menu")

    after = hass.states.get("climate.ac")
    assert after.state == before.state
    assert after.attributes == before.attributes


# ---------------------------------------------------------------------------
# The invariant that ties the capture plan to the service
# ---------------------------------------------------------------------------


_PLAN_SPECS: dict[str, dict[str, Any]] = {
    "climate": {
        "minTemperature": 16,
        "maxTemperature": 18,
        "precision": 1,
        "temperatureUnit": "C",
        "operationModes": ["cool", "heat"],
        "fanModes": ["low", "high"],
        "presets": ["turbo"],
        "extraCommands": ["menu", "led"],
    },
    "fan": {"speed": ["low", "high"], "extraCommands": ["timer"]},
    "light": {"brightness": [10, 255], "extraCommands": ["flash"]},
    "media_player": {
        "buttons": ["on", "off"],
        "sources": ["HDMI1", "Channel 1.2"],
        "extraCommands": ["menu"],
    },
}


@pytest.mark.parametrize("platform", sorted(_PLAN_SPECS))
def test_every_plan_cell_key_resolves_to_its_captured_code(platform: str) -> None:
    """The one invariant binding presets, extras, the plan and the service.

    A capture cell's key is what the panel shows and what the validator prints,
    so it has to be the same string the service accepts. If these ever diverge,
    every path a user copied out of the panel stops working.
    """
    spec = _PLAN_SPECS[platform]
    plan = capture_plan(platform, spec)
    # A distinct code per cell, so a wrong resolution cannot pass by coincidence.
    codes = {cell["key"]: f"code-{index}" for index, cell in enumerate(plan)}
    data = build_device_file(platform, spec, codes)

    for cell in plan:
        assert resolve_command(data["commands"], cell["key"]) == codes[cell["key"]], (
            cell["key"]
        )
