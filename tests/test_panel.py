"""Tests for the learning panel's server side.

The panel itself is deliberately thin, so this covers what it relies on: that a
learned code is really recovered from Broadlink's storage, that a capture plan
describes a tree the integration can actually walk, and that saving refuses to
write a file the integration could not use.
"""

from __future__ import annotations

from base64 import b64encode
import json
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch
from urllib.parse import unquote_plus

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hub_ir import (
    CODES_BASE_URL,
    REPO_SLUG,
    REPO_URL,
    frontend as frontend_module,
)
from custom_components.hub_ir.device_file import (
    CUSTOM_CODE_START,
    PLATFORMS,
    build_device_file,
    capture_plan,
    codes_from_device_file,
    is_recorded,
    preset_baseline,
    spec_from_device_file,
    temperature_steps,
    validate,
)
from custom_components.hub_ir.learn import (
    SCRATCH_DEVICE,
    async_learn_ir_code,
    broadlink_unique_id,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component

from .conftest import CLIMATE_DEVICE_DATA

BROADLINK_UNIQUE_ID = "34ea34b43b5a"
REMOTE_ENTITY_ID = "remote.broadlink"

# A well-formed Broadlink IR packet: type, repeat, little-endian payload length,
# four payload bytes, then the trailer.
GOOD_PACKET = bytes([0x26, 0x00, 0x04, 0x00, 0x10, 0x20, 0x30, 0x40, 0x0D, 0x05])
GOOD_CODE = b64encode(GOOD_PACKET).decode()


@pytest.fixture
async def broadlink_remote(hass: HomeAssistant):
    """Register a remote entity that looks like the Broadlink integration's.

    async_learn_ir_code finds the code store by walking entity registry → device
    registry → the Broadlink identifier, so the test has to build that chain
    rather than just putting a state on the bus.
    """
    assert await async_setup_component(hass, "remote", {})
    await hass.async_block_till_done()

    # The sent_commands fixture puts a bare state on remote.broadlink. The
    # entity registry avoids an entity_id that is already in the state machine,
    # so leaving it there would register this as remote.broadlink_2 and the
    # lookup under test would miss.
    hass.states.async_remove(REMOTE_ENTITY_ID)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=await _fake_config_entry(hass, "broadlink_entry"),
        identifiers={("broadlink", BROADLINK_UNIQUE_ID)},
        name="Broadlink RM4",
    )
    entry = er.async_get(hass).async_get_or_create(
        "remote",
        "broadlink",
        BROADLINK_UNIQUE_ID,
        suggested_object_id="broadlink",
        device_id=device.id,
    )
    assert entry.entity_id == REMOTE_ENTITY_ID

    hass.states.async_set(REMOTE_ENTITY_ID, "on")
    await hass.async_block_till_done()
    return device


async def _fake_config_entry(hass: HomeAssistant, entry_id: str) -> str:
    """Add a minimal config entry the device registry will accept."""
    entry = MockConfigEntry(domain="broadlink", entry_id=entry_id, title="RM4")
    entry.add_to_hass(hass)
    return entry.entry_id


async def _write_codes(hass: HomeAssistant, codes: dict[str, Any]) -> None:
    """Put codes into the store the Broadlink integration would write."""
    store = Store(hass, 1, f"broadlink_remote_{BROADLINK_UNIQUE_ID}_codes")
    await store.async_save(codes)


