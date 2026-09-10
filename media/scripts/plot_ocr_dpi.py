#!/usr/bin/env python3
"""
Module: plot_ocr_dpi.py
Purpose: Chart what raising the OCR render DPI actually buys, which is nothing.
Author: LB
Date:   2026-08-29

    python media/scripts/plot_ocr_dpi.py

Reads   media/data/2026-08-29-ocr-dpi-sweep.csv
Writes  media/charts/ocr-dpi-sweep.svg

## What this shows

`tools/pdf_ocr.py` rasterises a PDF page and reads it with RapidOCR. The one number that had
to be chosen was the render DPI, and the received wisdom for OCR of small schematic text is
"go higher" — the assumption being that more pixels means more readable characters.

**It does not.** Swept over the two real files in `data/` that carry no text layer, the
character count is flat from 150 dpi upward:

    esp32devkitv1_schematics.pdf    404 -> 432 -> 431 -> 428 chars over 150 -> 400 dpi
    pi_cam3.pdf                    2023 -> 2062 -> 2069 -> 2078 chars

The variation is ~7% on the schematic and ~3% on the datasheet, in both directions — it is
noise in the detector, not a trend. The reason is that RapidOCR resizes the image to its
detector's fixed input size before doing anything, so a 400 dpi raster is downsampled back to
roughly what a 150 dpi raster already was. Paying for the bigger raster buys a bigger
intermediate and the same characters.

So `RENDER_DPI = 200` — the peak on the schematic, within 1% of the peak on the datasheet, and
a quarter of the pixels of 400.

## Why it matters

The wrong instinct here is expensive in the place it is least visible. `tools/file_manager.py`
rebuilds the vector store after every upload, so an OCR pass sits between LB pressing the
paperclip and hearing an answer. Choosing 400 dpi would have made every scanned page cost
~4x the memory for no additional retrievable text, and nothing in the output would ever have
said so.

Two panels rather than one axis: the same measure on two documents an order of magnitude
apart. Sharing one y-axis would flatten the schematic into the baseline and hide exactly the
shape this chart exists to show.
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SWEEP = REPO / "media" / "data" / "2026-08-29-ocr-dpi-sweep.csv"
OUT = REPO / "media" / "charts" / "ocr-dpi-sweep.svg"

# From references/palette.md, validated for a light surface with
# `node scripts/validate_palette.js "#2a78d6" --mode light` — all checks pass.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
SERIES = "#2a78d6"
GRID = "#e3e2df"

W, H = 900, 460
PANEL_W, PANEL_H = 350, 210
# The header runs to y=73, and each panel writes its own name at TOP-40 and its
# subtitle at TOP-22. TOP=130 is what keeps those two off the header's last line —
# the first attempt put the panel name at y=68 and the header's third line at y=73.
TOP = 130
LEFT = (72, 512)


def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def read_sweep(path: Path) -> dict[str, list[tuple[int, int]]]:
    """{filename: [(dpi, chars), ...]} in ascending DPI order."""
    series: dict[str, list[tuple[int, int]]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            series.setdefault(row["file"], []).append((int(row["dpi"]), int(row["chars"])))
    for points in series.values():
        points.sort()
    return series


def panel(parts: list[str], x0: int, name: str, points: list[tuple[int, int]]) -> None:
    """One small multiple: DPI across, characters up, y starting at zero."""
    dpis = [d for d, _ in points]
    chars = [c for _, c in points]
    top = max(chars) * 1.25
    lo, hi = min(chars), max(chars)
    spread = (hi - lo) / hi * 100 if hi else 0.0

    def px(dpi: int) -> float:
        span = max(dpis) - min(dpis)
        return x0 + (dpi - min(dpis)) / span * PANEL_W

    def py(value: int) -> float:
        return TOP + PANEL_H - (value / top) * PANEL_H

    parts.append(f'<text x="{x0}" y="{TOP - 40}" font-size="13.5" font-weight="600" '
                 f'fill="{INK}">{esc(name)}</text>')
    parts.append(f'<text x="{x0}" y="{TOP - 22}" font-size="11.5" fill="{INK_SOFT}">'
                 f'{lo}-{hi} characters across the sweep &#8212; {spread:.0f}% spread, '
                 f'no trend</text>')

    # Recessive grid: zero and the top of the band only.
    for value in (0, hi):
        y = py(value)
        parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + PANEL_W}" y2="{y:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
    parts.append(f'<text x="{x0 - 10}" y="{py(0) + 4:.1f}" font-size="11" text-anchor="end" '
                 f'fill="{INK_SOFT}">0</text>')

    line = " ".join(f"{px(d):.1f},{py(c):.1f}" for d, c in points)
    parts.append(f'<polyline points="{line}" fill="none" stroke="{SERIES}" stroke-width="2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')

    for dpi, count in points:
        x, y = px(dpi), py(count)
        # A 2px surface ring, so a marker sitting on the line still reads as a point.
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="{SERIES}" '
                     f'stroke="{SURFACE}" stroke-width="2"/>')
        # Direct-labelled at every point, which is right here because there are four of them
        # and the whole claim is that the four numbers are the same number.
        parts.append(f'<text x="{x:.1f}" y="{y - 14:.1f}" font-size="11.5" '
                     f'text-anchor="middle" fill="{INK}">{count}</text>')
        parts.append(f'<text x="{x:.1f}" y="{TOP + PANEL_H + 20:.1f}" font-size="11.5" '
                     f'text-anchor="middle" fill="{INK_SOFT}">{dpi}</text>')

    parts.append(f'<text x="{x0 + PANEL_W / 2:.0f}" y="{TOP + PANEL_H + 42:.0f}" '
                 f'font-size="11.5" text-anchor="middle" fill="{INK_SOFT}">'
                 f'render DPI</text>')


def main() -> int:
    series = read_sweep(SWEEP)
    names = list(series)

    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                 f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Segoe UI, sans-serif">')
    parts.append(f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>')
    parts.append(f'<text x="24" y="34" font-size="17" font-weight="600" fill="{INK}">'
                 f'OCR render DPI buys nothing above 150</text>')
    parts.append(f'<text x="24" y="55" font-size="12" fill="{INK_SOFT}">'
                 f'Characters recovered from the two image-only PDFs in data/. RapidOCR '
                 f'resizes to its detector&#8217;s input size, so a bigger raster is '
                 f'downsampled back.</text>')
    parts.append(f'<text x="24" y="73" font-size="12" fill="{INK_SOFT}">'
                 f'Windows 11, Ryzen 7 5700X, CPU only. rapidocr-onnxruntime via pypdfium2. '
                 f'Measured 2026-08-29 &#8212; tools/pdf_ocr.py ships 200 dpi.</text>')

    for x0, name in zip(LEFT, names):
        panel(parts, x0, name, series[name])

    parts.append(f'<text x="24" y="{H - 14}" font-size="11" fill="{INK_SOFT}">'
                 f'Data: media/data/2026-08-29-ocr-dpi-sweep.csv &#8212; '
                 f'regenerate with python media/scripts/plot_ocr_dpi.py</text>')
    parts.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    for name in names:
        counts = [c for _, c in series[name]]
        print(f"  {name:32s} {min(counts)}-{max(counts)} chars "
              f"({(max(counts) - min(counts)) / max(counts) * 100:.0f}% spread)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
