#!/usr/bin/env python3
"""
Module:  measure_note_turn.py
Purpose: Time every notebook operation, and record what each one used to cost in API calls.
Author:  LB
Date:    2026-08-28

    python media/scripts/measure_note_turn.py
    python media/scripts/measure_note_turn.py --reps 50 --out media/data/mine.csv

## What is measured, and what is cited

**Measured here, on this machine, right now:** the wall-clock time of each notebook operation
through a real `engine.core.Engine`, and the number of Gemini calls each one makes — which is
zero, and is asserted rather than assumed (`api_calls_after` comes from a counter wired into
`router.router_agent`, so a regression that reintroduced a call would show up as a number, not
as a feeling).

**Cited, not measured here:** the "before" column. Dictating a note used to reach
`agents/persona_agent.py` through the paid router, and that path is three calls — route, the
tool call, the follow-up with the tool result. It is three by construction, from reading
`router.py` and `agents/persona_agent.py:114-119`, and this script does not spend LB's quota
re-proving it. The **router leg's** latency is likewise this repo's own earlier measurement:
750 ms on Windows, 9.8 s on the Pi (`orchestrator/route_hint.py`).

That split is the honest one and the chart says so on its face. A number whose conditions are
not recorded is not a measurement, and a number someone else measured is a citation.

## The free-tier probe is the other half

`--probe` re-runs the check that started this work: eight phrasings LB actually uses, put
through the free tier as it was before `orchestrator/note_intent.py` existed (planners
withheld) and as it is now. Before: 0 of 8. After: 8 of 8. That one is measured both ways,
because both ways are free.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import statistics
import sys
import tempfile
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

# A temp vault, set before anything under tools/ is imported. Same rule, same reason, as
# tools/verify_notes.py: `knowledge_vault.VAULT_DIR` is resolved at import time.
_TMP = Path(tempfile.mkdtemp(prefix="oddball-measure-notes-"))
os.environ["ODDBALL_VAULT_DIR"] = str(_TMP)
# Long enough and plausible enough for `engine.models._key_problem`, which rejects
# anything containing "paste", "here", "xxx" and friends. Nothing calls out — the
# tripwire in `_count_router_calls` makes that a measurement rather than a hope.
os.environ.setdefault("GOOGLE_API_KEY", "not-a-real-key-only-for-this-measurement")
os.environ["ODDBALL_SELF_CONTEXT"] = "0"

# What the paid path costs, per operation, by construction. Written down here rather than left
# in a comment in the chart script, so the CSV carries its own provenance.
BEFORE_CALLS = {
    "new": 3,        # router -> persona (tool call) -> persona (followup with the result)
    "append": 3,     # same path; save_to_vault appends when the note exists
    "read": 3,       # router -> persona (read_from_vault) -> persona (followup)
    "list": 3,       # router -> persona -> followup, and it answers from a substring scan
    "delete": None,  # there was no delete. Nothing in the repo could remove a note.
}

# The turns that make up one measured pass of each operation. A `new` note is three turns
# because he asks what to call it; that is the feature, so it is what gets timed.
SCRIPTS: list[tuple[str, list[str]]] = [
    ("new", ["take a note that the reg is an LM317 not a 7805", "regulator choice"]),
    ("append", ["add to my regulator choice note that it needs a heatsink"]),
    ("read", ["read me my regulator choice note"]),
    ("list", ["what notes do I have"]),
    ("delete", ["delete my regulator choice note", "yes"]),
]

FIELDS = ["measured_at", "operation", "turns", "wall_s", "api_calls_after",
          "api_calls_before", "platform", "notes"]


def _count_router_calls() -> "list[int]":
    """Wire a counter into the paid router, so 'no API call' is a measurement.

    Returns a one-element list used as a mutable counter. `router.router_agent` is replaced
    with something that increments it and raises — if any notebook path ever reaches the paid
    router, the run fails loudly instead of quietly costing a request.
    """
    counter = [0]

    import router as router_module

    def _tripwire(query: str):
        counter[0] += 1
        raise AssertionError(f"the notebook reached the PAID router with {query!r}")

    router_module.router_agent = _tripwire

    import engine.core as core
    core.router_agent = _tripwire          # imported by name at module load, so rebind both
    return counter


def measure(reps: int) -> list[dict]:
    calls = _count_router_calls()
    from engine.core import Engine
    from tools import knowledge_vault as kv

    stamp = datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []

    for operation, turns in SCRIPTS:
        samples: list[float] = []
        for _ in range(reps):
            engine = Engine(confirm_gates=True)
            # Each rep needs the note to exist for append/read/delete, and not to exist for
            # new. Rebuilt per rep rather than shared, so one slow first write cannot be
            # averaged away across the others.
            if operation != "new":
                for path in kv.notes():
                    path.unlink(missing_ok=True)
                kv.write_note("regulator choice", "the reg is an LM317 not a 7805", "notes")

            t0 = time.perf_counter()
            for text in turns:
                engine.ask(text)
            samples.append(time.perf_counter() - t0)

        rows.append({
            "measured_at": stamp,
            "operation": operation,
            "turns": len(turns),
            "wall_s": round(statistics.median(samples), 4),
            "api_calls_after": calls[0],
            "api_calls_before": BEFORE_CALLS[operation]
                                if BEFORE_CALLS[operation] is not None else "",
            "platform": f"{sys.platform} python{sys.version_info.major}."
                        f"{sys.version_info.minor}",
            "notes": f"median of {reps}; "
                     + ("no delete existed before" if operation == "delete"
                        else "before = router + persona tool call + followup"),
        })
        print(f"  {operation:8s} {len(turns)} turn(s)  "
              f"median {statistics.median(samples) * 1000:7.1f} ms  "
              f"api calls: {calls[0]}")

    if calls[0]:
        raise SystemExit(f"\n  the notebook made {calls[0]} paid router call(s) — "
                         f"the free path is broken\n")
    return rows


def probe() -> None:
    """Eight real phrasings, through the free tier with and without the new planner."""
    from orchestrator import launch_intent, note_intent
    from orchestrator.instant import Router

    phrasings = [
        "take a note",
        "take a note that the op amp is a TL072",
        "write this down in my ECE350 folder: the midterm is week 9",
        "make a new folder called amp board and save this note there",
        "add to my regulator note that it needs a heatsink",
        "read me my regulator note",
        "what notes do I have",
        "delete my scratch note",
    ]
    before = Router(planners={"launch": launch_intent.look_up})
    after = Router(planners={"note": note_intent.look_up, "launch": launch_intent.look_up})

    hits_before = hits_after = 0
    print("\n  free tier, before and after:\n")
    for text in phrasings:
        b = before.route(text)
        a = after.route(text)
        ok_b = b.handled or b.action is not None
        ok_a = a.handled or a.action is not None
        hits_before += ok_b
        hits_after += ok_a
        print(f"    {text!r:62} before={'HIT' if ok_b else 'miss':4}  "
              f"after={'HIT' if ok_a else 'miss'}")
    print(f"\n  {hits_before}/{len(phrasings)} answered free before, "
          f"{hits_after}/{len(phrasings)} after.\n")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="time the vault notebook")
    ap.add_argument("--reps", type=int, default=25)
    ap.add_argument("--out", default=str(REPO_ROOT / "media" / "data"
                                        / "2026-08-28-note-turn-cost.csv"))
    ap.add_argument("--probe", action="store_true", help="the free-tier before/after only")
    args = ap.parse_args(argv)

    try:
        if args.probe:
            probe()
            return 0

        print(f"\n  timing the notebook, {args.reps} reps each, vault in {_TMP.name}\n")
        rows = measure(args.reps)
        probe()

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  wrote {out.relative_to(REPO_ROOT).as_posix()}\n")
        return 0
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
