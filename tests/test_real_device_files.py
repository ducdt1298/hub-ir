"""Exhaustive checks against the device files this repository ships.

Every combination a user can select through the UI must resolve to a code. The
per-file tests above use small hand-written fixtures; these walk the real
database so a bad edit to codes/ fails the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.components.climate.const import ATTR_HVAC_MODE, HVAC_MODES
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.broadlink_ir.climate import _select, _select_temperature

from .conftest import payloads

REPO_ROOT = Path(__file__).resolve().parent.parent
CODES_DIR = REPO_ROOT / "codes"

CLIMATE_FILES = sorted(CODES_DIR.glob("climate/*.json"))
FAN_FILES = sorted(CODES_DIR.glob("fan/*.json"))
LIGHT_FILES = sorted(CODES_DIR.glob("light/*.json"))
MEDIA_PLAYER_FILES = sorted(CODES_DIR.glob("media_player/*.json"))
ALL_FILES = CLIMATE_FILES + FAN_FILES + LIGHT_FILES + MEDIA_PLAYER_FILES

# Device files inherited from upstream where whole branches were never recorded
# and only empty placeholders remain. The integration refuses to transmit those
# rather than sending an invalid payload; there is no way to recover the codes
# without the hardware. Listed so a *new* gap fails the suite.
# Run scripts/validate_codes.py for the per-file detail.
FILES_WITH_UNRECORDED_BRANCHES = {
    "1280",  # dry: every fan speed and temperature
    "1289",  # dry: every fan speed and temperature
    "1291",  # dry: every fan speed and temperature
    "1344",  # dry: every fan speed, swing and temperature
    "1400",  # fan_only
    "1692",  # heat: the whole 'low' fan speed
    "2380",  # fan_only
    "2580",  # fan_only
}
FILES_WITHOUT_AN_OFF_CODE = {
    "1801",  # 'off' is an empty placeholder upstream
}


def load(path: Path) -> dict:
    """Return a device file's parsed contents."""
    return json.loads(path.read_text(encoding="utf-8"))


def is_code(value) -> bool:
    """Return whether a resolved value is a sendable code or list of codes."""
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, str) and item for item in value)
    return False


def test_the_database_is_not_empty() -> None:
    """Guard against the glob silently matching nothing."""
    assert len(CLIMATE_FILES) > 300
    assert FAN_FILES and LIGHT_FILES and MEDIA_PLAYER_FILES


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_device_file_is_broadlink_and_parses(path: Path) -> None:
    """Every shipped file is valid JSON for a Broadlink remote."""
    data = load(path)

    assert data["supportedController"] == "Broadlink"
    assert data["commandsEncoding"] in ("Base64", "Hex", "Pronto")
    assert data["commands"]


@pytest.mark.parametrize(
    "path", CLIMATE_FILES, ids=lambda p: p.stem
)
def test_every_climate_combination_resolves(path: Path) -> None:
    """Each selectable mode/fan/swing/temperature resolves to a code."""
    data = load(path)
    commands = data["commands"]
    device_code = int(path.stem)
    unrecorded: list[str] = []

    if path.stem not in FILES_WITHOUT_AN_OFF_CODE:
        assert is_code(commands["off"]), f"{path.name}: no usable off code"

    fan_modes = data["fanModes"]
    swing_modes = data.get("swingModes")
    precision = data["precision"] or 1

    temperatures = []
    temp = data["minTemperature"]
    while temp <= data["maxTemperature"]:
        temperatures.append(temp)
        temp += precision

    for mode in data["operationModes"]:
        if mode not in HVAC_MODES or mode == HVACMode.OFF:
            # The entity drops these, so a user can never select them.
            continue

        node = _select(commands, mode, "operation mode", device_code)

        for fan_mode in fan_modes:
            fan_node = node if not isinstance(node, dict) else _select(
                node, fan_mode, "fan mode", device_code
            )

            for swing_mode in swing_modes or [None]:
                leaf = fan_node
                if swing_mode is not None and isinstance(leaf, dict):
                    leaf = _select(leaf, swing_mode, "swing mode", device_code)

                for temperature in temperatures:
                    resolved = leaf
                    if isinstance(resolved, dict):
                        # Raises only when nothing under this branch was ever
                        # recorded, which the validator reports separately.
                        try:
                            resolved = _select_temperature(
                                resolved, temperature, device_code
                            )
                        except HomeAssistantError:
                            unrecorded.append(
                                f"{mode}/{fan_mode}/{swing_mode}/{temperature}"
                            )
                            continue
                    assert is_code(resolved), (
                        f"{path.name}: {mode}/{fan_mode}/{swing_mode}/"
                        f"{temperature} resolved to {resolved!r}"
                    )

    if unrecorded:
        assert path.stem in FILES_WITH_UNRECORDED_BRANCHES, (
            f"{path.name}: {len(unrecorded)} combination(s) have no code "
            f"anywhere under them, e.g. {unrecorded[:3]}. If this is a new "
            f"device file, record the codes; otherwise add it to "
            f"FILES_WITH_UNRECORDED_BRANCHES."
        )


@pytest.mark.parametrize("path", FAN_FILES, ids=lambda p: p.stem)
def test_every_fan_speed_has_a_code(path: Path) -> None:
    """Each declared fan speed is reachable under each declared direction."""
    data = load(path)
    commands = data["commands"]

    assert is_code(commands["off"]), f"{path.name}: no usable off code"

    directions = [key for key in ("default", "forward", "reverse") if key in commands]
    assert directions, f"{path.name}: no direction group in commands"

    for direction in directions:
        for speed in data["speed"]:
            assert is_code(commands[direction].get(speed)), (
                f"{path.name}: {direction}/{speed} has no code"
            )


async def test_a_real_device_file_drives_a_real_entity(
    hass: HomeAssistant, monkeypatch, sent_commands, setup_platform
) -> None:
    """End to end with a file from codes/, not a test fixture."""
    from custom_components import broadlink_ir

    monkeypatch.setattr(broadlink_ir, "COMPONENT_ABS_DIR", str(REPO_ROOT))

    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "Real AC",
            "unique_id": "real_ac",
            # 1000.json: Toyotomi, Base64, 16-30 C, no swing modes.
            "device_code": 1000,
            "controller_data": "remote.broadlink",
        },
    )

    state = hass.states.get("climate.real_ac")
    assert state is not None
    assert state.attributes["manufacturer"] == "Toyotomi"
    assert state.attributes["min_temp"] == 16
    assert state.attributes["max_temp"] == 30

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "climate.real_ac"},
        blocking=True,
    )

    (payload,) = payloads(sent_commands)
    assert len(payload) == 1
    assert payload[0].startswith("b64:")
    assert hass.states.get("climate.real_ac").state != HVACMode.OFF
