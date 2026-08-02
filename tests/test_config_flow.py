"""Tests for creating and editing HubIR entities from the UI.

The point of the config flow is that adding a device no longer means SSH, a text
editor and a restart. These tests hold that promise to its word: a device code
that cannot work is rejected in the form, a created entity really transmits, and
changing which Broadlink it sits in front of takes effect without a restart.

They also pin the two things that must *not* change: a YAML platform still
behaves exactly as it did, and still gets no device.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hub_ir import DOMAIN
from custom_components.hub_ir.device_file import PLATFORMS
from custom_components.hub_ir.validation import ERROR_KEYS
from custom_components.hub_ir.websocket import _ABORT_MESSAGES
from homeassistant import config_entries, data_entry_flow
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    HVACMode,
)
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerDeviceClass,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from .conftest import (
    CLIMATE_DEVICE_DATA,
    FAN_DEVICE_DATA,
    LIGHT_DEVICE_DATA,
    MEDIA_PLAYER_DEVICE_DATA,
    SWITCH_DEVICE_DATA,
    get_entity,
    payloads,
)

REMOTE = "remote.broadlink"
OTHER_REMOTE = "remote.rm4"

# (menu option, entity domain, device data, the device code the tests use)
PLATFORM_CASES = [
    ("climate", CLIMATE_DOMAIN, CLIMATE_DEVICE_DATA, 9700),
    ("fan", FAN_DOMAIN, FAN_DEVICE_DATA, 9701),
    ("light", LIGHT_DOMAIN, LIGHT_DEVICE_DATA, 9702),
    ("media_player", MEDIA_PLAYER_DOMAIN, MEDIA_PLAYER_DEVICE_DATA, 9703),
    ("switch", SWITCH_DOMAIN, SWITCH_DEVICE_DATA, 9704),
]


@pytest.fixture
async def component(hass: HomeAssistant, codes_dir, sent_commands):
    """Load hub_ir with no YAML at all, the way a UI-only user has it."""
    with patch("custom_components.hub_ir.frontend.async_register_panel"):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    return codes_dir


@pytest.fixture
def start_flow(hass: HomeAssistant, component, write_device_file):
    """Return a helper that walks the create flow for one platform."""

    async def _start(
        platform: str,
        device_code: int,
        device_data: dict[str, Any] | None = None,
        *,
        name: str = "Test Thing",
        controller_data: str = REMOTE,
        extra: dict[str, Any] | None = None,
    ):
        if device_data is not None:
            write_device_file(platform, device_code, device_data)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": platform}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": name,
                # NumberSelector submits a float, which is exactly what makes
                # the int() in _split necessary.
                "device_code": float(device_code),
                "controller_data": controller_data,
                "delay": 0.5,
                **(extra or {}),
            },
        )
        await hass.async_block_till_done()
        return result

    return _start


@pytest.fixture
def add_entry(hass: HomeAssistant, component, write_device_file):
    """Return a helper that sets a HubIR entity up straight from an entry."""

    async def _add(
        platform: str,
        device_code: int,
        device_data: dict[str, Any],
        *,
        title: str = "Test Thing",
        options: dict[str, Any] | None = None,
    ) -> MockConfigEntry:
        write_device_file(platform, device_code, device_data)
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=title,
            unique_id=f"{platform}:{device_code}:{REMOTE}",
            data={
                "platform": platform,
                "device_code": device_code,
                "name": title,
            },
            options={"controller_data": REMOTE, "delay": 0.5, **(options or {})},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _add


# --------------------------------------------------------------------------
# The create flow
# --------------------------------------------------------------------------


async def test_the_first_step_offers_every_platform(
    hass: HomeAssistant, component
) -> None:
    """One integration, several kinds of device: the menu is how you pick.

    Derived from PLATFORMS rather than listed, so a platform added later has to
    appear in the menu instead of quietly having no way in.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert set(result["menu_options"]) == set(PLATFORMS)


