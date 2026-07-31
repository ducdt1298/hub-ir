"""Broadlink controller used by every Broadlink IR platform."""

from __future__ import annotations

from base64 import b64encode
import binascii
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import Helper

_LOGGER = logging.getLogger(__name__)

BROADLINK_CONTROLLER = "Broadlink"

ENC_BASE64 = "Base64"
ENC_HEX = "Hex"
ENC_PRONTO = "Pronto"

BROADLINK_COMMANDS_ENCODING = (ENC_BASE64, ENC_HEX, ENC_PRONTO)


def is_recorded(command: Any) -> bool:
    """Return whether a device file entry holds an actual code.

    Parts of the device database carry empty placeholders where a code was
    never captured. Those must not be advertised or transmitted.
    """
    if isinstance(command, str):
        return bool(command.strip())
    if isinstance(command, list):
        return bool(command) and all(is_recorded(entry) for entry in command)
    return False


def get_controller(
    hass: HomeAssistant,
    controller: str,
    encoding: str,
    controller_data: str,
    delay: float,
) -> BroadlinkController:
    """Return a controller for the device file's supportedController."""
    if controller != BROADLINK_CONTROLLER:
        raise HomeAssistantError(
            f"This fork of Broadlink IR only supports the {BROADLINK_CONTROLLER} "
            f"controller, but the device file requires '{controller}'. Pick a "
            "device code whose supportedController is Broadlink"
        )

    return BroadlinkController(hass, controller, encoding, controller_data, delay)


class BroadlinkController:
    """Sends IR/RF commands through a Broadlink remote entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: str,
        encoding: str,
        controller_data: str,
        delay: float,
    ) -> None:
        """Validate the encoding and store the send parameters."""
        if encoding not in BROADLINK_COMMANDS_ENCODING:
            raise HomeAssistantError(
                f"The encoding '{encoding}' is not supported by the Broadlink "
                f"controller. Supported: {', '.join(BROADLINK_COMMANDS_ENCODING)}"
            )

        self.hass = hass
        self._controller = controller
        self._encoding = encoding
        self._controller_data = controller_data
        self._delay = delay

    async def send(self, command: str | list[str]) -> None:
        """Send one command, or a list of commands, to the remote."""
        commands = command if isinstance(command, list) else [command]

        if not is_recorded(commands):
            raise HomeAssistantError(
                "The device file has no code recorded for this command"
            )

        payload = [f"b64:{self._encode(entry)}" for entry in commands]

        await self.hass.services.async_call(
            "remote",
            "send_command",
            {
                ATTR_ENTITY_ID: self._controller_data,
                "command": payload,
                "delay_secs": self._delay,
            },
            blocking=True,
        )

    def _encode(self, command: str) -> str:
        """Return the command as the base64 payload Broadlink expects."""
        if self._encoding == ENC_BASE64:
            return command

        if self._encoding == ENC_HEX:
            try:
                return b64encode(binascii.unhexlify(command)).decode("utf-8")
            except (binascii.Error, ValueError, TypeError) as err:
                raise HomeAssistantError(
                    f"Error converting Hex to Base64 encoding: {err}"
                ) from err

        try:
            pronto = bytearray.fromhex(command.replace(" ", ""))
            pulses = Helper.pronto2lirc(pronto)
            packet = Helper.lirc2broadlink(pulses)
        except (ValueError, TypeError, IndexError, ZeroDivisionError) as err:
            raise HomeAssistantError(
                f"Error converting Pronto to Base64 encoding: {err}"
            ) from err

        return b64encode(packet).decode("utf-8")
