#!/usr/bin/env python3
"""
Module:  measure_ampere_conversion.py
Purpose: Measure what Mr Odd Ball said about amperes before and after the 2026-08-21 fix,
         and draw the figure from the measurement.
Author:  LB
Date:    2026-08-21

    python media/scripts/measure_ampere_conversion.py

Writes `media/data/2026-08-21-ampere-conversion.csv` and
`media/charts/ampere-conversion.svg`.

## The "before" column is measured, not remembered

`BROKEN_AT` pins the commit that shipped the bug. This script pulls `orchestrator/convert.py`
out of git at that commit, loads it beside the current one, and asks both the same fourteen
questions in the same process. So the before column is a live run of the broken code rather
than a note of what it used to do, and the whole figure survives being re-derived a year from
now — which is the point of the media/ convention.

Only `convert.py` is rolled back. Its two imports (`calc`, `constants`) were untouched by the
fix, so the old module is exercised against the same helpers it always ran against.

## What the three verdicts mean, and why they are not equally bad

- **right**   — the number and the units are both correct.
- **refused** — he said "I don't know how to do that yet." A refusal ESCALATES: the router
                falls through to a tier that can answer. It costs a round trip, not an error.
- **WRONG**   — a confident number that is not the right number. This is the only column that
                matters, and it is the one D30 exists to keep empty.

The chart plots those three, before and after. A bar of "refused" shrinking is nice; the bar
of "wrong" going to zero is the result.
"""

from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "media" / "data"
CHARTS = REPO_ROOT / "media" / "charts"

sys.path.insert(0, str(REPO_ROOT))

# The commit that shipped the three defects. `git show BROKEN_AT:orchestrator/convert.py`.
BROKEN_AT = "ff3a43b"

STAMP = "2026-08-21"
CSV_PATH = DATA / f"{STAMP}-ampere-conversion.csv"
SVG_PATH = CHARTS / "ampere-conversion.svg"

# (question, expected spoken answer or None to mean "must refuse", which defect it probes)
#
# Expected values are typed from the arithmetic, independently of either module: 5 A is 5000
# mA; half an amp is 500 mA; 1 Ah is 3600 C; 2000 mAh is 2 Ah.
FIXTURES: list[tuple[str, str | None, str]] = [
    # Already working before the fix — carried so the figure shows what was NOT broken.
    ("how many milliamps in 2 amps",              "2 amps is 2000 milliamps.",              "-"),
    ("how many microamps in 3 milliamps",         "3 milliamps is 3000 microamps.",         "-"),
    ("how many kiloamps in 3000 amps",            "3000 amps is 3 kiloamps.",               "-"),
    # Defect 1 — the symbol forms. src="a" after the frame ate the number.
    ("convert 5 a to ma",                         "5 amps is 5000 milliamps.",              "symbol"),
    ("5 a in ma",                                 "5 amps is 5000 milliamps.",              "symbol"),
    ("how many ma in 5 a",                        "5 amps is 5000 milliamps.",              "symbol"),
    # Defect 2 — the amount defaulting to 1 with a digit still on the table.
    ("how many milliamps in point 5 amps",        "0.5 amps is 500 milliamps.",             "amount"),
    ("how many milliamps in .5 amps",             "0.5 amps is 500 milliamps.",             "amount"),
    ("how many milliamps in 1/2 amp",             None,                                     "amount"),
    # Defect 3 — the amp hour, which is charge and was being read as current.
    ("how many amps in 3000 milliamp hours",      None,                                     "amp hour"),
    ("how many amp hours in 3000 milliamp hours", "3000 milliamp hours is 3 amp hours.",    "amp hour"),
    ("how many coulombs in 1 amp hour",           "1 amp hour is 3600 coulombs.",           "amp hour"),
    ("convert 2000 mah to ah",                    "2000 milliamp hours is 2 amp hours.",    "amp hour"),
    ("how many mah in 2.5 ah",                    "2.5 amp hours is 2500 milliamp hours.",  "amp hour"),
]

INK = "#1B2430"
MUTED = "#5C6B7F"
GRID = "#DCE3EC"
BAR_RIGHT = "#3F8F5B"       # right — the number and the units both
BAR_REFUSED = "#E8A33D"     # refused — escalates, costs a round trip
BAR_WRONG = "#C6413F"       # wrong — a confident number that is not the answer