@pytest.mark.parametrize(("platform", "domain", "data", "code"), PLATFORM_CASES)
async def test_adding_a_device_creates_an_entity(
    hass: HomeAssistant, start_flow, platform, domain, data, code
) -> None:
    """The whole point: an entity, from the browser, with no restart."""
    result = await start_flow(platform, code, data, name="Bedroom Thing")

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY, result
    entry = result["result"]
    assert entry.title == "Bedroom Thing"
    assert entry.unique_id == f"{platform}:{code}:{REMOTE}"

    # Identity in data, settings in options: only the second is editable later.
    assert entry.data == {
        "platform": platform,
        "device_code": code,
        "name": "Bedroom Thing",
    }
    assert entry.options["controller_data"] == REMOTE
    assert entry.options["delay"] == 0.5

    assert hass.states.get(f"{domain}.bedroom_thing") is not None


async def test_the_device_code_is_stored_as_a_whole_number(
    hass: HomeAssistant, start_flow
) -> None:
    """NumberSelector submits a float, and 9700.0 asks for `9700.0.json`."""
    result = await start_flow("climate", 9700, CLIMATE_DEVICE_DATA)

    stored = result["result"].data["device_code"]
    assert stored == 9700
    assert isinstance(stored, int), f"stored as {type(stored).__name__}"


async def test_the_same_device_on_the_same_remote_is_refused(
    hass: HomeAssistant, start_flow
) -> None:
    """Two entities fighting over one device is never what anyone wanted."""
    assert (await start_flow("climate", 9700, CLIMATE_DEVICE_DATA))[
        "type"
    ] is data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await start_flow("climate", 9700)

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_the_same_device_on_another_remote_is_allowed(
    hass: HomeAssistant, start_flow
) -> None:
    """Two identical air conditioners in two rooms are two entities."""
    await start_flow("climate", 9700, CLIMATE_DEVICE_DATA, name="Bedroom")
    result = await start_flow(
        "climate", 9700, name="Study", controller_data=OTHER_REMOTE
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_a_device_code_that_cannot_be_fetched_is_reported_in_the_form(
    hass: HomeAssistant, start_flow
) -> None:
    """A typo in the device code must not leave a broken entity behind."""
    with patch(
        "custom_components.hub_ir.Helper.downloader",
        side_effect=HomeAssistantError("Got HTTP 404"),
    ):
        result = await start_flow("climate", 9799)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_load_device_file"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("platform", "code", "overrides", "expected"),
    [
        ("climate", 9710, {"supportedController": "Xiaomi"}, "unsupported_controller"),
        ("climate", 9711, {"commandsEncoding": "Wobble"}, "unsupported_encoding"),
        ("climate", 9712, {"operationModes": ["banana"]}, "no_operation_modes"),
        ("fan", 9713, {"speed": []}, "no_fan_speeds"),
    ],
)
async def test_a_device_file_that_cannot_work_is_reported_in_the_form(
    hass: HomeAssistant, start_flow, platform, code, overrides, expected
) -> None:
    """Everything that would blow up in the constructor is caught first."""
    base = CLIMATE_DEVICE_DATA if platform == "climate" else FAN_DEVICE_DATA
    result = await start_flow(platform, code, {**base, **overrides})

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_a_device_file_missing_required_keys_is_reported_in_the_form(
    hass: HomeAssistant, start_flow
) -> None:
    """A hand-written file without fanModes would fail at the first command."""
    incomplete = {
        key: value for key, value in CLIMATE_DEVICE_DATA.items() if key != "fanModes"
    }
    result = await start_flow("climate", 9714, incomplete)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_device_file"}


async def test_a_remote_that_does_not_exist_is_reported_in_the_form(
    hass: HomeAssistant, start_flow
) -> None:
    """A wrong controller_data is silent at runtime, so catch it here."""
    result = await start_flow(
        "climate", 9700, CLIMATE_DEVICE_DATA, controller_data="remote.nonexistent"
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "remote_not_found"}


