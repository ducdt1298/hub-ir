"""The device-file format: its schema, its validation, and how to build one.

This module deliberately imports nothing from Home Assistant. Three very
different callers need to agree on what a valid device file is:

* the integration, when it loads one and walks its command tree;
* the learning panel, when it assembles a new one and checks it before saving;
* ``scripts/validate_codes.py``, which has to run in CI without Home Assistant
  installed.

Keeping the rules here means they agree by construction instead of by
discipline. The script imports this file as a standalone top-level module,
because importing it as part of the ``hub_ir`` package would execute
``__init__.py`` and pull Home Assistant in with it.
"""

from __future__ import annotations

import base64
import struct
from typing import Any

BROADLINK = "Broadlink"
VALID_ENCODINGS = {"Base64", "Hex", "Pronto"}

PLATFORMS = ("climate", "fan", "light", "media_player")

# homeassistant.components.climate.const.HVAC_MODES, inlined so this module
# stays free of Home Assistant.
HVAC_MODES = {"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}

COMMON_KEYS = {
    "manufacturer",
    "supportedModels",
    "supportedController",
    "commandsEncoding",
    "commands",
}
PLATFORM_KEYS = {
    "climate": COMMON_KEYS
    | {"minTemperature", "maxTemperature", "precision", "operationModes", "fanModes"},
    "fan": COMMON_KEYS | {"speed"},
    "light": COMMON_KEYS,
    "media_player": COMMON_KEYS,
}

# Above this a min/max temperature cannot plausibly be Celsius.
FAHRENHEIT_THRESHOLD = 40

# Keys used inside command trees for documentation rather than for a command.
# Note that '' is a real, selectable fan mode in some device files, so only
# these prefixes count as annotations.
ANNOTATION_PREFIXES = ("_", "$")

# Free-form commands the four entity models cannot express — a television's
# arrow keys, an air conditioner's LED toggle — live under this key, reachable
# by name from the service rather than from an entity control.
EXTRAS_KEY = "extras"

# Keys at the top of a command tree that are never an operation mode. climate.py
# walks the tree by position and substitutes a sibling when the mode it wants is
# missing, so without this it could hand back the 'off' code, or a whole group
# dict that then gets walked as if it were the fan-mode level.
RESERVED_COMMAND_KEYS = frozenset({"on", "off", EXTRAS_KEY})

# Broadlink packet types: 0x26 is IR, the rest are the RF variants.
PACKET_IR = 0x26
PACKET_TYPES = (PACKET_IR, 0xB0, 0xB1, 0xB2, 0xD7)

# An IR packet is a type byte, a repeat byte and a 16-bit payload length.
IR_HEADER_LEN = 4

# How many offending entries a warning names before summarising the rest.
MAX_LISTED = 5

# Device codes at or above this belong to files the user recorded themselves.
# Staying clear of the upstream range means a local file can never shadow a
# shipped one, which would be a confusing way to lose a working device.
CUSTOM_CODE_START = 90000

# Codes inherited from upstream that cannot be decoded even after re-padding: a
# character was lost or added when they were captured, and no single-character
# repair yields an internally consistent Broadlink packet. They are reported as
# known issues rather than errors so the validator stays usable as a gate; a
# newly corrupt code is a real error. Kept in step with
# tests/test_real_device_files.py::CORRUPT_CODES.
KNOWN_CORRUPT = {
    ("climate", "1164", "heat/high/28"),
    ("climate", "1282", "heat/quiet/18"),
    ("climate", "1942", "cool/high/21"),
    ("climate", "1942", "heat/auto/24"),
    ("climate", "2160", "cool/low/28"),
    ("climate", "2380", "heat/low/24.5"),
    ("fan", "1000", "reverse/lowest"),
}


class Report:
    """Errors and warnings collected for one device file."""

    def __init__(self) -> None:
        """Start with nothing found."""
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        """Record something that stops a command from being sent."""
        self.errors.append(message)

    def warn(self, message: str) -> None:
        """Record something the integration tolerates."""
        self.warnings.append(message)

    def __bool__(self) -> bool:
        """Return whether anything at all was found."""
        return bool(self.errors or self.warnings)


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


def has_any_code(commands: Any) -> bool:
    """Return whether a command tree holds at least one usable code."""
    if isinstance(commands, dict):
        return any(
            has_any_code(value)
            for key, value in commands.items()
            if not str(key).startswith(ANNOTATION_PREFIXES)
        )
    if isinstance(commands, list):
        return any(has_any_code(entry) for entry in commands)
    return is_recorded(commands)


def is_documentation(path: tuple[str, ...]) -> bool:
    """Return whether a tree path points at a '_comment'-style annotation."""
    return any(part.startswith(ANNOTATION_PREFIXES) for part in path)


def walk(node: Any, path: tuple[str, ...] = ()):
    """Yield (path, value) for every leaf under a commands tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, (*path, str(key)))
    elif isinstance(node, list):
        if node and all(isinstance(entry, str) for entry in node):
            yield path, node  # a multi-code command
        else:
            for index, value in enumerate(node):
                yield from walk(value, (*path, str(index)))
    else:
        yield path, node


def data_packet(value: str) -> bytes:
    """Decode a code exactly as homeassistant.components.broadlink does.

    The Broadlink integration re-pads base64 itself, so a code missing its '='
    padding is fine; anything this still cannot decode would raise when sent.
    """
    extra = len(value) % 4
    if extra > 0:
        value = value + ("=" * (4 - extra))
    return base64.b64decode(value)


def describe_packet_problem(raw: bytes) -> str | None:
    """Return why a decoded packet looks wrong, or None when it looks fine."""
    if not raw:
        return "decodes to zero bytes"

    if raw[0] not in PACKET_TYPES:
        return f"first byte 0x{raw[0]:02x} is not a known packet type"

    if raw[0] == PACKET_IR and len(raw) >= IR_HEADER_LEN:
        declared = struct.unpack("<H", raw[2:4])[0]
        payload = len(raw) - IR_HEADER_LEN
        if declared > payload:
            return f"header declares {declared} payload bytes but only {payload} follow"

    return None


def is_number(value: Any) -> bool:
    """Return whether a command key parses as a temperature."""
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def temperature_unit(data: dict, max_temperature: float) -> str:
    """Return 'C' or 'F' for a climate file, preferring its own declaration."""
    declared = str(data.get("temperatureUnit", "")).upper().lstrip("°")
    if declared in ("C", "F"):
        return declared
    return "F" if max_temperature > FAHRENHEIT_THRESHOLD else "C"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(platform: str, data: Any, device_code: str = "") -> Report:
    """Return the errors and warnings found in one parsed device file."""
    report = Report()

    if not isinstance(data, dict):
        report.error("top level value must be a JSON object")
        return report

    if missing := sorted(PLATFORM_KEYS[platform] - data.keys()):
        report.error(f"missing key(s): {', '.join(missing)}")

    if (controller := data.get("supportedController")) != BROADLINK:
        report.error(f"supportedController is {controller!r}, expected {BROADLINK!r}")

    if (encoding := data.get("commandsEncoding")) not in VALID_ENCODINGS:
        report.error(
            f"commandsEncoding is {encoding!r}, expected one of "
            f"{', '.join(sorted(VALID_ENCODINGS))}"
        )

    if not isinstance(data.get("commands"), dict) or not data.get("commands"):
        report.error("commands must be a non-empty object")
    else:
        _check_placeholders(data["commands"], report)
        _check_codes_decode(data, report, platform, device_code)
        _check_extras(data["commands"], report)

    if platform == "climate":
        _check_climate(data, report)
    if platform == "fan" and not data.get("speed"):
        report.error("speed must be a non-empty list")

    return report


def _check_placeholders(commands: Any, report: Report) -> None:
    """Warn about entries left as empty placeholders instead of real codes."""
    placeholders = [
        path
        for path, value in walk(commands)
        if not is_documentation(path) and not is_recorded(value)
    ]
    if not placeholders:
        return

    shown = ", ".join("/".join(path) for path in placeholders[:MAX_LISTED])
    more = (
        f" (+{len(placeholders) - MAX_LISTED} more)"
        if len(placeholders) > MAX_LISTED
        else ""
    )
    report.warn(
        f"{len(placeholders)} command(s) have no code recorded: {shown}{more}. "
        "The integration skips these and refuses to transmit them"
    )


def _check_extras(commands: dict, report: Report, group: str = EXTRAS_KEY) -> None:
    """Check that a free-form command group is a flat mapping of names to codes.

    Flat is the contract the service's command paths depend on: 'extras/menu'
    names one code, so a nested object there would be a path that resolves to a
    dict nobody can transmit.
    """
    if group not in commands:
        return

    entries = commands[group]
    if not isinstance(entries, dict):
        report.error(f"commands.{group} must be an object mapping names to codes")
        return

    for name, value in entries.items():
        if isinstance(value, dict):
            report.error(
                f"commands.{group}[{name!r}] must be a code or a list of codes, "
                "not a nested object"
            )


def _check_codes_decode(
    data: dict, report: Report, platform: str, device_code: str
) -> None:
    """Check that every recorded code is something the remote can transmit."""
    encoding = data.get("commandsEncoding")
    commands = data.get("commands")
    if encoding != "Base64" or not isinstance(commands, dict):
        # Hex and Pronto are converted by the integration, which raises a clear
        # error of its own on bad input.
        return

    undecodable, known_bad, malformed = [], [], []

    for path, value in walk(commands):
        if is_documentation(path) or not is_recorded(value):
            continue
        for entry in value if isinstance(value, list) else [value]:
            where = "/".join(path)
            try:
                raw = data_packet(entry)
            except Exception as err:
                if (platform, device_code, where) in KNOWN_CORRUPT:
                    known_bad.append(where)
                else:
                    undecodable.append(f"{where} ({err})")
                continue
            if problem := describe_packet_problem(raw):
                malformed.append(f"{where} ({problem})")

    if undecodable:
        shown = ", ".join(undecodable[:MAX_LISTED])
        more = (
            f" (+{len(undecodable) - MAX_LISTED} more)"
            if len(undecodable) > MAX_LISTED
            else ""
        )
        report.error(f"{len(undecodable)} code(s) are not valid base64: {shown}{more}")

    if known_bad:
        report.warn(
            f"{len(known_bad)} known-corrupt code(s) inherited from upstream: "
            f"{', '.join(known_bad)}. The integration refuses to transmit these "
            "and says why; re-record them or use another device code"
        )

    if malformed:
        shown = ", ".join(malformed[:MAX_LISTED])
        more = (
            f" (+{len(malformed) - MAX_LISTED} more)"
            if len(malformed) > MAX_LISTED
            else ""
        )
        report.warn(
            f"{len(malformed)} code(s) decode but do not look like a Broadlink "
            f"packet: {shown}{more}. They were probably captured badly and may "
            "not work on the device"
        )


def _check_climate(data: dict, report: Report) -> None:
    """Check a climate file's temperature range and command tree."""
    min_temp = data.get("minTemperature")
    max_temp = data.get("maxTemperature")

    if not isinstance(min_temp, (int, float)) or not isinstance(max_temp, (int, float)):
        report.error("minTemperature and maxTemperature must be numbers")
        return

    if min_temp >= max_temp:
        report.error(
            f"minTemperature {min_temp} is not below maxTemperature {max_temp}"
        )

    # A declared unit that disagrees with the range means the entity would
    # advertise the wrong unit and HA would convert the wrong way.
    declared = str(data.get("temperatureUnit", "")).upper().lstrip("°")
    inferred = "F" if max_temp > FAHRENHEIT_THRESHOLD else "C"
    if declared and declared != inferred:
        report.error(
            f"temperatureUnit says {declared} but the {min_temp}-{max_temp} "
            f"range looks like {inferred}"
        )

    # Celsius is the safe default, so only a Fahrenheit file has to say so. Left
    # undeclared, climate.py would have to guess from the range, and a guess is
    # not something a shipped device file should rely on.
    if not declared and inferred == "F":
        report.error(
            f"the {min_temp}-{max_temp} range cannot be Celsius, so this file "
            'must declare "temperatureUnit": "F" instead of leaving the '
            "integration to infer it"
        )

    commands = data.get("commands")
    if not isinstance(commands, dict):
        return

    if "off" not in commands:
        report.error("commands has no 'off' entry")

    fan_modes = data.get("fanModes") or []
    swing_modes = data.get("swingModes") or []

    # climate.py drops any operationMode HA does not know, so those are
    # reported once and then skipped rather than checked for codes.
    operation_modes = []
    for mode in data.get("operationModes") or []:
        if mode == "off":
            # The entity handles off with the top-level 'off' code, not a
            # mode subtree, so there is nothing to walk here.
            continue
        if mode in HVAC_MODES:
            operation_modes.append(mode)
        else:
            report.warn(
                f"operationMode {mode!r} is not a Home Assistant HVAC mode, "
                "so the entity ignores it"
            )

    # The rest mirrors climate.py's lookup: a missing fan or swing mode is
    # substituted from what the mode does define, and a missing temperature
    # resolves to the closest recorded one. Substitutions are warnings; only a
    # level with nothing to fall back on is an error.
    for mode in operation_modes:
        if mode not in commands:
            report.error(f"commands has no entry for operationMode {mode!r}")
            continue
        node = commands[mode]

        if not isinstance(node, dict):
            # A bare code for the whole mode: nothing deeper to check.
            continue

        for fan_mode in fan_modes:
            fan_node, substituted = _select(node, fan_mode)
            if fan_node is None:
                report.error(
                    f"commands[{mode!r}] has no fan mode level to select "
                    f"{fan_mode!r} from"
                )
                continue
            if substituted:
                report.warn(
                    f"commands[{mode!r}] has no fanMode {fan_mode!r}; the "
                    f"entity substitutes {substituted!r}"
                )

            for swing_mode, leaf in _walk_swing(
                fan_node, swing_modes, report, mode, fan_mode
            ):
                where = f"commands[{mode!r}][{fan_mode!r}]"
                if swing_mode is not None:
                    where += f"[{swing_mode!r}]"
                if not isinstance(leaf, dict):
                    # A bare code here means the unit ignores temperature in
                    # this mode, which the entity sends as-is.
                    continue
                if not any(is_number(key) for key in leaf):
                    report.error(f"{where} records no temperatures")


def _walk_swing(
    fan_node: Any, swing_modes: list[str], report: Report, mode: str, fan_mode: str
):
    """Yield (swing_mode, leaf) pairs to check under one fan mode."""
    if not swing_modes or not isinstance(fan_node, dict):
        yield None, fan_node
        return

    for swing_mode in swing_modes:
        swing_node, substituted = _select(fan_node, swing_mode)
        if swing_node is None:
            report.error(
                f"commands[{mode!r}][{fan_mode!r}] has no swing mode level to "
                f"select {swing_mode!r} from"
            )
            continue
        if substituted:
            report.warn(
                f"commands[{mode!r}][{fan_mode!r}] has no swingMode "
                f"{swing_mode!r}; the entity substitutes {substituted!r}"
            )
        yield swing_mode, swing_node


def _select(node: Any, key: str):
    """Model climate.py's _select.

    Returns (value, substituted_key), where substituted_key is None on an exact
    hit and the name actually used when the key was absent. Returns
    (None, None) when there is nothing to select from at all.
    """
    if not isinstance(node, dict) or not node:
        return None, None
    if key in node:
        return node[key], None
    substitute = next(iter(node))
    return node[substitute], substitute


# ---------------------------------------------------------------------------
# Planning a capture session
# ---------------------------------------------------------------------------
#
# climate.py walks the command tree by *position*: mode, then fan mode, then
# swing mode if the file declares any, then temperature. It does not look at the
# key names to decide which level it is on.
#
# That makes "this mode ignores fan speed" a trap. Dropping the fan level would
# leave the temperature dict where the fan dict should be, and _select would
# happily substitute the code for 16 degrees when asked for a fan mode. So a
# capture that covers several tree positions is expanded into all of them
# instead: one press of the remote, the same code written under every fan mode.
#
# Dropping the *temperature* level is safe, because _resolve_command returns
# early as soon as a level is not a dict. A bare code under a fan mode is the
# documented way to say the unit ignores temperature there.


class PlanCell:
    """One code to capture, and every place in the tree it belongs."""

    def __init__(self, key: str, label: str, targets: list[list[str]], group: str = ""):
        """Describe a capture and where its code will be written."""
        self.key = key
        self.label = label
        self.targets = targets
        self.group = group

    def as_dict(self) -> dict[str, Any]:
        """Return the cell in the shape the panel consumes."""
        return {
            "key": self.key,
            "label": self.label,
            "targets": self.targets,
            "group": self.group,
        }


def temperature_steps(minimum: float, maximum: float, precision: float) -> list[str]:
    """Return the temperature keys between two bounds, as the tree spells them.

    Stepping with integers avoids the drift that repeated float addition would
    put into keys like '24.5'.

    A step that does not divide the range evenly stops below the maximum rather
    than overshooting it: climate.py refuses a target outside min/max, so a key
    above the maximum would be a code nobody could ever ask for.
    """
    if precision <= 0:
        raise ValueError("precision must be greater than zero")
    if maximum < minimum:
        raise ValueError("maxTemperature must not be below minTemperature")

    steps = []
    index = 0
    while True:
        value = round(minimum + index * precision, 2)
        if value > maximum:
            break
        steps.append(f"{value:g}")
        index += 1
    return steps


def capture_plan(platform: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered list of codes to capture for a device spec.

    The order matters as much as the contents: temperature is innermost and
    ascending, because that is the order the buttons sit in on the original
    remote. Holding *temp +* walks a whole row without touching the browser.
    """
    if platform == "climate":
        cells = _climate_plan(spec)
    elif platform == "fan":
        cells = _fan_plan(spec)
    elif platform == "light":
        cells = _light_plan(spec)
    elif platform == "media_player":
        cells = _media_player_plan(spec)
    else:
        raise ValueError(f"unknown platform {platform!r}")

    return [cell.as_dict() for cell in cells]


def _mode_options(spec: dict[str, Any], mode: str) -> tuple[bool, bool]:
    """Return whether a climate mode responds to fan speed and to temperature."""
    options = (spec.get("modeOptions") or {}).get(mode) or {}
    return bool(options.get("usesFan", True)), bool(
        options.get("usesTemperature", True)
    )


def _climate_plan(spec: dict[str, Any]) -> list[PlanCell]:
    """Plan the captures for an air conditioner."""
    cells = [PlanCell("off", "Off", [["off"]], "Power")]
    if spec.get("hasOnCommand"):
        cells.append(PlanCell("on", "On", [["on"]], "Power"))

    fan_modes = list(spec.get("fanModes") or [])
    swing_modes = list(spec.get("swingModes") or [])
    temperatures = temperature_steps(
        spec["minTemperature"], spec["maxTemperature"], spec["precision"]
    )
    unit = "°F" if str(spec.get("temperatureUnit", "C")).upper() == "F" else "°C"

    for mode in spec.get("operationModes") or []:
        if mode == "off":
            continue
        uses_fan, uses_temperature = _mode_options(spec, mode)

        # Capture one fan mode when the unit ignores fan speed here, but write
        # the code under every one of them.
        captured_fans = fan_modes if uses_fan else fan_modes[:1]

        for fan_mode in captured_fans:
            written_fans = [fan_mode] if uses_fan else fan_modes
            for swing_mode in swing_modes or [None]:
                prefixes = [
                    [mode, written, *([swing_mode] if swing_mode else [])]
                    for written in written_fans
                ]
                label_parts = [mode, fan_mode if uses_fan else "any fan"]
                if swing_mode:
                    label_parts.append(swing_mode)

                if not uses_temperature:
                    cells.append(
                        PlanCell(
                            "/".join(prefixes[0]),
                            " · ".join(label_parts),
                            prefixes,
                            mode,
                        )
                    )
                    continue

                for temperature in temperatures:
                    cells.append(
                        PlanCell(
                            "/".join([*prefixes[0], temperature]),
                            " · ".join([*label_parts, f"{temperature}{unit}"]),
                            [[*prefix, temperature] for prefix in prefixes],
                            mode,
                        )
                    )

    cells.extend(_extra_cells(spec))
    return cells


def _fan_plan(spec: dict[str, Any]) -> list[PlanCell]:
    """Plan the captures for a fan."""
    cells = [PlanCell("off", "Off", [["off"]], "Power")]

    speeds = list(spec.get("speed") or [])
    groups = ["forward", "reverse"] if spec.get("hasDirection") else ["default"]

    for group in groups:
        for speed in speeds:
            cells.append(
                PlanCell(
                    f"{group}/{speed}",
                    f"{group} · {speed}",
                    [[group, speed]],
                    group,
                )
            )

    if spec.get("hasOscillate"):
        cells.append(PlanCell("oscillate", "Oscillate", [["oscillate"]], "Extras"))

    cells.extend(_extra_cells(spec))
    return cells


def _light_plan(spec: dict[str, Any]) -> list[PlanCell]:
    """Plan the captures for a light."""
    cells = [
        PlanCell("on", "On", [["on"]], "Power"),
        PlanCell("off", "Off", [["off"]], "Power"),
    ]
    if spec.get("brightness"):
        cells.append(PlanCell("brighten", "Brighter", [["brighten"]], "Brightness"))
        cells.append(PlanCell("dim", "Dimmer", [["dim"]], "Brightness"))
    if spec.get("colorTemperature"):
        cells.append(PlanCell("colder", "Colder", [["colder"]], "Colour"))
        cells.append(PlanCell("warmer", "Warmer", [["warmer"]], "Colour"))
    if spec.get("hasNight"):
        cells.append(PlanCell("night", "Night light", [["night"]], "Brightness"))
    cells.extend(_extra_cells(spec))
    return cells


def _extra_cells(spec: dict[str, Any]) -> list[PlanCell]:
    """Return the capture cells for a spec's free-form extra commands.

    Every platform gets these. Four entity models cannot express a television's
    arrow keys, a projector's lens shift or an air conditioner's LED toggle, and
    a code nobody can name is a code no automation can reach.
    """
    return [
        PlanCell(
            f"{EXTRAS_KEY}/{name}",
            f"Extra · {name}",
            [[EXTRAS_KEY, name]],
            "Extra buttons",
        )
        for name in spec.get("extraCommands") or []
        if str(name) and not str(name).startswith(ANNOTATION_PREFIXES)
    ]


_MEDIA_PLAYER_BUTTONS = (
    ("on", "On", "Power"),
    ("off", "Off", "Power"),
    ("volumeUp", "Volume up", "Volume"),
    ("volumeDown", "Volume down", "Volume"),
    ("mute", "Mute", "Volume"),
    ("previousChannel", "Previous channel", "Channels"),
    ("nextChannel", "Next channel", "Channels"),
)


def _media_player_plan(spec: dict[str, Any]) -> list[PlanCell]:
    """Plan the captures for a media player."""
    wanted = set(spec.get("buttons") or [name for name, _, _ in _MEDIA_PLAYER_BUTTONS])
    cells = [
        PlanCell(name, label, [[name]], group)
        for name, label, group in _MEDIA_PLAYER_BUTTONS
        if name in wanted
    ]
    cells.extend(
        PlanCell(
            f"sources/{source}", f"Source · {source}", [["sources", source]], "Sources"
        )
        for source in spec.get("sources") or []
    )
    cells.extend(_extra_cells(spec))
    return cells


def build_device_file(
    platform: str, spec: dict[str, Any], codes: dict[str, Any]
) -> dict[str, Any]:
    """Assemble a device file from a spec and the codes captured against it.

    ``codes`` maps a plan cell's key to its captured code. A key that is missing
    or empty becomes an empty placeholder, which the integration already refuses
    to transmit and the validator reports as a gap.
    """
    data: dict[str, Any] = {
        "manufacturer": spec.get("manufacturer") or "Unknown",
        "supportedModels": list(spec.get("supportedModels") or ["Unknown"]),
        "supportedController": BROADLINK,
        "commandsEncoding": "Base64",
    }

    if platform == "climate":
        data["minTemperature"] = spec["minTemperature"]
        data["maxTemperature"] = spec["maxTemperature"]
        data["precision"] = spec["precision"]
        # Always declared, never inferred: see _check_climate.
        data["temperatureUnit"] = (
            "F" if str(spec.get("temperatureUnit", "C")).upper() == "F" else "C"
        )
        data["operationModes"] = [
            mode for mode in (spec.get("operationModes") or []) if mode != "off"
        ]
        data["fanModes"] = list(spec.get("fanModes") or [])
        if spec.get("swingModes"):
            data["swingModes"] = list(spec["swingModes"])
    elif platform == "fan":
        data["speed"] = list(spec.get("speed") or [])
    elif platform == "light":
        if spec.get("brightness"):
            data["brightness"] = list(spec["brightness"])
        if spec.get("colorTemperature"):
            data["colorTemperature"] = list(spec["colorTemperature"])

    commands: dict[str, Any] = {}
    for cell in capture_plan(platform, spec):
        code = codes.get(cell["key"], "")
        for target in cell["targets"]:
            _write_at(commands, target, code)

    data["commands"] = commands
    return data


def _write_at(tree: dict[str, Any], path: list[str], value: Any) -> None:
    """Store a value at a path in a nested command tree, creating levels."""
    node = tree
    for key in path[:-1]:
        existing = node.get(key)
        if not isinstance(existing, dict):
            existing = {}
            node[key] = existing
        node = existing
    node[path[-1]] = value


def _read_at(tree: Any, path: list[str]) -> Any:
    """Return the value at a path in a command tree, or None if it is absent."""
    node = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


# ---------------------------------------------------------------------------
# Reading an existing device file back into a spec
# ---------------------------------------------------------------------------


def spec_from_device_file(platform: str, data: dict[str, Any]) -> dict[str, Any]:
    """Recover the spec that would produce a device file like this one.

    Used to start from a file that already exists — one of the shipped ones, or
    an earlier attempt — so only the codes that are missing or wrong have to be
    learned again.
    """
    commands = data.get("commands") or {}
    spec: dict[str, Any] = {
        "manufacturer": data.get("manufacturer") or "",
        "supportedModels": list(data.get("supportedModels") or []),
        # Recovered by presence rather than by is_recorded, the way 'buttons' is:
        # a name whose code was never captured should still be offered for
        # capture rather than silently dropped from the file.
        "extraCommands": [
            name
            for name in commands.get(EXTRAS_KEY) or {}
            if not str(name).startswith(ANNOTATION_PREFIXES)
        ],
    }

    if platform == "climate":
        spec.update(
            minTemperature=data.get("minTemperature", 16),
            maxTemperature=data.get("maxTemperature", 30),
            precision=data.get("precision", 1),
            temperatureUnit=temperature_unit(data, data.get("maxTemperature", 30)),
            operationModes=[
                mode
                for mode in data.get("operationModes") or []
                if mode in HVAC_MODES and mode != "off"
            ],
            fanModes=list(data.get("fanModes") or []),
            swingModes=list(data.get("swingModes") or []),
            hasOnCommand=is_recorded(commands.get("on")),
        )
        spec["modeOptions"] = {
            mode: _infer_mode_options(commands.get(mode), spec)
            for mode in spec["operationModes"]
        }
    elif platform == "fan":
        spec.update(
            speed=list(data.get("speed") or []),
            hasDirection="forward" in commands and "reverse" in commands,
            hasOscillate="oscillate" in commands,
        )
    elif platform == "light":
        spec.update(
            brightness=list(data.get("brightness") or []),
            colorTemperature=list(data.get("colorTemperature") or []),
            hasNight="night" in commands,
        )
    elif platform == "media_player":
        spec.update(
            buttons=[name for name, _, _ in _MEDIA_PLAYER_BUTTONS if name in commands],
            sources=list((commands.get("sources") or {}).keys()),
        )

    return spec


def _infer_mode_options(node: Any, spec: dict[str, Any]) -> dict[str, bool]:
    """Work out whether a mode's subtree varies with fan speed and temperature.

    A file that records the same subtree under every fan mode is telling us the
    unit ignores fan speed there, and one that stops at a bare code is telling
    us it ignores temperature. Recovering that keeps a re-learn as short as the
    original capture was.
    """
    fan_modes = spec.get("fanModes") or []
    swing_modes = spec.get("swingModes") or []

    if not isinstance(node, dict):
        # A single code for the whole mode: nothing below it varies.
        return {"usesFan": False, "usesTemperature": False}

    present = [fan for fan in fan_modes if fan in node]
    uses_fan = len(present) > 1 and any(
        node[fan] != node[present[0]] for fan in present[1:]
    )

    below = node[present[0]] if present else next(iter(node.values()), None)
    if swing_modes and isinstance(below, dict):
        below = below.get(swing_modes[0], next(iter(below.values()), None))

    uses_temperature = isinstance(below, dict) and any(is_number(key) for key in below)

    return {"usesFan": uses_fan, "usesTemperature": uses_temperature}


def codes_from_device_file(
    platform: str, data: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """Return the codes an existing file already provides, keyed by plan cell.

    Anything the plan wants but the file does not record is simply absent, which
    is what leaves it to be captured.
    """
    commands = data.get("commands") or {}
    codes: dict[str, Any] = {}

    for cell in capture_plan(platform, spec):
        # Every target holds the same code by construction, but a file that
        # recorded only one fan mode under a level put it under whichever key it
        # felt like, so take the first that is actually there.
        for target in cell["targets"]:
            value = _read_at(commands, target)
            if is_recorded(value):
                codes[cell["key"]] = value
                break

    return codes
