"""Shared fixtures for the Broadlink IR tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import async_mock_service

# A minimal Broadlink climate device: two modes, one fan speed, 16-17 degrees.
CLIMATE_DEVICE_DATA: dict[str, Any] = {
    "manufacturer": "Test",
    "supportedModels": ["TEST-1"],
    "supportedController": "Broadlink",
    "commandsEncoding": "Base64",
    "minTemperature": 16,
    "maxTemperature": 17,
    "precision": 1,
    "operationModes": ["cool", "heat"],
    "fanModes": ["low"],
    "commands": {
        "off": "b2Zm",
        "cool": {"low": {"16": "Y29vbDE2", "17": "Y29vbDE3"}},
        "heat": {"low": {"16": "aGVhdDE2", "17": "aGVhdDE3"}},
    },
}

FAN_DEVICE_DATA: dict[str, Any] = {
    "manufacturer": "Test",
    "supportedModels": ["TEST-FAN"],
    "supportedController": "Broadlink",
    "commandsEncoding": "Base64",
    "speed": ["low", "high"],
    "commands": {
        "off": "b2Zm",
        "default": {"low": "bG93", "high": "aGlnaA=="},
    },
}

LIGHT_DEVICE_DATA: dict[str, Any] = {
    "manufacturer": "Test",
    "supportedModels": ["TEST-LIGHT"],
    "supportedController": "Broadlink",
    "commandsEncoding": "Base64",
    "brightness": [10, 128, 255],
    "colorTemperature": [2700, 4000, 6500],
    "commands": {
        "on": "b24=",
        "off": "b2Zm",
        "brighten": "YnJpZ2h0ZW4=",
        "dim": "ZGlt",
        "colder": "Y29sZGVy",
        "warmer": "d2FybWVy",
    },
}

MEDIA_PLAYER_DEVICE_DATA: dict[str, Any] = {
    "manufacturer": "Test",
    "supportedModels": ["TEST-TV"],
    "supportedController": "Broadlink",
    "commandsEncoding": "Base64",
    "commands": {
        "on": "b24=",
        "off": "b2Zm",
        "volumeUp": "dnUp",
        "volumeDown": "dmQp",
        "mute": "bXV0ZQ==",
        "previousChannel": "cHJldg==",
        "nextChannel": "bmV4dA==",
        "sources": {"HDMI1": "aGRtaTE=", "Channel 1": "Y2gx", "Channel 2": "Y2gy"},
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/broadlink_ir in every test."""
    return


@pytest.fixture
def codes_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the component's device-file directory at a temporary directory."""
    from custom_components import broadlink_ir

    monkeypatch.setattr(broadlink_ir, "COMPONENT_ABS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def write_device_file(codes_dir: Path):
    """Return a helper that writes a device file for a platform/code."""

    def _write(platform: str, device_code: int, data: dict[str, Any]) -> Path:
        path = codes_dir / "codes" / platform / f"{device_code}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _write


@pytest.fixture
async def sent_commands(hass: HomeAssistant) -> list[ServiceCall]:
    """Capture every remote.send_command call.

    The remote component is set up first: broadlink_ir depends on it, so platform
    setup would otherwise register the real service over the mock.
    """
    assert await async_setup_component(hass, "remote", {})
    await hass.async_block_till_done()
    return async_mock_service(hass, "remote", "send_command")


@pytest.fixture
async def setup_platform(hass: HomeAssistant):
    """Return a helper that sets a Broadlink IR platform up from YAML."""

    async def _setup(domain: str, config: dict[str, Any]) -> None:
        assert await async_setup_component(
            hass, domain, {domain: {"platform": "broadlink_ir", **config}}
        )
        await hass.async_block_till_done()

    return _setup


def payloads(calls: list[ServiceCall]) -> list[list[str]]:
    """Return the command payload of each captured remote.send_command call."""
    return [call.data["command"] for call in calls]


def get_entity(hass: HomeAssistant, entity_id: str):
    """Return the live entity object behind an entity_id."""
    domain = entity_id.split(".")[0]
    return hass.data[DATA_INSTANCES][domain].get_entity(entity_id)
