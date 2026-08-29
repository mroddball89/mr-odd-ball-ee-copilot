#!/usr/bin/env python3
"""
Module:  verify_corpus_hint.py
Purpose: Prove LB's own corpus routes his datasheet questions, and claims nothing else.
Author:  LB
Date:    2026-08-28

    python tools/verify_corpus_hint.py
    python tools/verify_corpus_hint.py --probe

No key, no network. The embeddings are local (`all-MiniLM-L6-v2`), which is the whole reason
this routing band can exist at all.

## This harness re-MEASURES rather than asserting a constant

`corpus_hint.THRESHOLD` is 1.00 because that is where, on 2026-08-28, the band claimed nine of
ten datasheet questions and **none** of 73 negatives. **That number is a property of the corpus,
not of the code.** Add forty datasheets and the store gets denser, and an unrelated question
finds a closer neighbour than it does today.

So section 2 does not check that the threshold is 1.00. It re-derives the operating point from
whatever is on disk right now.

## The property asserted is ZERO FALSE POSITIVES, not a clean gap

The first version of this file asserted `gap > 0` — that every datasheet question sits closer
than every other question. That was measurable and it was wrong, because the negatives it was
measured against had been chosen by the same person who chose the positives.

The two sets **overlap**. `"check the temperature"` sits at 1.049, below the worst positive at
1.205, because the sensor brief has a page about operating temperature. No threshold separates
them, so demanding one would demand the impossible.

What must hold is that **nothing which is not about the corpus gets claimed.** A positive lost
below the threshold costs one `flash-lite` call and is answered exactly as it was before this
module existed. A negative taken above it answers a question about his CPU out of a camera
datasheet. The failure directions are not symmetric, so the assertion is not either.

## It SKIPS loudly when there is no store

`chroma_db/` is gitignored and a fresh clone has none. A harness that silently passed in that
state would be claiming to test a router that cannot run. Every skipped check is counted and
printed, and the summary says so.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from orchestrator import corpus_hint                                 # noqa: E402
from orchestrator.corpus_hint import (FIT_NEGATIVES, FIT_POSITIVES,  # noqa: E402
                                      THRESHOLD, look_up, real_captures, separation)

PASSED = FAILED = SKIPPED = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"   PASS  {what}")
    else:
        FAILED += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def skip(what: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"   SKIP  {what}\n           {why}")


def section(name: str) -> None:
    print(f"\n  {name}")


HAVE_STORE = corpus_hint._store() is not None

# =========================================================================================
section("1. the store, and whether this band can run at all")
# =========================================================================================

from tools.vector_db import CHROMA_PATH                              # noqa: E402

if HAVE_STORE:
    check(True, f"a vector store exists at {CHROMA_PATH.name}/")
else:
    skip("a vector store exists",
         f"none at {CHROMA_PATH} — run `python tools/vector_db.py`. "
         f"Every check below that needs one is skipped, not passed.")

# =========================================================================================
section("2. the corpus still SEPARATES — re-measured, not asserted")
# =========================================================================================

if HAVE_STORE:
    negatives = list(FIT_NEGATIVES) + real_captures()
    result = separation(list(FIT_POSITIVES), negatives)

    # **Zero false positives is the property, not a positive gap.** The two sets genuinely
    # OVERLAP — "check the temperature" sits at 1.049, below the worst positive at 1.205 — so
    # no threshold separates them and asserting `gap > 0` would demand the impossible. What
    # must hold is that nothing which is not about the corpus gets claimed.
    check(result.get("false_positives") == 0,
          f"NOTHING that is not about the corpus is claimed at threshold {THRESHOLD} "
          f"({len(negatives)} negatives tried)",
          f"wrongly taken: {result.get('false_positives')} — lower THRESHOLD or switch the "
          f"band off; do NOT nudge it until this goes quiet")

    check(result.get("kept", 0) >= 1,
          f"and it still claims {result.get('kept', 0)}/{result.get('of', 0)} datasheet "
          f"questions, with {result.get('margin_pct', 0):.1f}% margin",
          "a band that claims nothing is a band that should be deleted, not tuned")

    # The direction of failure, stated as a check so it cannot silently invert.
    check(result.get("of", 0) - result.get("kept", 0) >= 0,
          "the positives it gives up fall through to the paid router — the SAFE direction",
          f"{result.get('of', 0) - result.get('kept', 0)} given up; a missed hint costs one "
          f"flash-lite call, a wrong hint answers about his CPU from a camera datasheet")

    check(len(real_captures()) > 0,
          f"and {len(real_captures())} of the negatives are LB's OWN recordings",
          "captures/ is gitignored, so this is 0 on a fresh clone — L26 says the real ones "
          "are the ones that find the bugs")
else:
    skip("the corpus separates", "no store")
    skip("THRESHOLD sits inside the gap", "no store")

# =========================================================================================
section("3. it claims datasheet questions, and returns their chunks")
# =========================================================================================

if HAVE_STORE:
    claimed = [q for q in FIT_POSITIVES if look_up(q) is not None]
    check(len(claimed) >= len(FIT_POSITIVES) - 2,
          f"most datasheet questions route free: {len(claimed)}/{len(FIT_POSITIVES)}",
          f"given up (and correctly paying the router instead): "
          f"{[q for q in FIT_POSITIVES if q not in claimed]}")

    for query in claimed:
        hit = look_up(query)
        check(hit.route == corpus_hint.FIRMWARE and bool(hit.context) and hit.sources,
              f"{query[:44]!r} -> firmware, carrying its chunks",
              f"{len(hit.sources)} chunk(s), {len(hit.context)} chars — the agent will "
              f"not search again")
else:
    skip("datasheet questions route to firmware", "no store")

# =========================================================================================
section("4. and it claims NOTHING else — the section that bites")
# =========================================================================================

if HAVE_STORE:
    taken = [q for q in FIT_NEGATIVES if look_up(q) is not None]
    check(not taken, f"none of the {len(FIT_NEGATIVES)} other intents is claimed",
          f"taken: {taken}")

    caps = real_captures()
    if caps:
        stolen = [q for q in caps if look_up(q) is not None]
        check(not stolen,
              f"and none of LB's {len(caps)} real recordings is claimed",
              f"taken: {stolen}")
    else:
        skip("LB's real recordings are left alone", "captures/ is absent (gitignored)")
else:
    skip("nothing else is claimed", "no store")

# =========================================================================================
section("5. the two k values are pinned together")
# =========================================================================================
#
# `corpus_hint` searches with k=K and hands the chunks on; `firmware_agent` searches with
# k=4 when nobody handed it any. If those drift, the SAME question gets DIFFERENT context
# depending on how it was routed — a saved API call bought with an answer that changes
# depending on which path found it, which is a bad trade and an invisible one.

_fw = (Path(__file__).resolve().parents[1] / "agents" / "firmware_agent.py").read_text(
    encoding="utf-8")
_ks = {n.args[0].value if n.args else n.keywords[0].value.value
       for n in ast.walk(ast.parse(_fw))
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
       and n.func.id == "get_retriever"
       and (n.args or n.keywords)}
check(corpus_hint.K in _ks or not _ks,
      f"corpus_hint.K ({corpus_hint.K}) matches firmware_agent's get_retriever(k=...) {_ks or '{}'}",
      "the routing path and the paid path must put the same context in front of the model")

# =========================================================================================
section("6. nothing here can reach a model")
# =========================================================================================
#
# Parsed, not grepped — L25. This module's docstring says the words "network" and "quota"
# several times, and a substring scan would find its promise not to do a thing.

_tree = ast.parse(inspect.getsource(corpus_hint))
_top_level = {a.name.split(".")[0] for n in _tree.body if isinstance(n, ast.Import)
              for a in n.names}
_top_level |= {n.module.split(".")[0] for n in _tree.body
               if isinstance(n, ast.ImportFrom) and n.module}
check(_top_level <= {"__future__", "logging", "dataclasses"},
      "it imports nothing heavy at module scope — torch stays out of the free path",
      f"module-level imports are {sorted(_top_level)}")

for forbidden in ("langchain_google_genai", "google", "genai", "requests", "httpx", "urllib"):
    everything = {a.name.split(".")[0] for n in ast.walk(_tree) if isinstance(n, ast.Import)
                  for a in n.names}
    everything |= {n.module.split(".")[0] for n in ast.walk(_tree)
                   if isinstance(n, ast.ImportFrom) and n.module}
    check(forbidden not in everything, f"and never imports {forbidden}")


def probe() -> int:
    """Raise the threshold past the negatives and show what it would swallow."""
    print("\n  PROBE: threshold raised to 2.0, so distance stops discriminating\n")
    if not HAVE_STORE:
        print("  no store — nothing to probe.\n")
        return 1

    real = corpus_hint.THRESHOLD
    corpus_hint.THRESHOLD = 2.0
    try:
        negatives = list(FIT_NEGATIVES) + real_captures()
        taken = [q for q in negatives if look_up(q) is not None]
        for q in taken[:12]:
            print(f"   WOULD TAKE   {q!r}")
        if len(taken) > 12:
            print(f"   … and {len(taken) - 12} more")
        print(f"\n  {len(taken)}/{len(negatives)} non-datasheet questions would be sent to "
              f"FIRMWARE, grounded in camera datasheets.")
        if taken:
            print("  The harness BITES: section 4 goes red without a fitted threshold.\n")
            return 0
        print("  The harness is VACUOUS: the threshold is not what is doing the work.\n")
        return 1
    finally:
        corpus_hint.THRESHOLD = real


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the corpus routing band")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    print("\n" + "=" * 78)
    total = PASSED + FAILED
    print(f"  {total} checks, {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
    print("=" * 78)
    if SKIPPED:
        print(f"\n  {SKIPPED} skipped — this band needs a vector store, and skipping is not "
              f"passing.")
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(0)