async def test_a_corrected_device_code_still_goes_through(
    hass: HomeAssistant, component, write_device_file
) -> None:
    """An error must not poison the flow: the second try has to work."""
    write_device_file("climate", 9700, CLIMATE_DEVICE_DATA)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "climate"}
    )
    with patch(
        "custom_components.hub_ir.Helper.downloader",
        side_effect=HomeAssistantError("Got HTTP 404"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Second Try",
                "device_code": 9799.0,
                "controller_data": REMOTE,
                "delay": 0.5,
            },
        )
    assert result["errors"] == {"base": "cannot_load_device_file"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Second Try",
            "device_code": 9700.0,
            "controller_data": REMOTE,
            "delay": 0.5,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert hass.states.get("climate.second_try") is not None


# --------------------------------------------------------------------------
# The panel's shortcut into the flow
# --------------------------------------------------------------------------


async def test_the_panel_source_creates_an_entry_without_a_form(
    hass: HomeAssistant, component, write_device_file
) -> None:
    """The panel already knows every answer, so it must not be asked again."""
    write_device_file("climate", 90000, CLIMATE_DEVICE_DATA)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "panel"},
        data={
            "platform": "climate",
            "device_code": 90000,
            "controller_data": REMOTE,
            "name": "Learned AC",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["result"].data["device_code"] == 90000
    assert hass.states.get("climate.learned_ac") is not None


async def test_the_panel_source_aborts_on_a_device_file_that_is_not_there(
    hass: HomeAssistant, component
) -> None:
    """A failed create must leave no half-made entry for the user to find."""
    with patch(
        "custom_components.hub_ir.Helper.downloader",
        side_effect=HomeAssistantError("Got HTTP 404"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "panel"},
            data={
                "platform": "climate",
                "device_code": 90001,
                "controller_data": REMOTE,
                "name": "Missing",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_load_device_file"
    assert not hass.config_entries.async_entries(DOMAIN)


# --------------------------------------------------------------------------
# Registry, device and behaviour
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("platform", "domain", "data", "code"), PLATFORM_CASES)
async def test_a_config_entry_entity_gets_a_device(
    hass: HomeAssistant, add_entry, platform, domain, data, code
) -> None:
    """The thing YAML cannot do: group the entity under a real device.

    The other half is tests/test_registry_and_units.py, which pins that a YAML
    entity still gets none.
    """
    entry = await add_entry(platform, code, data, title="Grouped Thing")

    entity_entry = er.async_get(hass).async_get(f"{domain}.grouped_thing")
    assert entity_entry is not None
    assert entity_entry.unique_id == entry.entry_id
    assert entity_entry.config_entry_id == entry.entry_id
    assert entity_entry.device_id is not None

    device = dr.async_get(hass).async_get(entity_entry.device_id)
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.name == "Grouped Thing"
    assert device.manufacturer == "Test"
    assert device.model == data["supportedModels"][0]


async def test_a_device_file_listing_many_models_names_only_the_first(
    hass: HomeAssistant, add_entry
) -> None:
    """One shipped file lists 34 models; all of them make the page unreadable."""
    many = {**CLIMATE_DEVICE_DATA, "supportedModels": ["M1", "M2", "M3"]}
    entry = await add_entry("climate", 9720, many)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device.model == "M1 (+2 more)"


async def test_an_entity_created_from_an_entry_actually_transmits(
    hass: HomeAssistant, add_entry, sent_commands
) -> None:
    """A registry entry proves nothing if no infrared ever leaves the box."""
    await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Sending AC")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.sending_ac", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    assert payloads(sent_commands) == [["b64:Y29vbDE2"]]
    assert sent_commands[-1].data[ATTR_ENTITY_ID] == REMOTE


async def test_a_media_player_entry_uses_its_device_class(
    hass: HomeAssistant, add_entry
) -> None:
    """The options flow stores a string; the entity wants the enum."""
    await add_entry("media_player", 9703, MEDIA_PLAYER_DEVICE_DATA, title="Telly")
    assert (
        get_entity(hass, "media_player.telly").device_class is MediaPlayerDeviceClass.TV
    )

    await add_entry(
        "media_player",
        9704,
        MEDIA_PLAYER_DEVICE_DATA,
        title="Amp",
        options={"device_class": "receiver"},
    )
    assert (
        get_entity(hass, "media_player.amp").device_class
        is MediaPlayerDeviceClass.RECEIVER
    )


async def test_an_entity_from_an_entry_can_be_renamed(
    hass: HomeAssistant, add_entry
) -> None:
    """It always has a unique_id now, which is what renaming needs."""
    await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Before")

    er.async_get(hass).async_update_entity("climate.before", name="After")
    assert er.async_get(hass).async_get("climate.before").name == "After"


# --------------------------------------------------------------------------
# The options flow — this is where "no restart" is proved
# --------------------------------------------------------------------------


async def _set_options(hass: HomeAssistant, entry, options: dict[str, Any]):
    """Run the options flow to completion and let the reload finish."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )
    await hass.async_block_till_done()
    return result


async def test_changing_the_remote_takes_effect_without_a_restart(
    hass: HomeAssistant, add_entry, sent_commands
) -> None:
    """The reason this feature exists: moving a device to another Broadlink."""
    entry = await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Movable")

    await _set_options(hass, entry, {"controller_data": OTHER_REMOTE, "delay": 0.5})

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.movable", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    assert sent_commands[-1].data[ATTR_ENTITY_ID] == OTHER_REMOTE
    assert entry.unique_id == f"climate:9700:{OTHER_REMOTE}"


async def test_changing_the_delay_takes_effect_without_a_restart(
    hass: HomeAssistant, add_entry, sent_commands
) -> None:
    """Same mechanism, and the one people tune when a device misses codes."""
    entry = await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Slow")

    await _set_options(hass, entry, {"controller_data": REMOTE, "delay": 1.5})

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.slow", ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )

    assert sent_commands[-1].data["delay_secs"] == 1.5


async def test_a_temperature_sensor_can_be_added_and_removed_again(
    hass: HomeAssistant, add_entry
) -> None:
    """Clearing a picker must clear the option, not store an empty string."""
    entry = await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Sensed")
    hass.states.async_set("sensor.room", "21.5")

    await _set_options(
        hass,
        entry,
        {
            "controller_data": REMOTE,
            "delay": 0.5,
            "temperature_sensor": "sensor.room",
        },
    )
    assert hass.states.get("climate.sensed").attributes["current_temperature"] == 21.5

    await _set_options(hass, entry, {"controller_data": REMOTE, "delay": 0.5})

    assert "temperature_sensor" not in entry.options
    assert (
        hass.states.get("climate.sensed").attributes.get("current_temperature") is None
    )


async def test_source_names_can_be_changed_from_the_options_flow(
    hass: HomeAssistant, add_entry
) -> None:
    """The one option with no sensible form field, so it lives here as a map."""
    entry = await add_entry("media_player", 9703, MEDIA_PLAYER_DEVICE_DATA, title="Box")

    await _set_options(
        hass,
        entry,
        {
            "controller_data": REMOTE,
            "delay": 0.5,
            "device_class": "tv",
            "source_names": {"HDMI1": "DVD", "Channel 2": None},
        },
    )

    # A rename appends the new key and drops the old one, so DVD moves to the
    # end. That is the order the dropdown will show.
    assert hass.states.get("media_player.box").attributes["source_list"] == [
        "Channel 1",
        "DVD",
    ]


async def test_the_options_flow_refuses_a_remote_another_entry_already_uses(
    hass: HomeAssistant, add_entry
) -> None:
    """Moving one entity onto another's remote would duplicate the device."""
    await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="First")
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second",
        unique_id=f"climate:9700:{OTHER_REMOTE}",
        data={"platform": "climate", "device_code": 9700, "name": "Second"},
        options={"controller_data": OTHER_REMOTE, "delay": 0.5},
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    result = await _set_options(hass, second, {"controller_data": REMOTE, "delay": 0.5})

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}
    assert second.options["controller_data"] == OTHER_REMOTE


