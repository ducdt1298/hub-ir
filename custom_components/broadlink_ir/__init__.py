"""Broadlink IR — Broadlink-only fork.

Controls climate, fan, light and media_player devices over IR/RF using a
Broadlink universal remote and a JSON database of device codes.
"""

from __future__ import annotations

import binascii
from collections.abc import AsyncIterator
import contextlib
from http import HTTPStatus
import json
import logging
import os
import struct
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

# Re-exported: the device-file rules live in a module free of Home Assistant so
# that scripts/validate_codes.py can share them, but the platforms import them
# from here.
from .device_file import has_any_code, is_recorded  # noqa: F401

_LOGGER = logging.getLogger(__name__)

DOMAIN = "broadlink_ir"
VERSION = "2.0.0"

COMPONENT_ABS_DIR = os.path.dirname(os.path.abspath(__file__))

# In a Broadlink IR packet a pulse below this is one byte; at or above it the
# pulse is escaped with 0x00 and follows as a big-endian 16-bit value.
_SINGLE_BYTE_PULSE_LIMIT = 256

CODES_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "ducdt1298/broadlink-ir-hass/main/"
    "codes/{platform}/{device_code}.json"
)

# Options handled by the removed self-updater. Accepted so that existing
# configuration.yaml files keep validating, but they no longer do anything:
# updates are HACS' job.
CONF_CHECK_UPDATES = "check_updates"
CONF_UPDATE_BRANCH = "update_branch"
_OBSOLETE_OPTIONS = (CONF_CHECK_UPDATES, CONF_UPDATE_BRANCH)

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CHECK_UPDATES): cv.boolean,
        vol.Optional(CONF_UPDATE_BRANCH): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    # A bare `broadlink_ir:` line is how the docs tell people to enable this, and
    # YAML gives that key the value None. Accept it: validating None against a
    # dict schema fails with "expected a dictionary".
    {DOMAIN: vol.Any(_OPTIONS_SCHEMA, None)},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Broadlink IR component."""
    conf = config.get(DOMAIN) or {}

    if obsolete := [option for option in _OBSOLETE_OPTIONS if option in conf]:
        _LOGGER.warning(
            "The Broadlink IR option(s) %s no longer do anything and can be removed "
            "from configuration.yaml. The built-in self-updater was dropped; "
            "update the integration through HACS instead",
            ", ".join(obsolete),
        )

    # Imported here rather than at module scope: these pull in the http and
    # frontend stacks, and importing them while the module is first loaded would
    # drag that cost into every platform setup.
    from .frontend import async_register_panel  # noqa: PLC0415
    from .websocket import async_register  # noqa: PLC0415

    async_register(hass)
    await async_register_panel(hass)

    return True


def remote_entity_id(value: Any) -> str:
    """Validate that controller_data names a remote entity.

    A wrong domain here is silent at runtime: remote.send_command matches no
    entity and Home Assistant only logs that the reference is missing.
    """
    entity_id = cv.entity_id(value)
    if entity_id.split(".")[0] != "remote":
        raise vol.Invalid(
            f"controller_data must be the entity_id of a Broadlink remote "
            f"(remote.something), got '{entity_id}'"
        )
    return entity_id


def warn_if_no_unique_id(platform: str, config: ConfigType) -> None:
    """Warn that omitting unique_id costs the entity its registry entry.

    Home Assistant only registers entities that have one, so without it the
    entity cannot be renamed, assigned to an area, hidden, or customised in any
    way from the UI.
    """
    if config.get("unique_id"):
        return

    _LOGGER.warning(
        "The Broadlink IR %s named '%s' has no unique_id, so Home Assistant "
        "cannot register it: it cannot be renamed, assigned to an area, or "
        "customised from the UI. Add a unique_id to the platform configuration "
        "to enable those",
        platform,
        config.get(CONF_NAME),
    )


@contextlib.asynccontextmanager
async def optimistic_state(entity: Any, *attributes: str) -> AsyncIterator[None]:
    """Publish an assumed state, but only if the command was actually sent.

    IR is open-loop, so these entities assume their command took effect: there
    is no feedback to confirm it. That is what ``iot_class: assumed_state``
    means, and it is the right model.

    A send that *fails*, though, is a different thing. If the remote is
    unavailable or the code is corrupt we know nothing reached the device, so
    leaving the entity advertising the state it was asked for would be a lie.
    Restore the attributes it changed and let the error reach the caller, so the
    service call fails instead of silently doing nothing.
    """
    snapshot = {name: getattr(entity, name) for name in attributes}
    try:
        yield
    except Exception:
        for name, value in snapshot.items():
            setattr(entity, name, value)
        entity.async_write_ha_state()
        raise
    entity.async_write_ha_state()


def codes_dir(platform: str) -> str:
    """Return the directory device files for a platform are cached in."""
    return os.path.join(COMPONENT_ABS_DIR, "codes", platform)


def device_file_path(platform: str, device_code: int) -> str:
    """Return where a platform's device file lives on disk."""
    return os.path.join(codes_dir(platform), f"{device_code}.json")


