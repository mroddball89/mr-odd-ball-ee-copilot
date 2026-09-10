#!/usr/bin/env python3
"""
Module: plot_wake_retrain.py
Purpose: Chart what retraining the wake model changed, and which threshold that licenses.
Author: LB
Date:   2026-09-10

    python media/scripts/plot_wake_retrain.py

Reads   media/data/2026-09-09-wake-retrain-compare.csv   old_peak and new_peak, per clip
        media/data/2026-09-07-wake-fixtures.csv          the label that says what each clip IS
Writes  media/charts/2026-09-09-wake-retrain.svg

## What this shows

The 2026-08-11 model was trained on synthetic speech and had never heard LB's room. It scored
`hey-mr-on-call-01.wav` — deliberately NOT the wake phrase — at 0.8214, while transcribing-clean
calls sat at 0.03. No threshold separates those, which is why 0.76 -> 0.53 -> 0.76 happened in
one week and reverted twice. The 2026-09-09 model was retrained on 60+ real calls with LB's own
false fires as hard negatives.

**Left panel — the sweep.** Every threshold from 0.05 to 0.95, and at each one: how many real
calls fire, and how many things that are not a call fire. The old model's two curves overlap,
which is the whole problem drawn — there is no x where one is high and the other is zero. The
new model's separate, and the gap between them is the range of usable thresholds.

**Right panel — why the gap exists.** The highest score each class reaches. The old model's
loudest not-call hit 0.8214; the new model's tops out at **0.0342**. That collapse is the whole
result: it opens a band of thresholds — everything from 0.05 to 0.2 and beyond — in which
nothing at all false-fires, and the old model had no such band at any setting.

**What the chart then says about 0.2.** It is safe but not optimal. 0.2 answers 25 of 53 real
calls; 0.05 answers 28, with the same zero false fires, because the nearest not-call is still
0.0158 below it. The shaded band is drawn for exactly this reason — the useful fact about it is
its WIDTH, and that 0.2 sits at the expensive end of it.

## The classes, and one judgement call in them

The fixtures CSV files seven clips as `known-limits`, which mixes two different things. Six of
them ARE the wake phrase — four spoken from across the room (`far-*`), two without the leading
"Hey" (`mister-odd-ball-*`). One is not: `hey-mr-on-call-01` is a deliberate near-miss phrase.

LB ruled on 2026-09-10 that "Mister Odd Ball" without the "Hey" is a call he expects answered.
So this chart counts all six as **real calls** and leaves `hey-mr-on-call` with the negatives,
and says so on its face rather than in a footnote — moving a clip between classes changes every
number on the page, and a reader who cannot see which way it was moved cannot check the claim.

**This is the harder accounting, not the flattering one.** Those six clips are among the hardest
in the set, so counting them as calls pushes the recall line DOWN at every threshold — 25 of 53
at 0.2, where the old taxonomy's 47 positives would have reported 24 of 47. Same model, same
scores, four points of apparent recall, and the only thing that moved was a label.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPARE = REPO / "media" / "data" / "2026-09-09-wake-retrain-compare.csv"
FIXTURES = REPO / "media" / "data" / "2026-09-07-wake-fixtures.csv"
OUT = REPO / "media" / "charts" / "2026-09-09-wake-retrain.svg"

# The one clip in `known-limits` that is genuinely not a call. Everything else in that kind is
# the wake phrase, spoken far away or without its first word.
NOT_A_CALL = "hey-mr-on-call"

# From references/palette.md, the same light-surface set the other charts in media/ use.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
NEW = "#2a78d6"
OLD = "#b8563e"
GRID = "#e3e2df"
CHOSEN = "#1f7a4d"

W, H = 960, 632
TOP = 168
PANEL_H = 260
LEFT_X, LEFT_W = 74, 470
RIGHT_X, RIGHT_W = 660, 196


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def vis_len(text: str) -> int:
    """Visible character count, counting an XML entity as the one glyph it renders as.

    `&#8212;` is seven characters and one em dash. Wrapping on `len()` therefore breaks a line
    early for every dash and quote mark in it, which is most of the prose on this chart.
    """
    return len(re.sub(r"&#\d+;", "X", text))


def wrap_text(text: str, x: float, y: float, size: float, fill: str,
              max_chars: int, line_height: float | None = None) -> tuple[list[str], float]:
    """SVG `<text>` lines, wrapped to `max_chars` visible characters. Returns (parts, next_y).

    SVG has no flow layout — a `<text>` runs to the edge of the canvas and past it, silently.
    Nothing errors and nothing clips; the sentence simply leaves the page. Three of the lines
    on this chart did exactly that at 960px wide, which the geometry check could not see
    because their x and y were both perfectly in bounds.
    """
    step = line_height if line_height is not None else size + 6
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if current and vis_len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    parts = [f'<text x="{x:.1f}" y="{y + i * step:.1f}" font-size="{size}" fill="{fill}">'
             f'{line}</text>' for i, line in enumerate(lines)]
    return parts, y + len(lines) * step


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def classify(rows: list[dict], labels: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Split every clip into real calls and not-calls. See the module docstring for the rule."""
    calls, not_calls = [], []
    for row in rows:
        label = labels.get(row["clip"], "")
        is_call = row["kind"] == "positive" or (
            row["kind"] == "known-limits" and label != NOT_A_CALL)
        (calls if is_call else not_calls).append(row)
    return calls, not_calls


