#!/usr/bin/env python3
"""
Module:  measure_kicad_parser.py
Purpose: Measure what the textbook KiCad parser answers versus what tools/kicad_parser.py
         answers, on the same twelve questions, and draw the figure from the measurement.
Author:  LB
Date:    2026-08-21

    python media/scripts/measure_kicad_parser.py

Writes `media/data/2026-08-21-kicad-parser.csv` and `media/charts/kicad-parser.svg`.

## The "before" column is a live run, not a recollection

`NAIVE_SOURCE` below is the tutorial implementation **verbatim** — the one that says
`schematic.symbols`, keeps every symbol block, reads only the file it is handed, and reports
`len(board.layers)` as the layer count. It is executed here, in this process, against the same
fixtures the shipped tools are given. So the left-hand bar is measured, and stays measured if
this is re-run a year from now.

That is the same convention `measure_ampere_conversion.py` uses, and for the same reason: a
before/after where "before" is a note of what it used to do is an anecdote.

## The three verdicts, and why only one of them matters

- **right**   — the answer matches the fixture's known content.
- **error**   — it said it could not do it. Safe: LB sees an error and goes and looks. It costs
                him a minute, not a board.
- **WRONG**   — a confident answer that is not the right answer. This is the column that
                matters. It is also, on this particular tool, the expensive one: a BOM is a
                thing you order parts from.

## Why "error" is the whole left-hand bar for every schematic question

`Schematic` in kiutils has no `.symbols` attribute — it is `schematicSymbols`. The tutorial
wraps its body in `except Exception` and returns the message, so every schematic question comes
back as *"Failed to parse schematic: 'Schematic' object has no attribute 'symbols'"*, which
reads like a corrupt file. Six of the twelve questions never get as far as being wrong.

The board questions do get that far, and those are the interesting ones — they are answers, and
they are wrong.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "media" / "data"
CHARTS = REPO_ROOT / "media" / "charts"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "kicad"

sys.path.insert(0, str(REPO_ROOT))

STAMP = "2026-08-21"
CSV_PATH = DATA / f"{STAMP}-kicad-parser.csv"
SVG_PATH = CHARTS / "kicad-parser.svg"

from tools.kicad_parser import analyze_kicad_pcb, extract_kicad_bom      # noqa: E402

# ============================================================ the "before" implementation
#
# Verbatim from the tutorial, with only the @tool decorators dropped so they can be called
# directly. Nothing else is changed — in particular `schematic.symbols` is left exactly as
# written, because that is the defect being measured.

NAIVE_SOURCE = "the tutorial implementation, unmodified"


def naive_extract_kicad_bom(file_path: str) -> str:
    from kiutils.schematic import Schematic
    import os

    if not os.path.exists(file_path):
        return f"Error: Schematic file not found at '{file_path}'."
    try:
        schematic = Schematic().from_file(file_path)
        bom = []
        for symbol in schematic.symbols:
            ref = ""
            val = ""
            footprint = ""
            for prop in symbol.properties:
                if prop.key == "Reference":
                    ref = prop.value
                elif prop.key == "Value":
                    val = prop.value
                elif prop.key == "Footprint":
                    footprint = prop.value
            if ref and not ref.startswith("#PWR") and "?" not in ref:
                bom.append(f"- {ref}: {val} (Footprint: {footprint})")
        if not bom:
            return "Schematic parsed successfully, but no components were found."
        return "KiCad Schematic BOM:\n" + "\n".join(sorted(bom))
    except Exception as e:
        return f"Failed to parse schematic: {str(e)}"


def naive_analyze_kicad_pcb(file_path: str) -> str:
    from kiutils.board import Board
    import os

    if not os.path.exists(file_path):
        return f"Error: PCB file not found at '{file_path}'."
    try:
        board = Board().from_file(file_path)
        num_layers = len(board.layers)
        num_nets = len(board.nets)
        num_footprints = len(board.footprints)
        return (f"PCB Analysis for {os.path.basename(file_path)}:\n"
                f"1. Total Layers Defined: {num_layers}\n"
                f"2. Total Electrical Nets: {num_nets}\n"
                f"3. Total Footprints (Components): {num_footprints}\n")
    except Exception as e:
        return f"Failed to parse PCB: {str(e)}"


# ============================================================ reading the two formats
#
# The two implementations print differently, so each is parsed on ITS OWN terms. That is
# deliberately generous to the tutorial version: every wrong number below is a number it
# genuinely reports, never an artefact of looking for our wording in its output.
#
#   tutorial   "- R1: 10k (Footprint: Resistor_SMD:R_0805_2012Metric)"     one line per BLOCK
#   ours       "  3x  10k   R_0805_2012Metric   R1, R2, R4"                one line per GROUP
#
# The comparable quantity is the number of PARTS each one claims — which for the tutorial is
# its line count, because it emits a line per symbol block, and for ours is the sum of the
# quantities. That difference is the multi-unit bug, and collapsing both to "distinct
# references" would hide it.

_ERRORS = ("Failed to parse", "could not be read", "Error:", "Error reading")

_OURS_ROW = re.compile(r"^\s+(\d+)x\s+(.+?)\s{2,}(\S+)\s{2,}(.+?)\s*$")
_NAIVE_ROW = re.compile(r"^- ([^:]+):\s*(.*?)\s*\(Footprint:\s*(.*?)\)\s*$")


def _failed(text: str) -> bool:
    return any(marker in text for marker in _ERRORS)


def parse_ours(text: str) -> list[tuple[int, str, str]] | None:
    """Our BOM as (quantity, value, footprint) rows, or None if it reported an error."""
    if _failed(text):
        return None
    rows = []
    for line in text.splitlines():
        match = _OURS_ROW.match(line)
        if match:
            rows.append((int(match.group(1)), match.group(2).strip(), match.group(3)))
    return rows


def parse_naive(text: str) -> list[tuple[int, str, str]] | None:
    """The tutorial BOM in the same shape — every line is one part, by its own reckoning."""
    if _failed(text):
        return None
    rows = []
    for line in text.splitlines():
        match = _NAIVE_ROW.match(line.strip())
        if match:
            rows.append((1, match.group(2).strip(), match.group(3)))
    return rows


def total_parts(rows) -> int | None:
    return None if rows is None else sum(qty for qty, _v, _f in rows)


def parts_valued(rows, value: str) -> int | None:
    return None if rows is None else sum(qty for qty, v, _f in rows if v == value)


def number_after(text: str, label: str) -> int | None:
    """The integer following `label`, or None when the output makes no such claim."""
    match = re.search(re.escape(label) + r"\D{0,4}(\d+)", text)
    return int(match.group(1)) if match else None


# The tutorial's board report uses its own headings for the same three numbers.
NAIVE_LABELS = {
    "layers": "Total Layers Defined",
    "nets": "Total Electrical Nets",
    "footprints": "Total Footprints (Components)",
}
OURS_LABELS = {
    "layers": "Copper layers",
    "nets": "Nets",
    "footprints": "Footprints placed",
}


def read(metric: str, text: str, naive: bool) -> int | None:
    """One question, answered out of one implementation's output."""
    if metric in NAIVE_LABELS:
        return number_after(text, (NAIVE_LABELS if naive else OURS_LABELS)[metric])
    if metric == "is_error":
        # 1 = it reported a problem. 0 = it produced a parts list, empty or otherwise, which
        # for a corrupt file is the dangerous answer.
        if "no components were found" in text.lower() or "No components found" in text:
            return 0
        return 1 if _failed(text) else 0

    rows = parse_naive(text) if naive else parse_ours(text)
    if metric == "parts":
        return total_parts(rows)
    if metric.startswith("value:"):
        return parts_valued(rows, metric.split(":", 1)[1])
    raise ValueError(f"unknown metric {metric!r}")


