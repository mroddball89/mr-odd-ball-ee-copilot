#!/usr/bin/env python3
"""
Module:  plot_app_launch.py
Purpose: Turn the app-launch measurement into the figure that goes beside D10.
Author:  LB
Date:    2026-08-21

    python media/scripts/plot_app_launch.py

Reads  media/data/2026-08-21-app-launch.csv   (written on the Pi by tools/measure_launch.py)
Writes media/charts/2026-08-21-app-launch.svg

SVG by hand rather than matplotlib, matching `measure_ampere_conversion.py` and
`measure_kicad_parser.py` — the Pi has no matplotlib and a chart that cannot be regenerated on
the machine that produced the data is a chart that goes stale.

## What the figure has to show

The headline is **not** speed. All three arms return in about 40 ms, and a bar chart of that
would say they are equivalent, which is the opposite of the finding. What separates them is
whether an application was running afterwards, and **what he said about it**.

So: bars for "did a window open", and beside each one the sentence he spoke. The middle arm is
the reported bug and is drawn in the warning colour, because a viewer skimming this should land
on it without reading the caption. "Done." over an empty screen is the whole story.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "media" / "data" / "2026-08-21-app-launch.csv"
OUT = REPO / "media" / "charts" / "2026-08-21-app-launch.svg"

W, H = 1000, 560
PAD_L, PAD_T = 76, 96
PLOT_W, PLOT_H = 380, 250

INK, MUTED, GRID = "#1B1B1B", "#666666", "#E3E3E3"
BAR_BAD, BAR_LIE, BAR_GOOD = "#C0504D", "#E8A33D", "#4F8A5B"

ORDER = ("old-blocking", "old-background", "new")
LABELS = {
    "old-blocking": ("before", "firefox"),
    "old-background": ("before", "firefox &"),
    "new": ("after", "launch_app"),
}
COLOURS = {"old-blocking": BAR_BAD, "old-background": BAR_LIE, "new": BAR_GOOD}


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def load() -> tuple[dict, dict, dict]:
    if not DATA.exists():
        sys.exit(f"no data at {DATA}\nRun tools/measure_launch.py on the Pi first.")

    alive: dict[str, list[int]] = defaultdict(list)
    spoken: dict[str, str] = {}
    survived: dict[str, str] = {}
    with DATA.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            arm = row["arm"]
            if row.get("survived_restart"):
                survived[arm] = row["survived_restart"]
            if row.get("alive"):
                alive[arm].append(int(row["alive"]))
                spoken.setdefault(arm, row.get("spoken", ""))
            elif row.get("alive") == "0":
                alive[arm].append(0)
                spoken.setdefault(arm, row.get("spoken", ""))
    return alive, spoken, survived


def main() -> int:
    alive, spoken, survived = load()
    arms = [a for a in ORDER if a in alive]
    if not arms:
        sys.exit("no trial rows in the CSV")
    trials = max(len(v) for v in alive.values())

    def y_of(count: int) -> float:
        return PAD_T + PLOT_H - (count / trials) * PLOT_H

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="{PAD_L}" y="38" font-size="19" font-weight="600" fill="{INK}">'
        f'{esc("Asking Mr Odd Ball to open Firefox — 5 trials per method")}</text>',
        f'<text x="{PAD_L}" y="60" font-size="13" fill="{MUTED}">'
        f'{esc("Raspberry Pi 5, Debian 13 trixie, labwc/Wayland, 2026-08-21. "
               "All three methods return in ~40 ms — speed was never what separated them.")}'
        '</text>',
    ]

    # --- gridlines and y axis --------------------------------------------------------------
    for count in range(trials + 1):
        y = y_of(count)
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + PLOT_W}" y2="{y:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{PAD_L - 12}" y="{y + 4:.1f}" font-size="12" fill="{MUTED}" '
                   f'text-anchor="end">{count}</text>')
    out.append(f'<text x="{PAD_L - 46}" y="{PAD_T + PLOT_H / 2:.1f}" font-size="12" '
               f'fill="{MUTED}" text-anchor="middle" '
               f'transform="rotate(-90 {PAD_L - 46} {PAD_T + PLOT_H / 2:.1f})">'
               f'{esc("trials with the app running")}</text>')

    # --- bars --------------------------------------------------------------------------------
    bar_w = 78
    slot = PLOT_W / len(arms)
    for i, arm in enumerate(arms):
        n = sum(alive[arm])
        x = PAD_L + i * slot + (slot - bar_w) / 2
        y = y_of(n)
        height = PAD_T + PLOT_H - y
        if height < 1:                      # a zero bar still needs to be visible as a zero
            out.append(f'<line x1="{x:.1f}" y1="{PAD_T + PLOT_H}" x2="{x + bar_w:.1f}" '
                       f'y2="{PAD_T + PLOT_H}" stroke="{COLOURS[arm]}" stroke-width="3"/>')
        else:
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{height:.1f}" '
                       f'fill="{COLOURS[arm]}" rx="3"/>')
        out.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 10:.1f}" font-size="16" '
                   f'font-weight="600" fill="{INK}" text-anchor="middle">{n}/{trials}</text>')

        when, cmd = LABELS[arm]
        out.append(f'<text x="{x + bar_w / 2:.1f}" y="{PAD_T + PLOT_H + 21:.1f}" '
                   f'font-size="12" fill="{MUTED}" text-anchor="middle">{esc(when)}</text>')
        out.append(f'<text x="{x + bar_w / 2:.1f}" y="{PAD_T + PLOT_H + 40:.1f}" '
                   f'font-size="13" font-weight="600" fill="{INK}" text-anchor="middle" '
                   f'font-family="Consolas, monospace">{esc(cmd)}</text>')
        note = {"survived": "survived a restart", "died": "died on restart"}.get(
            survived.get(arm, ""), "never started")
        out.append(f'<text x="{x + bar_w / 2:.1f}" y="{PAD_T + PLOT_H + 58:.1f}" '
                   f'font-size="11" fill="{MUTED}" text-anchor="middle">{esc(note)}</text>')

    out.append(f'<line x1="{PAD_L}" y1="{PAD_T + PLOT_H}" x2="{PAD_L + PLOT_W}" '
               f'y2="{PAD_T + PLOT_H}" stroke="{INK}" stroke-width="1.5"/>')

    # --- what he said -------------------------------------------------------------------------
    qx = PAD_L + PLOT_W + 72
    qw = W - qx - 40
    out.append(f'<text x="{qx}" y="{PAD_T - 16}" font-size="14" font-weight="600" '
               f'fill="{INK}">{esc("...and what he said about it")}</text>')
    for i, arm in enumerate(arms):
        y = PAD_T + i * 86
        out.append(f'<rect x="{qx}" y="{y}" width="{qw}" height="70" rx="5" '
                   f'fill="{COLOURS[arm]}" fill-opacity="0.10" stroke="{COLOURS[arm]}" '
                   f'stroke-width="1.2"/>')
        when, cmd = LABELS[arm]
        out.append(f'<text x="{qx + 14}" y="{y + 21}" font-size="11" fill="{MUTED}" '
                   f'font-family="Consolas, monospace">{esc(cmd)}</text>')
        said = spoken.get(arm, "")
        # Two lines if it will not fit on one at this width.
        if len(said) > 46:
            cut = said.rfind(" ", 0, 46)
            head, tail = said[:cut], said[cut + 1:]
        else:
            head, tail = said, ""
        out.append(f'<text x="{qx + 14}" y="{y + 43}" font-size="14" fill="{INK}" '
                   f'font-style="italic">{esc(chr(8220) + head)}</text>')
        out.append(f'<text x="{qx + 14}" y="{y + 61}" font-size="14" fill="{INK}" '
                   f'font-style="italic">{esc(tail + chr(8221) if tail else chr(8221))}</text>')

    out.append(f'<text x="{PAD_L}" y="{H - 40}" font-size="12" fill="#7A4A12">'
               f'{esc("The middle method is the one that was reported: the shell returns 0 "
                      "immediately, Firefox dies unseen, and he says “Done.”")}</text>')
    out.append(f'<text x="{PAD_L}" y="{H - 20}" font-size="12" fill="{MUTED}">'
               f'{esc("A confident success is the one failure shape nobody escalates. "
                      "Data: media/data/2026-08-21-app-launch.csv")}</text>')
    out.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
