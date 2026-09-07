#!/usr/bin/env python3
"""
Module:  verify_consent.py
Purpose: Prove the permission gate cannot be talked into yes. Nothing else tested this.
Author:  LB
Date:    2026-09-02

    python tools/verify_consent.py
    python tools/verify_consent.py --probe     # put the three defects back, expect RED

## Why this file exists

`tools/verify_engine.py` proves a gate OPENS and CLOSES — that a pending action waits, that
silence declines, that "no, don't do that" does not run the command. All true, all passing, and
none of it looks at the function that decides. `orchestrator/classify_yes.is_yes` had **three
independent defects** on 2026-09-02 and every one of them was green:

    is_yes("Don't run it")           = True    <-- APPROVES EXECUTION
    is_yes("Of course not")          = True    <-- APPROVES EXECUTION
    is_yes("What does that do?")     = True    <-- APPROVES EXECUTION

The gate was tested with the two phrases the gate's author had in mind. This is a corpus of
what a person actually says.

## The asymmetry this file enforces

**A false no costs one repeated question. A false yes runs a shell command LB refused.** So
section 1 is much larger than section 2, and a failure there is a different severity of failure.
`_NO` may be generous; `_YES` must be strict.

## Section 4 is the pair, and it is the point

`is_yes` and `os_controller.refuse` are the two halves of D4 — the model composes, a human
approves, the blocklist backstops. On 2026-09-02 both halves failed on the same sentence:
saying *"Don't run it"* to a proposed `Remove-Item C:\\Users\\user -Rec -Force` approved a
command that the blocklist then let through. Neither hole alone is a catastrophe; the pair is,
and nothing tested the pair.

`--probe` restores all three `is_yes` defects — the apostrophe-eating normaliser, the bare
words, and the missing question check — and shows sections 1 and 4 going red.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.harness_lib import bootstrap, check, counts as _tally, section  # noqa: E402

bootstrap()


from orchestrator import classify_yes                                # noqa: E402
from tools.os_controller import refuse                               # noqa: E402


# =========================================================================================
# The corpus.
#
# Every entry is something a person says at a spoken permission prompt. Nothing here is
# adversarial — this is not a test of an attacker, it is a test of LB in his own kitchen
# declining something, or asking what it does before deciding. Six of these were measured
# APPROVING SHELL EXECUTION on 2026-09-02 and are marked.
# =========================================================================================

MUST_NOT_APPROVE = [
    # --- apostrophes. The normaliser turned "don't" into "don t", so `_NO`'s "dont" never
    # matched and `_YES` picked up "run it" instead. The most natural refusal there is.
    ("Don't run it", "MEASURED True"),
    ("Don't do that", "MEASURED True"),
    ("Don't.", ""),
    ("No, don't", ""),
    ("I'd rather not", ""),
    ("Please don't", ""),
    ("Let's not", ""),
    # --- bare words in `_YES`. "of course not" contains "of course"; every clarifying
    # question contains "do" or "please".
    ("Of course not", "MEASURED True"),
    ("Course not", ""),
    ("please read that back to me first", "MEASURED True"),
    ("please don't do that", ""),
    # --- questions. Asking is not consenting, and there was nothing that knew it.
    ("What does that do?", "MEASURED True"),
    ("Do I need to do that?", "MEASURED True"),
    ("What is that command going to do?", ""),
    ("Why would I do that?", ""),
    ("How long will that take?", ""),
    ("Which folder does that touch?", ""),
    ("hold on, what does that command do", "MEASURED True"),
    # --- plain refusals that always worked. Regression cover.
    ("no", ""),
    ("nope", ""),
    ("no thanks", ""),
    ("cancel", ""),
    ("stop", ""),
    ("forget it", ""),
    ("leave it", ""),
    ("never mind", ""),
    ("abort", ""),
    ("skip it", ""),
    ("no way", ""),
    # --- "not yet" is not "yes". A gate that hears "wait" and runs the command has not
    # listened, whatever a dictionary says about the word.
    ("wait", ""),
    ("hold on", ""),
    ("hold off", ""),
    ("not now", ""),
    ("not yet", ""),
    ("better not", ""),
    # --- mumbles and room tone. None, not True.
    ("", ""),
    ("   ", ""),
    ("uh", ""),
    ("hmm", ""),
    ("the weather is nice today", ""),
    ("what time is it", ""),
]

# What must STILL work. A gate that refuses these is a gate LB stops using, and a gate LB
# stops using is one he turns off — which is strictly worse than a gate that is slightly loose.
MUST_APPROVE = [
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "OK.", "Yes.", "yes please",
    "go ahead", "go on", "do it", "run it", "go for it", "why not", "of course",
    "definitely", "affirmative", "please do", "Yes, go ahead", "sure, go ahead",
    "yeah do it", "ok run it",
]


def run(probe: bool = False) -> int:
    print("=" * 78)
    print("  verify_consent.py — what the permission gate says yes to")
    print("=" * 78)

    if probe:
        _reintroduce_the_bugs()

    is_yes = classify_yes.is_yes

    # =====================================================================================
    section("1. what must NEVER be read as consent")
    # =====================================================================================
    for text, note in MUST_NOT_APPROVE:
        verdict = is_yes(text)
        label = f"{text!r} does not approve" + (f"   [{note}]" if note else "")
        check(verdict is not True, label, "" if verdict is not True else f"returned {verdict}")

    # =====================================================================================
    section("2. what must STILL approve — a gate nobody can pass gets switched off")
    # =====================================================================================
    for text in MUST_APPROVE:
        verdict = is_yes(text)
        check(verdict is True, f"{text!r} approves",
              "" if verdict is True else f"returned {verdict}")

    # =====================================================================================
    section("3. the three properties, stated directly")
    # =====================================================================================
    check(classify_yes.normalise("don't") == "dont",
          "normalise DROPS the apostrophe, as its docstring always claimed",
          repr(classify_yes.normalise("don't")))
    check(classify_yes.normalise("don’t") == "dont",
          "...including the curly one Whisper emits",
          repr(classify_yes.normalise("don’t")))
    check("do" not in classify_yes._YES and "course" not in classify_yes._YES
          and "please" not in classify_yes._YES,
          "no bare 'do', 'course' or 'please' in the yes list",
          str([w for w in ("do", "course", "please") if w in classify_yes._YES]))
    check("do it" in classify_yes._YES and "of course" in classify_yes._YES,
          "...while the unambiguous compounds are still there")
    check(classify_yes.is_question("What does that do?"), "a '?' makes it a question")
    check(classify_yes.is_question("what does that do"), "so does a leading interrogative")
    check(not classify_yes.is_question("do it"),
          "'do it' is NOT a question — the interrogative list excludes 'do' on purpose")
    check(is_yes("why not") is True,
          "'why not' is agreement even though it starts with an interrogative",
          f"returned {is_yes('why not')}")

    # A no must beat a yes wherever both appear.
    for text in ("ok no", "yes, actually no", "sure — no, don't", "of course not"):
        check(is_yes(text) is False, f"{text!r}: the refusal wins over the yes word",
              f"returned {is_yes(text)}")

    # =====================================================================================
    section("4. the PAIR — the gate and the blocklist, on one sentence")
    # =====================================================================================
    # D4: the model composes, a human approves, the blocklist backstops. On 2026-09-02 all
    # three of these commands were proposable, approvable by saying "Don't run it", and
    # unblocked. Both halves failed in the same breath, which is the only reason it mattered.
    for command in ("Remove-Item C:\\Users\\user -Rec -Force",
                    "Remove-Item C:\\Users\\user -r -Force",
                    "Get-ChildItem C:\\Users\\user -Recurse | Remove-Item -Force",
                    "gci C:\\ -Recurse | ri -Force"):
        approved = is_yes("Don't run it") is True
        blocked = refuse(command) is not None
        check(not approved and blocked,
              f"refused by voice AND by the table: {command}",
              f"approved={approved} blocked={blocked}")

    # And the near-miss, from the other direction: the guard must not have been widened into
    # a ban on listing files.
    for command in ("Get-ChildItem C:\\Users\\user -Recurse | Select-Object Name",
                    "Get-ChildItem C:\\repo -Recurse | Select-String TODO",
                    "Remove-Item build\\temp.obj"):
        check(refuse(command) is None,
              f"still allowed, because it deletes nothing recursively: {command}",
              str(refuse(command)))

    print("\n" + "=" * 78)
    print(f"  {_tally.passed + _tally.failed} checks, {_tally.passed} passed, {_tally.failed} failed")
    print("=" * 78)
    if probe:
        if _tally.failed:
            print(f"\n  The harness BITES: {_tally.failed} check(s) went red.\n")
            return 0
        print("\n  PROBE DID NOT BITE — this harness is not testing what it claims.\n")
        return 1
    if _tally.failed:
        print(f"\n  {_tally.failed} RED\n")
        return 1
    print(f"\n  {_tally.passed}/{_tally.passed} checks passed — all green\n")
    return 0


def _reintroduce_the_bugs() -> None:
    """Put `is_yes` back exactly as it was before 2026-09-02.

    All three defects, because they interact: fixing only the word lists still leaves
    "Don't run it" approving, since the normaliser destroys the "don't" before the lists are
    ever consulted. A probe that restored one at a time would understate the hole.
    """
    print("\n  [--probe] restoring the pre-2026-09-02 is_yes: apostrophe-eating normalise, "
          "bare words, no question check\n")

    old_yes = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go on", "go ahead",
               "please do", "do it", "run it", "go for it", "why not", "affirmative",
               "course", "of course", "definitely", "please", "yes please", "do")
    old_no = ("no", "nope", "nah", "dont", "do not", "never mind", "nevermind", "forget it",
              "leave it", "cancel", "stop", "no thanks", "negative", "dont bother", "skip it",
              "abort", "no way")

    def old_normalise(text: str) -> str:
        # The bug: punctuation becomes a SPACE, so "don't" -> "don t" and "dont" never matches.
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()

    def old_is_yes(transcript: str):
        text = old_normalise(transcript)
        if not text:
            return None
        if classify_yes._matches(text, old_no):
            return False
        if classify_yes._matches(text, old_yes):
            return True
        return None

    classify_yes.is_yes = old_is_yes
    classify_yes.normalise = old_normalise
    classify_yes._YES = old_yes
    classify_yes._NO = old_no


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prove the gate cannot be talked into yes")
    ap.add_argument("--probe", action="store_true",
                    help="restore the three pre-2026-09-02 defects, expect RED")
    args = ap.parse_args(argv)
    return run(probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
