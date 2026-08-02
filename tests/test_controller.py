"""Tests for the Broadlink controller and the shared device-file loader."""

from __future__ import annotations

from base64 import b64decode
import json

import pytest

from custom_components.hub_ir import Helper
from custom_components.hub_ir.controller import get_controller
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

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


async def test_bad_hex_command_is_rejected(hass: HomeAssistant, sent_commands) -> None:
    """A malformed Hex code raises instead of sending garbage."""
    controller = get_controller(hass, "Broadlink", "Hex", "remote.rm4", 0.5)

    with pytest.raises(HomeAssistantError, match="Hex to Base64"):
        await controller.send("not-hex")

    assert sent_commands == []


async def test_loader_reads_a_local_device_file(
    hass: HomeAssistant, write_device_file
) -> None:
    """A device file already on disk is loaded without any download."""
    device_data = {"manufacturer": "Local", "commands": {"off": "b2Zm"}}
    write_device_file("climate", 4242, device_data)

    data = await Helper.load_device_data(hass, "climate", 4242)

    assert data == device_data


async def test_loader_downloads_a_missing_device_file(
    hass: HomeAssistant, codes_dir, aioclient_mock
) -> None:
    """A missing device file is fetched from the repository and cached."""
    aioclient_mock.get(
        "https://raw.githubusercontent.com/ducdt1298/hub-ir/main/"
        "codes/climate/4243.json",
        text=json.dumps({"manufacturer": "Downloaded", "commands": {"off": "b2Zm"}}),
    )

    data = await Helper.load_device_data(hass, "climate", 4243)

    assert data["manufacturer"] == "Downloaded"
    assert (codes_dir / "codes" / "climate" / "4243.json").is_file()


async def test_loader_reports_a_download_failure(
    hass: HomeAssistant, codes_dir, aioclient_mock
) -> None:
    """An unknown device code fails loudly and leaves no partial file."""
    aioclient_mock.get(
        "https://raw.githubusercontent.com/ducdt1298/hub-ir/main/"
        "codes/climate/4244.json",
        status=404,
    )

    with pytest.raises(HomeAssistantError, match="HTTP 404"):
        await Helper.load_device_data(hass, "climate", 4244)

    assert not (codes_dir / "codes" / "climate" / "4244.json").exists()


async def test_loader_reports_invalid_json(hass: HomeAssistant, codes_dir) -> None:
    """A corrupt device file names itself in the error."""
    path = codes_dir / "codes" / "climate" / "4245.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"manufacturer": "Broken",}', encoding="utf-8")

    with pytest.raises(HomeAssistantError, match="not valid JSON"):
        await Helper.load_device_data(hass, "climate", 4245)


@pytest.mark.parametrize(
    "code",
    [
        "JgBQAA",  # 6 chars, needs '=='
        "JgBQAAA",  # 7 chars, needs '='
        "JgBQAAAA",  # already a multiple of 4
    ],
)
async def test_unpadded_base64_codes_are_accepted(
    hass: HomeAssistant, sent_commands, code
) -> None:
    """Most codes in the database are stored without '=' padding.

    The Broadlink integration re-pads before decoding, so rejecting these would
    break the majority of the device database.
    """
    controller = get_controller(hass, "Broadlink", "Base64", "remote.rm4", 0.5)

    await controller.send(code)

    assert payloads(sent_commands) == [[f"b64:{code}"]]


async def test_corrupt_base64_code_is_diagnosed(
    hass: HomeAssistant, sent_commands
) -> None:
    """A code that cannot be decoded even after re-padding says so."""
    controller = get_controller(hass, "Broadlink", "Base64", "remote.rm4", 0.5)

    # 5 characters: 1 more than a multiple of 4, which no padding can fix.
    with pytest.raises(HomeAssistantError, match="not valid base64"):
        await controller.send("JgBQA")

    assert sent_commands == []


async def test_loader_refuses_a_device_file_with_no_codes(
    hass: HomeAssistant, write_device_file
) -> None:
    """A template file whose codes were never captured is refused.

    light/1040 in the database is exactly this: every command is an empty
    placeholder, so it would produce a light entity that silently does nothing.
    """
    write_device_file(
        "light",
        4246,
        {
            "manufacturer": "Toshiba",
            "supportedController": "Broadlink",
            "commandsEncoding": "Base64",
            "commands": {"on": "", "off": ["", ""], "brighten": "", "dim": ""},
        },
    )

    with pytest.raises(HomeAssistantError, match="no codes recorded at all"):
        await Helper.load_device_data(hass, "light", 4246)


async def test_loader_keeps_a_file_that_has_at_least_one_code(
    hass: HomeAssistant, write_device_file
) -> None:
    """A file with gaps but at least one real code is still usable."""
    write_device_file(
        "light",
        4247,
        {
            "manufacturer": "Partial",
            "supportedController": "Broadlink",
            "commandsEncoding": "Base64",
            "commands": {"on": "b24=", "off": "", "_comment": "off not captured"},
        },
    )

    data = await Helper.load_device_data(hass, "light", 4247)

    assert data["commands"]["on"] == "b24="


async def test_annotations_alone_do_not_count_as_codes(
    hass: HomeAssistant, write_device_file
) -> None:
    """A file holding only '_comment' prose has no codes."""
    write_device_file(
        "light",
        4248,
        {
            "manufacturer": "Empty",
            "supportedController": "Broadlink",
            "commandsEncoding": "Base64",
            "commands": {"_comment": "todo: capture these", "on": "", "off": ""},
        },
    )

    with pytest.raises(HomeAssistantError, match="no codes recorded at all"):
        await Helper.load_device_data(hass, "light", 4248)


def test_pronto_conversion_is_pinned_byte_for_byte() -> None:
    """The Pronto to Broadlink conversion must not drift.

    The pulse-to-tick step truncates rather than rounds. That looks like a
    redundant int() cast, but changing it alters every emitted timing, and a
    remote that receives slightly wrong timings simply does not respond. Pinning
    the exact bytes makes such a "cleanup" fail here instead of in the living
    room.
    """
    # A NEC-style Pronto code: 4-pair preamble then four burst pairs.
    pronto = "0000 006D 0004 0000 0155 00AA 0016 0016 0016 0040 0016 05F7"

    pulses = Helper.pronto2lirc(bytearray.fromhex(pronto.replace(" ", "")))
    packet = Helper.lirc2broadlink(pulses)

    assert pulses == [8967, 4470, 579, 579, 579, 1683, 579, 40154]
    assert packet.hex() == "26000c000001269213131337130005260d0500000000000000000000"
    assert packet[0] == 0x26  # IR packet
    assert len(packet) % 16 == 12  # padded so len + 4 is a multiple of 16


async def test_pronto_send_produces_the_pinned_packet(
    hass: HomeAssistant, sent_commands
) -> None:
    """The controller ships exactly that packet, base64 encoded."""
    controller = get_controller(hass, "Broadlink", "Pronto", "remote.rm4", 0.5)

    await controller.send("0000 006D 0004 0000 0155 00AA 0016 0016 0016 0040 0016 05F7")

    assert payloads(sent_commands) == [["b64:JgAMAAABJpITExM3EwAFJg0FAAAAAAAAAAAAAA=="]]
