#!/usr/bin/env python3
"""
Module:  verify_typed.py
Purpose: Prove he can be woken and dismissed by TYPING, and that ordinary questions still are.
Author:  LB
Date:    2026-08-19

    python tools/verify_typed.py
    python tools/verify_typed.py --probe

No audio, no model, no key. Pure string matching, which is all the typed control path is.

## Why the typed channel has to carry wake and sleep too

Spoken, waking him is openWakeWord's job and dismissal is `instant._is_dismissal`'s. Typed,
there is no audio to score — so both have to be matched as text or they simply do not exist on
that channel.

That is not symmetry for its own sake. Measured on the Pi 2026-08-19, LB's wake utterances
peaked **0.17-0.28 against a 0.76 threshold** and mostly did not fire, and what did get through
transcribed as nonsense. Typing is the channel that works when the microphone does not, so it
has to be able to do everything the voice can, including the two things that are not questions.

## Section 3 is the one that bites

A wake matcher that fires on any line containing his name turns every question ABOUT him into a
doorbell, and a dismissal matcher that fires anywhere ends a conversation mid-sentence —
`verify_turn.py` already caught "I bought it at the goodbye sale" doing exactly that. So the
negatives are the point: a question that mentions him must be ANSWERED, not obeyed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import orchestrator.instant as instant                               # noqa: E402
from orchestrator.instant import is_sleep, is_wake                   # noqa: E402

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


WAKES = [
    "hey mr odd ball",
    "Hey Mr Odd Ball",
    "HEY MR ODD BALL",
    "hey mr odd ball!",
    "  hey mr odd ball  ",
    "hey mister odd ball",
    "hey mr oddball",
    "hey oddball",
    "mr odd ball",
    "oddball",
    "odd ball",
    "wake up",
    "hey, wake up",
    "ok wake up please",
    "are you awake",
    "you there",
    "hello there",
]

SLEEPS = [
    "go to sleep",
    "Go To Sleep",
    "go to sleep.",
    "  go to sleep  ",
    "go back to sleep",
    "ok go to sleep",
    "goodnight",
    "good night",
    "thats all",
    "that's all",
    "im done",
    "goodbye",
    "bye",
    "you can rest",
    "mr odd ball, go to sleep",
]

# The ones that must do NEITHER — they are questions, and a question gets answered.
NEITHER = [
    "what does mr odd ball run on",
    "is mr odd ball asleep",
    "how much sleep did i get last night",
    "i bought it at the goodbye sale",
    "what is the sleep current of this chip",
    "how do i wake a microcontroller from deep sleep",
    "whats the trace width for 3 amps",
    "tell me a joke",
    "what time is it",
    "explain how a wake word detector works",
    "does the esp32 have a sleep mode",
    "why did my board go to sleep",
]

# =========================================================================================
section("1. typing the wake phrase wakes him")
# =========================================================================================

for text in WAKES:
    check(is_wake(text), f"wake: {text!r}")
    check(not is_sleep(text), f"...and is not read as a dismissal: {text!r}")

# =========================================================================================
section("2. typing a dismissal sends him to sleep")
# =========================================================================================

for text in SLEEPS:
    check(is_sleep(text), f"sleep: {text!r}")
    check(not is_wake(text), f"...and is not read as a wake: {text!r}")

# =========================================================================================
section("3. NEGATIVES — a question that mentions him is a QUESTION")
# =========================================================================================

for text in NEITHER:
    w, s = is_wake(text), is_sleep(text)
    check(not w and not s, f"neither wake nor sleep: {text!r}",
          f"wake={w} sleep={s} — this line would be obeyed instead of answered"
          if (w or s) else "")

check(not is_wake("") and not is_sleep(""), "an empty line does nothing at all")
check(not is_wake("   ") and not is_sleep("   "), "whitespace does nothing at all")

# =========================================================================================
section("4. the two matchers cannot both claim a line")
# =========================================================================================

both = [t for t in WAKES + SLEEPS + NEITHER if is_wake(t) and is_sleep(t)]
check(not both, "no line is read as BOTH wake and sleep", f"{both}")

check(instant._WAKE_FILLER is not instant._DISMISS_FILLER,
      "wake and dismissal use DIFFERENT filler sets",
      "sharing one would let 'mr odd ball' reduce to nothing and match every wake phrase")
check("mr" in instant._DISMISS_FILLER and "mr" not in instant._WAKE_FILLER,
      "'mr' is filler around a dismissal and load-bearing in a wake phrase")

# =========================================================================================
section("5. how LB really dismisses him — 2026-08-29, from oddball.log")
# =========================================================================================
#
# Every one of these routed to PERSONA that day. Each cost TWO API calls -- one to route, one
# to answer -- and together they were his entire OpenRouter allowance: 50 requests, 0 left by
# noon, spent telling him to go away.
#
#     sleep · Sleep. · Go to sleep, sleep. · Go to sleep. Sleep. · Nothing go to sleep.
#     Nothing.
#
# Two separate faults. `_SLEEP_PHRASES` had no bare "sleep" -- excluded on purpose, over a
# worry the end-anchor already answered -- and `_is_bare` stripped ONE phrase, so the doubling
# a transcriber produces on a short utterance failed the anchor with a second copy of the same
# dismissal left over.

SPOKEN_DISMISSALS = [
    ("sleep", "07:39 and four more times"),
    ("Sleep.", "11:22:19, with the full stop Whisper adds"),
    ("Go to sleep, sleep.", "12:03 - said once, transcribed twice"),
    ("Go to sleep. Sleep.", "the same thing with different punctuation"),
    ("Nothing go to sleep.", "12:03:21 - answered by a model, 48.9s"),
    ("Nothing.", "12:04:45 - his reply to 'What's up LB?'"),
]

for text, where in SPOKEN_DISMISSALS:
    got = is_sleep(text)
    check(got, f"{text!r} ends the conversation for free", where if got else
          f"{where} - still costs a router call and a persona call")
    check(not is_wake(text), f"...and is not read as a wake: {text!r}")

# The exclusion note that kept "sleep" out for weeks, tested rather than trusted. The anchor
# was always going to hold these; nobody had checked.
STILL_QUESTIONS = [
    "how much sleep did i get",
    "whats a good sleep schedule",
    "how do i sleep a thread in python",
    "nothing is working",
    "nothing else matters in this circuit",
    "sleep mode on the esp32",
    "does the pi sleep when idle",
    "take a note that nothing worked",
]
for text in STILL_QUESTIONS:
    got = is_sleep(text)
    check(not got, f"but {text!r} is still a question",
          "" if not got else "the end-anchor stopped holding -- this is the D38 failure")

# `repeat` is opt-in, and the wake path must not have acquired it: a doubled wake phrase is
# not something a person TYPES, and widening that matcher widens the doorbell.
check(instant._is_bare("go to sleep sleep", instant._SLEEP_PHRASES,
                       instant._DISMISS_FILLER) is False,
      "without repeat=True the doubling still fails -- the flag is what fixes it, not the list")
check(instant._is_bare("go to sleep sleep", instant._SLEEP_PHRASES,
                       instant._DISMISS_FILLER, repeat=True) is True,
      "and with it, the same string passes")

# Longest-first, which cost a working feature when it was written the other way round.
check(is_sleep("go to sleep"),
      "a phrase containing a shorter phrase still matches whole",
      "written in tuple order, 'sleep' is removed first, leaves 'go to', and this fails")

# =========================================================================================


def probe() -> int:
    """Replace the end-anchor rule with 'contains the phrase' and confirm section 3 goes red.

    That is the exact bug the rule exists to prevent, and the one `verify_turn.py` caught in
    the spoken path: a matcher that fires anywhere in the line turns "how do i wake a
    microcontroller from deep sleep" into a command.
    """
    print("\n  PROBE: matching anywhere in the line instead of end-anchored\n")

    import re

    def loose_wake(text: str) -> bool:
        flat = instant.normalise(text)
        return any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", flat)
                   for p in instant._WAKE_PHRASES)

    def loose_sleep(text: str) -> bool:
        flat = instant.normalise(text)
        return any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", flat)
                   for p in instant._SLEEP_PHRASES)

    caught = 0
    for text in NEITHER:
        w, s = loose_wake(text), loose_sleep(text)
        if w or s:
            caught += 1
            print(f"   WOULD OBEY   {'wake' if w else 'sleep'}: {text!r}")

    print(f"\n  {caught}/{len(NEITHER)} questions would be obeyed instead of answered.")
    if caught:
        print("  The harness BITES: section 3 goes red without the end-anchor rule.\n")
        return 0
    print("  The harness is VACUOUS: loosening the rule changed nothing.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify typed wake and sleep")
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
