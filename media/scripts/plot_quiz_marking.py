#!/usr/bin/env python3
"""
Module: plot_quiz_marking.py
Purpose: Chart what marking a quiz answer costs, before and after it stopped phoning out.
Author: LB
Date:   2026-09-02

    python media/scripts/plot_quiz_marking.py

Reads   media/data/2026-09-02-quiz-marking.csv
Writes  media/charts/quiz-marking.svg

## What this shows

Until 2026-09-02 the quiz marked every answer by sending it to Gemini. `agents/quiz_agent.py`
built a prompt, called `ChatGoogleGenerativeAI`, and asked a language model whether "V = I R"
matches "V = I * R". That is one network round trip per answer.

`tools/quiz_grade.py` now does it on this machine: normalised string matching, a numeric
comparison with a tolerance, and sympy for the algebra. The left panel is the latency, on a log
axis because the two are three orders of magnitude apart and a linear axis would draw every
local bar as a flat line on the floor.

**The right panel is the one that mattered.** The free tier is counted in REQUESTS — 20 per
model name per day (D3) — so a ten-question quiz spent half of a day's budget on marking, and
a second quiz took the router, the persona agent and the firmware agent down with it. Latency
was never the complaint; the complaint was that revising for a midterm silently broke the rest
of the assistant until midnight.

## What is measured and what is cited

Every local bar is measured — `media/scripts/measure_quiz_marking.py`, median of 30 calls per
fixture on this machine. The remote bar is **cited**, from `2026-08-29-turn-latency.csv`, and
the CSV's `source` column says so on every row. Re-measuring it would have spent LB's quota to
learn a number this repo already has, which is the exact cost the change was made to avoid.
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "media" / "data" / "2026-09-02-quiz-marking.csv"
OUT = REPO / "media" / "charts" / "quiz-marking.svg"

# From references/palette.md, the same light-surface set the other charts in media/ use.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
LOCAL = "#2a78d6"
REMOTE = "#b8563e"
GRID = "#e3e2df"

W, H = 940, 520
TOP = 150
PANEL_H = 250
LEFT_X, LEFT_W = 210, 380
RIGHT_X, RIGHT_W = 700, 190


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    if not DATA.exists():
        print(f"missing {DATA.relative_to(REPO).as_posix()} — run "
              f"media/scripts/measure_quiz_marking.py first")
        return 1

    rows = read_rows(DATA)
    local = [r for r in rows if r["grader"] == "local" and "cold" not in r["case"]]
    cold = next((r for r in rows if "cold" in r["case"]), None)
    remote = next(r for r in rows if r["grader"].startswith("remote"))

    bars = [(r["case"], float(r["median_ms"]), LOCAL) for r in local]
    bars.append(("the old path (one Gemini call)", float(remote["median_ms"]), REMOTE))

    import math

    # Log scale. The local bars span 0.005 ms to 15 ms and the remote one is 820 ms; drawn
    # linearly, every local bar is a hairline against the axis and the chart says only "the
    # remote one is big", which the reader already knew.
    floor_ms = 0.001
    top_ms = 1000.0

    def bar_w(ms: float) -> float:
        ms = max(ms, floor_ms)
        span = math.log10(top_ms) - math.log10(floor_ms)
        return max(2.0, (math.log10(ms) - math.log10(floor_ms)) / span * LEFT_W)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
        f'<text x="24" y="34" font-size="17" font-weight="600" fill="{INK}">'
        f'Marking a quiz answer stopped costing a network request</text>',
        f'<text x="24" y="55" font-size="12" fill="{INK_SOFT}">'
        f'Median of 30 calls per case. Windows 11, Ryzen 7 5700X, CPU only. '
        f'Measured 2026-09-02 &#8212; tools/quiz_grade.py.</text>',
        f'<text x="24" y="73" font-size="12" fill="{INK_SOFT}">'
        f'The remote bar is CITED from media/data/2026-08-29-turn-latency.csv, not '
        f're-measured: re-timing it would spend the quota this change exists to save.</text>',
        f'<text x="24" y="{TOP - 34}" font-size="13.5" font-weight="600" fill="{INK}">'
        f'Latency per answer (log scale, milliseconds)</text>',
        f'<text x="{RIGHT_X}" y="{TOP - 34}" font-size="13.5" font-weight="600" fill="{INK}">'
        f'API requests, 10-question quiz</text>',
    ]

    # --- left panel: latency ------------------------------------------------------------
    row_h = PANEL_H / len(bars)
    for index, (label, ms, colour) in enumerate(bars):
        y = TOP + index * row_h
        width = bar_w(ms)
        parts.append(f'<rect x="{LEFT_X}" y="{y:.1f}" width="{width:.1f}" '
                     f'height="{row_h - 8:.1f}" fill="{colour}" rx="2"/>')
        parts.append(f'<text x="{LEFT_X - 10}" y="{y + row_h / 2:.1f}" font-size="11" '
                     f'text-anchor="end" fill="{INK_SOFT}">{esc(label)}</text>')
        shown = f"{ms:.3f} ms" if ms < 1 else (f"{ms:.1f} ms" if ms < 100 else f"{ms:.0f} ms")
        parts.append(f'<text x="{LEFT_X + width + 8:.1f}" y="{y + row_h / 2:.1f}" '
                     f'font-size="11" fill="{INK_SOFT}">{shown}</text>')

    # Decade gridlines, labelled, so the log axis is readable rather than merely compact.
    for decade in (0.01, 1, 100):
        x = LEFT_X + bar_w(decade)
        parts.append(f'<line x1="{x:.1f}" y1="{TOP - 12}" x2="{x:.1f}" '
                     f'y2="{TOP + PANEL_H - 4:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{TOP + PANEL_H + 12:.1f}" font-size="10" '
                     f'text-anchor="middle" fill="{INK_SOFT}">'
                     f'{decade:g} ms</text>')

    # --- right panel: the number that mattered ------------------------------------------
    scale = PANEL_H / 20.0                                   # 20 = the whole daily free tier
    for index, (label, count, colour) in enumerate((("after", 0, LOCAL), ("before", 10, REMOTE))):
        x = RIGHT_X + index * 105
        height = max(2.0, count * scale)
        y = TOP + PANEL_H - height
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="62" height="{height:.1f}" '
                     f'fill="{colour}" rx="2"/>')
        parts.append(f'<text x="{x + 31}" y="{y - 8:.1f}" font-size="15" font-weight="600" '
                     f'text-anchor="middle" fill="{INK}">{count}</text>')
        parts.append(f'<text x="{x + 31}" y="{TOP + PANEL_H + 16:.1f}" font-size="11" '
                     f'text-anchor="middle" fill="{INK_SOFT}">{label}</text>')

    # The daily ceiling, drawn, because 10 means nothing without the 20 it was half of.
    ceiling = TOP + PANEL_H - 20 * scale
    parts.append(f'<line x1="{RIGHT_X - 10}" y1="{ceiling:.1f}" x2="{RIGHT_X + RIGHT_W}" '
                 f'y2="{ceiling:.1f}" stroke="{REMOTE}" stroke-width="1" '
                 f'stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{RIGHT_X - 10}" y="{ceiling - 6:.1f}" font-size="10.5" '
                 f'fill="{INK_SOFT}">20/day &#8212; the whole free tier (D3)</text>')

    if cold:
        parts.append(f'<text x="24" y="{H - 34}" font-size="11" fill="{INK_SOFT}">'
                     f'The first answer of a session costs '
                     f'{float(cold["median_ms"]):.0f} ms, which is '
                     f'<tspan font-style="italic">import sympy</tspan> and is paid once.'
                     f'</text>')
    parts.append(f'<text x="24" y="{H - 14}" font-size="11" fill="{INK_SOFT}">'
                 f'Data: media/data/2026-09-02-quiz-marking.csv &#8212; regenerate with '
                 f'python media/scripts/measure_quiz_marking.py then '
                 f'python media/scripts/plot_quiz_marking.py</text>')
    parts.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    fastest = min(float(r["median_ms"]) for r in local)
    slowest = max(float(r["median_ms"]) for r in local)
    print(f"  local marking: {fastest:.3f}-{slowest:.1f} ms, 0 requests")
    print(f"  the old path:  {float(remote['median_ms']):.0f} ms, 1 request per answer "
          f"({remote['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
