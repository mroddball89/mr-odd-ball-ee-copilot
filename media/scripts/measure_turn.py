#!/usr/bin/env python3
"""
Module:  measure_turn.py
Purpose: Time the router and the whole turn, per route, and write the CSV the chart reads.
Author:  LB
Date:    2026-08-19

    python media/scripts/measure_turn.py --out media/data/2026-08-19-turn-latency.csv
    python media/scripts/measure_turn.py --utility-only     # costs one router call each

## The number this exists to produce

`~/oddball/docs/PLAN.md` budgets **2.0 seconds** from the end of LB's speech to the start of
his. The merge changed what is inside that budget: `orchestrator/classify.py` was a pure
function costing ~0 ms, and `router.py` is a Gemini round trip. LB chose that and it is not
being re-argued — it is being **measured**, so the cost is a number on a chart rather than a
feeling.

What is timed here is `route` + `agent`. The full spoken turn adds STT (measured on the Pi at
1.05-1.40s for `tiny.en`) and Piper synthesis (~0.4s), neither of which this script can
measure off-Pi. The chart says so; a number whose conditions are not recorded is not a
measurement.

**`--utility-only` exists because of D3.** The free tier is 20 requests per model per day, and
a full sweep of nine routes costs more than that in agent calls alone. The utility sweep costs
one router call per question and nothing else, so it can be run repeatedly — which makes it
the one arm with enough samples to mean anything.
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

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from engine import models                                            # noqa: E402
from engine.core import Engine                                       # noqa: E402

# Questions chosen to land on one route each. The comment is the expected route, and a
# mismatch is worth seeing — it is a routing bug, and the CSV records what actually happened
# rather than what was intended.
UTILITY = [
    "what time is it",
    "what day is it today",
    "what is the date",
    "what does mosfet mean",
    "what does i2c stand for",
    "convert 3.3 volts to millivolts",
    "how many ohms in a kilohm",
    "what is the speed of light",
    "what is planck's constant",
    "what does capacitance mean",
]

AGENTS = [
    ("hardware", "how wide does a trace need to be for 3 amps on 1oz external copper "
                 "with a 10 degree rise"),
    ("math",     "what is the cutoff frequency of an RC low pass filter with R equals "
                 "10 kilohms and C equals 1 microfarad"),
    ("firmware", "how do I configure GPIO 13 as an output on an ESP32"),
    ("persona",  "tell me a joke about capacitors"),
]


def timed(engine: Engine, question: str) -> dict:
    t0 = time.monotonic()
    response = engine.ask(question)
    total = time.monotonic() - t0
    log = engine.last
    return {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "route": log.route or response.route,
        "route_s": round(log.route_s, 3),
        "agent_s": round(log.agent_s, 3),
        "total_s": round(total, 3),
        "speech_words": len(response.speech.split()),
        "cards": len(response.cards),
        "ok": "no" if any(e.startswith("error") for e in log.extras) else "yes",
        "notes": "; ".join(log.extras),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="measure turn latency by route")
    ap.add_argument("--out", default=str(REPO_ROOT / "media" / "data" /
                                        f"{datetime.now():%Y-%m-%d}-turn-latency.csv"))
    ap.add_argument("--utility-only", action="store_true",
                    help="skip the agent routes. One router call per question, no agent "
                         "calls — the only arm that is cheap enough to repeat (D3).")
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args(argv)

    engine = Engine()
    rows: list[dict] = []

    print(f"  router: {models.ROUTER_MODEL}")
    print(f"  agents: {models.AGENT_MODEL}\n")

    for _ in range(args.repeats):
        for question in UTILITY:
            row = timed(engine, question)
            rows.append(row)
            print(f"  {row['route']:9} {row['total_s']:6.2f}s  "
                  f"(route {row['route_s'] * 1000:4.0f}ms)  {question[:44]}")

        if not args.utility_only:
            for expected, question in AGENTS:
                row = timed(engine, question)
                row["notes"] = (row["notes"] + f"; expected {expected}").strip("; ")
                rows.append(row)
                print(f"  {row['route']:9} {row['total_s']:6.2f}s  "
                      f"(route {row['route_s'] * 1000:4.0f}ms, agent {row['agent_s']:.2f}s)  "
                      f"{question[:44]}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    good = [r for r in rows if r["ok"] == "yes"]
    print(f"\n  wrote {len(rows)} rows to {out}")
    if good:
        routes = sorted({r["route"] for r in good})
        print(f"  routes seen: {', '.join(routes)}")
        print(f"  router median: {statistics.median(r['route_s'] for r in good) * 1000:.0f}ms")
        print(f"  total  median: {statistics.median(r['total_s'] for r in good):.2f}s")
        over = [r for r in good if r["total_s"] > 2.0]
        print(f"  over the 2.0s PLAN.md budget: {len(over)}/{len(good)}")
    failed = len(rows) - len(good)
    if failed:
        print(f"  {failed} row(s) failed — most likely the 20/day free tier (D3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
