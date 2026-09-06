#!/usr/bin/env python3
"""
Module: plot_wasted_turns.py
Purpose: Chart where eight days of thinking time actually went.
Author: LB
Date:   2026-09-03

    python media/scripts/plot_wasted_turns.py

Reads   media/data/2026-09-03-wasted-turns.csv
Writes  media/charts/wasted-turns.svg

## What this shows

127 real turns, 2026-08-26 to 2026-09-03, off `data/oddball.log`. The left panel splits the
agent's total thinking time into requests and non-requests — a non-request being an
acknowledgement, a fragment, or room tone that got past the credibility gate. **Just under a
third of it went on things nobody asked.**

The right panel is why that mattered rather than merely being untidy: those turns are not
cheap, they are the most expensive ones in the log. 'Yeah, yeah, yeah, yeah.' cost 147.8
seconds of a shut microphone and a frozen face, because the cloud branch had no timeout on it
and a free-tier model on a bad day takes as long as it takes.

Two fixes, and they attack different halves of the same bar. The `ack` intent in
`orchestrator/instant.py` stops these utterances reaching the router at all — they are answered
locally, in microseconds, for no API call. `CLOUD_TIMEOUT_S` bounds whatever still gets through
at 20 seconds, so the worst case is a slow turn rather than a hung one.

## What the number is NOT

The classifier in `measure_wasted_turns.py` is a strict keyword list — every word of the
utterance must be an acknowledgement or filler, and it must be six words or shorter. It
therefore **undercounts**: 'Mr. Albo.' at 134.6 seconds is plainly room tone and is not in this
total, because "albo" is not a word anybody thought to list. The real figure is higher than
32%; this is the floor, which is the direction a claim should err in.
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "media" / "data" / "2026-09-03-wasted-turns.csv"
OUT = REPO / "media" / "charts" / "wasted-turns.svg"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
REAL = "#2a78d6"
WASTE = "#b8563e"
GRID = "#e3e2df"

W, H = 940, 500
TOP = 150
PANEL_H = 250


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> int:
    if not DATA.exists():
        print(f"missing {DATA.relative_to(REPO).as_posix()} — run "
              f"media/scripts/measure_wasted_turns.py first")
        return 1

    with DATA.open(encoding="utf-8") as fh:
        rows = [{**r, "agent_s": float(r["agent_s"]),
                 "wasted": r["wasted"] == "True"} for r in csv.DictReader(fh)]

    wasted = [r for r in rows if r["wasted"]]
    real = [r for r in rows if not r["wasted"]]
    wasted_s = sum(r["agent_s"] for r in wasted)
    real_s = sum(r["agent_s"] for r in real)
    share = wasted_s / (wasted_s + real_s) * 100

    worst = sorted(wasted, key=lambda r: -r["agent_s"])[:6]

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
        f'<text x="24" y="34" font-size="17" font-weight="600" fill="{INK}">'
        f'Nearly a third of his thinking time answered nobody</text>',
        f'<text x="24" y="55" font-size="12" fill="{INK_SOFT}">'
        f'{len(rows)} turns from data/oddball.log, 2026-08-26 to 2026-09-03. A '
        f'&#8220;non-request&#8221; is an acknowledgement, a fragment, or room tone that got '
        f'past the credibility gate.</text>',
        f'<text x="24" y="73" font-size="12" fill="{INK_SOFT}">'
        f'The keyword classifier is strict, so this UNDERCOUNTS &#8212; '
        f'&#8216;Mr. Albo.&#8217; at 134.6s is room tone and is not in this total.</text>',
        f'<text x="24" y="{TOP - 34}" font-size="13.5" font-weight="600" fill="{INK}">'
        f'Total agent seconds</text>',
        f'<text x="470" y="{TOP - 34}" font-size="13.5" font-weight="600" fill="{INK}">'
        f'The most expensive non-requests</text>',
    ]

    # --- left: the split ----------------------------------------------------------------
    total = wasted_s + real_s
    scale = PANEL_H / total
    y = TOP
    for label, seconds, count, colour in (("a real request", real_s, len(real), REAL),
                                          ("not a request", wasted_s, len(wasted), WASTE)):
        height = seconds * scale
        parts.append(f'<rect x="60" y="{y:.1f}" width="120" height="{height:.1f}" '
                     f'fill="{colour}"/>')
        parts.append(f'<text x="196" y="{y + height / 2 - 4:.1f}" font-size="12.5" '
                     f'font-weight="600" fill="{INK}">{esc(label)}</text>')
        parts.append(f'<text x="196" y="{y + height / 2 + 13:.1f}" font-size="11.5" '
                     f'fill="{INK_SOFT}">{seconds / 60:.1f} min over {count} turns</text>')
        y += height

    parts.append(f'<text x="60" y="{TOP + PANEL_H + 26:.1f}" font-size="22" '
                 f'font-weight="600" fill="{WASTE}">{share:.0f}%</text>')
    parts.append(f'<text x="112" y="{TOP + PANEL_H + 26:.1f}" font-size="12" '
                 f'fill="{INK_SOFT}">of his thinking went on utterances</text>')
    parts.append(f'<text x="112" y="{TOP + PANEL_H + 42:.1f}" font-size="12" '
                 f'fill="{INK_SOFT}">that were never requests</text>')

    # --- right: the worst ---------------------------------------------------------------
    longest = max(r["agent_s"] for r in worst)
    row_h = PANEL_H / len(worst)
    for index, row in enumerate(worst):
        top = TOP + index * row_h
        width = row["agent_s"] / longest * 250
        parts.append(f'<rect x="470" y="{top:.1f}" width="{width:.1f}" '
                     f'height="{row_h - 10:.1f}" fill="{WASTE}" rx="2"/>')
        parts.append(f'<text x="{470 + width + 8:.1f}" y="{top + row_h / 2 - 2:.1f}" '
                     f'font-size="11.5" font-weight="600" fill="{INK}">'
                     f'{row["agent_s"]:.1f}s</text>')
        label = row["transcript"][:34]
        parts.append(f'<text x="474" y="{top + row_h / 2 + 13:.1f}" font-size="11" '
                     f'fill="{SURFACE if width > 120 else INK_SOFT}">'
                     f'&#8220;{esc(label)}&#8221;</text>')

    # The bound, drawn, because it is the fix and it belongs on the same axis as the problem.
    bound_x = 470 + 20.0 / longest * 250
    parts.append(f'<line x1="{bound_x:.1f}" y1="{TOP - 12}" x2="{bound_x:.1f}" '
                 f'y2="{TOP + PANEL_H - 4:.1f}" stroke="{REAL}" stroke-width="1.5" '
                 f'stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{bound_x + 6:.1f}" y="{TOP + PANEL_H + 12:.1f}" font-size="10.5" '
                 f'fill="{REAL}">CLOUD_TIMEOUT_S = 20s, the new ceiling</text>')

    parts.append(f'<text x="24" y="{H - 32}" font-size="11" fill="{INK_SOFT}">'
                 f'Fixed two ways: the `ack` intent answers these locally for no API call, and '
                 f'CLOUD_TIMEOUT_S bounds whatever still reaches a model.</text>')
    parts.append(f'<text x="24" y="{H - 14}" font-size="11" fill="{INK_SOFT}">'
                 f'Data: media/data/2026-09-03-wasted-turns.csv &#8212; regenerate with '
                 f'python media/scripts/measure_wasted_turns.py then '
                 f'python media/scripts/plot_wasted_turns.py</text>')
    parts.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    print(f"  {share:.0f}% of {total / 60:.1f} minutes of agent time was not a request")
    print(f"  worst single non-request: {longest:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
