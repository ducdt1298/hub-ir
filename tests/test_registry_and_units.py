"""Tests for registry integration, fallback choice and temperature units.

These cover the things a user notices in the UI rather than in the log: whether
the entity can be renamed and put in an area at all, which code a sparse device
file falls back to, and whether a Fahrenheit device file is recognised as one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    HVACMode,
)
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    CLIMATE_DEVICE_DATA,
    FAN_DEVICE_DATA,
    LIGHT_DEVICE_DATA,
    MEDIA_PLAYER_DEVICE_DATA,
    get_entity,
    payloads,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PLATFORMS = [
    (CLIMATE_DOMAIN, "climate", CLIMATE_DEVICE_DATA, "TEST-1"),
    (FAN_DOMAIN, "fan", FAN_DEVICE_DATA, "TEST-FAN"),
    (LIGHT_DOMAIN, "light", LIGHT_DEVICE_DATA, "TEST-LIGHT"),
    (MEDIA_PLAYER_DOMAIN, "media_player", MEDIA_PLAYER_DEVICE_DATA, "TEST-TV"),
]


# --------------------------------------------------------------------------
# Device registry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("domain", "platform", "data", "model"), PLATFORMS)
async def test_a_unique_id_registers_the_entity(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    domain,
    platform,
    data,
    model,
) -> None:
    """With a unique_id the entity is in the registry, so the UI can manage it.

    That is what unlocks renaming, area assignment, hiding and disabling. There
    is no device: Home Assistant only creates one for entities that belong to a
    config entry, and these platforms are configured in YAML — see
    test_yaml_platforms_cannot_have_devices.
    """
    write_device_file(platform, 9600, data)
    await setup_platform(
        domain,
        {
            "name": "Reg Thing",
            "unique_id": "reg_thing",
            "device_code": 9600,
            "controller_data": "remote.broadlink",
        },
    )

    entity_id = f"{domain}.reg_thing"
    entity_entry = er.async_get(hass).async_get(entity_id)
    assert entity_entry is not None, "entity was not registered"
    assert entity_entry.unique_id == "reg_thing"
    assert entity_entry.platform == "hub_ir"

    # Renaming and area assignment go through the registry, so prove they take.
    er.async_get(hass).async_update_entity(entity_id, name="Renamed")
    assert er.async_get(hass).async_get(entity_id).name == "Renamed"


async def test_yaml_platforms_cannot_have_devices(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """Pin the reason these entities are not grouped into a device.

    Home Assistant registers a device only for entities that belong to a config
    entry: entity_platform guards the whole block with `if self.config_entry`,
    and device_registry.async_get_or_create requires a config_entry_id. A YAML
    platform has neither, so returning device_info would be dead code.

    Grouping these into devices therefore needs a config flow, which would change
    how every user configures the integration. If that ever lands, this test
    should start failing.
    """
    write_device_file("climate", 9603, CLIMATE_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "No Device",
            "unique_id": "no_device",
            "device_code": 9603,
            "controller_data": "remote.broadlink",
        },
    )

    entity_entry = er.async_get(hass).async_get("climate.no_device")
    assert entity_entry is not None
    assert entity_entry.device_id is None

    assert not [
        device
        for device in dr.async_get(hass).devices.values()
        if any(domain == "hub_ir" for domain, _ in device.identifiers)
    ]


@pytest.mark.parametrize(("domain", "platform", "data", "model"), PLATFORMS)
async def test_without_unique_id_there_is_no_device(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    caplog,
    domain,
    platform,
    data,
    model,
) -> None:
    """No unique_id means no registry entry, so the warning has to say so.

    The entity still works — it just cannot be renamed, put in an area, hidden or
    customised, because all of that lives in the entity registry. Upstream said
    nothing, so this was invisible until you went looking for the entity in the
    UI and found it unmanageable.
    """
    write_device_file(platform, 9601, data)
    await setup_platform(
        domain,
        {
            "name": "Anon Thing",
            "device_code": 9601,
            "controller_data": "remote.broadlink",
        },
    )

    entity_id = f"{domain}.anon_thing"
    assert hass.states.get(entity_id) is not None, "entity should still work"
    assert er.async_get(hass).async_get(entity_id) is None

    assert "has no unique_id" in caplog.text
    assert "Anon Thing" in caplog.text


@pytest.mark.parametrize(("domain", "platform", "data", "model"), PLATFORMS)
async def test_no_unique_id_warning_when_one_is_given(
    hass: HomeAssistant,
    write_device_file,
    sent_commands,
    setup_platform,
    caplog,
    domain,
    platform,
    data,
    model,
) -> None:
    """The warning must not cry wolf for a correctly configured platform."""
    write_device_file(platform, 9602, data)
    await setup_platform(
        domain,
        {
            "name": "Named Thing",
            "unique_id": "named_thing",
            "device_code": 9602,
            "controller_data": "remote.broadlink",
        },
    )

    assert "has no unique_id" not in caplog.text


# --------------------------------------------------------------------------
# Which code a sparse device file falls back to
# --------------------------------------------------------------------------


async def test_missing_fan_mode_falls_back_to_the_nearest_one(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A missing fan mode is substituted by its neighbour, not by the first key.

    33 shipped files record only some fan speeds under 'dry'/'fan_only'. Picking
    whichever key was written first could answer a request for the lowest speed
    with the highest.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "operationModes": ["cool", "dry"],
        "fanModes": ["low", "mid", "high"],
        "commands": {
            "off": "b2Zm",
            "cool": {
                "low": {"16": "Y29vbExvdw==", "17": "Y29vbExvdzE3"},
                "mid": {"16": "Y29vbE1pZA==", "17": "Y29vbE1pZDE3"},
                "high": {"16": "Y29vbEhpZ2g=", "17": "Y29vbEhpZ2gxNw=="},
            },
            # 'high' is written first, but 'mid' is the neighbour of 'low'.
            "dry": {
                "high": {"16": "ZHJ5SGlnaA==", "17": "ZHJ5SGlnaDE3"},
                "mid": {"16": "ZHJ5TWlk", "17": "ZHJ5TWlkMTc="},
            },
        },
    }
    write_device_file("climate", 9610, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Sparse AC",
            "unique_id": "sparse_ac",
            "device_code": 9610,
            "controller_data": "remote.broadlink",
        },
    )

    # Ask for the lowest fan speed, which 'dry' does not record.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: "climate.sparse_ac", ATTR_FAN_MODE: "low"},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.sparse_ac", ATTR_HVAC_MODE: HVACMode.DRY},
        blocking=True,
    )

    sent = payloads(sent_commands)[-1]
    assert sent == ["b64:ZHJ5TWlk"], "expected the 'mid' code, the neighbour of 'low'"


async def test_annotation_keys_are_never_substituted(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """A '_comment' written first must not be transmitted as a code."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "operationModes": ["cool", "dry"],
        "fanModes": ["low", "high"],
        "commands": {
            "off": "b2Zm",
            "cool": {
                "low": {"16": "Y29vbExvdw==", "17": "Y29vbExvdzE3"},
                "high": {"16": "Y29vbEhpZ2g=", "17": "Y29vbEhpZ2gxNw=="},
            },
            "dry": {
                "_comment": "the unit ignores fan speed in dry mode",
                "high": {"16": "ZHJ5SGlnaA==", "17": "ZHJ5SGlnaDE3"},
            },
        },
    }
    write_device_file("climate", 9611, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Note AC",
            "unique_id": "note_ac",
            "device_code": 9611,
            "controller_data": "remote.broadlink",
        },
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.note_ac", ATTR_HVAC_MODE: HVACMode.DRY},
        blocking=True,
    )

    sent = payloads(sent_commands)[-1]
    assert sent == ["b64:ZHJ5SGlnaA=="]
    assert not any("ignores fan speed" in code for code in sent)