async def test_the_options_flow_refuses_a_remote_that_does_not_exist(
    hass: HomeAssistant, add_entry
) -> None:
    """Saving a typo here would silently stop the entity working."""
    entry = await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Typo")

    result = await _set_options(
        hass, entry, {"controller_data": "remote.gone", "delay": 0.5}
    )

    assert result["errors"] == {"base": "remote_not_found"}
    assert entry.options["controller_data"] == REMOTE


# --------------------------------------------------------------------------
# Lifecycle and failure
# --------------------------------------------------------------------------


async def test_unloading_an_entry_makes_its_entity_unavailable(
    hass: HomeAssistant, add_entry
) -> None:
    """And setting it up again brings the entity back.

    An entity with a unique_id has a registry entry, so unloading its platform
    does not remove the state: Home Assistant leaves an 'unavailable'
    placeholder carrying restored: True. Asserting the state disappears passes
    over the half of this test that matters.
    """
    entry = await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="Toggle")
    assert hass.states.get("climate.toggle") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("climate.toggle")
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes.get("restored") is True

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("climate.toggle")
    assert state.state != STATE_UNAVAILABLE
    assert "restored" not in state.attributes


def _sensor_listeners(hass: HomeAssistant, entity_id: str) -> int:
    """Return how many callbacks are tracking an entity's state changes.

    Reaches into Home Assistant's own tracker because the leak has no other
    symptom: a listener nobody cancelled keeps firing into a removed entity,
    which is silent until it writes a state that no longer has an owner.
    """
    tracker = hass.data.get("track_state_change_data")
    return len(tracker.callbacks.get(entity_id, [])) if tracker else 0


