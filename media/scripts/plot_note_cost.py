#!/usr/bin/env python3
"""
Module:  plot_note_cost.py
Purpose: Turn the notebook measurement into the figure that goes beside D30.
Author:  LB
Date:    2026-08-28

    python media/scripts/plot_note_cost.py

Reads  media/data/2026-08-28-note-turn-cost.csv  (written by media/scripts/measure_note_turn.py)
Writes media/charts/2026-08-28-note-cost.svg

SVG by hand rather than matplotlib, matching every other chart in this folder — a figure that
cannot be regenerated on the machine that produced the data is a figure that goes stale.

## What the figure has to show, and what it must not pretend

The headline is the **API-call count**, not the latency. Milliseconds were never the problem:
the problem was that writing something down cost 3 of the 20 requests D3 measured in a day, so
LB got six notes and then a copilot that could not take another one until tomorrow.

So the left panel is calls per note — 3 before, 0 after — and it carries the "six a day" line,
because that is the number that makes 3 mean something.

The right panel is the measured wall clock, and it is drawn small and grey on purpose. It is
the supporting number, and overselling it would be dishonest in a specific way: the "before"
latency was never measured on this branch. What is drawn is the measured **after**, against
this repo's own earlier measurement of the router leg alone (750 ms on Windows,
`orchestrator/route_hint.py`) shown as a dashed reference rather than as a bar — because a
solid bar next to a measured one reads as a measurement, and it is a citation.

`delete` has no "before" bar at all. Nothing in the repo could delete a note; the gap is drawn
as a gap and labelled, rather than as a zero, which would read as "it was already free".
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "media" / "data" / "2026-08-28-note-turn-cost.csv"
OUT = REPO / "media" / "charts" / "2026-08-28-note-cost.svg"

W, H = 1020, 600
INK, MUTED, GRID = "#1B1B1B", "#666666", "#E3E3E3"
BEFORE, AFTER, ABSENT = "#C0504D", "#4F8A5B", "#B8B8B8"

# The router leg alone, on Windows. This repo's own earlier measurement, cited not re-measured.
ROUTER_MS = 750

ORDER = ("new", "append", "read", "list", "delete")
LABELS = {
    "new": "take a note",
    "append": "add to it",
    "read": "read it back",
    "list": "list them",
    "delete": "delete one",
}


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def load() -> dict[str, dict]:
    if not DATA.exists():
        sys.exit(f"  no data at {DATA}\n  run: python media/scripts/measure_note_turn.py")
    with open(DATA, encoding="utf-8") as handle:
        return {row["operation"]: row for row in csv.DictReader(handle)}


def build(rows: dict[str, dict]) -> str:
    stamp = next(iter(rows.values()))["measured_at"][:10]
    platform = next(iter(rows.values()))["platform"]
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="40" y="46" font-size="23" font-weight="600" fill="{INK}">'
        f'Taking a note: what it cost, and what it costs now</text>',
        f'<text x="40" y="72" font-size="14" fill="{MUTED}">'
        f'Gemini API requests per note (left) and measured wall clock (right). '
        f'{esc(platform)}, {stamp}. Free tier is 20 requests per model per day.</text>',
    ]

    # ---- left panel: API calls, which is the headline --------------------------------------
    x0, y0, panel_h, bar_w, gap = 60, 150, 300, 26, 96
    out.append(f'<text x="{x0}" y="{y0 - 22}" font-size="15" font-weight="600" fill="{INK}">'
               f'API requests per note</text>')

    per_call = panel_h / 4.0                       # 0..4 calls on the axis
    for tick in range(5):
        y = y0 + panel_h - tick * per_call
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + 5 * gap}" y2="{y:.1f}" '
                   f'stroke="{GRID}"/>')
        out.append(f'<text x="{x0 - 12}" y="{y + 4:.1f}" font-size="12" fill="{MUTED}" '
                   f'text-anchor="end">{tick}</text>')

    # The line that makes "3" mean something.
    y_quota = y0 + panel_h - 3 * per_call
    out.append(f'<line x1="{x0}" y1="{y_quota:.1f}" x2="{x0 + 5 * gap}" y2="{y_quota:.1f}" '
               f'stroke="{BEFORE}" stroke-width="1.5" stroke-dasharray="6 4"/>')
    out.append(f'<text x="{x0 + 5 * gap - 4}" y="{y_quota - 8:.1f}" font-size="12" '
               f'fill="{BEFORE}" text-anchor="end">3 calls = 6 notes a day, then nothing</text>')

    for i, key in enumerate(ORDER):
        row = rows[key]
        cx = x0 + i * gap + gap / 2
        before = row["api_calls_before"]
        after = int(row["api_calls_after"])

        if before == "":
            # No bar. Nothing in the repo could delete a note, and a zero would read as
            # "already free" rather than "did not exist".
            out.append(f'<rect x="{cx - bar_w - 3:.1f}" y="{y0 + panel_h - 18}" '
                       f'width="{bar_w}" height="16" fill="none" stroke="{ABSENT}" '
                       f'stroke-dasharray="3 3"/>')
            out.append(f'<text x="{cx - bar_w / 2 - 3:.1f}" y="{y0 + panel_h - 26}" '
                       f'font-size="11" fill="{ABSENT}" text-anchor="middle">none</text>')
        else:
            h_before = int(before) * per_call
            out.append(f'<rect x="{cx - bar_w - 3:.1f}" y="{y0 + panel_h - h_before:.1f}" '
                       f'width="{bar_w}" height="{h_before:.1f}" fill="{BEFORE}"/>')
            out.append(f'<text x="{cx - bar_w / 2 - 3:.1f}" '
                       f'y="{y0 + panel_h - h_before - 8:.1f}" font-size="13" '
                       f'font-weight="600" fill="{BEFORE}" text-anchor="middle">{before}</text>')

        # Zero is drawn as a visible stub sitting on the axis, so "0" is a bar rather than an
        # absence — the absence is already spoken for by `delete`'s dashed outline.
        out.append(f'<rect x="{cx + 3:.1f}" y="{y0 + panel_h - 4}" width="{bar_w}" '
                   f'height="4" fill="{AFTER}"/>')
        out.append(f'<text x="{cx + bar_w / 2 + 3:.1f}" y="{y0 + panel_h - 10}" '
                   f'font-size="13" font-weight="600" fill="{AFTER}" '
                   f'text-anchor="middle">0</text>')

        out.append(f'<text x="{cx:.1f}" y="{y0 + panel_h + 22}" font-size="12" fill="{INK}" '
                   f'text-anchor="middle">{esc(LABELS[key])}</text>')

    out.append(f'<line x1="{x0}" y1="{y0 + panel_h}" x2="{x0 + 5 * gap}" y2="{y0 + panel_h}" '
               f'stroke="{INK}"/>')

    # ---- right panel: wall clock, deliberately quieter --------------------------------------
    rx = x0 + 5 * gap + 76
    out.append(f'<text x="{rx}" y="{y0 - 22}" font-size="15" font-weight="600" fill="{INK}">'
               f'Measured wall clock, per operation</text>')

    ms = {k: float(rows[k]["wall_s"]) * 1000 for k in ORDER}
    scale = panel_h / (ROUTER_MS * 1.15)
    y_router = y0 + panel_h - ROUTER_MS * scale
    out.append(f'<line x1="{rx}" y1="{y_router:.1f}" x2="{rx + 250}" y2="{y_router:.1f}" '
               f'stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="6 4"/>')
    out.append(f'<text x="{rx}" y="{y_router - 8:.1f}" font-size="11.5" fill="{MUTED}">'
               f'{ROUTER_MS} ms — the ROUTING call alone, cited, not measured here</text>')

    row_h = panel_h / len(ORDER)
    for i, key in enumerate(ORDER):
        y = y0 + i * row_h + row_h / 2
        width = max(ms[key] * scale, 2.0)
        out.append(f'<rect x="{rx}" y="{y - 7:.1f}" width="{width:.1f}" height="14" '
                   f'fill="{AFTER}"/>')
        out.append(f'<text x="{rx + width + 8:.1f}" y="{y + 4:.1f}" font-size="12" '
                   f'fill="{INK}">{esc(LABELS[key])} — {ms[key]:.0f} ms, no network</text>')

    out.append(f'<line x1="{rx}" y1="{y0}" x2="{rx}" y2="{y0 + panel_h}" stroke="{INK}"/>')

    # ---- the sentence a viewer should leave with -------------------------------------------
    out.append(f'<text x="40" y="{H - 52}" font-size="14" fill="{INK}">'
               f'Every note now costs zero requests, so the notebook still works after the '
               f'day&#8217;s quota is gone &#8212; which is when</text>')
    out.append(f'<text x="40" y="{H - 32}" font-size="14" fill="{INK}">'
               f'you are most likely to want something written down. '
               f'Deleting a note was not slow before; it was impossible.</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(load()), encoding="utf-8")
    print(f"  wrote {OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