# --------------------------------------------------------------------------
# Temperature units
# --------------------------------------------------------------------------


def test_every_fahrenheit_file_declares_its_unit() -> None:
    """A shipped file must never leave its unit to be inferred.

    climate.py can infer Fahrenheit from an impossible Celsius range, but that is
    a guess and a guess should not decide what 333 files mean. This keeps the
    declaration and the range in agreement for every file in the database.
    """
    undeclared = []
    for path in sorted((REPO_ROOT / "codes" / "climate").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        max_temp = data.get("maxTemperature")
        if not isinstance(max_temp, (int, float)) or max_temp <= 40:
            continue
        if not data.get("temperatureUnit"):
            undeclared.append(path.name)

    assert undeclared == [], (
        "these files have a range that cannot be Celsius but do not declare "
        f'"temperatureUnit": "F": {undeclared}'
    )


async def test_a_declared_fahrenheit_file_is_read_as_fahrenheit(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """The declaration drives the entity's unit, and HA converts for display."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "minTemperature": 61,
        "maxTemperature": 86,
        "temperatureUnit": "F",
        "commands": {
            "off": "b2Zm",
            "cool": {"low": {"61": "Y29vbDYx", "86": "Y29vbDg2"}},
            "heat": {"low": {"61": "aGVhdDYx", "86": "aGVhdDg2"}},
        },
    }
    write_device_file("climate", 9620, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "F AC",
            "unique_id": "f_ac",
            "device_code": 9620,
            "controller_data": "remote.broadlink",
        },
    )

    entity = get_entity(hass, "climate.f_ac")
    assert entity.temperature_unit == UnitOfTemperature.FAHRENHEIT


async def test_a_declaration_of_celsius_beats_the_range_check(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform, caplog
) -> None:
    """An explicit unit is obeyed even when the range looks like the other one.

    This is the escape hatch for a hand-written file: say what you mean and the
    inference never runs.
    """
    data = {
        **CLIMATE_DEVICE_DATA,
        "minTemperature": 50,
        "maxTemperature": 60,
        "temperatureUnit": "C",
        "commands": {
            "off": "b2Zm",
            "cool": {"low": {"50": "Y29vbDUw", "60": "Y29vbDYw"}},
            "heat": {"low": {"50": "aGVhdDUw", "60": "aGVhdDYw"}},
        },
    }
    write_device_file("climate", 9621, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "C AC",
            "unique_id": "c_ac",
            "device_code": 9621,
            "controller_data": "remote.broadlink",
        },
    )

    entity = get_entity(hass, "climate.c_ac")
    assert entity.temperature_unit == UnitOfTemperature.CELSIUS
    assert "too high to be Celsius" not in caplog.text


async def test_inferring_fahrenheit_warns(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform, caplog
) -> None:
    """A hand-written file with no declaration still works, but says so."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "minTemperature": 61,
        "maxTemperature": 86,
        "commands": {
            "off": "b2Zm",
            "cool": {"low": {"61": "Y29vbDYx", "86": "Y29vbDg2"}},
            "heat": {"low": {"61": "aGVhdDYx", "86": "aGVhdDg2"}},
        },
    }
    write_device_file("climate", 9622, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Guess AC",
            "unique_id": "guess_ac",
            "device_code": 9622,
            "controller_data": "remote.broadlink",
        },
    )

    entity = get_entity(hass, "climate.guess_ac")
    assert entity.temperature_unit == UnitOfTemperature.FAHRENHEIT
    assert "too high to be Celsius" in caplog.text


async def test_the_temperature_unit_option_overrides_the_file(
    hass: HomeAssistant, write_device_file, sent_commands, setup_platform
) -> None:
    """The YAML option wins over the device file's own declaration."""
    data = {
        **CLIMATE_DEVICE_DATA,
        "minTemperature": 61,
        "maxTemperature": 86,
        "temperatureUnit": "F",
        "commands": {
            "off": "b2Zm",
            "cool": {"low": {"61": "Y29vbDYx", "86": "Y29vbDg2"}},
            "heat": {"low": {"61": "aGVhdDYx", "86": "aGVhdDg2"}},
        },
    }
    write_device_file("climate", 9623, data)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Override AC",
            "unique_id": "override_ac",
            "device_code": 9623,
            "controller_data": "remote.broadlink",
            "temperature_unit": UnitOfTemperature.CELSIUS,
        },
    )

    entity = get_entity(hass, "climate.override_ac")
    assert entity.temperature_unit == UnitOfTemperature.CELSIUS
