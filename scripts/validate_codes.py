#!/usr/bin/env python3
"""Validate every device file in ``codes/``.

Checks that each file is valid JSON, carries the keys its platform needs, and
targets the Broadlink controller with an encoding this fork can send. For
climate files it also walks the command tree using the same fallback rules as
climate.py, so only gaps the integration cannot bridge are reported.

Errors set a non-zero exit code; warnings describe things the integration
tolerates but a maintainer may want to know about.

The rules themselves live in custom_components/hub_ir/device_file.py, so
this script, the integration and the learning panel cannot drift apart. That
module is imported as a standalone file rather than as part of the package,
because importing the package would execute its __init__.py and require Home
Assistant, which this script deliberately runs without.

Usage: python scripts/validate_codes.py [codes_dir]
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "hub_ir"),
)

from device_file import PLATFORM_KEYS, Report, validate


def check_file(path: Path, platform: str) -> Report:
    """Return the errors and warnings found in one device file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        report = Report()
        report.error(f"invalid JSON: {err}")
        return report

    return validate(platform, data, path.stem)


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
