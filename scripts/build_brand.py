#!/usr/bin/env python3
"""Render the brand PNGs from the SVGs in ``brand/``.

Home Assistant does not read a logo out of ``custom_components``; the
integrations page fetches it from brands.home-assistant.io. Getting HubIR's
logo there means opening a pull request against home-assistant/brands with
these four files under ``custom_integrations/hub_ir/``, which is what this
script produces:

    icon.png     256x256      logo.png     up to 256 tall
    icon@2x.png  512x512      logo@2x.png  twice that

Rasterising is done by whichever of these is installed, in order of preference:
``cairosvg``, ``rsvg-convert``, ``inkscape``, or a headless Chrome. Chrome is
the fallback that works on a bare Windows box, and is the reason the wordmark
may be set in a system font: it renders with whatever the machine has.

    python scripts/build_brand.py
    python scripts/build_brand.py --renderer chrome
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = REPO_ROOT / "brand"

# (source svg, output name, width, height)
TARGETS = [
    ("hub-ir-icon.svg", "icon.png", 256, 256),
    ("hub-ir-icon.svg", "icon@2x.png", 512, 512),
    ("hub-ir-logo.svg", "logo.png", 720, 256),
    ("hub-ir-logo.svg", "logo@2x.png", 1440, 512),
]


def _render_cairosvg(svg: Path, out: Path, width: int, height: int) -> None:
    """Rasterise with cairosvg, which is the tidiest option when present."""
    import cairosvg  # noqa: PLC0415

    cairosvg.svg2png(
        url=str(svg), write_to=str(out), output_width=width, output_height=height
    )


def _render_rsvg(svg: Path, out: Path, width: int, height: int) -> None:
    """Rasterise with librsvg's command-line tool."""
    subprocess.run(
        [
            "rsvg-convert",
            "-w",
            str(width),
            "-h",
            str(height),
            "-o",
            str(out),
            str(svg),
        ],
        check=True,
    )


def _render_inkscape(svg: Path, out: Path, width: int, height: int) -> None:
    """Rasterise with Inkscape."""
    subprocess.run(
        [
            "inkscape",
            str(svg),
            f"--export-width={width}",
            f"--export-height={height}",
            f"--export-filename={out}",
        ],
        check=True,
    )


def _chrome() -> str | None:
    """Return a Chrome or Edge binary that can screenshot a page."""
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        if found := shutil.which(name):
            return found

    for candidate in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _render_chrome(svg: Path, out: Path, width: int, height: int) -> None:
    """Rasterise by screenshotting the SVG in a headless browser.

    The SVG goes in as a data URI inside a page with no margin and a
    transparent body, sized to the exact pixels wanted, so the screenshot needs
    no cropping afterwards.
    """
    binary = _chrome()
    if binary is None:
        raise RuntimeError("no Chrome or Edge found to render with")

    data = base64.b64encode(svg.read_bytes()).decode("ascii")
    page = (
        "<!doctype html><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent}"
        f"img{{display:block;width:{width}px;height:{height}px}}</style>"
        f"<img src='data:image/svg+xml;base64,{data}'>"
    )

    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "page.html"
        html.write_text(page, encoding="utf-8")
        subprocess.run(
            [
                binary,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--default-background-color=00000000",
                f"--screenshot={out}",
                f"--window-size={width},{height}",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                html.as_uri(),
            ],
            check=True,
            capture_output=True,
        )


RENDERERS = {
    "cairosvg": _render_cairosvg,
    "rsvg": _render_rsvg,
    "inkscape": _render_inkscape,
    "chrome": _render_chrome,
}


def _pick_renderer() -> str:
    """Return the name of the first renderer available on this machine."""
    try:
        import cairosvg  # noqa: F401, PLC0415

        return "cairosvg"
    except ImportError:
        pass

    if shutil.which("rsvg-convert"):
        return "rsvg"
    if shutil.which("inkscape"):
        return "inkscape"
    if _chrome():
        return "chrome"

    raise SystemExit(
        "No SVG renderer found. Install cairosvg (pip install cairosvg), "
        "librsvg, or Inkscape — or have Chrome or Edge on the machine."
    )


def main() -> int:
    """Render every target and report what was written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer", choices=sorted(RENDERERS), default=None)
    args = parser.parse_args()

    name = args.renderer or _pick_renderer()
    render = RENDERERS[name]
    print(f"Rendering with {name}")

    for source, output, width, height in TARGETS:
        svg = BRAND_DIR / source
        out = BRAND_DIR / output
        render(svg, out, width, height)
        print(f"  {out.relative_to(REPO_ROOT)}  {width}x{height}")

    print(
        "\nFor the integrations page and HACS, open a pull request against\n"
        "https://github.com/home-assistant/brands adding these four files as\n"
        "custom_integrations/hub_ir/. Until it merges Home Assistant shows the\n"
        "default puzzle-piece icon; nothing in this repository can change that."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
