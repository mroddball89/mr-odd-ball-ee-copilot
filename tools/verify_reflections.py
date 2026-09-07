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
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.harness_lib import bootstrap, check, counts as _tally, section  # noqa: E402

bootstrap()


from tools import reflections                                        # noqa: E402


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

    # =====================================================================================
    section("6. the same mistake twice is ONE entry with a count")
    # =====================================================================================

    # The measured pathology of 2026-09-04: 22 slow-turn entries, identical but for the
    # transcript and the number of seconds, six of which rode on every single agent prompt.
    reflections.clear()
    started = datetime.now().isoformat(timespec="seconds").replace("T", " ")
    for spoken, secs in (("ball.", 85), ("Okay.", 57), ("Mr. Albo.", 135),
                         ("Yeah, yeah, yeah, yeah.", 149), ("Whoa.", 51)):
        reflections.note("slow-turn", f"answer {spoken!r} via the persona path",
                         f"it took {secs} seconds — 1s to route and {secs - 1}s in the agent",
                         "prefer the free path for this kind of question where one exists")

    entries = reflections.recent(limit=0)
    check(len(entries) == 1, "five false-wake slow-turns collapse to one entry",
          f"got {len(entries)}: {[e.what for e in entries]}")
    check(entries[0].count == 5, "...carrying the count", f"got {entries[0].count}")
    check("×5" in entries[0].line(), "...and the prompt line says how many times",
          entries[0].line())
    check(entries[0].what.endswith("via the persona path"),
          "...keeping the NEWEST wording, not the oldest")
    # These five land inside one second, so `first` and `when` are equal here and that is
    # correct. What this can prove is that `first` is anchored at the START of the run rather
    # than blank or copied from the last recording; section 7 proves it survives a real gap.
    check(started <= entries[0].first <= entries[0].when,
          "...and `first` marks the start of the run, not the latest occurrence",
          f"started={started} first={entries[0].first} when={entries[0].when}")

    # The count is the whole point, so it has to survive being written and read back.
    check(reflections.recent()[0].count == 5, "the count round-trips through the file")
    check("**Seen:** 5 times" in reflections.LEDGER.read_text(encoding="utf-8"),
          "...and reads as a sentence in the file LB opens")

    # A DIFFERENT route is a different mistake and must not be swallowed by the count.
    reflections.note("slow-turn", "answer 'look in the vault' via the general path",
                     "it took 48 seconds — 1s to route and 47s in the agent", "")
    check(len(reflections.recent(limit=0)) == 2,
          "a slow turn on a different path is a SEPARATE entry",
          "collapsing these would hide which path is actually slow")

    # The negative that matters most: a digit-bearing token names a specific thing, and
    # `similar()` was measured into weighting it double. Merging on it would be self-defeating.
    reflections.clear()
    reflections.note("academic", "read the ECE350 syllabus", "the note was not in the vault")
    reflections.note("academic", "read the ECE250 syllabus", "the note was not in the vault")
    check(len(reflections.recent(limit=0)) == 2,
          "two course codes stay two entries",
          "ECE350 and ECE250 are different courses; the digits ARE the identity")

    reflections.clear()
    reflections.note("os/timeout", "run `apt-get update`", "killed at 15 seconds")
    reflections.note("os/timeout", "run `pip install numpy`", "killed at 15 seconds")
    check(len(reflections.recent(limit=0)) == 2,
          "two different commands stay two entries",
          "backticks mark a command, and the command is what failed")

    reflections.clear()
    reflections.note("handled/quiz", "quiz explanation degraded",
                     "ImportError: cannot import name 'CLOUD_TIMEOUT_S' from 'engine.models'")
    reflections.note("handled/quiz", "quiz explanation degraded",
                     "ImportError: cannot import name 'AGENT_MODEL' from 'engine.models'")
    check(len(reflections.recent(limit=0)) == 2,
          "two different missing names stay two entries",
          "an exception names what it could not find IN QUOTES; that is not a transcript")

    # A lesson written once must not be lost by a later recording that had nothing to add.
    reflections.clear()
    reflections.note("crash", "answer 'a' via the persona path", "it took 60 seconds",
                     "prefer the free path")
    reflections.note("crash", "answer 'b' via the persona path", "it took 90 seconds", "")
    check(reflections.recent()[0].lesson == "prefer the free path",
          "the earlier lesson survives a later recording that had none")

    # `note` merges on TEXT so that a block it cannot parse is not quietly deleted around it.
    reflections.clear()
    reflections.note("crash", "answer 'a' via the persona path", "it took 60 seconds", "")
    hand = reflections.LEDGER.read_text(encoding="utf-8") + "\nLB wrote this by hand.\n"
    reflections.LEDGER.write_text(hand, encoding="utf-8")
    reflections.note("crash", "answer 'b' via the persona path", "it took 90 seconds", "")
    check("LB wrote this by hand." in reflections.LEDGER.read_text(encoding="utf-8"),
          "a hand-written line survives a merge happening around it",
          "the file is documented as safe to edit; a merge must not be a rewrite")

    # --- the negatives a code review found on 2026-09-06 --------------------------------

    # An exception names what it could not find IN QUOTES, and that name is the identity even
    # when it contains digits. Stripping every number from `why` merged two different missing
    # decks into one — the same mistake `_QUOTED` refuses to make in `what`, by the other door.
    reflections.clear()
    for deck in ("ece350", "ece250"):
        reflections.note("handled/quiz", "load a deck",
                         f"FileNotFoundError: No such file: 'data/quiz/{deck}.json'")
    check(len(reflections.recent(limit=0)) == 2,
          "two different missing FILES stay two entries",
          "the digits inside the quoted path are the identity, not a duration")
    both = reflections.LEDGER.read_text(encoding="utf-8")
    check("ece350.json" in both and "ece250.json" in both,
          "...and BOTH deck names survive in the file",
          "a merge keeps only the newest text, so the older identifier would be gone")
    check(reflections.similar("data/quiz/ece350.json") != [],
          "...and the ece350 failure is still findable by its path")

    # ...while the volatile numbers OUTSIDE quotes still collapse, or nothing merges at all.
    reflections.clear()
    reflections.note("slow-turn", "answer 'a' via the general path", "it took 48 seconds")
    reflections.note("slow-turn", "answer 'b' via the general path", "it took 103 seconds")
    check(len(reflections.recent(limit=0)) == 1,
          "durations outside quotes still collapse")

    # `_flatten` caps a field at 400 chars. Substituting AFTER that cap loses the closing quote
    # on a long transcript, `_QUOTED` stops matching, and the entry never merges with itself.
    # `agents/screen_agent.py` builds `what` from an unbounded question, so this is reachable.
    reflections.clear()
    for tail in ("first", "second"):
        reflections.note("screen-capture",
                         f"look at the screen to answer {'x' * 420 + tail!r}",
                         "no-tool: grim is not installed")
    check(len(reflections.recent(limit=0)) == 1,
          "a transcript longer than the field cap still merges",
          f"got {len(reflections.recent(limit=0))} — the closing quote was being truncated away")

    # `_annotations` must carry a hand-added field, not just hand-added prose. The house style
    # for this file IS `- **Name:** value`, so that is the shape LB will reach for.
    reflections.clear()
    reflections.note("crash", "answer 'a' via the persona path", "it took 60 seconds")
    text = reflections.LEDGER.read_text(encoding="utf-8").rstrip()
    reflections.LEDGER.write_text(
        text + "\n- **Note:** LB — watch this one after the timeout change\n"
               "- **Ticket:** ODD-14\n", encoding="utf-8")
    reflections.note("crash", "answer 'b' via the persona path", "it took 90 seconds")
    after = reflections.LEDGER.read_text(encoding="utf-8")
    check("- **Note:** LB — watch this one" in after,
          "a hand-added **Note:** field survives a merge",
          "only the four fields this module regenerates may be dropped")
    check("- **Ticket:** ODD-14" in after, "...and so does a hand-added **Ticket:**")
    check(after.count("**Seen:**") == 1,
          "...and the module's own fields are still regenerated once, not duplicated")

    # `--compact` must not overwrite its own backup on a second run. The second run is the
    # likely one: compact, read the file, wonder if something was dropped, run it again.
    reflections.clear()
    for i in range(4):
        reflections.note("bulk-compact", f"do the thing", f"it failed after {i} seconds")
    for stale in reflections.LEDGER.parent.glob(reflections.LEDGER.name + ".*.bak"):
        stale.unlink()
    reflections.note("other", "something else", "a different failure")
    first_before, _ = reflections.compact()
    time.sleep(1.05)                       # the backup name is stamped to the second
    reflections.compact()

    baks = sorted(reflections.LEDGER.parent.glob(reflections.LEDGER.name + ".*.bak"))
    counts = [b.read_text(encoding="utf-8").count("\n## ") for b in baks]
    check(len(baks) == 2, "each --compact writes its OWN dated backup", f"got {len(baks)}")
    check(first_before in counts,
          "...so the FIRST run's pre-compaction file is still recoverable",
          f"entries per backup {counts}, first run compacted {first_before}")

    # =====================================================================================
    section("7. a mistake that stopped happening stops being injected")
    # =====================================================================================

    reflections.clear()
    old = (datetime.now() - timedelta(days=reflections.PROMPT_MAX_AGE_DAYS + 10))
    stamp = old.isoformat(timespec="seconds").replace("T", " ")
    reflections.LEDGER.write_text(
        reflections._BANNER + f"\n## {stamp} — os/not-installed\n"
        "- **Tried:** open `firefox`\n"
        "- **Went wrong:** firefox is not on the pinned PATH\n", encoding="utf-8")

    check(reflections.recent() == [],
          f"an entry older than {reflections.PROMPT_MAX_AGE_DAYS} days is not 'recent'")
    check(len(reflections.recent(limit=0, max_age_days=0)) == 1,
          "...but it is still in the FILE, which is LB's to read in full")
    check(reflections.similar("can you open firefox") != [],
          "...and `similar` still finds it",
          "'have I broken this before' does not have an expiry date; 'what went wrong lately' does")

    block = reflections.for_prompt("")
    check(block == "", "a ledger of only stale entries injects NOTHING into a prompt",
          f"got {block!r}")

    # Recurrence is what keeps an entry alive — nothing has to decide when a problem is over.
    reflections.note("os/not-installed", "open `firefox`", "firefox is not on the pinned PATH")
    fresh = reflections.recent()
    check(len(fresh) == 1 and fresh[0].count == 2,
          "recording it again revives the SAME entry rather than starting a new one",
          f"got {[(e.what, e.count) for e in fresh]}")
    check(fresh[0].first.startswith(stamp[:10]),
          "...and it still remembers when the run started",
          f"first={fresh[0].first}")

    # =====================================================================================
    section("8. an existing ledger can be compacted in place")
    # =====================================================================================

    # A ledger written before any of this existed: no `Seen:` lines anywhere.
    legacy = [reflections._BANNER]
    for n, secs in enumerate((85, 57, 135, 149, 51, 66, 94, 103), start=1):
        legacy.append(f"\n## 2026-09-0{n} 07:2{n}:14 — slow-turn\n"
                      f"- **Tried:** answer 'thing {n}' via the persona path\n"
                      f"- **Went wrong:** it took {secs} seconds — 1s to route and "
                      f"{secs - 1}s in the agent\n")
    reflections.LEDGER.write_text("".join(legacy), encoding="utf-8")

    check(len(reflections.recent(limit=0, max_age_days=0)) == 8, "eight legacy entries to start")
    before_n, after_n = reflections.compact()
    check((before_n, after_n) == (8, 1), "compaction merges them to one",
          f"got {before_n} -> {after_n}")
    check(reflections.recent(limit=0, max_age_days=0)[0].count == 8,
          "...with the full count")
    baks = sorted(reflections.LEDGER.parent.glob(reflections.LEDGER.name + ".*.bak"))
    check(bool(baks), "...and a dated .bak is written first, so it is reversible")
    check(any("07:21:14" in b.read_text(encoding="utf-8") for b in baks),
          "...containing the original entries")

    # =====================================================================================
    section("9. one verbose entry cannot take the prompt budget")
    # =====================================================================================

    # A real OpenRouter 429: the message, a headers dict, a limit source and a reset epoch.
    # Two of these were consuming half the injected block after the 2026-09-04 compaction.
    fat = ("RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit exceeded: "
           "free-models-per-day. Add 10 credits to unlock 1000 free model requests per day', "
           "'code': 429, 'metadata': {'headers': {'X-RateLimit-Limit': '50', "
           "'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1788048000000'}}}}")
    reflections.clear()
    reflections.note("crash", "answer 'a question' via the persona agent", fat)

    stored = reflections.recent()[0]
    check(len(stored.why) > reflections.LINE_MAX_CHARS,
          "the FILE keeps the long error, because LB reads it to debug",
          f"stored {len(stored.why)} chars")
    check(len(stored.line()) < len(fat),
          "...but the prompt line is clipped", f"line is {len(stored.line())} chars")
    check("RateLimitError" in stored.line() and "free-models-per-day" in stored.line(),
          "...keeping the part that identifies the failure")
    check("X-RateLimit-Reset" not in stored.line(),
          "...and dropping the part no model needs")

    reflections.clear()
    for i in range(reflections.PROMPT_ENTRIES + 2):
        reflections.note(f"crash-{i}", f"answer 'question {i}' via the persona agent", fat)
    block = reflections.for_prompt("")
    check(block.count("\n- [") >= reflections.PROMPT_ENTRIES,
          f"all {reflections.PROMPT_ENTRIES} recent mistakes now fit in the block",
          f"got {block.count(chr(10) + '- [')} — before clipping, two of these filled it")
    check(len(block) <= reflections.MAX_PROMPT_CHARS,
          "...and the block is still under the cap", f"got {len(block)}")

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
    print(f"  {_tally.passed + _tally.failed} checks, {_tally.passed} passed, {_tally.failed} failed")
    print("=" * 78)
    if _tally.failed:
        print(f"\n  {_tally.failed} RED\n")
        raise SystemExit(1)
    print(f"\n  {_tally.passed}/{_tally.passed} checks passed — all green\n")
    raise SystemExit(0)