async def test_reloading_an_entry_does_not_stack_sensor_listeners(
    hass: HomeAssistant, add_entry
) -> None:
    """The options flow reloads on every Save, so a leak here grows all day."""
    hass.states.async_set("sensor.room", "21")
    entry = await add_entry(
        "climate",
        9700,
        CLIMATE_DEVICE_DATA,
        options={
            "controller_data": REMOTE,
            "delay": 0.5,
            "temperature_sensor": "sensor.room",
            "power_sensor": "binary_sensor.ac",
        },
    )

    assert _sensor_listeners(hass, "sensor.room") == 1

    for _ in range(3):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert _sensor_listeners(hass, "sensor.room") == 1
    assert _sensor_listeners(hass, "binary_sensor.ac") == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _sensor_listeners(hass, "sensor.room") == 0
    assert _sensor_listeners(hass, "binary_sensor.ac") == 0


async def test_removing_an_entry_removes_its_device(
    hass: HomeAssistant, add_entry
) -> None:
    """Deleting the integration entry must not leave an orphan device behind."""
    entry = await add_entry("climate", 9700, CLIMATE_DEVICE_DATA)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        is None
    )


async def test_a_device_file_that_cannot_be_downloaded_retries_later(
    hass: HomeAssistant, component, write_device_file
) -> None:
    """A network that is not up yet is a reason to wait, not to give up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Offline",
        data={"platform": "climate", "device_code": 9790, "name": "Offline"},
        options={"controller_data": REMOTE, "delay": 0.5},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hub_ir.Helper.downloader",
        side_effect=HomeAssistantError("Got HTTP 500"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_device_file_that_is_broken_does_not_retry(
    hass: HomeAssistant, component, write_device_file
) -> None:
    """Retrying a file that is present and wrong just repeats the same failure."""
    incomplete = {
        key: value for key, value in CLIMATE_DEVICE_DATA.items() if key != "fanModes"
    }
    write_device_file("climate", 9791, incomplete)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Broken",
        data={"platform": "climate", "device_code": 9791, "name": "Broken"},
        options={"controller_data": REMOTE, "delay": 0.5},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_an_entry_loads_even_when_its_remote_is_not_up_yet(
    hass: HomeAssistant, component, write_device_file
) -> None:
    """At boot the Broadlink may load after us; the entry must not flap."""
    write_device_file("climate", 9700, CLIMATE_DEVICE_DATA)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Early",
        data={"platform": "climate", "device_code": 9700, "name": "Early"},
        options={"controller_data": "remote.not_yet", "delay": 0.5},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.early") is not None


# --------------------------------------------------------------------------
# Coexistence with YAML, and the panel bootstrap
# --------------------------------------------------------------------------


async def test_a_yaml_entity_and_an_entry_entity_live_side_by_side(
    hass: HomeAssistant, add_entry, write_device_file, setup_platform
) -> None:
    """Adding the config flow must not change what YAML users already have.

    The YAML platform is set up first on purpose. Adding the entry forwards to
    the climate platform, which puts 'climate' into hass.config.components, and
    async_setup_component then returns True without reading the config it was
    handed — so the YAML entity would never be created at all.
    """
    write_device_file("climate", 9705, CLIMATE_DEVICE_DATA)
    await setup_platform(
        CLIMATE_DOMAIN,
        {
            "name": "From YAML",
            "unique_id": "from_yaml",
            "device_code": 9705,
            "controller_data": REMOTE,
        },
    )
    # Fails loudly here rather than at an unrelated AttributeError below if
    # async_setup_component ever short-circuits again.
    assert hass.states.get("climate.from_yaml") is not None

    await add_entry("climate", 9700, CLIMATE_DEVICE_DATA, title="From UI")

    registry = er.async_get(hass)
    assert registry.async_get("climate.from_yaml").device_id is None
    assert registry.async_get("climate.from_ui").device_id is not None


def test_every_error_the_flow_can_report_is_translated() -> None:
    """An untranslated key shows up in the dialog as `cannot_load_device_file`.

    The keys are raised in validation.py and surface through the config flow,
    the options flow and the panel's own error box, so all four files have to
    agree on them.
    """
    package = Path(__file__).resolve().parent.parent / "custom_components" / "hub_ir"
    strings = json.loads((package / "strings.json").read_text(encoding="utf-8"))
    raised = set(ERROR_KEYS)

    for section in (strings["config"]["error"], strings["options"]["error"]):
        assert raised <= section.keys(), raised - section.keys()

    # The panel step reports the same keys as abort reasons, plus the duplicate.
    aborts = raised | {"already_configured"}
    assert aborts <= strings["config"]["abort"].keys()

    # And the panel spells them out again, because a flow result carries the
    # key rather than the translated sentence.
    assert aborts <= _ABORT_MESSAGES.keys(), aborts - _ABORT_MESSAGES.keys()


async def test_the_panel_comes_up_without_any_yaml(
    hass: HomeAssistant, write_device_file, sent_commands
) -> None:
    """A fresh install used to need a `hub_ir:` line just to see the sidebar.

    Home Assistant sets a component up before setting up its config entries, so
    async_setup still runs — and that is what registers the panel and the
    websocket commands.
    """
    with patch(
        "custom_components.hub_ir.frontend.async_register_panel"
    ) as register_panel:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Only Entry",
            data={"platform": "climate", "device_code": 9700, "name": "Only Entry"},
            options={"controller_data": REMOTE, "delay": 0.5},
        )
        entry.add_to_hass(hass)
        write_device_file("climate", 9700, CLIMATE_DEVICE_DATA)

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert register_panel.called
    assert DOMAIN in hass.config.components
