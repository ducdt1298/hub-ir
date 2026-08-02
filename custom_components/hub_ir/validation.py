"""Pre-flight checks shared by the config flow and config-entry setup.

Everything that can make an entity blow up in its constructor is checked here
first, so that a device code which cannot work is reported in the form the user
is looking at rather than as a traceback in the log a minute later.

This lives in its own module rather than in ``__init__.py`` because it needs
``.controller``, and ``controller.py`` imports ``Helper`` back out of
``__init__.py`` — importing it at package scope would be a genuine cycle.
"""

from __future__ import annotations

import os
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import Helper, device_file_path
from .controller import BROADLINK_CONTROLLER, get_controller
from .device_file import HVAC_MODES, PLATFORM_KEYS, is_recorded

# Every reason this module can refuse a device code. Declared rather than
# discovered, so the translations can be checked against the list itself: one of
# these is raised from inside a conditional expression, where any regular
# expression over the source would quietly miss it.
ERROR_KEYS = (
    "cannot_load_device_file",
    "invalid_device_file",
    "no_fan_speeds",
    "no_operation_modes",
    "no_switch_commands",
    "remote_not_found",
    "unsupported_controller",
    "unsupported_encoding",
)


class DeviceFileError(HomeAssistantError):
    """A device code that cannot produce a working entity."""

    def __init__(
        self, error_key: str, message: str, *, transient: bool = False
    ) -> None:
        """Record the translation key to show and whether retrying can help."""
        assert error_key in ERROR_KEYS, f"undeclared error key {error_key!r}"
        super().__init__(message)
        self.error_key = error_key
        self.transient = transient


async def async_validate_device(
    hass: HomeAssistant,
    platform: str,
    device_code: int,
    controller_data: str,
    *,
    check_remote: bool = False,
) -> dict[str, Any]:
    """Return the device data for a code, or say why it cannot be used.

    Set ``check_remote`` when a human is waiting for the answer. At startup the
    Broadlink integration may not have created its remote entity yet, and
    failing there would make config entries flap on every restart; at runtime
    ``BroadlinkController`` already refuses to send through a remote that is
    missing or unavailable.
    """
    if check_remote and hass.states.get(controller_data) is None:
        raise DeviceFileError(
            "remote_not_found",
            f"The remote entity {controller_data} does not exist",
        )

    try:
        device_data = await Helper.load_device_data(hass, platform, device_code)
    except HomeAssistantError as err:
        # Helper.downloader writes atomically and removes its temporary file on
        # failure, so nothing on disk means the download is what failed, and
        # that is worth retrying. A file that is present and still rejected will
        # be rejected again next time.
        present = await hass.async_add_executor_job(
            os.path.exists, device_file_path(platform, device_code)
        )
        raise DeviceFileError(
            "invalid_device_file" if present else "cannot_load_device_file",
            str(err),
            transient=not present,
        ) from err

    if missing := sorted(PLATFORM_KEYS[platform] - device_data.keys()):
        raise DeviceFileError(
            "invalid_device_file",
            f"The device file for {platform} code {device_code} is missing "
            f"{', '.join(missing)}",
        )

    if device_data["supportedController"] != BROADLINK_CONTROLLER:
        raise DeviceFileError(
            "unsupported_controller",
            f"Device code {device_code} was recorded for "
            f"'{device_data['supportedController']}', not {BROADLINK_CONTROLLER}",
        )

    # Builds the controller the entity will build, so an encoding this fork
    # cannot send is caught by the code that would have to send it. The delay is
    # not asked for: it plays no part in whether a controller can be built, and
    # every value the options flow accepts is a valid one.
    try:
        get_controller(
            hass,
            device_data["supportedController"],
            device_data["commandsEncoding"],
            controller_data,
            0.0,
        )
    except HomeAssistantError as err:
        raise DeviceFileError("unsupported_encoding", str(err)) from err

    if platform == "climate":
        # Mirrors climate.py's own check, against device_file's inlined copy of
        # HVAC_MODES, so validation and runtime cannot disagree.
        usable = [
            mode
            for mode in (device_data.get("operationModes") or [])
            if mode in HVAC_MODES
        ]
        if not usable:
            raise DeviceFileError(
                "no_operation_modes",
                f"Device code {device_code} lists no usable operationModes",
            )

    if platform == "fan" and not device_data.get("speed"):
        raise DeviceFileError(
            "no_fan_speeds", f"Device code {device_code} lists no fan speeds"
        )

    if platform == "switch":
        # Mirrors switch.py's own constructor check: a switch with none of the
        # three power codes could never do anything at all.
        commands = device_data.get("commands") or {}
        if not any(is_recorded(commands.get(name)) for name in ("on", "off", "toggle")):
            raise DeviceFileError(
                "no_switch_commands",
                f"Device code {device_code} records no on, off or toggle code",
            )

    return device_data
