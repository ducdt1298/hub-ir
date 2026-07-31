"""Tests for the Broadlink controller and the shared device-file loader."""

from __future__ import annotations

from base64 import b64decode
import json

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.broadlink_ir import Helper
from custom_components.broadlink_ir.controller import get_controller

from .conftest import payloads


async def test_base64_command_is_passed_through(
    hass: HomeAssistant, sent_commands
) -> None:
    """A Base64 device file's codes are sent as-is with the b64: prefix."""
    controller = get_controller(hass, "Broadlink", "Base64", "remote.rm4", 0.5)

    await controller.send("Y29vbA==")

    assert payloads(sent_commands) == [["b64:Y29vbA=="]]
    assert sent_commands[0].data["delay_secs"] == 0.5
    assert sent_commands[0].data["entity_id"] == "remote.rm4"


async def test_hex_command_is_converted_to_base64(
    hass: HomeAssistant, sent_commands
) -> None:
    """A Hex device file's codes are re-encoded to base64."""
    controller = get_controller(hass, "Broadlink", "Hex", "remote.rm4", 0.1)

    await controller.send("26005000")

    assert payloads(sent_commands) == [["b64:JgBQAA=="]]
    assert b64decode("JgBQAA==") == bytes.fromhex("26005000")


async def test_list_of_commands_is_sent_as_one_call(
    hass: HomeAssistant, sent_commands
) -> None:
    """A list of codes becomes a single remote.send_command with a list."""
    controller = get_controller(hass, "Broadlink", "Base64", "remote.rm4", 0.5)

    await controller.send(["YQ==", "Yg=="])

    assert payloads(sent_commands) == [["b64:YQ==", "b64:Yg=="]]


async def test_pronto_command_is_converted(hass: HomeAssistant, sent_commands) -> None:
    """A Pronto device file's codes are converted to a Broadlink packet."""
    controller = get_controller(hass, "Broadlink", "Pronto", "remote.rm4", 0.5)
    # A one-burst-pair Pronto code: preamble then a single on/off pair.
    pronto = "0000 006D 0001 0000 0060 0018"

    await controller.send(pronto)

    (payload,) = payloads(sent_commands)
    packet = b64decode(payload[0].removeprefix("b64:"))
    assert packet[:2] == b"\x26\x00"  # IR packet header
    assert len(packet) % 16 == 12  # padded so len+4 is a multiple of 16


async def test_unsupported_controller_is_rejected(hass: HomeAssistant) -> None:
    """A non-Broadlink device file fails with an actionable message."""
    with pytest.raises(HomeAssistantError, match="only supports the Broadlink"):
        get_controller(hass, "Xiaomi", "Pronto", "remote.rm4", 0.5)


async def test_unsupported_encoding_is_rejected(hass: HomeAssistant) -> None:
    """Raw is not a Broadlink encoding."""
    with pytest.raises(HomeAssistantError, match="not supported by the Broadlink"):
        get_controller(hass, "Broadlink", "Raw", "remote.rm4", 0.5)


@pytest.mark.parametrize("command", [None, "", "   ", [], ["YQ==", ""]])
async def test_unrecorded_command_is_rejected(
    hass: HomeAssistant, sent_commands, command
) -> None:
    """Null and empty placeholder codes never reach the remote.

    Parts of the device database leave commands as empty strings; upstream sent
    those as the literal payload 'b64:'.
    """
    controller = get_controller(hass, "Broadlink", "Base64", "remote.rm4", 0.5)

    with pytest.raises(HomeAssistantError, match="no code recorded"):
        await controller.send(command)

    assert sent_commands == []


async def test_bad_hex_command_is_rejected(
    hass: HomeAssistant, sent_commands
) -> None:
    """A malformed Hex code raises instead of sending garbage."""
    controller = get_controller(hass, "Broadlink", "Hex", "remote.rm4", 0.5)

    with pytest.raises(HomeAssistantError, match="Hex to Base64"):
        await controller.send("not-hex")

    assert sent_commands == []


async def test_loader_reads_a_local_device_file(
    hass: HomeAssistant, write_device_file
) -> None:
    """A device file already on disk is loaded without any download."""
    write_device_file("climate", 4242, {"manufacturer": "Local"})

    data = await Helper.load_device_data(hass, "climate", 4242)

    assert data == {"manufacturer": "Local"}


async def test_loader_downloads_a_missing_device_file(
    hass: HomeAssistant, codes_dir, aioclient_mock
) -> None:
    """A missing device file is fetched from the repository and cached."""
    aioclient_mock.get(
        "https://raw.githubusercontent.com/ducdt1298/broadlink-ir-hass/main/"
        "codes/climate/4243.json",
        text=json.dumps({"manufacturer": "Downloaded"}),
    )

    data = await Helper.load_device_data(hass, "climate", 4243)

    assert data == {"manufacturer": "Downloaded"}
    assert (codes_dir / "codes" / "climate" / "4243.json").is_file()


async def test_loader_reports_a_download_failure(
    hass: HomeAssistant, codes_dir, aioclient_mock
) -> None:
    """An unknown device code fails loudly and leaves no partial file."""
    aioclient_mock.get(
        "https://raw.githubusercontent.com/ducdt1298/broadlink-ir-hass/main/"
        "codes/climate/4244.json",
        status=404,
    )

    with pytest.raises(HomeAssistantError, match="HTTP 404"):
        await Helper.load_device_data(hass, "climate", 4244)

    assert not (codes_dir / "codes" / "climate" / "4244.json").exists()


async def test_loader_reports_invalid_json(
    hass: HomeAssistant, codes_dir
) -> None:
    """A corrupt device file names itself in the error."""
    path = codes_dir / "codes" / "climate" / "4245.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"manufacturer": "Broken",}', encoding="utf-8")

    with pytest.raises(HomeAssistantError, match="not valid JSON"):
        await Helper.load_device_data(hass, "climate", 4245)