# (question LB would ask, fixture, tool kind, metric, the true answer, what it probes)
QUESTIONS = [
    ("how many parts are on the flat board",
     "flat.kicad_sch", "sch", "parts", 8, "multi-unit"),
    ("how many 10k resistors are on it",
     "flat.kicad_sch", "sch", "value:10k", 3, "grouping"),
    ("how many TL074s are on it",
     "flat.kicad_sch", "sch", "value:TL074", 1, "multi-unit"),
    ("is the do-not-populate LED listed",
     "flat.kicad_sch", "sch", "value:LED", 1, "DNP"),
    ("how many parts on a schematic with no in_bom token",
     "no-inbom.kicad_sch", "sch", "parts", 3, "in_bom default"),
    ("how many parts across the hierarchical design",
     "hier-root.kicad_sch", "sch", "parts", 5, "sub-sheets"),
    ("how many parts when a sheet is placed twice",
     "repeat-root.kicad_sch", "sch", "parts", 3, "sub-sheets"),
    ("does a corrupt schematic report an error, not an empty list",
     "truncated.kicad_sch", "sch", "is_error", 1, "corrupt file"),
    ("how many layers is the two-layer board",
     "two-layer.kicad_pcb", "pcb", "layers", 2, "layer count"),
    ("how many layers is the four-layer board",
     "four-layer.kicad_pcb", "pcb", "layers", 4, "layer count"),
    ("how many nets are on the two-layer board",
     "two-layer.kicad_pcb", "pcb", "nets", 5, "net 0"),
    ("how many footprints are placed on it",
     "two-layer.kicad_pcb", "pcb", "footprints", 5, "-"),
]


