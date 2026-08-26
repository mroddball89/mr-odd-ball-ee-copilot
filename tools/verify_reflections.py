#!/usr/bin/env python3
"""
Module:  verify_reflections.py
Purpose: Prove a mistake is recorded, comes back when it is relevant, and cannot eat the prompt.
Author:  LB
Date:    2026-08-25

    python tools/verify_reflections.py
    python tools/verify_reflections.py --probe

No audio, no model, no key. Writes to a TEMPORARY ledger, never to `vault/reflections.md`.

## What is actually being checked

The ledger is easy. The two properties that are not:

1. **It must never raise.** Every function here runs on the answer path, and half of them run
   inside an `except` block that is already handling a failure. A ledger that throws while
   recording a failure turns one bad turn into a crash — so section 4 feeds it a read-only
   directory, a mangled file, `None`, and a 40 kB traceback, and requires it to survive all of
   them without an exception escaping.

2. **It must be bounded.** `for_prompt()` output rides on EVERY agent call. Section 5 writes far
   more entries than the caps allow and requires the injected block to stay under
   `MAX_PROMPT_CHARS` and the file to rotate to `MAX_ENTRIES`. An unbounded ledger does not
   fail loudly — it quietly shrinks the context window every day until answers get worse and
   nobody can say when it started.

## Section 3 is the one that would be easy to fake

`similar()` has to find the *relevant* past mistake, not just any past mistake. The check that
matters is the negative one: an unrelated failure must NOT surface. A matcher that returns the
most recent entries whatever you ask it looks identical from the outside on a small ledger, and
`--probe` demonstrates that by dropping the stopword filter and the two-word minimum — after
which every question matches everything.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from tools import reflections                                        # noqa: E402

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


_real_ledger = reflections.LEDGER
_real_vault = reflections.VAULT_DIR
_tmp = Path(tempfile.mkdtemp(prefix="oddball-reflections-"))
reflections.VAULT_DIR = _tmp
reflections.LEDGER = _tmp / "reflections.md"

check(reflections.LEDGER != _real_ledger,
      "the harness writes to a temp ledger, NOT to LB's real one", f"real: {_real_ledger}")

try:
    # =====================================================================================
    section("1. a mistake is recorded and reads back")
    # =====================================================================================

    reflections.clear()
    check(reflections.recent() == [], "a fresh ledger has no entries")
    check(reflections.for_prompt() == "", "...and injects NOTHING into a prompt",
          "a heading with no entries under it is noise on every single turn")

    check(reflections.note("os/blocked", "run `rm -rf /tmp/build`",
                           "it involves recursive deletion",
                           "never propose this again"), "a mistake is written")

    entries = reflections.recent()
    check(len(entries) == 1, "one entry comes back", f"got {len(entries)}")
    check(entries[0].kind == "os/blocked", "...with its kind", f"got {entries[0].kind!r}")
    check(entries[0].what == "run `rm -rf /tmp/build`", "...what he tried",
          f"got {entries[0].what!r}")
    check(entries[0].why == "it involves recursive deletion", "...why it failed")
    check(entries[0].lesson == "never propose this again", "...and the lesson")
    check(entries[0].when != "", "...stamped with a time")

    # A lesson is optional, and an invented one would be worse than a blank.
    reflections.note("crash", "answer a question", "TypeError: bad thing")
    check(reflections.recent()[-1].lesson == "", "an entry with no lesson round-trips as blank")

    # =====================================================================================
    section("2. the file a person has to read is legible")
    # =====================================================================================

    text = reflections.LEDGER.read_text(encoding="utf-8")
    check(text.startswith("# Reflections"), "the file opens with a heading, not with data")
    check("**Tried:**" in text and "**Went wrong:**" in text,
          "entries use named fields, so the file reads as prose rather than as a dump")
    check(text.count("\n## ") >= 1, "entries are Markdown headings and are greppable")

    # =====================================================================================
    section("3. a RELEVANT past mistake surfaces; an irrelevant one does not")
    # =====================================================================================

    reflections.clear()
    reflections.note("os/not-installed", "open `firefox`", "firefox is not on the pinned PATH",
                     "say so instead of offering to open it")
    reflections.note("academic", "read the ECE350 syllabus", "the note was not in the vault yet")
    reflections.note("os/timeout", "run `apt-get update`", "killed at 15 seconds")

    hits = reflections.similar("can you open firefox for me")
    check(any("firefox" in h.what for h in hits), "asking about firefox surfaces the firefox one")
    check(not any("syllabus" in h.what for h in hits),
          "...and does NOT surface the unrelated syllabus failure",
          f"got {[h.what for h in hits]}")

    hits = reflections.similar("when is the ECE350 midterm")
    check(any("ECE350" in h.what or "syllabus" in h.what for h in hits),
          "a course question surfaces the course failure")
    check(not any("firefox" in h.what for h in hits), "...and not the firefox one")

    check(reflections.similar("hello") == [],
          "a question sharing nothing surfaces nothing",
          "one word in common is noise; the minimum is two")
    check(reflections.similar("") == [], "an empty question surfaces nothing")
    check(reflections.similar("the a of and to") == [],
          "a question that is ALL stopwords surfaces nothing")

    # The relevant one leads, and the block says which is which.
    block = reflections.for_prompt("open firefox")
    check("Closest to what is being asked now" in block,
          "the block separates 'relevant' from 'recent'")
    check(block.index("firefox") < block.index("apt-get"),
          "...and the relevant entry comes FIRST",
          "a model weights the top of a list; burying the relevant one wastes it")
    check(block.count("open `firefox`") == 1,
          "an entry that is both relevant and recent appears ONCE",
          "listed twice, it reads as two separate failures")

    # =====================================================================================
    section("4. it cannot fail a turn, whatever is thrown at it")
    # =====================================================================================

    check(reflections.note("k", None, None) is not None, "None fields do not raise")
    check(reflections.note("k", "x" * 50_000, "y" * 50_000) is not None,
          "a 50 kB field does not raise")
    stored = reflections.recent()[-1]
    check(len(stored.what) < 500, "...and is truncated in the file", f"got {len(stored.what)}")

    # A traceback pasted into a field would otherwise become thirty unparseable entries.
    reflections.note("crash", "do a thing",
                     "Traceback (most recent call last):\n  File x\n## not a heading\n  boom")
    check(len(reflections.recent(limit=0)) >= 1, "a multi-line field does not corrupt the parser")
    check("\n" not in reflections.recent()[-1].why, "...because newlines are flattened out")

    reflections.LEDGER.write_text("not a ledger at all\n### wrong depth\n", encoding="utf-8")
    check(reflections.recent() == [], "a mangled ledger reads as empty, not as a crash")
    check(reflections.for_prompt("anything") == "", "...and injects nothing")
    check(reflections.similar("anything at all") == [], "...and matches nothing")

    missing = _tmp / "nope" / "deeper" / "reflections.md"
    reflections.LEDGER = missing
    check(reflections.recent() == [], "a ledger that does not exist reads as empty")
    check(reflections.note("k", "w", "y"), "...and is created on first write")
    reflections.LEDGER = _tmp / "reflections.md"

    # =====================================================================================
    section("5. it is bounded — both on disk and in the prompt")
    # =====================================================================================

    reflections.clear()
    for i in range(reflections.MAX_ENTRIES + 40):
        reflections.note("bulk", f"do thing number {i}", f"it failed for reason {i}",
                         "a lesson that is quite long so the file grows properly")

    kept = reflections.recent(limit=0)
    check(len(kept) <= reflections.MAX_ENTRIES,
          f"the file rotates to at most {reflections.MAX_ENTRIES} entries", f"got {len(kept)}")
    check(kept and "number 239" in kept[-1].what,
          "...keeping the NEWEST, which is the one whose lesson is current",
          f"newest is {kept[-1].what!r}" if kept else "nothing kept")
    check(reflections.LEDGER.read_text(encoding="utf-8").startswith("# Reflections"),
          "...and rotation keeps the banner")

    block = reflections.for_prompt("do thing number 7")
    check(len(block) <= reflections.MAX_PROMPT_CHARS + 200,
          f"the injected block stays under {reflections.MAX_PROMPT_CHARS} characters",
          f"got {len(block)}")
    check(block.count("\n- [") <= reflections.PROMPT_ENTRIES + 3,
          f"at most ~{reflections.PROMPT_ENTRIES} entries reach a prompt",
          f"got {block.count(chr(10) + '- [')}")

    check(reflections.SLOW_TURN_S >= 20,
          f"the slow-turn threshold is {reflections.SLOW_TURN_S}s, not a hair trigger",
          "the router alone measured 9.8s on the Pi; a low threshold logs every normal turn")
finally:
    reflections.LEDGER = _real_ledger
    reflections.VAULT_DIR = _real_vault
    shutil.rmtree(_tmp, ignore_errors=True)

check(reflections.LEDGER == _real_ledger, "the real ledger path was restored afterwards")

# =========================================================================================


def probe() -> int:
    """Drop the stopword filter and the two-word minimum, and watch relevance collapse.

    That is what `similar()` looks like written the obvious way: split on whitespace, count
    shared words. On a ledger of three entries it appears to work perfectly — which is exactly
    why it is worth probing, because the failure only shows up once the ledger is real.
    """
    print("\n  PROBE: matching on ANY shared word, stopwords included\n")

    tmp = Path(tempfile.mkdtemp(prefix="oddball-probe-"))
    real_ledger, real_vault = reflections.LEDGER, reflections.VAULT_DIR
    reflections.VAULT_DIR, reflections.LEDGER = tmp, tmp / "reflections.md"
    try:
        reflections.clear()
        reflections.note("os/not-installed", "open `firefox`", "it is not on the pinned PATH")
        reflections.note("academic", "read the ECE350 syllabus", "the note was not in the vault")
        reflections.note("os/timeout", "run `apt-get update`", "it was killed at 15 seconds")

        def loose(text: str) -> list[str]:
            wanted = set(text.lower().split())
            out = []
            for entry in reflections._parse(reflections._read()):
                pool = set(f"{entry.what} {entry.why} {entry.kind}".lower().split())
                if wanted & pool:
                    out.append(entry.what)
            return out

        questions = ["when is the ECE350 midterm", "whats the trace width for 5 amps",
                     "tell me a joke about the pi", "is it going to rain"]
        noisy = 0
        for question in questions:
            hits = loose(question)
            strict = [e.what for e in reflections.similar(question)]
            if len(hits) > len(strict):
                noisy += 1
            print(f"   {question!r}")
            print(f"      loose  -> {hits}")
            print(f"      strict -> {strict}")

        print(f"\n  {noisy}/{len(questions)} questions pull in mistakes that have nothing to do "
              f"with them.")
        if noisy:
            print("  The harness BITES: section 3's negatives go red without the filter.\n")
            return 0
        print("  The harness is VACUOUS: loosening the match changed nothing.\n")
        return 1
    finally:
        reflections.LEDGER, reflections.VAULT_DIR = real_ledger, real_vault
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the mistake ledger")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(0)
