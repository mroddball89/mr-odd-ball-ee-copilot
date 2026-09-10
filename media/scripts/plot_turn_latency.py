#!/usr/bin/env python3
"""
Module:  plot_turn_latency.py
Purpose: Draw the turn-latency chart from the CSV, so the figure can always be rebuilt.
Author:  LB
Date:    2026-08-19

    python media/scripts/plot_turn_latency.py

Reads `media/data/*-turn-latency.csv` (and the before/after pair) and writes
`media/charts/turn-latency.svg`.

**No matplotlib.** It is not installed here and pulling it in for one bar chart would add tens
of megabytes to a Pi image for a figure this script can emit directly. SVG is text; a bar chart
is rectangles. The output is also sharper than a PNG at any zoom, which matters when the figure
ends up on a video timeline.

Per LB's convention the chart is never the artifact on its own — the CSV beside it is the
evidence and this script is the method, and all three are committed together.
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "media" / "data"
CHARTS = REPO_ROOT / "media" / "charts"

BUDGET_S = 2.0          # docs/PLAN.md in the standalone assistant

W, H = 900, 470
PAD_L, PAD_R, PAD_T, PAD_B = 210, 30, 74, 96
PLOT_W = W - PAD_L - PAD_R
PLOT_H = H - PAD_T - PAD_B

INK = "#1B2430"
MUTED = "#5C6B7F"
GRID = "#DCE3EC"
BAR_ROUTE = "#4C7FE0"       # the router call — paid on every turn
BAR_AGENT = "#E8A33D"       # the agent call — paid only when the tables miss
BUDGET = "#C6413F"


def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def short(question: str) -> str:
    q = question.replace("what is the ", "").replace("what does ", "").replace("what is ", "")
    q = q.replace(" stand for", "").replace(" mean", "")
    return q if len(q) <= 26 else q[:25] + "…"


def render(rows: list[dict], out: Path, before: dict[str, float] | None = None) -> None:
    rows = [r for r in rows if r.get("ok") == "yes"]
    if not rows:
        raise SystemExit("no successful rows to plot")

    top = max(max(float(r["total_s"]) for r in rows),
              max(before.values()) if before else 0, BUDGET_S) * 1.12
    n = len(rows)
    band = PLOT_H / n
    bar_h = min(19.0, band * 0.5)

    def x(seconds: float) -> float:
        return PAD_L + (seconds / top) * PLOT_W

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="{PAD_L}" y="30" font-size="16" font-weight="600" fill="{INK}">'
        f'Mr Odd Ball — time to answer, by question</text>',
        f'<text x="{PAD_L}" y="50" font-size="12" fill="{MUTED}">'
        f'UTILITY route, measured 2026-08-19 on the Windows desk box. '
        f'Excludes speech-to-text and Piper synthesis.</text>',
    ]

    # gridlines every 0.5s, labelled in seconds — axis units are not optional
    tick = 0.5
    t = 0.0
    while t <= top:
        gx = x(t)
        svg.append(f'<line x1="{gx:.1f}" y1="{PAD_T}" x2="{gx:.1f}" y2="{PAD_T + PLOT_H}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        svg.append(f'<text x="{gx:.1f}" y="{PAD_T + PLOT_H + 18}" font-size="11" '
                   f'fill="{MUTED}" text-anchor="middle">{t:.1f}</text>')
        t += tick
    svg.append(f'<text x="{PAD_L + PLOT_W / 2:.0f}" y="{PAD_T + PLOT_H + 40}" font-size="12" '
               f'fill="{MUTED}" text-anchor="middle">seconds</text>')

    # the budget line
    bx = x(BUDGET_S)
    svg.append(f'<line x1="{bx:.1f}" y1="{PAD_T - 8}" x2="{bx:.1f}" y2="{PAD_T + PLOT_H}" '
               f'stroke="{BUDGET}" stroke-width="1.5" stroke-dasharray="5 4"/>')
    svg.append(f'<text x="{bx + 6:.1f}" y="{PAD_T - 12}" font-size="11" fill="{BUDGET}">'
               f'2.0 s budget (PLAN.md)</text>')

    for i, row in enumerate(rows):
        cy = PAD_T + band * i + band / 2
        label = short(row["question"])
        route_s, total_s = float(row["route_s"]), float(row["total_s"])

        svg.append(f'<text x="{PAD_L - 10}" y="{cy + 4:.1f}" font-size="12" fill="{INK}" '
                   f'text-anchor="end">{esc(label)}</text>')

        # router first, agent stacked after it — the split is the story
        svg.append(f'<rect x="{PAD_L}" y="{cy - bar_h / 2:.1f}" '
                   f'width="{max(x(route_s) - PAD_L, 1):.1f}" height="{bar_h:.1f}" '
                   f'fill="{BAR_ROUTE}" rx="2"/>')
        if total_s - route_s > 0.02:
            svg.append(f'<rect x="{x(route_s):.1f}" y="{cy - bar_h / 2:.1f}" '
                       f'width="{x(total_s) - x(route_s):.1f}" height="{bar_h:.1f}" '
                       f'fill="{BAR_AGENT}" rx="2"/>')
        svg.append(f'<text x="{x(total_s) + 7:.1f}" y="{cy + 4:.1f}" font-size="11" '
                   f'fill="{MUTED}">{total_s:.2f} s</text>')

        # the before/after marker, where there is one
        was = (before or {}).get(row["question"])
        if was and abs(was - total_s) > 0.1:
            svg.append(f'<line x1="{x(was):.1f}" y1="{cy - bar_h / 2 - 4:.1f}" '
                       f'x2="{x(was):.1f}" y2="{cy + bar_h / 2 + 4:.1f}" '
                       f'stroke="{INK}" stroke-width="1.5"/>')
            svg.append(f'<text x="{x(was) + 5:.1f}" y="{cy - bar_h / 2 - 7:.1f}" '
                       f'font-size="10" fill="{INK}">was {was:.2f} s</text>')

    ly = H - 34
    for dx, colour, text in ((0, BAR_ROUTE, "router call (every turn)"),
                             (215, BAR_AGENT, "agent call (only when the tables miss)")):
        svg.append(f'<rect x="{PAD_L + dx}" y="{ly - 9}" width="12" height="12" '
                   f'fill="{colour}" rx="2"/>')
        svg.append(f'<text x="{PAD_L + dx + 18}" y="{ly + 1}" font-size="11" fill="{MUTED}">'
                   f'{esc(text)}</text>')

    med = statistics.median(float(r["total_s"]) for r in rows)
    over = sum(1 for r in rows if float(r["total_s"]) > BUDGET_S)
    svg.append(f'<text x="{PAD_L}" y="{H - 12}" font-size="11" fill="{MUTED}">'
               f'median {med:.2f} s · {over}/{len(rows)} over budget · '
               f'router gemini-3.5-flash-lite, agents gemini-3.5-flash</text>')

    svg.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(svg), encoding="utf-8")
    print(f"  wrote {out}  ({len(rows)} bars, median {med:.2f}s, {over} over budget)")


def main() -> int:
    latest = sorted(DATA.glob("*-turn-latency.csv"))
    if not latest:
        print("  no turn-latency CSV found — run media/scripts/measure_turn.py first")
        return 1

    rows = load(latest[-1])

    # The "before" arm: the same questions measured before the acronym rows were added, kept
    # so the figure can show the change rather than only the end state. A fix with no before
    # is an anecdote.
    before_path = DATA / "2026-08-19-turn-latency-before-acronyms.csv"
    before = None
    if before_path.exists():
        before = {r["question"]: float(r["total_s"]) for r in load(before_path)}

    render(rows, CHARTS / "turn-latency.svg", before)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