class Helper:
    """Static helpers shared by the Broadlink IR platforms."""

    @staticmethod
    async def load_device_data(
        hass: HomeAssistant, platform: str, device_code: int
    ) -> dict[str, Any]:
        """Return the JSON device data for a device code.

        Looks for the file below ``custom_components/broadlink_ir/codes/<platform>``
        and downloads it from the repository if it isn't there yet. Raises
        HomeAssistantError with an actionable message on any failure.
        """
        device_dir = codes_dir(platform)
        device_path = device_file_path(platform, device_code)

        # os.path/os.makedirs and open() are blocking; HA forbids those in the
        # event loop, so every filesystem touch goes to the executor.
        content = await hass.async_add_executor_job(_read_if_exists, device_path)

        if content is None:
            source = CODES_BASE_URL.format(platform=platform, device_code=device_code)
            _LOGGER.info(
                "Device code %s for %s not found locally, downloading it from %s",
                device_code,
                platform,
                source,
            )
            await hass.async_add_executor_job(os.makedirs, device_dir, 0o777, True)
            await Helper.downloader(hass, source, device_path)
            content = await hass.async_add_executor_job(_read_if_exists, device_path)

        if content is None:
            raise HomeAssistantError(
                f"Could not read the device file for {platform} code {device_code}"
            )

        try:
            device_data = json.loads(content)
        except ValueError as err:
            raise HomeAssistantError(
                f"The device file for {platform} code {device_code} is not valid "
                f"JSON: {err}"
            ) from err

        if not isinstance(device_data, dict):
            raise HomeAssistantError(
                f"The device file for {platform} code {device_code} must contain "
                "a JSON object"
            )

        # Some files in the database are templates whose codes were never
        # captured. Setting up an entity from one gives a device that silently
        # does nothing, so refuse it here instead.
        if not has_any_code(device_data.get("commands")):
            raise HomeAssistantError(
                f"The device file for {platform} code {device_code} has no codes "
                "recorded at all, so it cannot control anything. Pick another "
                "device code or record your own"
            )

        return device_data

    @staticmethod
    async def downloader(hass: HomeAssistant, source: str, dest: str) -> None:
        """Download ``source`` to ``dest``, leaving no partial file behind."""
        session = async_get_clientsession(hass)
        try:
            async with session.get(source) as response:
                if response.status != HTTPStatus.OK:
                    raise HomeAssistantError(
                        f"Got HTTP {response.status} downloading {source}. Check "
                        "that the device code exists, or place the file manually "
                        "in the codes directory"
                    )
                payload = await response.read()
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(f"Error downloading {source}: {err}") from err

        await hass.async_add_executor_job(_write_atomic, dest, payload)

    @staticmethod
    def pronto2lirc(pronto: bytearray) -> list[int]:
        """Convert a Pronto hex code to LIRC pulse timings."""
        codes = [
            int(binascii.hexlify(pronto[i : i + 2]), 16)
            for i in range(0, len(pronto), 2)
        ]

        if codes[0]:
            raise ValueError("Pronto code should start with 0000")
        if len(codes) != 4 + 2 * (codes[2] + codes[3]):
            raise ValueError("Number of pulse widths does not match the preamble")

        frequency = 1 / (codes[1] * 0.241246)
        return [round(code / frequency) for code in codes[4:]]

    @staticmethod
    def lirc2broadlink(pulses: list[int]) -> bytearray:
        """Convert LIRC pulse timings to a Broadlink IR packet."""
        array = bytearray()

        for pulse in pulses:
            # Truncation, not rounding: this is the conversion the Broadlink
            # protocol implementations use, and it changes the emitted timings.
            # Deliberately rebinding the loop variable, so the pinned conversion
            # stays on one line and cannot drift.
            pulse = int(pulse * 269 / 8192)  # noqa: PLW2901

            if pulse < _SINGLE_BYTE_PULSE_LIMIT:
                array += bytearray(struct.pack(">B", pulse))
            else:
                # Longer pulses are escaped with 0x00 and sent as a big-endian
                # 16-bit value.
                array += bytearray([0x00])
                array += bytearray(struct.pack(">H", pulse))

        packet = bytearray([0x26, 0x00])
        packet += bytearray(struct.pack("<H", len(array)))
        packet += array
        packet += bytearray([0x0D, 0x05])

        # Pad to a multiple of 16 for 128-bit AES encryption.
        remainder = (len(packet) + 4) % 16
        if remainder:
            packet += bytearray(16 - remainder)
        return packet


def _read_if_exists(path: str) -> str | None:
    """Read a UTF-8 file, returning None when it does not exist."""
    try:
        with open(path, encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        return None


def _write_atomic(path: str, payload: bytes) -> None:
    """Write payload to path via a temporary file, so a failure leaves no stub."""
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "wb") as file:
            file.write(payload)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise
