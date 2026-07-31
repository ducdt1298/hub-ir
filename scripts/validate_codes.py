#!/usr/bin/env python3
"""Validate every device file in ``codes/``.

Checks that each file is valid JSON, carries the keys its platform needs, and
targets the Broadlink controller with an encoding this fork can send. For
climate files it also walks the command tree using the same fallback rules as
climate.py, so only gaps the integration cannot bridge are reported.

Errors set a non-zero exit code; warnings describe things the integration
tolerates but a maintainer may want to know about.

Usage: python scripts/validate_codes.py [codes_dir]
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

BROADLINK = "Broadlink"
VALID_ENCODINGS = {"Base64", "Hex", "Pronto"}

# homeassistant.components.climate.const.HVAC_MODES, inlined so the validator
# runs without Home Assistant installed.
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

# Mirrors climate.py: above this a min/max temperature cannot be Celsius.
FAHRENHEIT_THRESHOLD = 40


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


def check_file(path: Path, platform: str) -> Report:
    """Return the errors and warnings found in one device file."""
    report = Report()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        report.error(f"invalid JSON: {err}")
        return report

    if not isinstance(data, dict):
        report.error("top level value must be a JSON object")
        return report

    if missing := sorted(PLATFORM_KEYS[platform] - data.keys()):
        report.error(f"missing key(s): {', '.join(missing)}")

    if (controller := data.get("supportedController")) != BROADLINK:
        report.error(
            f"supportedController is {controller!r}, expected {BROADLINK!r}"
        )

    if (encoding := data.get("commandsEncoding")) not in VALID_ENCODINGS:
        report.error(
            f"commandsEncoding is {encoding!r}, expected one of "
            f"{', '.join(sorted(VALID_ENCODINGS))}"
        )

    if not isinstance(data.get("commands"), dict) or not data.get("commands"):
        report.error("commands must be a non-empty object")
    else:
        count_placeholders(data["commands"], report)

    if platform == "climate":
        check_climate(data, report)
    if platform == "fan" and not data.get("speed"):
        report.error("speed must be a non-empty list")

    return report


def count_placeholders(commands, report: Report) -> None:
    """Warn about entries left as empty placeholders instead of real codes."""
    placeholders = [path for path, value in walk(commands) if not is_recorded(value)]
    if not placeholders:
        return

    shown = ", ".join("/".join(path) for path in placeholders[:5])
    more = f" (+{len(placeholders) - 5} more)" if len(placeholders) > 5 else ""
    report.warn(
        f"{len(placeholders)} command(s) have no code recorded: {shown}{more}. "
        "The integration skips these and refuses to transmit them"
    )


def walk(node, path=()):
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


def is_recorded(command) -> bool:
    """Mirror controller.is_recorded."""
    if isinstance(command, str):
        return bool(command.strip())
    if isinstance(command, list):
        return bool(command) and all(is_recorded(entry) for entry in command)
    return False


def check_climate(data: dict, report: Report) -> None:
    """Check a climate file's temperature range and command tree."""
    min_temp = data.get("minTemperature")
    max_temp = data.get("maxTemperature")

    if not isinstance(min_temp, (int, float)) or not isinstance(
        max_temp, (int, float)
    ):
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
            fan_node, substituted = select(node, fan_mode)
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

            for swing_mode, leaf in walk_swing(
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


def walk_swing(fan_node, swing_modes, report: Report, mode: str, fan_mode: str):
    """Yield (swing_mode, leaf) pairs to check under one fan mode."""
    if not swing_modes or not isinstance(fan_node, dict):
        yield None, fan_node
        return

    for swing_mode in swing_modes:
        swing_node, substituted = select(fan_node, swing_mode)
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


def select(node, key):
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


def is_number(value) -> bool:
    """Return whether a command key parses as a temperature."""
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def main() -> int:
    """Validate every device file and print a per-platform summary."""
    codes_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "codes")
    if not codes_dir.is_dir():
        print(f"No such directory: {codes_dir}", file=sys.stderr)
        return 2

    total = with_errors = with_warnings = 0

    for platform in sorted(PLATFORM_KEYS):
        paths = sorted((codes_dir / platform).glob("*.json"))
        platform_errors = platform_warnings = 0

        for path in paths:
            total += 1
            report = check_file(path, platform)
            if not report:
                continue

            print(f"{path}:")
            for message in report.errors:
                print(f"    error: {message}")
            for message in report.warnings:
                print(f"    warning: {message}")

            if report.errors:
                platform_errors += 1
                with_errors += 1
            if report.warnings:
                platform_warnings += 1
                with_warnings += 1

        print(
            f"{platform}: {len(paths)} file(s), "
            f"{platform_errors} with errors, {platform_warnings} with warnings"
        )

    print(
        f"\n{total} device file(s) checked: "
        f"{with_errors} with errors, {with_warnings} with warnings"
    )
    return 1 if with_errors else 0


if __name__ == "__main__":
    sys.exit(main())