def main() -> int:
    for path in (COMPARE, FIXTURES):
        if not path.exists():
            print(f"missing {path.relative_to(REPO).as_posix()} — run "
                  f"media/scripts/compare_wake_models.py first")
            return 1

    rows = read_rows(COMPARE)
    labels = {r["clip"]: r.get("label", "") for r in read_rows(FIXTURES)}
    calls, not_calls = classify(rows, labels)

    def fires(subset: list[dict], column: str, threshold: float) -> int:
        return sum(1 for r in subset if float(r[column]) >= threshold)

    steps = [round(0.05 + i * 0.025, 4) for i in range(37)]          # 0.05 .. 0.95
    chosen = 0.2

    def x_of(threshold: float) -> float:
        return LEFT_X + (threshold - 0.05) / 0.90 * LEFT_W

    def y_of(count: int, total: int) -> float:
        return TOP + PANEL_H - (count / total) * PANEL_H

    def path_for(subset: list[dict], column: str, total: int) -> str:
        pts = [f"{x_of(t):.1f},{y_of(fires(subset, column, t), total):.1f}" for t in steps]
        return "M" + " L".join(pts)

    n_calls, n_not = len(calls), len(not_calls)
    new_hit = fires(calls, "new_peak", chosen)
    old_hit = fires(calls, "old_peak", chosen)
    new_false = fires(not_calls, "new_peak", chosen)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
        f'<text x="24" y="34" font-size="17" font-weight="600" fill="{INK}">'
        f'The retrain separated the classes; it did not make him hear better</text>',
    ]

    intro, _ = wrap_text(
        f'{len(rows)} clips scored by the real ONNX model. Old = '
        f'hey_mr_odd_ball.2026-08-11.onnx (synthetic training set), new = 2026-09-09, '
        f'retrained on 60+ real calls with LB&#8217;s own false fires as hard negatives. '
        f'Real calls = {n_calls}, INCLUDING the 4 across-the-room and 2 &#8220;Mister Odd '
        f'Ball&#8221; clips; not-calls = {n_not}, including hey-mr-on-call, a deliberate '
        f'near-miss phrase. Counting those 6 hard clips as calls is the stricter accounting '
        f'&#8212; it lowers the recall line everywhere. Measured 2026-09-09.',
        x=24, y=55, size=12, fill=INK_SOFT, max_chars=128, line_height=17)
    parts.extend(intro)

    parts += [
        f'<text x="24" y="{TOP - 30}" font-size="13.5" font-weight="600" fill="{INK}">'
        f'Threshold sweep &#8212; what fires, out of {n_calls} real calls and '
        f'{n_not} not-calls</text>',
        f'<text x="{RIGHT_X}" y="{TOP - 30}" font-size="13.5" font-weight="600" fill="{INK}">'
        f'Loudest score each class reaches</text>',
    ]

    # --- left panel: the sweep ----------------------------------------------------------
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = TOP + PANEL_H - frac * PANEL_H
        parts.append(f'<line x1="{LEFT_X}" y1="{y:.1f}" x2="{LEFT_X + LEFT_W}" y2="{y:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{LEFT_X - 8}" y="{y + 3.5:.1f}" font-size="10" '
                     f'text-anchor="end" fill="{INK_SOFT}">{frac * 100:.0f}%</text>')
    for tick in (0.05, 0.2, 0.4, 0.6, 0.76, 0.95):
        parts.append(f'<text x="{x_of(tick):.1f}" y="{TOP + PANEL_H + 15:.1f}" font-size="10" '
                     f'text-anchor="middle" fill="{INK_SOFT}">{tick:g}</text>')
    parts.append(f'<text x="{LEFT_X + LEFT_W / 2:.1f}" y="{TOP + PANEL_H + 34:.1f}" '
                 f'font-size="11" text-anchor="middle" fill="{INK_SOFT}">'
                 f'wake threshold (model score, 0&#8211;1)</text>')

    # --- the band no threshold could previously occupy -----------------------------------
    #
    # Every not-call the new model scores lands under `loudest_false`, so any threshold above it
    # false-fires on NOTHING in this set. Drawn as a band rather than stated as a number,
    # because the useful fact is its WIDTH: the old model's band was empty, and choosing 0.2
    # inside it is leaving recall unclaimed at no measured cost.
    loudest_false = max(float(r["new_peak"]) for r in not_calls)
    band_left = x_of(max(0.05, loudest_false))
    parts.append(f'<rect x="{band_left:.1f}" y="{TOP}" '
                 f'width="{x_of(chosen) - band_left:.1f}" height="{PANEL_H}" '
                 f'fill="{CHOSEN}" opacity="0.07"/>')
    parts.append(f'<text x="{band_left + 4:.1f}" y="{TOP + PANEL_H - 8:.1f}" font-size="10" '
                 f'fill="{CHOSEN}">zero false fires anywhere in here '
                 f'(loudest not-call {loudest_false:.4f})</text>')

    # The two thresholds that have actually been configured, named on the plot.
    for value, note in ((0.76, "0.76 &#8212; the old setting"), (chosen, "0.2 &#8212; now")):
        colour = CHOSEN if value == chosen else INK_SOFT
        parts.append(f'<line x1="{x_of(value):.1f}" y1="{TOP - 8}" x2="{x_of(value):.1f}" '
                     f'y2="{TOP + PANEL_H:.1f}" stroke="{colour}" stroke-width="1.5" '
                     f'stroke-dasharray="4 3"/>')
        anchor = "start" if value == chosen else "end"
        dx = 6 if value == chosen else -6
        parts.append(f'<text x="{x_of(value) + dx:.1f}" y="{TOP - 13}" font-size="10.5" '
                     f'text-anchor="{anchor}" fill="{colour}">{note}</text>')

    for subset, column, colour, dash, total in (
            (calls, "old_peak", OLD, "5 4", n_calls),
            (calls, "new_peak", NEW, "", n_calls),
            (not_calls, "old_peak", OLD, "2 3", n_not),
            (not_calls, "new_peak", NEW, "2 3", n_not)):
        width = 2.4 if not dash or dash == "5 4" else 1.6
        da = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<path d="{path_for(subset, column, total)}" fill="none" '
                     f'stroke="{colour}" stroke-width="{width}"{da}/>')

    legend = [(NEW, "", "new: real calls that fire"),
              (OLD, "5 4", "old: real calls that fire"),
              (NEW, "2 3", "new: FALSE fires"),
              (OLD, "2 3", "old: FALSE fires")]
    for index, (colour, dash, text) in enumerate(legend):
        y = TOP + 14 + index * 17
        da = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<line x1="{LEFT_X + LEFT_W - 172}" y1="{y}" '
                     f'x2="{LEFT_X + LEFT_W - 146}" y2="{y}" stroke="{colour}" '
                     f'stroke-width="2.2"{da}/>')
        parts.append(f'<text x="{LEFT_X + LEFT_W - 140}" y="{y + 3.5}" font-size="10.5" '
                     f'fill="{INK_SOFT}">{text}</text>')

    # --- right panel: the separation that opened -----------------------------------------
    groups = [
        ("real calls", calls, "the ceiling, not the typical clip"),
        ("not-calls", not_calls, "every false fire lives under this"),
    ]
    bar_h = 26
    for gi, (name, subset, note) in enumerate(groups):
        base = TOP + gi * 118
        parts.append(f'<text x="{RIGHT_X}" y="{base + 2}" font-size="11.5" '
                     f'font-weight="600" fill="{INK}">{name}</text>')
        parts.append(f'<text x="{RIGHT_X}" y="{base + 18}" font-size="10" '
                     f'fill="{INK_SOFT}">{note}</text>')
        for bi, (column, colour, tag) in enumerate((("old_peak", OLD, "old"),
                                                    ("new_peak", NEW, "new"))):
            peak = max(float(r[column]) for r in subset)
            y = base + 30 + bi * (bar_h + 8)
            width = max(2.0, peak * RIGHT_W)
            parts.append(f'<rect x="{RIGHT_X}" y="{y}" width="{width:.1f}" height="{bar_h}" '
                         f'fill="{colour}" rx="2"/>')
            parts.append(f'<text x="{RIGHT_X + width + 7:.1f}" y="{y + bar_h / 2 + 4:.1f}" '
                         f'font-size="11" fill="{INK_SOFT}">{tag} {peak:.4f}</text>')

    # 0.2 drawn across the right panel too, so "safe" is visible rather than asserted.
    x = RIGHT_X + chosen * RIGHT_W
    parts.append(f'<line x1="{x:.1f}" y1="{TOP - 8}" x2="{x:.1f}" y2="{TOP + PANEL_H - 24:.1f}" '
                 f'stroke="{CHOSEN}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{x + 5:.1f}" y="{TOP - 13}" font-size="10.5" '
                 f'fill="{CHOSEN}">0.2</text>')

    # The best threshold that still false-fires on nothing, and what it is worth over 0.2.
    safe = [t for t in steps if fires(not_calls, "new_peak", t) == 0]
    best = min(safe) if safe else chosen
    best_hit = fires(calls, "new_peak", best)

    verdict, next_y = wrap_text(
        f'At 0.2 the new model answers {new_hit} of {n_calls} real calls '
        f'({new_hit / n_calls * 100:.0f}%) with {new_false} false fire'
        f'{"" if new_false == 1 else "s"}. The old model answered {old_hit} there &#8212; but '
        f'could never have been RUN there, because its loudest not-call peaked at 0.8214. '
        f'{best:g} is the lowest swept threshold that still false-fires on nothing, and '
        f'answers {best_hit}: {best_hit - new_hit} more calls than 0.2, at no measured cost.',
        x=24, y=500, size=11.5, fill=INK, max_chars=130, line_height=17)
    parts.extend(verdict)

    tail, _ = wrap_text(
        f'The {n_calls - best_hit} still missed there are off-axis, mid-sentence and quiet. '
        f'A more conservative model cannot recover them &#8212; a second stage has to. '
        f'Data: media/data/2026-09-09-wake-retrain-compare.csv + 2026-09-07-wake-fixtures.csv '
        f'&#8212; regenerate with python media/scripts/compare_wake_models.py then '
        f'python media/scripts/plot_wake_retrain.py',
        x=24, y=next_y + 4, size=10.5, fill=INK_SOFT, max_chars=142, line_height=15)
    parts.extend(tail)
    parts.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    print(f"  real calls {n_calls}, not-calls {n_not} "
          f"(known-limits split on label != {NOT_A_CALL!r})")
    print(f"  at {chosen}: new {new_hit}/{n_calls} calls, {new_false} false "
          f"| old {old_hit}/{n_calls} calls, {fires(not_calls, 'old_peak', chosen)} false")
    print(f"  loudest not-call: old {max(float(r['old_peak']) for r in not_calls):.4f} "
          f"-> new {max(float(r['new_peak']) for r in not_calls):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
