#!/usr/bin/env python3
"""
Module:  measure_wasted_turns.py
Purpose: Count what the assistant spent answering things nobody asked it.
Author:  LB
Date:    2026-09-03

    python media/scripts/measure_wasted_turns.py
    python media/scripts/measure_wasted_turns.py --log data/oddball.log

Writes `media/data/2026-09-03-wasted-turns.csv`.

## What this measures, and why it is not a benchmark

Every other measurement script in `media/scripts/` runs something and times it. This one reads
`data/oddball.log` and counts what already happened, because the thing being measured is not a
component's speed — it is **eight days of LB actually using the rig**, and that cannot be
re-run.

For each turn the log gives the transcript, the route taken, and the agent leg in seconds. This
pairs them and asks one question: *how much of that time went on utterances that were not
requests?* An utterance counts as wasted when it is an acknowledgement, a fragment, or room
tone that `orchestrator/credible.py` let through — the population the `ack` intent now absorbs.

## The number that came out of it

The thirteen slowest PERSONA turns in the whole log are all in that population. Not one is a
question. `Okay.` alone appears six times, costing 4.8s to 56.3s each, because the cloud branch
had no timeout on it (D55) and a free-tier model on a bad day takes as long as it takes.

## Honesty about the classifier

`_is_wasted` below is a keyword list, and a keyword list applied to transcripts is exactly the
thing D38 warns about six times over. It is used HERE and nowhere near the turn path: this is
a report, and the worst it can do is misattribute a second. The `sample` column carries the
transcript for every row so any classification can be checked by eye rather than trusted.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "media" / "data"
OUT_CSV = DATA_DIR / "2026-09-03-wasted-turns.csv"
DEFAULT_LOG = REPO_ROOT / "data" / "oddball.log"

_HEARD = re.compile(r"heard ['\"](?P<text>.*?)['\"] in [\d.]+s")
_TURN = re.compile(r"turn: route (?P<route_ms>\d+)ms -> (?P<route>[a-z]+) \| "
                   r"agent (?P<agent_s>[\d.]+)s")

# What counts as not-a-request. Matched on the whole normalised transcript, end-anchored, for
# the same reason `instant._is_bare` is: "okay" is a shrug and "okay what's the trace width"
# is a question.
_WASTED = frozenset("""
okay ok alright allright right sure cool nice great yeah yep yup mhm mmhm hmm huh whoa wow oh
ah aha gotcha understood interesting thing ball elbow bobo sleep nothing
""".split())

_FILLER = frozenset({"mr", "odd", "ball", "oddball", "um", "uh", "well", "so", "just", "i",
                     "you", "a", "the", "and", "that", "its", "it", "is", "s"})


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


def _is_wasted(text: str) -> bool:
    """True when the transcript is an acknowledgement, a fragment, or room tone.

    Every word must be either a wasted-word or filler, so a real question containing "okay"
    is not counted. Empty transcripts are excluded — those never reached an agent.
    """
    words = _normalise(text).split()
    if not words or len(words) > 6:
        return False
    return all(w in _WASTED or w in _FILLER for w in words)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="what was spent answering nobody")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    log = Path(args.log)
    if not log.exists():
        print(f"no log at {log}")
        return 1

    rows: list[dict] = []
    heard = ""
    with log.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = _HEARD.search(line)
            if match:
                heard = match.group("text")
                continue
            turn = _TURN.search(line)
            if turn and heard:
                rows.append({"transcript": heard,
                             "route": turn.group("route"),
                             "agent_s": float(turn.group("agent_s")),
                             "wasted": _is_wasted(heard)})
                heard = ""

    if not rows:
        print("no turns found in the log")
        return 1

    wasted = [r for r in rows if r["wasted"]]
    useful = [r for r in rows if not r["wasted"]]
    wasted_s = sum(r["agent_s"] for r in wasted)
    useful_s = sum(r["agent_s"] for r in useful)

    by_route: dict[str, list[float]] = defaultdict(list)
    for r in wasted:
        by_route[r["route"]].append(r["agent_s"])

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["transcript", "route", "agent_s", "wasted"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  {len(rows)} turns in {log.name}\n")
    print(f"  {'':<26} {'turns':>6} {'seconds':>9} {'minutes':>8}")
    print("  " + "-" * 52)
    print(f"  {'not a request':<26} {len(wasted):>6} {wasted_s:>9.0f} {wasted_s / 60:>8.1f}")
    print(f"  {'a real request':<26} {len(useful):>6} {useful_s:>9.0f} {useful_s / 60:>8.1f}")
    share = wasted_s / (wasted_s + useful_s) * 100 if (wasted_s + useful_s) else 0.0
    print(f"\n  {share:.0f}% of all agent time went on utterances that were not requests.")

    print("\n  Where it went:")
    for route, secs in sorted(by_route.items(), key=lambda kv: -sum(kv[1])):
        print(f"    {route:<12} {len(secs):>3} turns  {sum(secs):>7.0f}s  "
              f"(worst {max(secs):.0f}s)")

    worst = sorted(wasted, key=lambda r: -r["agent_s"])[:8]
    print("\n  The worst of them:")
    for r in worst:
        print(f"    {r['agent_s']:>7.1f}s  {r['route']:<9} {r['transcript'][:52]!r}")

    print(f"\n  Wrote {OUT_CSV.relative_to(REPO_ROOT).as_posix()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
