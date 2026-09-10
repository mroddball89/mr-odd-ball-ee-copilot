#!/usr/bin/env python3
"""
Module:  measure_quiz_marking.py
Purpose: Measure what one marked answer costs, before and after the local grader.
Author:  LB
Date:    2026-09-02

    python media/scripts/measure_quiz_marking.py
    python media/scripts/measure_quiz_marking.py --repeats 50

Writes `media/data/2026-09-02-quiz-marking.csv` and prints the headline.

## What is being measured, and what is being cited

Two numbers per answer, and only one of them is measured here:

**Local marking latency** — measured, on this machine, right now. `tools/quiz_grade.grade`
is called `--repeats` times per fixture and the median is taken. The first call pays for
`import sympy`, so it is reported separately rather than averaged into the rest: a one-off
import cost quoted as a per-answer cost would be a lie about the steady state, and quoting the
steady state without mentioning the import would be a lie about the first question.

**Remote marking latency** — NOT measured here, and deliberately. The old grader was one
`ChatGoogleGenerativeAI` invoke per answer; measuring it would spend LB's 20-a-day quota to
re-derive a number this repo already has. `media/data/2026-08-29-turn-latency.csv` timed the
same class of call at **0.77-0.88 s** on the routes that work, and `docs/DECISIONS.md` records
four pathological ones at 91 s, 127 s, 162 s and 286 s on the same day. The CSV cites that
rather than inventing a fresh measurement, and the `source` column says which rows are measured
and which are cited.

## The number that actually matters is not the latency

It is the **request count**. The free tier is counted in requests, not tokens: 20 per model
name per day (D3). Marking used to spend one per answer, so a ten-question quiz cost half a
day's quota and a second quiz took the router and the persona agent down with it. It now spends
zero. That is a change from 10 to 0 on a budget of 20 — which is why this is a design fix
rather than a performance tweak, and why the chart plots requests beside milliseconds.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

DATA_DIR = REPO_ROOT / "media" / "data"
OUT_CSV = DATA_DIR / "2026-09-02-quiz-marking.csv"

# Cited, not measured. See the module docstring for why re-measuring would cost quota to learn
# nothing new.
REMOTE_MEDIAN_MS = 820.0          # media/data/2026-08-29-turn-latency.csv, agent leg
REMOTE_SOURCE = "cited: 2026-08-29-turn-latency.csv"

# One fixture per grader, so the CSV shows where the time goes rather than one blended number.
# The `kind` is left to `infer_kind`, exactly as a real deck entry would be.
FIXTURES: tuple[tuple[str, str, str, dict], ...] = (
    ("mcq-letter", "B", "B", {"A": "x", "B": "2x", "C": "x^2/2", "D": "2"}),
    ("mcq-spoken", "B", "two x", {"A": "x", "B": "2x", "C": "x^2/2", "D": "2"}),
    ("numeric", "9.81 m/s^2", "9.8", {}),
    ("numeric-range", "Around 1.8V to 2.0V", "about 1.9 volts", {}),
    ("symbolic", "V = I * R", "voltage equals current times resistance", {}),
    ("symbolic-rearranged", "V = I * R", "R = V / I", {}),
    ("short-fuzzy", "Inter-Integrated Circuit", "interintegrated circut", {}),
    ("prose", "Free will does not exist because every action is causally determined",
     "it does not exist, every action is causally determined", {}),
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="what one marked answer costs")
    ap.add_argument("--repeats", type=int, default=30, help="timed calls per fixture")
    args = ap.parse_args(argv)

    from tools.quiz_bank import QuizItem                             # noqa: PLC0415
    from tools.quiz_grade import grade                               # noqa: PLC0415

    stamp = datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []

    # The cold call, on its own row. `import sympy` happens inside the first symbolic
    # comparison, and folding that into a per-answer median would misreport both numbers.
    cold_item = QuizItem(question="q", answer="V = I * R")
    t0 = time.perf_counter()
    grade(cold_item, "voltage equals current times resistance")
    cold_ms = (time.perf_counter() - t0) * 1000
    rows.append({"stamp": stamp, "case": "first answer of the session (cold sympy import)",
                 "grader": "local", "median_ms": round(cold_ms, 2), "requests": 0,
                 "source": "measured"})

    print(f"\n  cold start (first answer, sympy imported): {cold_ms:.0f} ms\n")
    print(f"  {'case':<22} {'median ms':>10} {'requests':>9}")
    print("  " + "-" * 43)

    for name, official, given, choices in FIXTURES:
        item = QuizItem(question="q", answer=official, choices=choices)
        samples = []
        for _ in range(args.repeats):
            t0 = time.perf_counter()
            grade(item, given)
            samples.append((time.perf_counter() - t0) * 1000)
        median = statistics.median(samples)
        rows.append({"stamp": stamp, "case": name, "grader": "local",
                     "median_ms": round(median, 3), "requests": 0, "source": "measured"})
        print(f"  {name:<22} {median:>10.3f} {0:>9}")

    rows.append({"stamp": stamp, "case": "any answer", "grader": "remote (the old path)",
                 "median_ms": REMOTE_MEDIAN_MS, "requests": 1, "source": REMOTE_SOURCE})
    print(f"  {'remote (old path)':<22} {REMOTE_MEDIAN_MS:>10.1f} {1:>9}   <- {REMOTE_SOURCE}")

    warm = [r["median_ms"] for r in rows
            if r["grader"] == "local" and "cold" not in r["case"]]
    warm_median = statistics.median(warm)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["stamp", "case", "grader", "median_ms",
                                                "requests", "source"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  Warm median, local: {warm_median:.2f} ms per answer, 0 requests.")
    print(f"  A ten-question quiz: {warm_median * 10:.0f} ms and 0 requests, against "
          f"{REMOTE_MEDIAN_MS * 10 / 1000:.1f} s and 10 requests before —")
    print(f"  and 10 requests is HALF the 20-a-day free tier (D3), which is the number that "
          f"actually mattered.")
    print(f"\n  Wrote {OUT_CSV.relative_to(REPO_ROOT).as_posix()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