W, H = 900, 430
PAD_L, PAD_R, PAD_T, PAD_B = 92, 30, 78, 96
PLOT_W = W - PAD_L - PAD_R
PLOT_H = H - PAD_T - PAD_B


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_broken(commit: str):
    """The pre-fix `convert` module, loaded from git at `commit`.

    Args:
        commit: any git revision that contains `orchestrator/convert.py`.

    Returns:
        The imported module.

    Registered in `sys.modules` before it executes, because a frozen dataclass resolves its
    own annotations through `sys.modules[cls.__module__]` and fails on a module that is not
    there yet.
    """
    source = subprocess.run(
        ["git", "show", f"{commit}:orchestrator/convert.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    scratch = REPO_ROOT / "media" / "scripts" / "_convert_broken.py"
    scratch.write_text(source, encoding="utf-8")
    try:
        spec = importlib.util.spec_from_file_location("_convert_broken", scratch)
        module = importlib.util.module_from_spec(spec)
        sys.modules["_convert_broken"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        scratch.unlink(missing_ok=True)


def verdict(spoken: str | None, expected: str | None) -> str:
    """right / refused / wrong, from what he said against what the arithmetic says."""
    if spoken is None:
        # Refusing when refusal IS the right answer is right, not a refusal.
        return "right" if expected is None else "refused"
    return "right" if spoken == expected else "wrong"


def measure() -> list[dict]:
    old = load_broken(BROKEN_AT)
    from orchestrator.convert import convert as new

    rows = []
    for question, expected, defect in FIXTURES:
        before = old.convert(question)
        after = new(question)
        before_said = before.spoken if before else None
        after_said = after.spoken if after else None
        rows.append({
            "question": question,
            "defect": defect,
            "expected": expected if expected is not None else "(must refuse)",
            "before_spoken": before_said or "(refused)",
            "before_verdict": verdict(before_said, expected),
            "after_spoken": after_said or "(refused)",
            "after_verdict": verdict(after_said, expected),
        })
    return rows


def write_csv(rows: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_svg(rows: list[dict]) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    total = len(rows)
    groups = [
        (f"before  ({BROKEN_AT})", "before_verdict"),
        (f"after  ({STAMP})", "after_verdict"),
    ]
    verdicts = [("right", BAR_RIGHT), ("refused", BAR_REFUSED), ("wrong", BAR_WRONG)]

    def y_of(count: int) -> float:
        return PAD_T + PLOT_H - (count / total) * PLOT_H

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="{PAD_L}" y="34" font-size="19" font-weight="600" fill="{INK}">'
        f'{esc("Mr Odd Ball on amperes: 14 questions, before and after the 2026-08-21 fix")}'
        '</text>',
        f'<text x="{PAD_L}" y="56" font-size="13" fill="{MUTED}">'
        f'{esc("Questions answered (count). A refusal escalates to the next tier; a wrong answer does not.")}'
        '</text>',
    ]

    for count in range(0, total + 1, 2):
        y = y_of(count)
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + PLOT_W}" y2="{y:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{PAD_L - 12}" y="{y + 4:.1f}" font-size="12" fill="{MUTED}" '
                   f'text-anchor="end">{count}</text>')

    group_w = PLOT_W / len(groups)
    bar_w = 96
    gap = 26
    for gi, (label, field) in enumerate(groups):
        counts = [sum(1 for r in rows if r[field] == name) for name, _ in verdicts]
        span = len(verdicts) * bar_w + (len(verdicts) - 1) * gap
        x0 = PAD_L + gi * group_w + (group_w - span) / 2
        for bi, ((name, colour), count) in enumerate(zip(verdicts, counts)):
            x = x0 + bi * (bar_w + gap)
            y = y_of(count)
            height = PAD_T + PLOT_H - y
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{height:.1f}" '
                       f'fill="{colour}" rx="3"/>')
            out.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 8:.1f}" font-size="15" '
                       f'font-weight="600" fill="{INK}" text-anchor="middle">{count}</text>')
            out.append(f'<text x="{x + bar_w / 2:.1f}" y="{PAD_T + PLOT_H + 20:.1f}" '
                       f'font-size="12" fill="{MUTED}" text-anchor="middle">{esc(name)}</text>')
        out.append(f'<text x="{PAD_L + gi * group_w + group_w / 2:.1f}" '
                   f'y="{PAD_T + PLOT_H + 46:.1f}" font-size="14" font-weight="600" '
                   f'fill="{INK}" text-anchor="middle">{esc(label)}</text>')

    out.append(f'<line x1="{PAD_L}" y1="{PAD_T + PLOT_H}" x2="{PAD_L + PLOT_W}" '
               f'y2="{PAD_T + PLOT_H}" stroke="{INK}" stroke-width="1.5"/>')
    out.append(f'<text x="{PAD_L}" y="{H - 22}" font-size="12" fill="{MUTED}">'
               f'{esc("Source: media/data/" + CSV_PATH.name + " — regenerate with "
                      "media/scripts/measure_ampere_conversion.py")}</text>')
    out.append("</svg>")
    SVG_PATH.write_text("\n".join(out), encoding="utf-8")


def main() -> int:
    rows = measure()
    write_csv(rows)
    write_svg(rows)

    for field, when in (("before_verdict", "before"), ("after_verdict", "after ")):
        tally = {v: sum(1 for r in rows if r[field] == v) for v in ("right", "refused", "wrong")}
        print(f"  {when}: {tally['right']:2d} right, {tally['refused']:2d} refused, "
              f"{tally['wrong']:2d} WRONG")
    print(f"\n  {CSV_PATH.relative_to(REPO_ROOT)}")
    print(f"  {SVG_PATH.relative_to(REPO_ROOT)}")

    wrong_after = [r for r in rows if r["after_verdict"] == "wrong"]
    for row in wrong_after:
        print(f"  STILL WRONG: {row['question']!r} -> {row['after_spoken']!r}")
    return 1 if wrong_after else 0


if __name__ == "__main__":
    raise SystemExit(main())