def verdict(got, expected) -> str:
    if got is None:
        return "error"
    return "right" if got == expected else "wrong"


def measure() -> list[dict]:
    rows = []
    for question, fixture, kind, metric, expected, probes in QUESTIONS:
        path = str(FIXTURES / fixture)

        if kind == "sch":
            before_text = naive_extract_kicad_bom(path)
            after_text = extract_kicad_bom.invoke({"file_path": path})
        else:
            before_text = naive_analyze_kicad_pcb(path)
            after_text = analyze_kicad_pcb.invoke({"file_path": path})

        before_got = read(metric, before_text, naive=True)
        after_got = read(metric, after_text, naive=False)

        rows.append({
            "question": question,
            "fixture": fixture,
            "probes": probes,
            "expected": expected,
            "before_answer": "(error)" if before_got is None else before_got,
            "before_verdict": verdict(before_got, expected),
            "after_answer": "(error)" if after_got is None else after_got,
            "after_verdict": verdict(after_got, expected),
        })
    return rows


# ============================================================ output

INK = "#1B2430"
MUTED = "#5C6B7F"
GRID = "#DCE3EC"
BAR_RIGHT = "#3F8F5B"       # right — matches what is actually in the file
BAR_ERROR = "#E8A33D"       # error — safe: he goes and looks
BAR_WRONG = "#C6413F"       # wrong — a confident number he would order parts from

W, H = 980, 470
PAD_L, PAD_R, PAD_T, PAD_B = 92, 30, 78, 96
PLOT_W = W - PAD_L - PAD_R
PLOT_H = H - PAD_T - PAD_B


def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def write_csv(rows: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_svg(rows: list[dict]) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    total = len(rows)
    groups = [("before  (the tutorial parser)", "before_verdict"),
              (f"after  ({STAMP})", "after_verdict")]
    verdicts = [("right", BAR_RIGHT), ("error", BAR_ERROR), ("wrong", BAR_WRONG)]

    def y_of(count: int) -> float:
        return PAD_T + PLOT_H - (count / total) * PLOT_H

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="{PAD_L}" y="34" font-size="19" font-weight="600" fill="{INK}">'
        f'{esc("Reading a KiCad design: 12 questions, tutorial parser vs. shipped parser")}'
        '</text>',
        f'<text x="{PAD_L}" y="56" font-size="13" fill="{MUTED}">'
        f'{esc("Questions answered (count). An error sends him to look at the file; a wrong "
               "number is one he would order parts from.")}'
        '</text>',
    ]

    for count in range(0, total + 1, 2):
        y = y_of(count)
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + PLOT_W}" y2="{y:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{PAD_L - 12}" y="{y + 4:.1f}" font-size="12" fill="{MUTED}" '
                   f'text-anchor="end">{count}</text>')

    group_w = PLOT_W / len(groups)
    bar_w, gap = 96, 26
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
                      "media/scripts/measure_kicad_parser.py")}</text>')
    out.append("</svg>")
    SVG_PATH.write_text("\n".join(out), encoding="utf-8")


def main() -> int:
    rows = measure()
    write_csv(rows)
    write_svg(rows)

    for field, when in (("before_verdict", "before"), ("after_verdict", "after ")):
        tally = {v: sum(1 for r in rows if r[field] == v) for v in ("right", "error", "wrong")}
        print(f"  {when}: {tally['right']:2d} right, {tally['error']:2d} error, "
              f"{tally['wrong']:2d} WRONG")

    print("\n  the wrong answers the tutorial parser gives, in full:")
    for row in rows:
        if row["before_verdict"] == "wrong":
            print(f"    {row['question']:52} said {row['before_answer']}, "
                  f"actually {row['expected']}   [{row['probes']}]")

    print(f"\n  {CSV_PATH.relative_to(REPO_ROOT)}")
    print(f"  {SVG_PATH.relative_to(REPO_ROOT)}")

    still_wrong = [r for r in rows if r["after_verdict"] != "right"]
    for row in still_wrong:
        print(f"  NOT RIGHT AFTER: {row['question']!r} -> {row['after_answer']!r} "
              f"(expected {row['expected']})")
    return 1 if still_wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
