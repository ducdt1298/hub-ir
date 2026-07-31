"""Broadlink controller used by every Broadlink IR platform."""

from __future__ import annotations

from base64 import b64decode, b64encode
import binascii
import logging

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import Helper, is_recorded

_LOGGER = logging.getLogger(__name__)

BROADLINK_CONTROLLER = "Broadlink"

ENC_BASE64 = "Base64"
ENC_HEX = "Hex"
ENC_PRONTO = "Pronto"

BROADLINK_COMMANDS_ENCODING = (ENC_BASE64, ENC_HEX, ENC_PRONTO)


def _decode_like_broadlink(value: str) -> bytes:
    """Decode a base64 code exactly as the Broadlink integration does.

    Mirrors homeassistant.components.broadlink.helpers.data_packet, which
    re-pads the string first. Many codes in the database are stored without
    their '=' padding and are perfectly valid, so anything stricter than this
    would reject codes that work.
    """
    extra = len(value) % 4
    if extra > 0:
        value = value + ("=" * (4 - extra))
    return b64decode(value)


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
            f"This integration only supports the {BROADLINK_CONTROLLER} controller, "
            f"but this device file requires '{controller}'. Pick a "
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
            # A handful of codes in the database are corrupt beyond repair.
            # Decoding them the way the Broadlink integration will lets us say
            # so, instead of surfacing its generic binascii error.
            try:
                _decode_like_broadlink(command)
            except (binascii.Error, ValueError) as err:
                raise HomeAssistantError(
                    f"The recorded code is not valid base64 ({err}). This entry "
                    "in the device file is corrupt; re-record it or pick another "
                    "device code"
                ) from err
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