def _learning_remote(hass: HomeAssistant, code: Any):
    """Register a learn_command that stores a code the way Broadlink does.

    Note what it does *not* do: raise. The real service logs its failures and
    returns, which is exactly why the code has to be recovered by diffing the
    store rather than by trusting the call.
    """
    calls: list[ServiceCall] = []

    async def handle_learn(call: ServiceCall) -> None:
        calls.append(call)
        if code is None:
            return
        store = Store(hass, 1, f"broadlink_remote_{BROADLINK_UNIQUE_ID}_codes")
        existing = await store.async_load() or {}
        existing.setdefault(call.data["device"], {})[call.data["command"]] = code
        await store.async_save(existing)

    @callback
    def handle_delete(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("remote", "learn_command", handle_learn)
    hass.services.async_register("remote", "delete_command", handle_delete)
    return calls


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


async def test_the_broadlink_identifier_is_found_from_the_remote(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """The code store is keyed by it, so this is the whole SSH step."""
    assert broadlink_unique_id(hass, REMOTE_ENTITY_ID) == BROADLINK_UNIQUE_ID


async def test_a_learned_code_is_read_back_out_of_storage(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """The captured code comes back as the base64 a device file stores."""
    calls = _learning_remote(hass, GOOD_CODE)

    code = await async_learn_ir_code(hass, REMOTE_ENTITY_ID)

    assert code == GOOD_CODE
    learn = next(call for call in calls if call.service == "learn_command")
    assert learn.data["device"] == SCRATCH_DEVICE
    assert learn.data["command_type"] == "ir"


async def test_the_scratch_code_is_deleted_afterwards(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """A 180-code session must not leave 180 strays in Broadlink's storage."""
    calls = _learning_remote(hass, GOOD_CODE)

    await async_learn_ir_code(hass, REMOTE_ENTITY_ID)

    delete = next(call for call in calls if call.service == "delete_command")
    assert delete.data["device"] == SCRATCH_DEVICE


async def test_nothing_learned_is_reported_as_a_failure(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """A timeout looks like a successful service call, so it must be caught.

    homeassistant.components.broadlink.remote catches its own errors and logs
    them; async_learn_command returns normally whether or not a code arrived.
    """
    _learning_remote(hass, None)

    with pytest.raises(HomeAssistantError, match="No infrared code arrived"):
        await async_learn_ir_code(hass, REMOTE_ENTITY_ID)


async def test_a_stale_code_is_not_mistaken_for_a_fresh_one(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """Codes left over from an earlier session must not be handed back."""
    await _write_codes(hass, {SCRATCH_DEVICE: {"code_OLD": "c3RhbGU="}})
    _learning_remote(hass, None)

    with pytest.raises(HomeAssistantError, match="No infrared code arrived"):
        await async_learn_ir_code(hass, REMOTE_ENTITY_ID)


async def test_consecutive_captures_each_return_their_own_code(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """Capturing in a run must not keep handing back the first code.

    The test harness makes this sharper than production does: its storage mock
    pins the loaded data onto the Store instance, so a reused reader would go
    stale after one load. Real Store.async_load reads through to the file. Either
    way, a run of 180 captures has to return 180 distinct codes.
    """
    codes = []
    for index in range(3):
        packet = bytes([0x26, 0x00, 0x04, 0x00, index, 0x20, 0x30, 0x40, 0x0D, 0x05])
        _learning_remote(hass, b64encode(packet).decode())
        codes.append(await async_learn_ir_code(hass, REMOTE_ENTITY_ID))

    assert len(set(codes)) == 3


async def test_learning_from_a_remote_that_is_off_says_so(
    hass: HomeAssistant, broadlink_remote
) -> None:
    """The Broadlink integration silently declines to learn while off."""
    _learning_remote(hass, GOOD_CODE)
    hass.states.async_set(REMOTE_ENTITY_ID, "off")

    with pytest.raises(HomeAssistantError, match="turned off"):
        await async_learn_ir_code(hass, REMOTE_ENTITY_ID)


async def test_learning_from_a_non_broadlink_remote_says_so(
    hass: HomeAssistant, sent_commands
) -> None:
    """Only Broadlink writes into the storage this reads back."""
    er.async_get(hass).async_get_or_create(
        "remote", "demo", "demo_remote", suggested_object_id="other"
    )
    hass.states.async_set("remote.other", "on")

    with pytest.raises(HomeAssistantError, match="needs a Broadlink remote"):
        await async_learn_ir_code(hass, "remote.other")


# ---------------------------------------------------------------------------
# Capture plans
# ---------------------------------------------------------------------------


def test_temperature_steps_do_not_drift() -> None:
    """Half-degree keys have to read '24.5', not '24.500000000000004'."""
    assert temperature_steps(16, 18, 0.5) == ["16", "16.5", "17", "17.5", "18"]
    assert temperature_steps(16, 30, 1)[-1] == "30"
    assert len(temperature_steps(61, 86, 1)) == 26


@pytest.mark.parametrize(
    ("minimum", "maximum", "precision"),
    [(16, 30, 4), (16, 30, 0.75), (16, 17, 0.3), (10, 32, 1), (16, 30, 0.5)],
)
def test_temperature_steps_never_exceed_the_maximum(
    minimum, maximum, precision
) -> None:
    """A step that does not divide the range evenly must stop short, not over.

    climate.py refuses a target outside min/max, so a key above the maximum
    would be a code the user could never reach — dead weight in the file and a
    capture nobody should be asked to make.
    """
    steps = [float(step) for step in temperature_steps(minimum, maximum, precision)]

    assert steps[0] == minimum
    assert all(minimum <= step <= maximum for step in steps)


def test_a_plain_air_conditioner_needs_every_combination() -> None:
    """3 modes x 4 fan speeds x 15 temperatures, plus off: the real workload."""
    plan = capture_plan(
        "climate",
        {
            "minTemperature": 16,
            "maxTemperature": 30,
            "precision": 1,
            "operationModes": ["heat", "cool", "fan_only"],
            "fanModes": ["low", "mid", "high", "auto"],
        },
    )
    assert len(plan) == 3 * 4 * 15 + 1


def test_a_mode_that_ignores_temperature_costs_one_capture_per_fan_speed() -> None:
    """Pin the lever that makes learning an air conditioner bearable."""
    spec = {
        "minTemperature": 16,
        "maxTemperature": 30,
        "precision": 1,
        "operationModes": ["cool", "fan_only"],
        "fanModes": ["low", "high"],
        "modeOptions": {"fan_only": {"usesTemperature": False}},
    }
    plan = capture_plan("climate", spec)

    assert len(plan) == 1 + (2 * 15) + 2
    fan_only = [cell for cell in plan if cell["group"] == "fan_only"]
    assert [cell["targets"] for cell in fan_only] == [
        [["fan_only", "low"]],
        [["fan_only", "high"]],
    ]


def test_a_mode_that_ignores_fan_speed_is_captured_once_and_written_everywhere() -> (
    None
):
    """Dropping the fan level would break the integration's positional walk.

    climate.py resolves mode, then fan, then temperature by position, not by
    key name. A tree without the fan level would have _select answer a request
    for a fan mode with a temperature's code, so the one capture is duplicated
    across every fan mode instead.
    """
    plan = capture_plan(
        "climate",
        {
            "minTemperature": 16,
            "maxTemperature": 17,
            "precision": 1,
            "operationModes": ["dry"],
            "fanModes": ["low", "mid", "high"],
            "modeOptions": {"dry": {"usesFan": False, "usesTemperature": False}},
        },
    )

    (off, dry) = plan
    assert off["key"] == "off"
    assert dry["targets"] == [["dry", "low"], ["dry", "mid"], ["dry", "high"]]


def test_temperature_is_the_innermost_and_ascending_step() -> None:
    """The order has to match the buttons on the physical remote."""
    plan = capture_plan(
        "climate",
        {
            "minTemperature": 16,
            "maxTemperature": 19,
            "precision": 1,
            "operationModes": ["cool"],
            "fanModes": ["low", "high"],
        },
    )
    keys = [cell["key"] for cell in plan[1:]]
    assert keys[:4] == ["cool/low/16", "cool/low/17", "cool/low/18", "cool/low/19"]
    assert keys[4] == "cool/high/16"


@pytest.mark.parametrize(
    ("platform", "spec", "expected"),
    [
        (
            "fan",
            {"speed": ["low", "high"], "hasDirection": True, "hasOscillate": True},
            [
                "off",
                "forward/low",
                "forward/high",
                "reverse/low",
                "reverse/high",
                "oscillate",
            ],
        ),
        (
            "fan",
            {"speed": ["low", "high"]},
            ["off", "default/low", "default/high"],
        ),
        (
            "light",
            {"brightness": [10, 255], "colorTemperature": [2700, 6500]},
            ["on", "off", "brighten", "dim", "colder", "warmer"],
        ),
        (
            "media_player",
            {"buttons": ["on", "off", "mute"], "sources": ["HDMI1"]},
            ["on", "off", "mute", "sources/HDMI1"],
        ),
        ("switch", {}, ["on", "off"]),
        ("switch", {"hasToggle": True}, ["on", "off", "toggle"]),
    ],
)
def test_plans_for_the_other_platforms(platform, spec, expected) -> None:
    """The flat platforms map straight onto their command names."""
    assert [cell["key"] for cell in capture_plan(platform, spec)] == expected


# ---------------------------------------------------------------------------
# Building a device file
# ---------------------------------------------------------------------------


def test_a_built_file_passes_the_same_validation_as_a_shipped_one() -> None:
    """What the panel writes has to satisfy the validator, not just look right."""
    spec = {
        "manufacturer": "Daikin",
        "supportedModels": ["FTKC35"],
        "minTemperature": 16,
        "maxTemperature": 18,
        "precision": 1,
        "temperatureUnit": "C",
        "operationModes": ["cool", "fan_only"],
        "fanModes": ["low", "high"],
        "modeOptions": {"fan_only": {"usesTemperature": False, "usesFan": False}},
    }
    plan = capture_plan("climate", spec)
    data = build_device_file("climate", spec, {cell["key"]: GOOD_CODE for cell in plan})

    report = validate("climate", data, "90000")
    assert report.errors == []
    assert report.warnings == []


def test_a_built_fahrenheit_file_declares_its_unit() -> None:
    """The validator rejects a file that leaves the unit to be inferred."""
    spec = {
        "minTemperature": 61,
        "maxTemperature": 86,
        "precision": 1,
        "temperatureUnit": "F",
        "operationModes": ["cool"],
        "fanModes": ["low"],
    }
    plan = capture_plan("climate", spec)
    data = build_device_file("climate", spec, {cell["key"]: GOOD_CODE for cell in plan})

    assert data["temperatureUnit"] == "F"
    assert validate("climate", data, "90001").errors == []


def test_a_skipped_capture_becomes_a_placeholder_not_a_missing_key() -> None:
    """The integration already refuses placeholders; a hole would be a KeyError."""
    spec = {
        "minTemperature": 16,
        "maxTemperature": 17,
        "precision": 1,
        "operationModes": ["cool"],
        "fanModes": ["low"],
    }
    data = build_device_file(
        "climate", spec, {"off": GOOD_CODE, "cool/low/16": GOOD_CODE}
    )

    assert data["commands"]["cool"]["low"]["17"] == ""
    report = validate("climate", data, "90002")
    assert report.errors == []
    assert any("no code recorded" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# One-touch buttons
# ---------------------------------------------------------------------------

_PRESET_SPEC: dict[str, Any] = {
    "minTemperature": 16,
    "maxTemperature": 30,
    "precision": 1,
    "temperatureUnit": "C",
    "operationModes": ["cool", "heat", "dry"],
    "fanModes": ["low", "mid", "high", "auto"],
    "presets": ["turbo", "eco"],
}


def test_presets_do_not_multiply_the_capture_plan() -> None:
    """A preset is one code, not another dimension of the matrix.

    Three modes by four fan speeds by fifteen temperatures is 180 captures plus
    'off'. Making presets a fourth dimension would make it 720, which is why
    they are a flat group instead.
    """
    without = capture_plan("climate", {**_PRESET_SPEC, "presets": []})
    with_presets = capture_plan("climate", _PRESET_SPEC)

    assert len(without) == 181
    assert len(with_presets) == 183


def test_preset_cells_name_the_base_state_to_dial_in() -> None:
    """The label is the only place the person holding the remote is told."""
    cells = capture_plan("climate", _PRESET_SPEC)
    turbo = next(cell for cell in cells if cell["key"] == "presets/turbo")

    assert turbo["targets"] == [["presets", "turbo"]]
    assert turbo["group"] == "Presets"
    for part in ("turbo", "cool", "low", "23"):
        assert part in turbo["label"], turbo["label"]


def test_the_preset_label_and_the_written_baseline_agree() -> None:
    """A capture instruction that disagrees with the record is worse than none."""
    plan = capture_plan("climate", _PRESET_SPEC)
    data = build_device_file(
        "climate", _PRESET_SPEC, {cell["key"]: GOOD_CODE for cell in plan}
    )
    baseline = data["presetBaseline"]
    label = next(cell["label"] for cell in plan if cell["key"] == "presets/turbo")

    assert baseline == {
        "operationMode": "cool",
        "fanMode": "low",
        "temperature": 23,
    }
    for value in baseline.values():
        assert f"{value:g}" if isinstance(value, int) else value in label
    assert validate("climate", data, "90020").errors == []
    assert validate("climate", data, "90020").warnings == []


def test_a_declared_baseline_is_kept_when_the_spec_can_honour_it() -> None:
    """Whoever recorded the codes knows which state they used."""
    spec = {
        **_PRESET_SPEC,
        "presetBaseline": {
            "operationMode": "heat",
            "fanMode": "high",
            "temperature": 26,
        },
    }

    assert preset_baseline(spec) == {
        "operationMode": "heat",
        "fanMode": "high",
        "temperature": 26,
    }


def test_a_baseline_outside_the_declared_modes_is_refused_by_validate() -> None:
    """The entity would set an hvac_mode HA rejects, so the file cannot save."""
    plan = capture_plan("climate", _PRESET_SPEC)
    data = build_device_file(
        "climate", _PRESET_SPEC, {cell["key"]: GOOD_CODE for cell in plan}
    )
    data["presetBaseline"]["operationMode"] = "fan_only"

    report = validate("climate", data, "90021")
    assert any("presetBaseline.operationMode" in error for error in report.errors)


def test_recorded_presets_without_a_baseline_are_a_warning() -> None:
    """The codes work; the entity just cannot say what state they command."""
    plan = capture_plan("climate", _PRESET_SPEC)
    data = build_device_file(
        "climate", _PRESET_SPEC, {cell["key"]: GOOD_CODE for cell in plan}
    )
    del data["presetBaseline"]

    report = validate("climate", data, "90022")
    assert report.errors == []
    assert any("presetBaseline is missing" in warning for warning in report.warnings)


def test_presets_round_trip_through_the_template_loader() -> None:
    """Re-opening the file must recover the buttons and the state they need."""
    plan = capture_plan("climate", _PRESET_SPEC)
    data = build_device_file(
        "climate", _PRESET_SPEC, {cell["key"]: GOOD_CODE for cell in plan}
    )

    recovered = spec_from_device_file("climate", data)
    assert recovered["presets"] == ["turbo", "eco"]
    assert recovered["presetBaseline"] == data["presetBaseline"]

    codes = codes_from_device_file("climate", data, recovered)
    assert codes["presets/turbo"] == GOOD_CODE
    assert codes["presets/eco"] == GOOD_CODE

    # The mode/fan/temperature part of the plan is free to shrink here, because
    # every cell was given the same code and _infer_mode_options reads identical
    # fan subtrees as "this unit ignores fan speed". The preset cells are what
    # this test is about, and they have to come back unchanged.
    replanned = capture_plan("climate", recovered)
    assert [cell for cell in replanned if cell["group"] == "Presets"] == [
        cell for cell in plan if cell["group"] == "Presets"
    ]


def test_presets_on_another_platform_are_a_warning_not_an_error() -> None:
    """The codes stay reachable by name; they are just not preset modes there."""
    data = {
        "manufacturer": "Test",
        "supportedModels": ["T"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "commands": {"on": GOOD_CODE, "presets": {"turbo": GOOD_CODE}},
    }

    report = validate("media_player", data, "90023")
    assert report.errors == []
    assert any("only offered as preset modes" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# Free-form extra commands
# ---------------------------------------------------------------------------

_EXTRA_SPECS: dict[str, dict[str, Any]] = {
    "climate": {
        "minTemperature": 16,
        "maxTemperature": 17,
        "precision": 1,
        "operationModes": ["cool"],
        "fanModes": ["low"],
    },
    "fan": {"speed": ["low"]},
    "light": {},
    "media_player": {"buttons": ["on"]},
    "switch": {},
}


@pytest.mark.parametrize("platform", sorted(_EXTRA_SPECS))
def test_extra_commands_become_capture_cells(platform: str) -> None:
    """Every platform can record buttons its entity model cannot express."""
    spec = {**_EXTRA_SPECS[platform], "extraCommands": ["menu", "ok"]}
    cells = capture_plan(platform, spec)

    assert [cell["key"] for cell in cells[-2:]] == ["extras/menu", "extras/ok"]
    assert cells[-2]["targets"] == [["extras", "menu"]]
    assert cells[-1]["group"] == "Extra buttons"


@pytest.mark.parametrize("platform", sorted(_EXTRA_SPECS))
def test_a_spec_without_extras_plans_exactly_what_it_did_before(platform: str) -> None:
    """None of the 407 shipped files declares extras; none may gain a cell."""
    spec = _EXTRA_SPECS[platform]

    assert capture_plan(platform, spec) == capture_plan(
        platform, {**spec, "extraCommands": []}
    )


def test_extras_round_trip_through_the_template_loader() -> None:
    """Re-opening a file must not quietly drop the buttons it already holds."""
    spec = {**_EXTRA_SPECS["media_player"], "extraCommands": ["menu", "up"]}
    plan = capture_plan("media_player", spec)
    data = build_device_file(
        "media_player", spec, {cell["key"]: GOOD_CODE for cell in plan}
    )

    assert data["commands"]["extras"] == {"menu": GOOD_CODE, "up": GOOD_CODE}
    assert validate("media_player", data, "90010").errors == []

    recovered = spec_from_device_file("media_player", data)
    assert recovered["extraCommands"] == ["menu", "up"]
    codes = codes_from_device_file("media_player", data, recovered)
    assert codes["extras/menu"] == GOOD_CODE


def test_an_annotation_under_extras_is_never_offered_for_capture() -> None:
    """Transmitting a '_comment' would send its prose as a code."""
    data = {
        "manufacturer": "Test",
        "supportedModels": ["T"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "commands": {"on": GOOD_CODE, "extras": {"_note": "why", "menu": GOOD_CODE}},
    }

    spec = spec_from_device_file("media_player", data)
    assert spec["extraCommands"] == ["menu"]
    assert validate("media_player", data, "90011").errors == []


def test_a_nested_extras_entry_is_refused() -> None:
    """'extras/menu' names one code, so a dict there is a path to nothing."""
    data = {
        "manufacturer": "Test",
        "supportedModels": ["T"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "commands": {"on": GOOD_CODE, "extras": {"menu": {"deep": GOOD_CODE}}},
    }

    report = validate("media_player", data, "90012")
    assert any("not a nested object" in error for error in report.errors)


def test_an_extras_group_that_is_not_an_object_is_refused() -> None:
    """A bare string there would be walked as if it were a mapping."""
    data = {
        "manufacturer": "Test",
        "supportedModels": ["T"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "commands": {"on": GOOD_CODE, "extras": GOOD_CODE},
    }

    report = validate("media_player", data, "90013")
    assert any("mapping names to codes" in error for error in report.errors)


def test_an_extras_placeholder_is_reported_as_a_gap() -> None:
    """A skipped extra is a gap like any other, named by its path."""
    spec = {**_EXTRA_SPECS["fan"], "extraCommands": ["timer"]}
    plan = capture_plan("fan", spec)
    codes = {cell["key"]: GOOD_CODE for cell in plan if cell["key"] != "extras/timer"}
    data = build_device_file("fan", spec, codes)

    report = validate("fan", data, "90014")
    assert report.errors == []
    assert any("extras/timer" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# Starting from a file that already exists
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def _shipped(platform: str, device_code: int) -> dict[str, Any]:
    """Return a device file from the shipped database."""
    path = REPO_ROOT / "codes" / platform / f"{device_code}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_shipped_file_round_trips_through_the_template_loader() -> None:
    """Loading climate/1000 must recover every code it holds, not just its shape."""
    data = _shipped("climate", 1000)

    spec = spec_from_device_file("climate", data)
    codes = codes_from_device_file("climate", data, spec)
    plan = capture_plan("climate", spec)

    assert spec["operationModes"] == ["heat", "cool", "fan_only"]
    assert spec["fanModes"] == ["low", "mid", "high", "auto"]
    assert len(codes) == len(plan), "the template left gaps in a complete file"

    rebuilt = build_device_file("climate", spec, codes)
    assert validate("climate", rebuilt, "90000").errors == []


@pytest.mark.parametrize(
    ("platform", "device_code"),
    [("climate", 1000), ("climate", 1044), ("fan", 1000), ("media_player", 1000)],
)
def test_no_shipped_code_is_lost_when_a_file_is_used_as_a_template(
    platform, device_code
) -> None:
    """A cell reported as a gap must really be absent from the file.

    Reporting a false gap would make someone re-record a code they already had,
    which is exactly the tedium this panel exists to remove.
    """
    data = _shipped(platform, device_code)
    spec = spec_from_device_file(platform, data)
    codes = codes_from_device_file(platform, data, spec)
    commands = data["commands"]

    for cell in capture_plan(platform, spec):
        if cell["key"] in codes:
            continue
        for target in cell["targets"]:
            node = commands
            for key in target:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            assert not is_recorded(node), f"{cell['key']} was there all along"


def test_a_mode_recorded_without_temperatures_is_recognised() -> None:
    """The reduction has to survive a round trip, or a re-learn gets longer."""
    data = {
        "manufacturer": "Test",
        "supportedModels": ["T"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "minTemperature": 16,
        "maxTemperature": 18,
        "precision": 1,
        "temperatureUnit": "C",
        "operationModes": ["cool", "fan_only"],
        "fanModes": ["low", "high"],
        "commands": {
            "off": GOOD_CODE,
            "cool": {
                "low": {"16": GOOD_CODE, "17": GOOD_CODE, "18": GOOD_CODE},
                "high": {"16": GOOD_CODE, "17": GOOD_CODE, "18": GOOD_CODE},
            },
            # No temperature level, and identical under both fan speeds.
            "fan_only": {"low": GOOD_CODE, "high": GOOD_CODE},
        },
    }

    spec = spec_from_device_file("climate", data)

    assert spec["modeOptions"]["cool"] == {"usesFan": False, "usesTemperature": True}
    assert spec["modeOptions"]["fan_only"] == {
        "usesFan": False,
        "usesTemperature": False,
    }
    # cool records the same subtree under both fan speeds, so one capture covers
    # it; fan_only collapses to a single code.
    assert len(capture_plan("climate", spec)) == 1 + 3 + 1


def test_a_mode_whose_fan_speeds_differ_is_not_collapsed() -> None:
    """Collapsing a mode that really varies would throw codes away."""
    data = {
        "manufacturer": "Test",
        "supportedModels": ["T"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "minTemperature": 16,
        "maxTemperature": 16,
        "precision": 1,
        "temperatureUnit": "C",
        "operationModes": ["cool"],
        "fanModes": ["low", "high"],
        "commands": {
            "off": GOOD_CODE,
            "cool": {"low": {"16": GOOD_CODE}, "high": {"16": "JgAEABEgMEANBQ=="}},
        },
    }

    spec = spec_from_device_file("climate", data)

    assert spec["modeOptions"]["cool"]["usesFan"] is True
    assert len(capture_plan("climate", spec)) == 3


async def test_get_hands_the_panel_a_spec_and_the_codes_it_already_has(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Cover what makes 'start from an existing file' more than a slogan."""
    path = panel / "codes" / "climate" / "1234.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_shipped("climate", 1000)), encoding="utf-8")

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "hub_ir/get", "platform": "climate", "device_code": 1234}
    )
    result = (await client.receive_json())["result"]

    assert result["spec"]["fanModes"] == ["low", "mid", "high", "auto"]
    assert len(result["codes"]) == 181
    assert result["errors"] == []


async def test_listing_separates_your_own_files_from_the_shipped_ones(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The panel offers your recordings without burying them in 407 others."""
    for device_code in (1000, CUSTOM_CODE_START):
        path = panel / "codes" / "climate" / f"{device_code}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "hub_ir/list", "platform": "climate"})
    result = (await client.receive_json())["result"]

    assert result["codes"] == [1000, CUSTOM_CODE_START]
    assert result["custom"] == [CUSTOM_CODE_START]


# ---------------------------------------------------------------------------
# Websocket commands
# ---------------------------------------------------------------------------


@pytest.fixture
async def panel(hass: HomeAssistant, codes_dir, sent_commands):
    """Set the component up with its websocket commands registered."""
    with patch.object(frontend_module, "async_register_panel"):
        assert await async_setup_component(hass, "hub_ir", {"hub_ir": {}})
        await hass.async_block_till_done()
    return codes_dir


async def test_info_reports_which_remotes_can_learn(
    hass: HomeAssistant, panel, hass_ws_client, broadlink_remote
) -> None:
    """A remote from another integration cannot learn into Broadlink storage."""
    er.async_get(hass).async_get_or_create(
        "remote", "demo", "demo_remote", suggested_object_id="other"
    )
    hass.states.async_set("remote.other", "on")

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "hub_ir/info"})
    result = (await client.receive_json())["result"]

    by_id = {remote["entity_id"]: remote for remote in result["remotes"]}
    assert by_id[REMOTE_ENTITY_ID]["can_learn"] is True
    assert by_id["remote.other"]["can_learn"] is False
    assert result["next_code"]["climate"] == CUSTOM_CODE_START


async def test_saving_writes_a_file_the_integration_can_load(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The round trip that matters: save from the panel, then set a platform up."""
    spec = {
        "manufacturer": "Panel",
        "supportedModels": ["P-1"],
        "minTemperature": 16,
        "maxTemperature": 17,
        "precision": 1,
        "temperatureUnit": "C",
        "operationModes": ["cool"],
        "fanModes": ["low"],
    }
    codes = {cell["key"]: GOOD_CODE for cell in capture_plan("climate", spec)}

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "hub_ir/save",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
            "spec": spec,
            "codes": codes,
        }
    )
    saved = await client.receive_json()
    assert saved["success"], saved

    written = json.loads(
        (panel / "codes" / "climate" / f"{CUSTOM_CODE_START}.json").read_text("utf-8")
    )
    assert written["manufacturer"] == "Panel"

    assert await async_setup_component(
        hass,
        "climate",
        {
            "climate": {
                "platform": "hub_ir",
                "name": "Learned AC",
                "unique_id": "learned_ac",
                "device_code": CUSTOM_CODE_START,
                "controller_data": REMOTE_ENTITY_ID,
            }
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get("climate.learned_ac") is not None


async def test_saving_refuses_a_file_the_integration_could_not_use(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A file with no codes at all would produce an entity that does nothing."""
    spec = {
        "manufacturer": "Empty",
        "minTemperature": 16,
        "maxTemperature": 17,
        "precision": 1,
        "temperatureUnit": "C",
        "operationModes": ["cool"],
        "fanModes": ["low"],
    }

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "hub_ir/save",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START + 1,
            "spec": spec,
            "codes": {"cool/low/16": "not base64 at all!!"},
        }
    )
    answer = await client.receive_json()

    assert not answer["success"]
    assert "not valid base64" in answer["error"]["message"]
    assert not (panel / "codes" / "climate" / f"{CUSTOM_CODE_START + 1}.json").exists()


async def test_saving_cannot_overwrite_a_shipped_device_file(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Device codes below the custom range belong to upstream, not to the panel."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "hub_ir/save",
            "platform": "climate",
            "device_code": 1000,
            "spec": {},
            "codes": {},
        }
    )
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "invalid_format"


def _switch_save(device_code: int, manufacturer: str, **extra: Any) -> dict[str, Any]:
    """Return a save message for a minimal switch file."""
    return {
        "type": "hub_ir/save",
        "platform": "switch",
        "device_code": device_code,
        "spec": {"manufacturer": manufacturer, "supportedModels": ["X"]},
        "codes": {"on": GOOD_CODE, "off": GOOD_CODE},
        **extra,
    }


async def test_saving_over_your_own_file_needs_permission(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The panel guesses the next free code once, and that guess goes stale.

    The assertion that matters is the last one: the original is still there, so
    nothing was clobbered before the refusal.
    """
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(_switch_save(CUSTOM_CODE_START, "First"))
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(_switch_save(CUSTOM_CODE_START, "Second"))
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "already_exists"

    written = json.loads(
        (panel / "codes" / "switch" / f"{CUSTOM_CODE_START}.json").read_text("utf-8")
    )
    assert written["manufacturer"] == "First"


async def test_saving_over_your_own_file_is_allowed_when_asked(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Re-recording a device you already taught is the second commonest path."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(_switch_save(CUSTOM_CODE_START, "First"))
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(
        _switch_save(CUSTOM_CODE_START, "Second", overwrite=True)
    )
    assert (await client.receive_json())["success"]

    written = json.loads(
        (panel / "codes" / "switch" / f"{CUSTOM_CODE_START}.json").read_text("utf-8")
    )
    assert written["manufacturer"] == "Second"


# ---------------------------------------------------------------------------
# Getting a recording off the machine
# ---------------------------------------------------------------------------


async def test_export_hands_back_the_file_exactly_as_it_was_written(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A re-serialisation is not guaranteed to match what the validator sees.

    A contributed file has to be byte-for-byte the text on disk.
    """
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(_switch_save(CUSTOM_CODE_START, "Yamaha"))
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {
            "type": "hub_ir/export",
            "platform": "switch",
            "device_code": CUSTOM_CODE_START,
        }
    )
    answer = await client.receive_json()
    assert answer["success"], answer

    result = answer["result"]
    on_disk = (panel / "codes" / "switch" / f"{CUSTOM_CODE_START}.json").read_text(
        "utf-8"
    )
    assert result["json"] == on_disk
    assert result["bytes"] == len(on_disk.encode("utf-8"))
    assert result["filename"] == f"{CUSTOM_CODE_START}.json"
    assert result["summary"]["manufacturer"] == "Yamaha"
    assert result["summary"]["code_count"] == 2


async def test_the_issue_link_carries_the_facts_but_never_the_codes(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A 100 kB file cannot fit in a URL, so it carries none of it.

    Putting part of the file in would be a silent truncation, which is worse
    than asking for an attachment.
    """
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(_switch_save(CUSTOM_CODE_START, "Yamaha"))
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {
            "type": "hub_ir/export",
            "platform": "switch",
            "device_code": CUSTOM_CODE_START,
        }
    )
    result = (await client.receive_json())["result"]
    url = result["issue_url"]

    assert url.startswith(f"{REPO_URL}/issues/new?")
    assert "Yamaha" in unquote_plus(url)
    assert "switch" in unquote_plus(url)
    # The codes themselves are the thing that must not be in there.
    assert GOOD_CODE not in url
    assert GOOD_CODE not in unquote_plus(url)
    assert len(url) < 6000, f"the issue URL grew to {len(url)} characters"


async def test_export_reports_the_gaps_it_knows_about(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Whoever reviews the contribution should not have to find them."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            **_switch_save(CUSTOM_CODE_START, "Partial"),
            "codes": {"on": GOOD_CODE},
        }
    )
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {
            "type": "hub_ir/export",
            "platform": "switch",
            "device_code": CUSTOM_CODE_START,
        }
    )
    result = (await client.receive_json())["result"]

    assert result["summary"]["code_count"] == 1
    assert any("no code recorded" in warning for warning in result["warnings"])
    assert "no code recorded" in unquote_plus(result["issue_url"])


async def test_exporting_a_device_file_that_is_not_there_says_so(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Better than an empty download nobody can explain."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "hub_ir/export", "platform": "switch", "device_code": 99999}
    )
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "not_found"


def test_the_repository_is_named_in_one_place() -> None:
    """Two copies of the slug would drift, and one of them would 404."""
    assert REPO_SLUG in CODES_BASE_URL
    assert REPO_SLUG in REPO_URL


def test_the_panel_can_get_a_recording_off_the_machine() -> None:
    """HACS ships only the component, so a recording exists nowhere else.

    Losing one to a reinstall is the exact complaint this answers.
    """
    js = _panel_source()

    assert 'type: "hub_ir/export"' in js
    assert "_copyJson" in js
    assert "_downloadJson" in js
    assert "createObjectURL" in js
    assert "data-download" in js, "earlier recordings cannot be downloaded"


async def test_learn_over_the_websocket_returns_the_code(
    hass: HomeAssistant, panel, hass_ws_client, broadlink_remote
) -> None:
    """The panel's main call, end to end."""
    _learning_remote(hass, GOOD_CODE)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "hub_ir/learn", "remote_entity_id": REMOTE_ENTITY_ID}
    )
    answer = await client.receive_json()

    assert answer["success"]
    assert answer["result"]["code"] == GOOD_CODE


async def test_learn_reports_a_timeout_rather_than_succeeding_emptily(
    hass: HomeAssistant, panel, hass_ws_client, broadlink_remote
) -> None:
    """The panel must be able to tell a captured code from a missed press."""
    _learning_remote(hass, None)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "hub_ir/learn", "remote_entity_id": REMOTE_ENTITY_ID}
    )
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "learn_failed"


async def test_send_transmits_a_captured_code_for_testing(
    hass: HomeAssistant, panel, hass_ws_client, sent_commands
) -> None:
    """Trying a code before keeping it is the point of the test button."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "hub_ir/send",
            "remote_entity_id": REMOTE_ENTITY_ID,
            "code": GOOD_CODE,
        }
    )
    answer = await client.receive_json()

    assert answer["success"]
    assert sent_commands[-1].data["command"] == [f"b64:{GOOD_CODE}"]


async def test_plan_is_served_over_the_websocket(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The panel asks the server what to capture rather than working it out."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "hub_ir/plan",
            "platform": "climate",
            "spec": {
                "minTemperature": 16,
                "maxTemperature": 18,
                "precision": 1,
                "operationModes": ["cool"],
                "fanModes": ["low"],
            },
        }
    )
    result = (await client.receive_json())["result"]

    assert result["total"] == 4
    assert result["cells"][1]["label"] == "cool · low · 16°C"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "hub_ir/info"},
        {
            "type": "hub_ir/create_entity",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
            "controller_data": REMOTE_ENTITY_ID,
            "name": "Sneaky",
        },
    ],
)
async def test_the_panel_commands_are_admin_only(
    hass: HomeAssistant, panel, hass_ws_client, hass_admin_user, message
) -> None:
    """Saving writes into the configuration directory, and creating adds entities."""
    hass_admin_user.groups = []

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(message)
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# Creating the entity from the panel
# ---------------------------------------------------------------------------


def _create_message(**overrides: Any) -> dict[str, Any]:
    """Return the message the panel's Create button sends."""
    return {
        "type": "hub_ir/create_entity",
        "platform": "climate",
        "device_code": CUSTOM_CODE_START,
        "controller_data": REMOTE_ENTITY_ID,
        "name": "Bedroom AC",
        **overrides,
    }


async def test_creating_an_entity_from_the_panel_needs_no_restart(
    hass: HomeAssistant, panel, hass_ws_client, write_device_file
) -> None:
    """The last leave-the-browser step this panel exists to remove."""
    write_device_file("climate", CUSTOM_CODE_START, CLIMATE_DEVICE_DATA)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(_create_message())
    answer = await client.receive_json()
    await hass.async_block_till_done()

    assert answer["success"], answer
    assert answer["result"]["entity_id"] == "climate.bedroom_ac"
    assert answer["result"]["existing"] is False
    assert hass.states.get("climate.bedroom_ac") is not None
    assert len(hass.config_entries.async_entries("hub_ir")) == 1


async def test_creating_the_same_device_twice_reloads_it_instead_of_failing(
    hass: HomeAssistant, panel, hass_ws_client, write_device_file
) -> None:
    """Saving more codes into a device already added must not be an error.

    Without this the live entity would keep the device file it parsed at setup,
    and the codes just learned would do nothing until a restart.
    """
    write_device_file("climate", CUSTOM_CODE_START, CLIMATE_DEVICE_DATA)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(_create_message())
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()

    await client.send_json_auto_id(_create_message())
    answer = await client.receive_json()
    await hass.async_block_till_done()

    assert answer["success"], answer
    assert answer["result"]["existing"] is True
    assert answer["result"]["entity_id"] == "climate.bedroom_ac"
    assert len(hass.config_entries.async_entries("hub_ir")) == 1


async def test_creating_an_entity_refuses_a_controller_that_is_not_a_remote(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A wrong domain there is silent at runtime, so it is caught at the door."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        _create_message(controller_data="light.not_a_remote")
    )
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "invalid_format"
    assert not hass.config_entries.async_entries("hub_ir")


async def test_creating_an_entity_from_a_device_file_that_is_gone_says_so(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A failed create must leave no half-made entry behind to puzzle over."""
    client = await hass_ws_client(hass)
    with patch(
        "custom_components.hub_ir.Helper.downloader",
        side_effect=HomeAssistantError("Got HTTP 404"),
    ):
        await client.send_json_auto_id(_create_message())
        answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "create_failed"
    assert not hass.config_entries.async_entries("hub_ir")


# ---------------------------------------------------------------------------
# Panel registration
# ---------------------------------------------------------------------------


async def test_the_panel_is_registered_with_the_frontend(
    hass: HomeAssistant, codes_dir, sent_commands
) -> None:
    """The sidebar entry and the module URL are what make the panel reachable."""
    with patch(
        "homeassistant.components.panel_custom.async_register_panel"
    ) as register:
        assert await async_setup_component(hass, "hub_ir", {"hub_ir": {}})
        await hass.async_block_till_done()

    assert register.call_count == 1
    kwargs = register.call_args.kwargs
    assert kwargs["frontend_url_path"] == "hub-ir"
    assert kwargs["webcomponent_name"] == "hub-ir-panel"
    assert kwargs["require_admin"] is True
    assert kwargs["module_url"].endswith("/hub-ir-panel.js")


async def test_setup_survives_a_frontend_that_cannot_take_the_panel(
    hass: HomeAssistant, codes_dir, sent_commands, caplog
) -> None:
    """A headless Home Assistant should still get working platforms."""
    with patch(
        "homeassistant.components.panel_custom.async_register_panel",
        side_effect=RuntimeError("no frontend here"),
    ):
        assert await async_setup_component(hass, "hub_ir", {"hub_ir": {}})
        await hass.async_block_till_done()

    assert "only the code-learning panel is unavailable" in caplog.text


def test_the_pieces_of_the_integration_agree_on_its_name() -> None:
    """The domain, the package directory and the version must not drift apart.

    A rename touches the directory name, the manifest, a constant, the panel's
    URL and the websocket command prefix. Getting one of them wrong leaves an
    integration that half loads, so they are checked against each other here.
    """
    package = Path(__file__).resolve().parent.parent / "custom_components" / "hub_ir"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    init = (package / "__init__.py").read_text(encoding="utf-8")
    hacs = json.loads((package.parent.parent / "hacs.json").read_text(encoding="utf-8"))

    assert manifest["domain"] == package.name
    assert re.search(r'^DOMAIN = "([^"]+)"', init, re.M).group(1) == manifest["domain"]
    assert (
        re.search(r'^VERSION = "([^"]+)"', init, re.M).group(1) == manifest["version"]
    )
    assert manifest["name"] == hacs["name"]
    assert manifest["documentation"].endswith("/hub-ir")

    # Home Assistant refuses to load an integration that promises a config flow
    # and does not ship one, and an untranslated flow shows raw keys as labels.
    assert manifest["config_flow"] is True
    assert (package / "config_flow.py").is_file()
    assert (package / "translations" / "en.json").is_file()
    assert json.loads((package / "strings.json").read_text(encoding="utf-8")) == (
        json.loads((package / "translations" / "en.json").read_text(encoding="utf-8"))
    )


def test_every_translation_carries_exactly_the_keys_english_does() -> None:
    """A key only the translation has is dead weight; a missing one shows English.

    Home Assistant looks every label up by key and falls back to English when a
    key is absent, so drift is silent: a renamed key leaves a dialog in two
    languages and nothing complains. Checked structurally rather than file by
    file, so a language added later is covered without touching this test.
    """
    package = Path(__file__).resolve().parent.parent / "custom_components" / "hub_ir"
    english = json.loads((package / "strings.json").read_text(encoding="utf-8"))

    def paths(node: object, prefix: str = "") -> set[str]:
        if not isinstance(node, dict):
            return {prefix}
        return {
            p for key, value in node.items() for p in paths(value, f"{prefix}/{key}")
        }

    expected = paths(english)

    for path in sorted((package / "translations").glob("*.json")):
        if path.name == "en.json":
            # Asserted equal to strings.json above, which is stricter.
            continue
        translated = paths(json.loads(path.read_text(encoding="utf-8")))
        assert translated == expected, (
            f"{path.name}: missing {sorted(expected - translated)}, "
            f"unexpected {sorted(translated - expected)}"
        )


def test_the_panel_calls_only_commands_the_server_defines() -> None:
    """A renamed or mistyped command would fail silently in the browser.

    It also catches the reverse: an endpoint nobody calls. Both have happened
    here before.
    """
    package = Path(__file__).resolve().parent.parent / "custom_components" / "hub_ir"
    js = (package / "www" / "hub-ir-panel.js").read_text(encoding="utf-8")
    server = (package / "websocket.py").read_text(encoding="utf-8")
    frontend = (package / "frontend.py").read_text(encoding="utf-8")

    # Anchored on the domain rather than any "x/y" string: a MIME type in a Blob
    # is also `type: "…/…"`. A command misspelled in the domain half still shows
    # up, as an endpoint nobody calls.
    called = set(re.findall(r'type:\s*"(hub_ir/[a-z_]+)"', js))
    defined = set(re.findall(r'vol\.Required\("type"\):\s*"(hub_ir/[a-z_]+)"', server))

    assert called, "the panel calls nothing at all"
    assert called <= defined, f"panel calls undefined commands: {called - defined}"
    assert defined <= called, f"unused websocket endpoints: {defined - called}"

    element = re.search(r'customElements\.define\("([^"]+)"', js).group(1)
    assert element == re.search(r'WEBCOMPONENT_NAME = "([^"]+)"', frontend).group(1)

    module = re.search(r'_MODULE_FILE = "([^"]+)"', frontend).group(1)
    assert (package / "www" / module).is_file()


def test_the_panel_module_is_shipped_and_self_contained() -> None:
    """Home Assistant's CSP blocks anything the panel would try to fetch."""
    module = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "hub_ir"
        / "www"
        / "hub-ir-panel.js"
    )
    source = module.read_text(encoding="utf-8")

    assert 'customElements.define("hub-ir-panel"' in source
    assert "http://" not in source
    assert "https://" not in source
    assert "import(" not in source


# ---------------------------------------------------------------------------
# The lists the user builds by hand
# ---------------------------------------------------------------------------


def _panel_source() -> str:
    """Return the panel module, the only way to check it without a browser."""
    return (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "hub_ir"
        / "www"
        / "hub-ir-panel.js"
    ).read_text(encoding="utf-8")


def test_no_list_is_still_edited_as_a_comma_separated_string() -> None:
    """The regression guard for the whole list editor.

    A comma-separated text field accepted 'low,, high' and silently dropped the
    hole, took 'High' alongside 'high' as two different keys in the command tree,
    and gave no way at all to reorder — while the order is exactly what the
    server matches fan speeds by.
    """
    js = _panel_source()

    assert "splitList" not in js, "the comma-splitting helper is back"
    assert '.join(", ")' not in js, "a list is being rendered into a text field"
    assert "comma separated" not in js
    assert "_listEditor(" in js


@pytest.mark.parametrize(
    "field",
    [
        "models",
        "fanModes",
        "swingModes",
        "speed",
        "brightness",
        "colorTemperature",
        "sources",
    ],
)
def test_every_hand_built_list_uses_the_one_list_editor(field: str) -> None:
    """One component, not seven near-copies that drift apart."""
    js = _panel_source()

    assert f'_listEditor("{field}"' in js
    assert re.search(rf"^  {field}: {{", js, re.M), f"{field} is not in LIST_FIELDS"


def test_only_the_numeric_lists_are_declared_numeric() -> None:
    """A string '2700' would reach the device file, and light.py compares it.

    Declaring a text list numeric would be worse still: every name typed into it
    would be refused.
    """
    js = _panel_source()
    block = re.search(r"const LIST_FIELDS = \{(.+?)\n\};", js, re.S).group(1)

    numeric = {
        name
        for name, body in re.findall(r"\n  (\w+): \{(.+?)\n  \},", block, re.S)
        if "numeric: true" in body
    }
    assert numeric == {"brightness", "colorTemperature"}


def test_the_ordered_lists_say_so() -> None:
    """Order is what the server matches by, so the UI has to admit it."""
    js = _panel_source()
    block = re.search(r"const LIST_FIELDS = \{(.+?)\n\};", js, re.S).group(1)

    ordered = {
        name
        for name, body in re.findall(r"\n  (\w+): \{(.+?)\n  \},", block, re.S)
        if "ordered: true" in body
    }
    assert {"fanModes", "swingModes", "speed"} <= ordered


def test_a_list_can_be_reordered_and_pruned() -> None:
    """Without these an entry typed in the wrong place could only be retyped."""
    js = _panel_source()

    for act in ("add", "remove", "up", "down", "preset", "fill"):
        assert f'act === "{act}"' in js, f"the list editor cannot {act}"


def test_the_panel_can_capture_presets_and_extra_buttons() -> None:
    """Without these the two new command groups are hand-edited JSON only."""
    js = _panel_source()

    assert '_listEditor("presets"' in js
    assert '_listEditor("extraCommands"' in js
    # The base state has to be pickable, or every preset is captured from
    # whatever the remote happened to be showing.
    for control in ("presetBaseMode", "presetBaseFanMode", "presetBaseTemperature"):
        assert control in js


def test_the_panel_sends_the_toggle_flag_when_learning() -> None:
    """ws_learn has accepted toggle since it was written; nothing ever sent it.

    So a remote whose button alternates between two packets could not be
    captured from the UI at all.
    """
    js = _panel_source()

    assert re.search(r'type:\s*"hub_ir/learn"[^}]*toggle:', js, re.S)


def test_the_panel_asks_before_replacing_a_device_file() -> None:
    """Saving over one of your own recordings used to happen silently."""
    js = _panel_source()

    assert re.search(r'type:\s*"hub_ir/save"[^}]*overwrite:', js, re.S)
    assert "already_exists" in js


def test_the_panel_never_invents_the_next_device_code() -> None:
    """The free code is a fact about the filesystem, and this guessed it.

    'the last code plus one' walked straight over any file the user already had
    at that number.
    """
    js = _panel_source()

    assert "(this._state.deviceCode || 0) + 1" not in js
    assert "_refreshNextCode" in js


def test_the_panel_offers_every_platform_the_server_knows() -> None:
    """A platform the server supports but the panel hides cannot be recorded.

    Switch matters most here: no switch device files are shipped at all, so the
    panel is the only way to get one.
    """
    js = _panel_source()
    block = re.search(r"const PLATFORM_LABELS = \{(.+?)\n\};", js, re.S).group(1)
    offered = set(re.findall(r"\n  (\w+):", block))

    assert offered == set(PLATFORMS)

    spec_block = re.search(r"const DEFAULT_SPEC = \{(.+?)\n\};", js, re.S).group(1)
    assert set(re.findall(r"\n  (\w+): \{", spec_block)) == set(PLATFORMS)
