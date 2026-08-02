"""Websocket commands behind the learning panel.

Everything the panel does that needs the server lives here: listing remotes and
device files, learning a code, testing one, and writing a device file. All of it
is admin-only, because saving writes into the configuration directory.

The panel is deliberately thin, so decisions that can be tested — what to
capture and in what order, whether a file is valid, which device code is free —
are made here rather than in JavaScript.
"""

from __future__ import annotations

import json
import os
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType, UnknownHandler
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv

from . import (
    DOMAIN,
    Helper,
    _write_atomic,
    codes_dir,
    device_file_path,
    remote_entity_id,
)
from .const import CONF_CONTROLLER_DATA, CONF_DEVICE_CODE, CONF_PLATFORM, SOURCE_PANEL
from .controller import BROADLINK_CONTROLLER, get_controller
from .device_file import (
    CUSTOM_CODE_START,
    PLATFORMS,
    build_device_file,
    capture_plan,
    codes_from_device_file,
    spec_from_device_file,
    validate,
)
from .learn import async_learn_ir_code

PLATFORM_SELECTOR = vol.In(PLATFORMS)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register every panel command."""
    for handler in (
        ws_info,
        ws_plan,
        ws_list,
        ws_get,
        ws_save,
        ws_learn,
        ws_send,
        ws_create_entity,
    ):
        websocket_api.async_register_command(hass, handler)


def _local_codes(platform: str) -> list[int]:
    """Return the device codes already present on disk for a platform."""
    directory = codes_dir(platform)
    try:
        names = os.listdir(directory)
    except OSError:
        return []

    codes = []
    for name in names:
        stem, extension = os.path.splitext(name)
        if extension == ".json" and stem.isdigit():
            codes.append(int(stem))
    return sorted(codes)


def _next_free_code(platform: str) -> int:
    """Return the next unused device code in the range reserved for the user."""
    taken = {code for code in _local_codes(platform) if code >= CUSTOM_CODE_START}
    candidate = CUSTOM_CODE_START
    while candidate in taken:
        candidate += 1
    return candidate


def _next_free_codes() -> dict[str, int]:
    """Return the next free device code for every platform. Blocking."""
    return {platform: _next_free_code(platform) for platform in PLATFORMS}


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "hub_ir/info"})
@websocket_api.async_response
async def ws_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Report the remotes that can learn and the next free device code."""
    registry = er.async_get(hass)

    # Scanning the codes directory is blocking, and Home Assistant reports
    # blocking I/O in the event loop as a warning.
    next_code = await hass.async_add_executor_job(_next_free_codes)

    remotes = []
    for state in hass.states.async_all("remote"):
        entry = registry.async_get(state.entity_id)
        remotes.append(
            {
                "entity_id": state.entity_id,
                "name": state.attributes.get("friendly_name") or state.entity_id,
                "state": state.state,
                # Only a Broadlink remote can learn into the storage this
                # integration reads back.
                "can_learn": entry is not None and entry.platform == "broadlink",
            }
        )

    connection.send_result(
        msg["id"],
        {
            "platforms": list(PLATFORMS),
            "remotes": sorted(remotes, key=lambda remote: remote["name"]),
            "custom_code_start": CUSTOM_CODE_START,
            "next_code": next_code,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/plan",
        vol.Required("platform"): PLATFORM_SELECTOR,
        vol.Required("spec"): dict,
    }
)
@callback
def ws_plan(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the ordered list of codes a spec needs, and how many that is."""
    try:
        cells = capture_plan(msg["platform"], msg["spec"])
    except (KeyError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_spec", str(err))
        return

    connection.send_result(msg["id"], {"cells": cells, "total": len(cells)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/list",
        vol.Required("platform"): PLATFORM_SELECTOR,
    }
)
@websocket_api.async_response
async def ws_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List the device files already cached for a platform."""
    platform = msg["platform"]
    codes = await hass.async_add_executor_job(_local_codes, platform)
    connection.send_result(
        msg["id"],
        {
            "codes": codes,
            "custom": [code for code in codes if code >= CUSTOM_CODE_START],
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/get",
        vol.Required("platform"): PLATFORM_SELECTOR,
        vol.Required("device_code"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def ws_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a device file, downloading it if it is not cached yet."""
    platform, device_code = msg["platform"], msg["device_code"]
    try:
        data = await Helper.load_device_data(hass, platform, device_code)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    report = validate(platform, data, str(device_code))

    # The spec and the codes are what the panel needs to carry on from here:
    # it can show what the file already provides and only capture the rest.
    try:
        spec = spec_from_device_file(platform, data)
        codes = codes_from_device_file(platform, data, spec)
    except (KeyError, ValueError) as err:
        connection.send_error(msg["id"], "unreadable_device_file", str(err))
        return

    connection.send_result(
        msg["id"],
        {
            "device_code": device_code,
            "data": data,
            "spec": spec,
            "codes": codes,
            "errors": report.errors,
            "warnings": report.warnings,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/save",
        vol.Required("platform"): PLATFORM_SELECTOR,
        vol.Required("device_code"): vol.All(int, vol.Range(min=CUSTOM_CODE_START)),
        vol.Required("spec"): dict,
        vol.Required("codes"): dict,
    }
)
@websocket_api.async_response
async def ws_save(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Assemble, validate and write a device file.

    The device code is constrained to the user's own range by the schema above,
    so saving can never overwrite one of the shipped files.
    """
    platform, device_code = msg["platform"], msg["device_code"]

    try:
        data = build_device_file(platform, msg["spec"], msg["codes"])
    except (KeyError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_spec", str(err))
        return

    report = validate(platform, data, str(device_code))
    if report.errors:
        connection.send_error(
            msg["id"], "invalid_device_file", "; ".join(report.errors)
        )
        return

    path = device_file_path(platform, device_code)
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

    def _write() -> None:
        os.makedirs(os.path.dirname(path), 0o777, exist_ok=True)
        _write_atomic(path, payload)

    try:
        await hass.async_add_executor_job(_write)
    except OSError as err:
        connection.send_error(msg["id"], "write_failed", str(err))
        return

    connection.send_result(
        msg["id"],
        {"device_code": device_code, "path": path, "warnings": report.warnings},
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/learn",
        vol.Required("remote_entity_id"): str,
        vol.Optional("toggle", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_learn(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Learn a single IR code and hand it straight back to the panel."""
    try:
        code = await async_learn_ir_code(
            hass, msg["remote_entity_id"], toggle=msg["toggle"]
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "learn_failed", str(err))
        return

    connection.send_result(msg["id"], {"code": code})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/send",
        vol.Required("remote_entity_id"): str,
        vol.Required("code"): vol.Any(str, [str]),
    }
)
@websocket_api.async_response
async def ws_send(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Transmit a code, so it can be checked before it is kept."""
    controller = get_controller(
        hass, BROADLINK_CONTROLLER, "Base64", msg["remote_entity_id"], 0.5
    )
    try:
        await controller.send(msg["code"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "send_failed", str(err))
        return

    connection.send_result(msg["id"], {"sent": True})


def _matching_entry(hass: HomeAssistant, msg: dict[str, Any]) -> ConfigEntry | None:
    """Return the config entry that already describes this device, if any."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            entry.data.get(CONF_PLATFORM) == msg["platform"]
            and entry.data.get(CONF_DEVICE_CODE) == msg["device_code"]
            and entry.options.get(CONF_CONTROLLER_DATA) == msg["controller_data"]
        ):
            return entry
    return None


def _entity_id_for(hass: HomeAssistant, entry_id: str) -> str | None:
    """Return the entity a config entry produced, if it has appeared yet."""
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    return entities[0].entity_id if entities else None


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hub_ir/create_entity",
        vol.Required("platform"): PLATFORM_SELECTOR,
        vol.Required("device_code"): vol.All(int, vol.Range(min=0)),
        vol.Required("controller_data"): remote_entity_id,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1)),
    }
)
@websocket_api.async_response
async def ws_create_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create the entity for a device file the panel has just written.

    The last step that used to mean leaving the browser. Everything the config
    flow asks for was settled while learning, so the flow is started here with
    the answers already filled in rather than sending someone to Settings to
    type them a second time. Deciding it on this side also keeps the flow's step
    names out of the JavaScript, where nothing tests them.
    """
    data = {
        CONF_PLATFORM: msg["platform"],
        CONF_DEVICE_CODE: msg["device_code"],
        CONF_CONTROLLER_DATA: msg["controller_data"],
        CONF_NAME: msg["name"],
    }

    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_PANEL}, data=data
        )
    except UnknownHandler:
        connection.send_error(
            msg["id"],
            "no_config_flow",
            "This version of HubIR cannot add entities from the panel. Add the "
            "device to configuration.yaml instead and restart",
        )
        return
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "create_failed", str(err))
        return

    if result["type"] is FlowResultType.CREATE_ENTRY:
        entry = result["result"]
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                # None only while the platform is still adding the entity; the
                # panel falls back to naming the entry.
                "entity_id": _entity_id_for(hass, entry.entry_id),
                "existing": False,
            },
        )
        return

    if result["type"] is FlowResultType.ABORT:
        # Teaching a few more codes to a device that is already set up is the
        # second most common way through this panel. The entry is right; only
        # its device file has changed. Reload it and report success, rather than
        # an error the user can do nothing useful with.
        if (entry := _matching_entry(hass, msg)) is not None:
            await hass.config_entries.async_reload(entry.entry_id)
            connection.send_result(
                msg["id"],
                {
                    "entry_id": entry.entry_id,
                    "title": entry.title,
                    "entity_id": _entity_id_for(hass, entry.entry_id),
                    "existing": True,
                },
            )
            return

        connection.send_error(
            msg["id"], "create_failed", _abort_message(result.get("reason", ""))
        )
        return

    connection.send_error(
        msg["id"],
        "create_failed",
        "The HubIR config flow asked for more than the panel can answer. Add "
        "the device from Settings instead",
    )


# The abort reasons the flow can return, in words the panel can show. Keyed by
# the same strings as translations/en.json, but the panel has no access to a
# translated flow result, so they are spelled out again here.
_ABORT_MESSAGES = {
    "already_configured": (
        "There is already a HubIR entity for this device code and remote"
    ),
    "cannot_load_device_file": (
        "The device file could not be read back after saving it"
    ),
    "invalid_device_file": "The saved device file cannot produce a working entity",
    "unsupported_controller": (
        "That device file was recorded for a different kind of hub"
    ),
    "unsupported_encoding": (
        "That device file stores its codes in an encoding this integration cannot send"
    ),
    "no_operation_modes": (
        "That device file lists no operation mode Home Assistant recognises"
    ),
    "no_fan_speeds": "That device file lists no fan speeds",
    "remote_not_found": "That remote entity does not exist",
}


def _abort_message(reason: str) -> str:
    """Return something a person can act on for a flow abort reason."""
    return _ABORT_MESSAGES.get(reason) or f"The entity could not be created: {reason}"
