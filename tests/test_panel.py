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
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.broadlink_ir import frontend as frontend_module
from custom_components.broadlink_ir.device_file import (
    CUSTOM_CODE_START,
    build_device_file,
    capture_plan,
    codes_from_device_file,
    is_recorded,
    spec_from_device_file,
    temperature_steps,
    validate,
)
from custom_components.broadlink_ir.learn import (
    SCRATCH_DEVICE,
    async_learn_ir_code,
    broadlink_unique_id,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component

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
        {"type": "broadlink_ir/get", "platform": "climate", "device_code": 1234}
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
    await client.send_json_auto_id({"type": "broadlink_ir/list", "platform": "climate"})
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
        assert await async_setup_component(hass, "broadlink_ir", {"broadlink_ir": {}})
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
    await client.send_json_auto_id({"type": "broadlink_ir/info"})
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
            "type": "broadlink_ir/save",
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
                "platform": "broadlink_ir",
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
            "type": "broadlink_ir/save",
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
            "type": "broadlink_ir/save",
            "platform": "climate",
            "device_code": 1000,
            "spec": {},
            "codes": {},
        }
    )
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "invalid_format"


async def test_learn_over_the_websocket_returns_the_code(
    hass: HomeAssistant, panel, hass_ws_client, broadlink_remote
) -> None:
    """The panel's main call, end to end."""
    _learning_remote(hass, GOOD_CODE)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "broadlink_ir/learn", "remote_entity_id": REMOTE_ENTITY_ID}
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
        {"type": "broadlink_ir/learn", "remote_entity_id": REMOTE_ENTITY_ID}
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
            "type": "broadlink_ir/send",
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
            "type": "broadlink_ir/plan",
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


async def test_the_panel_commands_are_admin_only(
    hass: HomeAssistant, panel, hass_ws_client, hass_admin_user
) -> None:
    """Saving writes into the configuration directory."""
    hass_admin_user.groups = []

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "broadlink_ir/info"})
    answer = await client.receive_json()

    assert not answer["success"]
    assert answer["error"]["code"] == "unauthorized"


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
        assert await async_setup_component(hass, "broadlink_ir", {"broadlink_ir": {}})
        await hass.async_block_till_done()

    assert register.call_count == 1
    kwargs = register.call_args.kwargs
    assert kwargs["frontend_url_path"] == "broadlink-ir"
    assert kwargs["webcomponent_name"] == "broadlink-ir-panel"
    assert kwargs["require_admin"] is True
    assert kwargs["module_url"].endswith("/broadlink-ir-panel.js")


async def test_setup_survives_a_frontend_that_cannot_take_the_panel(
    hass: HomeAssistant, codes_dir, sent_commands, caplog
) -> None:
    """A headless Home Assistant should still get working platforms."""
    with patch(
        "homeassistant.components.panel_custom.async_register_panel",
        side_effect=RuntimeError("no frontend here"),
    ):
        assert await async_setup_component(hass, "broadlink_ir", {"broadlink_ir": {}})
        await hass.async_block_till_done()

    assert "only the code-learning panel is unavailable" in caplog.text


def test_the_panel_module_is_shipped_and_self_contained() -> None:
    """Home Assistant's CSP blocks anything the panel would try to fetch."""
    module = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "broadlink_ir"
        / "www"
        / "broadlink-ir-panel.js"
    )
    source = module.read_text(encoding="utf-8")

    assert 'customElements.define("broadlink-ir-panel"' in source
    assert "http://" not in source
    assert "https://" not in source
    assert "import(" not in source
