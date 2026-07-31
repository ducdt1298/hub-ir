"""Broadlink IR — Broadlink-only fork.

Controls climate, fan, light and media_player devices over IR/RF using a
Broadlink universal remote and a JSON database of device codes.
"""

from __future__ import annotations

import binascii
import json
import logging
import os
import struct
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "broadlink_ir"
VERSION = "2.0.0"

COMPONENT_ABS_DIR = os.path.dirname(os.path.abspath(__file__))

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

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_CHECK_UPDATES): cv.boolean,
                vol.Optional(CONF_UPDATE_BRANCH): cv.string,
            }
        )
    },
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

    return True


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
        device_dir = os.path.join(COMPONENT_ABS_DIR, "codes", platform)
        device_path = os.path.join(device_dir, f"{device_code}.json")

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

        return device_data

    @staticmethod
    async def downloader(hass: HomeAssistant, source: str, dest: str) -> None:
        """Download ``source`` to ``dest``, leaving no partial file behind."""
        session = async_get_clientsession(hass)
        try:
            async with session.get(source) as response:
                if response.status != 200:
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
        return [int(round(code / frequency)) for code in codes[4:]]

    @staticmethod
    def lirc2broadlink(pulses: list[int]) -> bytearray:
        """Convert LIRC pulse timings to a Broadlink IR packet."""
        array = bytearray()

        for pulse in pulses:
            pulse = int(pulse * 269 / 8192)

            if pulse < 256:
                array += bytearray(struct.pack(">B", pulse))
            else:
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
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
