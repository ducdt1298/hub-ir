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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import Helper, _write_atomic, codes_dir, device_file_path
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
@websocket_api.websocket_command({vol.Required("type"): "broadlink_ir/info"})
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
        vol.Required("type"): "broadlink_ir/plan",
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
        vol.Required("type"): "broadlink_ir/list",
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
        vol.Required("type"): "broadlink_ir/get",
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
        vol.Required("type"): "broadlink_ir/save",
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
        vol.Required("type"): "broadlink_ir/learn",
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
        vol.Required("type"): "broadlink_ir/send",
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
