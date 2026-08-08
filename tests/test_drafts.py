"""Tests for drafts: a recording parked half-finished and picked up later.

An air conditioner is a hundred-odd codes, so the interesting failures are the
ones that lose work quietly — a draft that never reached the disk, a pair of
codes from a two-packet button flattened to one, a resumed draft that no longer
builds the same device file as the session it came from. Those are what this
covers.
"""

from __future__ import annotations

from base64 import b64encode
import json
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.hub_ir import (
    drafts as drafts_module,
    frontend as frontend_module,
)
from custom_components.hub_ir.device_file import CUSTOM_CODE_START
from custom_components.hub_ir.drafts import (
    DRAFT_DATA_KEY,
    DRAFT_STORAGE_KEY,
    async_load_drafts,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

GOOD_PACKET = bytes([0x26, 0x00, 0x04, 0x00, 0x10, 0x20, 0x30, 0x40, 0x0D, 0x05])
GOOD_CODE = b64encode(GOOD_PACKET).decode()

# The smallest spec that still produces a device file the integration accepts:
# one mode, one fan speed, two temperatures. Three cells with the off code.
CLIMATE_PLAN_SIZE = 3

CLIMATE_SPEC: dict[str, Any] = {
    "manufacturer": "Draftwell",
    "supportedModels": ["DW-1"],
    "minTemperature": 16,
    "maxTemperature": 17,
    "precision": 1,
    "temperatureUnit": "C",
    "operationModes": ["cool"],
    "fanModes": ["low"],
}

# Two captured, one deliberately skipped: a session stopped part way, which is
# the only state a draft is ever in.
CLIMATE_CODES: dict[str, Any] = {
    "off": GOOD_CODE,
    # A two-packet button, stored as the pair the controller sends in turn.
    "cool/low/16": [GOOD_CODE, GOOD_CODE],
}


@pytest.fixture
async def panel(hass: HomeAssistant, codes_dir, sent_commands):
    """Set the component up with its websocket commands registered."""
    with patch.object(frontend_module, "async_register_panel"):
        assert await async_setup_component(hass, "hub_ir", {"hub_ir": {}})
        await hass.async_block_till_done()
    return codes_dir


def draft_message(**overrides: Any) -> dict[str, Any]:
    """Return a hub_ir/draft_save message, with room to vary one field."""
    return {
        "type": "hub_ir/draft_save",
        "platform": "climate",
        "device_code": CUSTOM_CODE_START,
        "spec": CLIMATE_SPEC,
        "codes": CLIMATE_CODES,
        "skipped": {"cool/low/17": True},
        "index": 2,
        "toggle": True,
        "remote_entity_id": "remote.broadlink",
        "total": CLIMATE_PLAN_SIZE,
        **overrides,
    }


async def send(client, message: dict[str, Any]) -> dict[str, Any]:
    """Send one websocket message and return the whole answer."""
    await client.send_json_auto_id(message)
    return await client.receive_json()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


async def test_a_draft_survives_the_round_trip_intact(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Everything the panel cannot recompute has to come back exactly."""
    client = await hass_ws_client(hass)

    saved = await send(client, draft_message())
    assert saved["success"]
    assert saved["result"]["key"] == f"climate/{CUSTOM_CODE_START}"
    assert saved["result"]["updated"]

    got = await send(
        client,
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )
    draft = got["result"]["draft"]

    assert draft["spec"] == CLIMATE_SPEC
    assert draft["codes"] == CLIMATE_CODES
    assert draft["skipped"] == {"cool/low/17": True}
    assert draft["index"] == 2
    assert draft["toggle"] is True
    assert draft["remote_entity_id"] == "remote.broadlink"
    assert draft["total"] == CLIMATE_PLAN_SIZE


async def test_a_two_packet_code_stays_a_pair(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Accepting only strings would refuse every draft using two-packet mode.

    The pair is what the controller alternates between, so flattening it to one
    string is a code that works every other press.
    """
    client = await hass_ws_client(hass)
    await send(client, draft_message())

    got = await send(
        client,
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )

    assert got["result"]["draft"]["codes"]["cool/low/16"] == [GOOD_CODE, GOOD_CODE]


async def test_a_draft_reaches_the_disk(
    hass: HomeAssistant, panel, hass_ws_client, hass_storage
) -> None:
    """A draft held only in memory is exactly the thing this feature replaces."""
    client = await hass_ws_client(hass)
    await send(client, draft_message())

    stored = hass_storage[DRAFT_STORAGE_KEY]["data"]["drafts"]
    assert list(stored) == [f"climate/{CUSTOM_CODE_START}"]
    assert stored[f"climate/{CUSTOM_CODE_START}"]["codes"] == CLIMATE_CODES

    # Drop the cache the way a restart would, and read it back from the store.
    hass.data.pop(DRAFT_DATA_KEY)
    assert await async_load_drafts(hass) == stored


async def test_saving_the_same_target_twice_updates_rather_than_duplicates(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Platform and device code are the key: a second sitting is the same draft."""
    client = await hass_ws_client(hass)

    await send(client, draft_message())
    await send(client, draft_message(codes={"off": GOOD_CODE}, index=1))

    listed = await send(client, {"type": "hub_ir/draft_list"})
    drafts = listed["result"]["drafts"]

    assert len(drafts) == 1
    assert drafts[0]["done"] == 1


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def test_the_listing_describes_a_draft_without_shipping_its_codes(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """One climate draft is tens of kilobytes; the list must stay a list."""
    client = await hass_ws_client(hass)
    await send(client, draft_message())

    listed = await send(client, {"type": "hub_ir/draft_list"})
    (summary,) = listed["result"]["drafts"]

    assert summary == {
        "key": f"climate/{CUSTOM_CODE_START}",
        "platform": "climate",
        "device_code": CUSTOM_CODE_START,
        "label": "Draftwell DW-1",
        "done": 2,
        "skipped": 1,
        "total": CLIMATE_PLAN_SIZE,
        "updated": summary["updated"],
    }
    assert GOOD_CODE not in json.dumps(listed["result"])


async def test_the_listing_puts_the_most_recent_draft_first(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The one someone is most likely coming back for."""
    client = await hass_ws_client(hass)

    await send(client, draft_message(device_code=CUSTOM_CODE_START))
    await send(client, draft_message(device_code=CUSTOM_CODE_START + 1))

    listed = await send(client, {"type": "hub_ir/draft_list"})
    codes = [summary["device_code"] for summary in listed["result"]["drafts"]]

    assert codes == [CUSTOM_CODE_START + 1, CUSTOM_CODE_START]


async def test_listing_nothing_is_not_an_error(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The first time the panel is ever opened there is no store at all."""
    client = await hass_ws_client(hass)
    listed = await send(client, {"type": "hub_ir/draft_list"})

    assert listed["success"]
    assert listed["result"]["drafts"] == []


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


async def test_deleting_removes_the_draft(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The panel does this itself once the device file has been written."""
    client = await hass_ws_client(hass)
    await send(client, draft_message())

    deleted = await send(
        client,
        {
            "type": "hub_ir/draft_delete",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )

    assert deleted["result"]["deleted"] is True
    listed = await send(client, {"type": "hub_ir/draft_list"})
    assert listed["result"]["drafts"] == []


async def test_deleting_something_already_gone_is_not_an_error(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Two tabs finishing the same recording is a race nobody should see."""
    client = await hass_ws_client(hass)

    deleted = await send(
        client,
        {
            "type": "hub_ir/draft_delete",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )

    assert deleted["success"]
    assert deleted["result"]["deleted"] is False


async def test_fetching_a_draft_that_is_not_there_says_so(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A stale list in an old tab should fail with something readable."""
    client = await hass_ws_client(hass)

    got = await send(
        client,
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )

    assert not got["success"]
    assert got["error"]["code"] == "draft_not_found"


# ---------------------------------------------------------------------------
# What the store refuses
# ---------------------------------------------------------------------------


async def test_a_draft_cannot_target_a_shipped_device_code(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """A draft is a device file in the making, held to the same rule as saving."""
    client = await hass_ws_client(hass)

    answer = await send(client, draft_message(device_code=1000))

    assert not answer["success"]
    assert answer["error"]["code"] == "invalid_format"


async def test_the_number_of_drafts_is_capped(
    hass: HomeAssistant, panel, hass_ws_client, monkeypatch
) -> None:
    """Nothing ever tidies this store, so it must not grow without limit."""
    monkeypatch.setattr(drafts_module, "MAX_DRAFTS", 2)
    client = await hass_ws_client(hass)

    for offset in range(2):
        message = draft_message(device_code=CUSTOM_CODE_START + offset)
        assert (await send(client, message))["success"]

    answer = await send(client, draft_message(device_code=CUSTOM_CODE_START + 2))

    assert not answer["success"]
    assert answer["error"]["code"] == "draft_rejected"
    assert "Delete one" in answer["error"]["message"]


async def test_an_existing_draft_can_still_be_saved_at_the_cap(
    hass: HomeAssistant, panel, hass_ws_client, monkeypatch
) -> None:
    """Refusing to save progress on work under way would be the wrong moment."""
    monkeypatch.setattr(drafts_module, "MAX_DRAFTS", 1)
    client = await hass_ws_client(hass)

    assert (await send(client, draft_message()))["success"]
    again = await send(client, draft_message(index=3))

    assert again["success"]
    got = await send(
        client,
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )
    assert got["result"]["draft"]["index"] == 3


async def test_an_implausible_pile_of_codes_is_refused(
    hass: HomeAssistant, panel, hass_ws_client, monkeypatch
) -> None:
    """Well above any real device file, so this only catches a runaway."""
    monkeypatch.setattr(drafts_module, "MAX_DRAFT_CODES", 1)
    client = await hass_ws_client(hass)

    answer = await send(client, draft_message())

    assert not answer["success"]
    assert answer["error"]["code"] == "draft_rejected"


async def test_a_draft_may_be_far_from_a_valid_device_file(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Unfinished is the whole point; validation belongs to plan and save."""
    client = await hass_ws_client(hass)

    answer = await send(
        client,
        draft_message(
            spec={"manufacturer": ""}, codes={}, skipped={}, index=0, total=0
        ),
    )

    assert answer["success"]


@pytest.mark.parametrize(
    "message",
    [
        {"type": "hub_ir/draft_list"},
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
        {
            "type": "hub_ir/draft_delete",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    ],
)
async def test_the_draft_commands_are_admin_only(
    hass: HomeAssistant, panel, hass_ws_client, hass_admin_user, message
) -> None:
    """A draft holds codes for the hardware in someone's house."""
    hass_admin_user.groups = []

    client = await hass_ws_client(hass)
    answer = await send(client, message)

    assert not answer["success"]
    assert answer["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# Fidelity: resuming must not change what gets written
# ---------------------------------------------------------------------------


async def test_a_resumed_draft_writes_the_same_file_as_the_session_it_came_from(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """The guarantee that matters: parking work does not quietly alter it.

    A draft that came back subtly different — a pair collapsed, a temperature
    key restringified — would produce a device file nobody could tell apart
    from the right one until the air conditioner did the wrong thing.
    """
    client = await hass_ws_client(hass)

    direct = await send(
        client,
        {
            "type": "hub_ir/save",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
            "spec": CLIMATE_SPEC,
            "codes": CLIMATE_CODES,
        },
    )
    assert direct["success"]
    expected = json.loads(
        (panel / "codes" / "climate" / f"{CUSTOM_CODE_START}.json").read_text("utf-8")
    )

    await send(client, draft_message(device_code=CUSTOM_CODE_START + 1))
    got = await send(
        client,
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START + 1,
        },
    )
    draft = got["result"]["draft"]

    resumed = await send(
        client,
        {
            "type": "hub_ir/save",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START + 1,
            "spec": draft["spec"],
            "codes": draft["codes"],
        },
    )
    assert resumed["success"]
    actual = json.loads(
        (panel / "codes" / "climate" / f"{CUSTOM_CODE_START + 1}.json").read_text(
            "utf-8"
        )
    )

    assert actual == expected


async def test_a_drafts_spec_still_builds_a_plan(
    hass: HomeAssistant, panel, hass_ws_client
) -> None:
    """Resuming rebuilds the plan from the spec, so the spec has to survive.

    The plan is deliberately not stored: keeping it would let an old draft
    revive a list of cells the current version no longer agrees with.
    """
    client = await hass_ws_client(hass)
    await send(client, draft_message())

    got = await send(
        client,
        {
            "type": "hub_ir/draft_get",
            "platform": "climate",
            "device_code": CUSTOM_CODE_START,
        },
    )

    planned = await send(
        client,
        {
            "type": "hub_ir/plan",
            "platform": "climate",
            "spec": got["result"]["draft"]["spec"],
        },
    )

    assert planned["result"]["total"] == CLIMATE_PLAN_SIZE
    assert {cell["key"] for cell in planned["result"]["cells"]} >= set(CLIMATE_CODES)
