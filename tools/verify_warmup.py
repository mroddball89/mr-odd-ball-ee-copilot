#!/usr/bin/env python3
"""
Module:  verify_warmup.py
Purpose: Prove the embedding model loads at start-up, not in the middle of a turn.
Author:  LB
Date:    2026-08-29

    python tools/verify_warmup.py
    python tools/verify_warmup.py --probe

Loads torch, so it is one of the slower harnesses. That is unavoidable: the thing under test
IS the load, and a version of this that stubbed it would test the stub.

## The nine seconds

2026-08-29, from `oddball.log`. `tools/vector_db.get_embeddings` is lazy — correctly, since a
session that never asks a datasheet question should not pay for torch — but the first one that
does pays all of it, and `engine/core._corpus_route` sits in the FREE tier in front of the
router, so it lands inside a turn:

    12:03:23  loading the embedding model (all-MiniLM-L6-v2)
    12:03:34  route 'Nothing go to sleep.' -> persona
    12:04:09  turn: ... => answered in 48.89s

Nine of those seconds were this, on a turn whose answer was a canned dismissal.

Measured split, so the fix is aimed at the right half:

    contacting the hub   import torch+langchain 5.61s   construct 3.81s
    local_files_only     import torch+langchain 5.62s   construct 2.61s

**The network is one second of it.** The other 8.4 is torch and the weights, and nothing
touches that — which is why the fix is `warm()` at start-up, and skipping the hub is a small
extra rather than the point.

## The global env var was tried first and does not work

`HF_HUB_OFFLINE=1` was the obvious way and it failed in the real process for a reason a small
test process cannot show: `huggingface_hub` reads that variable into a module constant **when
it is imported**, and `faster_whisper` imports it while loading `base.en` — on the main thread,
at start-up, racing the warm-up thread that would have set it. The rig logged

    loading the embedding model (all-MiniLM-L6-v2) from the local cache

directly above **thirty-two HTTP requests to huggingface.co**, and the harness was green.

`local_files_only` goes to the model constructor instead: scoped to this one load, immune to
import order, and — the part that matters most — unable to stop `faster_whisper` fetching a
Whisper model it does not have. A global flag would have to be right for every HuggingFace
consumer in the process; this one only has to be right for this model.

## Section 2 is the one that could break a fresh clone

Passing `local_files_only` unconditionally makes a machine that has never downloaded the model
fail, complaining about local files while perfectly online. That is the bare NO_SUCHFILE shape
`audio/wake.ensure_feature_models` exists to prevent, and it is why the argument is passed ONLY
when the files are already on disk. Section 2 points the cache at an empty directory, and at a
half-written one, and asserts it stays off for both.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PASSED = 0
FAILED = 0


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


def section(name: str) -> None:
    print(f"\n  {name}")


def fresh_module(**env):
    """Re-import `tools.vector_db` with `env` applied and HF_HUB_OFFLINE cleared."""
    import importlib
    saved = {k: os.environ.get(k) for k in
             ("HF_HUB_OFFLINE", "HF_HUB_CACHE", "HF_HOME")}
    os.environ.pop("HF_HUB_OFFLINE", None)
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import tools.vector_db as vdb
    importlib.reload(vdb)
    return vdb, saved


def restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run() -> None:
    # =====================================================================================
    section("1. the model is found in the cache, so the hub is skipped")
    # =====================================================================================
    vdb, saved = fresh_module(HF_HUB_CACHE=None, HF_HOME=None)
    try:
        cached = vdb._cache_dir()
        check(cached is not None,
              "the cached snapshot is located by path",
              str(cached) if cached else
              "not found — run `python tools/vector_db.py` once to populate the cache, or "
              "section 3 will simply measure a download")
        if cached is not None:
            check((cached / "config.json").is_file(),
                  "and it holds a real snapshot, not a half-written directory")

        check(vdb._local_only() is True,
              "so the model is loaded with local_files_only")
        check("HF_HUB_OFFLINE" not in os.environ,
              "and NO global env var is set — that approach was tried and does not work",
              "huggingface_hub reads HF_HUB_OFFLINE into a constant when it is imported, and "
              "faster_whisper imports it while loading base.en, racing the warm-up thread. "
              "The rig logged 'from the local cache' above 32 requests to huggingface.co")
    finally:
        restore(saved)

    # =====================================================================================
    section("2. a machine WITHOUT the model still gets to download it")
    # =====================================================================================
    empty = Path(tempfile.mkdtemp(prefix="oddball-nocache-"))
    vdb, saved = fresh_module(HF_HUB_CACHE=str(empty), HF_HOME=None)
    try:
        check(vdb._cache_dir() is None,
              "an empty cache reports NOT cached",
              f"looked in {empty}")
        check(vdb._local_only() is False,
              "so local_files_only is NOT passed",
              "passing it here is how a fresh clone fails to download the model while "
              "complaining about local files, on a machine that is perfectly online")
    finally:
        restore(saved)
        import shutil
        shutil.rmtree(empty, ignore_errors=True)

    # A cache directory that exists but holds no snapshot — a half-populated download, which
    # is the case a bare `folder.is_dir()` check would get wrong.
    half = Path(tempfile.mkdtemp(prefix="oddball-halfcache-"))
    (half / "models--sentence-transformers--all-MiniLM-L6-v2" / "blobs").mkdir(parents=True)
    vdb, saved = fresh_module(HF_HUB_CACHE=str(half), HF_HOME=None)
    try:
        check(vdb._cache_dir() is None,
              "a half-written cache directory with no snapshot also reports NOT cached",
              "checking only that the folder exists would call this cached and load local-only "
              "against files that are not there")
    finally:
        restore(saved)
        import shutil
        shutil.rmtree(half, ignore_errors=True)

    # =====================================================================================
    section("3. warm() pays the cost once, and a question afterwards pays nothing")
    # =====================================================================================
    vdb, saved = fresh_module(HF_HUB_CACHE=None, HF_HOME=None)
    try:
        t0 = time.monotonic()
        vdb.warm()
        cold = time.monotonic() - t0

        t0 = time.monotonic()
        vdb.warm()
        again = time.monotonic() - t0

        t0 = time.monotonic()
        vdb.get_embeddings().embed_query("reverse recovery time of a 1N4148")
        query = time.monotonic() - t0

        check(cold > 0.5, f"the first warm() really loads something ({cold:.2f}s)",
              "under half a second means it was already loaded and this measured nothing")
        check(again < 0.05,
              f"a second warm() is free ({again:.3f}s) — it is idempotent",
              "start-up calling it must not cost anything if a turn got there first")
        check(query < 1.0,
              f"and a query after warming costs {query:.3f}s, not {cold:.1f}s",
              "this is the whole point: the load moved off the answer path")

        check(vdb._embeddings is not None,
              "the module-level handle is populated, so `_corpus_route` reuses it")
    finally:
        restore(saved)

    # =====================================================================================
    section("4. warm() never raises — start-up must survive a broken cache")
    # =====================================================================================
    import importlib
    vdb, saved = fresh_module(HF_HUB_CACHE=None, HF_HOME=None)
    try:
        importlib.reload(vdb)
        boom = Path(tempfile.mkdtemp(prefix="oddball-broken-"))
        vdb._embeddings = None
        real_model = vdb.EMBEDDING_MODEL
        vdb.EMBEDDING_MODEL = str(boom / "not-a-model-at-all")
        try:
            vdb.warm()
            survived = True
        except Exception:                                             # noqa: BLE001
            survived = False
        vdb.EMBEDDING_MODEL = real_model
        vdb._embeddings = None
        check(survived,
              "a model that cannot possibly load is swallowed, not raised",
              "the voice loop starts this in a thread; an exception there kills the warm-up "
              "silently and the first question pays what it always did — which is survivable. "
              "Raising into the thread is not.")
        import shutil
        shutil.rmtree(boom, ignore_errors=True)
    finally:
        restore(saved)

    # =====================================================================================
    section("5. it is actually wired into start-up")
    # =====================================================================================
    #
    # L24 again: a warm-up nobody calls makes every check above pass while the first turn
    # still waits nine seconds. Read from the SOURCE with ast, not by importing run_voice and
    # poking at it — and not by grepping the prose, which mentions `warm` in three comments.
    import ast

    src = (Path(__file__).resolve().parents[1] / "engine" / "run_voice.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)

    warmer = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_warm_embeddings"), None)
    check(warmer is not None, "engine/run_voice.py defines _warm_embeddings")
    check(warmer is not None and any(
        isinstance(n, ast.ImportFrom) and n.module == "tools.vector_db"
        and any(a.name == "warm" for a in n.names) for n in ast.walk(warmer)),
        "...and it calls tools.vector_db.warm")

    started = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "start"
               and isinstance(n.func.value, ast.Call)
               and getattr(n.func.value.func, "attr", "") == "Thread"]
    warm_threads = [n for n in started
                    if any(isinstance(k.value, ast.Name)
                           and k.value.id == "_warm_embeddings"
                           for k in n.func.value.keywords)]
    check(bool(warm_threads),
          "...and start-up starts it in a thread, immediately",
          "called inline it would delay the wake word by the time it was meant to save")
    check(all(any(k.arg == "daemon" and k.value.value is True
                  for k in t.func.value.keywords) for t in warm_threads),
          "...as a DAEMON, so it can never hold up shutdown")


def probe() -> int:
    """Show what a turn pays with no warm-up — the state before this existed."""
    print("\n  --probe: first embed_query in a cold process, with no warm() first\n")
    import importlib
    import tools.vector_db as vdb
    importlib.reload(vdb)
    t0 = time.monotonic()
    vdb.get_embeddings().embed_query("reverse recovery time of a 1N4148")
    cold = time.monotonic() - t0
    print(f"   a single question on a cold process: {cold:.2f}s")
    print("   That is what `_corpus_route` used to add to the FIRST turn of every session,")
    print("   in the free tier, in front of the router.\n")
    if cold > 3.0:
        print("  The harness BITES.\n")
        return 0
    print("  PARTIAL: the load was faster than expected; the saving is smaller than stated.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the embedding-model warm-up")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    run()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
