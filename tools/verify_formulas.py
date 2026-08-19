#!/usr/bin/env python3
"""
Module:  verify_formulas.py
Purpose: Prove the Tier 0 formula table is correct, reachable, and speakable.
Author:  LB
Date:    2026-08-13

    venv/bin/python tools/verify_formulas.py

Sixth harness, same contract as the other five: exit 0 = all passed, and every claim is
measured rather than asserted. No microphone, no model, no network — the whole tier is a
pure function of a string, so all of it is testable as ordinary logic.

## What this actually checks, and why each one earns its place

**The arithmetic in the worked examples.** This is the point of the file. `formulas.py` exists
because D30 caught every local model stating electronics relationships confidently and wrongly
— so a table that repeated the same mistake in a hand-typed example would be worse than no
table, because LB would trust it. Every `Worked` entry is recomputed here and the result must
appear in the sentence he hears. A slip fails the harness instead of teaching him 14 pins.

**That the router actually reaches them.** "What's the time constant?" contains the whole word
"time", and `router.INTENTS` has a `time` intent. Ordering is the difference between a formula
tier and a clock that interrupts. Asserted here rather than trusted to the comment.

**That the formula tier does not swallow ordinary questions.** The mirror of the above, and the
more dangerous direction: an over-eager trigger that eats "what time is it" would break Tier 0
commands that already work. Every existing router fixture is re-run through the new ordering.

**That every string is speakable.** `tau` not the Greek letter, "pi" not the symbol. Piper
reads these aloud; a symbol is silence or a mangled word, and neither is visible in code review.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from orchestrator import formulas                                    # noqa: E402
from orchestrator.formulas import FORMULAS, look_up, unspeakable     # noqa: E402
from orchestrator.instant import FALLBACK, Router, normalise          # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


FIXED_CLOCK = datetime(2026, 8, 13, 9, 47)
router = Router(now=lambda: FIXED_CLOCK)


def route(question: str):
    return router.route(question)


# ============================================================ 1. the table is well-formed

section("table")

check(len(FORMULAS) >= 15, "the table is non-empty and substantial",
      f"{len(FORMULAS)} formulas")
# An empty table would make every check below pass vacuously — the exact failure this repo
# has now shipped four times. Assert the input before trusting any result derived from it.
check(bool(FORMULAS), "FORMULAS is not empty (guards every check below)")

seen_keys = [f.key for f in FORMULAS]
check(len(seen_keys) == len(set(seen_keys)), "every key is unique",
      f"{len(seen_keys)} keys, {len(set(seen_keys))} distinct")

for f in FORMULAS:
    check(bool(f.spoken.strip()), f"{f.key}: has something to say")
    check(bool(f.triggers), f"{f.key}: has at least one trigger group")
    check(all(g for g in f.triggers), f"{f.key}: no empty trigger group")
    check(all(p == p.lower().strip() for g in f.triggers for p in g),
          f"{f.key}: triggers are normalised (lowercase, trimmed)")


# ============================================================ 2. THE ARITHMETIC

section("arithmetic")

# The reason this file exists. Each worked example is recomputed and the answer must appear
# in the sentence LB hears.
worked_count = 0
for f in FORMULAS:
    for w in f.worked:
        worked_count += 1
        # The number AND its unit, together and verbatim. Matching a bare number is not
        # enough on two counts: it passed a sentence saying "5 kilohms" against a value of
        # 5000 ohms (right arithmetic, wrong unit, meaningless check), and a single-digit
        # value can match some unrelated digit elsewhere in the sentence by luck.
        candidates = {f"{int(round(w.value))} {w.unit}".strip(),
                      f"{w.value:.1f} {w.unit}".strip(),
                      f"{w.value:g} {w.unit}".strip()}
        hit = any(c in f.spoken for c in candidates)
        check(hit, f"{f.key}: spoken value matches the arithmetic ({w.describe})",
              f"computed {w.value:.4g} -> expected one of {sorted(candidates)} "
              f"in {f.spoken[:64]!r}")
        check(bool(w.unit), f"{f.key}: worked example names its unit ({w.describe})")

check(worked_count >= 5, "enough formulas carry machine-checked arithmetic",
      f"{worked_count} worked examples")

# Spot-check the three the models actually got wrong in D30, by value, independently of the
# table's own worked examples. If formulas.py and this file were both edited wrongly in the
# same way, these still catch it.
check("10 milliseconds" in look_up("time constant").spoken,
      "tau = RC: 10k x 1uF is 10 milliseconds (D30: model said divide by frequency)")
check("150 ohms" in look_up("led resistor").spoken,
      "LED resistor: (5-2)/0.02 = 150 ohms (D30: model garbled Ohm's law)")
check("16 hertz" in look_up("cutoff frequency").spoken,
      "cutoff: 1/(2 pi x 10k x 1uF) is about 16 Hz (D30: model said ratio of R to C)")


# ============================================================ 3. every formula is reachable

section("reachability")

for f in FORMULAS:
    for group in f.triggers:
        # The question a person would actually ask, built from the trigger's own words.
        question = normalise("what is the " + " ".join(group))
        hit = look_up(question)
        check(hit is not None and hit.key == f.key,
              f"{f.key}: reachable via {' + '.join(group)!r}",
              f"got {hit.key if hit else None!r} from {question!r}")


# ============================================================ 4. real phrasings route right

section("phrasing")

# Fixtures assert the KEY, not the wording — the same convention verify_stt.py uses for
# intent. Wording can be improved without breaking the harness; routing cannot.
PHRASINGS = [
    ("what is the time constant of an rc circuit",        "rc_time_constant"),
    ("how do i work out the time constant",               "rc_time_constant"),
    ("whats tau for an rc circuit",                       "rc_time_constant"),
    ("what resistor do i need for an led",                "led_resistor"),
    ("what size current limiting resistor",               "led_resistor"),
    ("whats the cutoff frequency of a low pass filter",   "rc_cutoff"),
    ("where is the corner frequency",                     "rc_cutoff"),
    ("remind me of ohms law",                             "ohms_law"),
    ("how do i calculate power",                          "power"),
    ("how does a voltage divider work",                   "voltage_divider"),
    ("two resistors in parallel",                         "resistors_series_parallel"),
    ("capacitors in series",                              "capacitors_series_parallel"),
    ("what is capacitive reactance",                      "capacitive_reactance"),
    ("whats the resonant frequency",                      "resonant_frequency"),
    ("how much energy is stored in a capacitor",          "capacitor_energy"),
    ("what is rms",                                       "rms"),
    ("how do decibels work",                              "decibels"),
    ("whats the gain of an op amp",                       "opamp_gain"),
    ("what sample rate do i need",                        "nyquist"),
    ("explain kirchhoff",                                 "kirchhoff"),
    ("what is the speed of light",                        "speed_of_light"),
    ("how fast is sound",                                 "speed_of_sound"),
]

for question, want in PHRASINGS:
    hit = look_up(normalise(question))
    check(hit is not None and hit.key == want, f"{question!r} -> {want}",
          f"got {hit.key if hit else None!r}")


# ============================================================ 5. THE COLLISION

section("collision")

# "time constant" contains "time". Before this tier existed, router.INTENTS matched `time`
# on the whole word and answered with the clock. This is the check that keeps the ordering
# honest — move the formula intent below `time` in INTENTS and this fails immediately.
for question in ("what is the time constant of an rc circuit",
                 "how do i work out the time constant",
                 "whats the time constant"):
    reply = route(question)
    check(reply.intent == "formula", f"{question!r} does NOT get answered with the clock",
          f"intent={reply.intent} text={reply.text[:48]!r}")
    check("9 47" not in reply.text, f"{question!r} contains no clock reading",
          reply.text[:48])


# ============================================================ 6. it swallows nothing

section("no over-reach")

# The mirror, and the more dangerous direction. Tier 0 commands already work; a greedy formula
# trigger would silently break them. Every one of these must reach its ORIGINAL intent.
UNTOUCHED = [
    ("what time is it",              "time"),
    ("whats the time",               "time"),
    ("what day is it today",         "date"),
    ("what is the date",             "date"),
    ("set a timer for five minutes", "timer"),
    ("who are you",                  "identity"),
    ("hello",                        "hello"),
    ("thanks",                       "thanks"),
    ("stop",                         "stop"),
]

for question, want in UNTOUCHED:
    reply = route(question)
    check(reply.intent == want, f"{question!r} still routes to {want}",
          f"got {reply.intent!r} -> {reply.text[:44]!r}")

# And things with no answer here must still fall through to the model, not be absorbed.
for question in ("why is the sky blue", "how far away is the moon", "tell me a joke",
                 "what should i have for lunch"):
    reply = route(question)
    check(reply.intent != "formula", f"{question!r} is NOT claimed by the formula tier",
          f"intent={reply.intent}")
    check(not reply.handled, f"{question!r} still escalates (handled=False)",
          f"handled={reply.handled}")


# ============================================ 6b. the right ENTRY, not just the right intent

section("entry collision")

# Sections 5 and 6 both ask "does the right INTENT win?" — formula versus the clock, formula
# versus chit-chat. **Neither can see a question that reaches the right intent and the wrong
# ENTRY**, and that blind spot shipped four wrong answers, all found on 2026-08-15 by a
# harness written for the EE encyclopedia rather than by this file:
#
#     what is a current divider              -> the VOLTAGE divider ratio (inverted)
#     what is the time constant of an rl ...  -> "resistance times capacitance"  (tau = L/R)
#     what is the nyquist stability criterion -> the sampling theorem  (control, not DSP)
#     what is johnson nyquist noise           -> the sampling theorem
#
# Root cause each time: a trigger broad enough to claim a neighbouring question. D38 says Tier
# 0 matches phrases and never bare keywords, and this table's own docstring claims its triggers
# are multi-word — but ("nyquist",), ("cutoff",) and ("divider",) are bare words, and "time
# constant" says nothing about which reactance it means.
#
# So this section pins the entry, not the intent. Each pair is a question and the key that must
# answer it; `None` means no formula may claim it at all, because answering it from this table
# would be a wrong answer rather than a missing one.
ENTRY_EXPECTED: list[tuple[str, str | None]] = [
    # the sampling theorem owns sampling, and nothing else Nyquist put his name to
    ("what is the nyquist rate",                    "nyquist"),
    ("what is the nyquist frequency",               "nyquist"),
    ("what is aliasing",                            "nyquist"),
    ("what is the nyquist stability criterion",     None),
    ("what is a nyquist plot",                      None),
    ("what is johnson nyquist noise",               "thermal_noise"),
    # a divider is not a divider. Answered by its OWN entry rather than suppressed: the
    # current-divider ratio is inverted relative to the voltage one, so LB was being handed a
    # wrong number he would have acted on, and a missing answer is not the fix for that.
    ("what is the voltage divider formula",         "voltage_divider"),
    ("what is a current divider",                   "current_divider"),
    # tau is R times C only when there is a C
    ("what is the time constant of an rc circuit",  "rc_time_constant"),
    ("what is the time constant of an rl circuit",  "rl_time_constant"),
    ("whats the time constant",                     "rc_time_constant"),
    # the RC corner is not every corner
    ("what is the cutoff frequency",                "rc_cutoff"),
    ("what is the cutoff frequency of a waveguide", "waveguide_cutoff"),
]

for question, want in ENTRY_EXPECTED:
    got = look_up(normalise(question))
    key = got.key if got else None
    if want is None:
        check(key is None,
              f"{question!r} is NOT answered by the formula table",
              f"answered by {key!r} — a wrong answer, not a missing one: {got.spoken[:60]!r}"
              if got else "")
    else:
        check(key == want, f"{question!r} is answered by {want!r}", f"got {key!r}")


# ============================================================ 7. speakable

section("speakable")

for f in FORMULAS:
    bad = unspeakable(f.spoken)
    check(not bad, f"{f.key}: no characters Piper cannot say", f"found {bad!r}")
    check(f.spoken.isascii(), f"{f.key}: pure ASCII",
          "".join(c for c in f.spoken if not c.isascii())[:20])
    # Operators read as silence or as the wrong word entirely.
    for symbol in ("*", "/", "^", "=", "<", ">", "_"):
        check(symbol not in f.spoken, f"{f.key}: no bare {symbol!r} operator",
              f.spoken[:56])
    words = len(f.spoken.split())
    check(words <= 45, f"{f.key}: short enough to speak ({words} words)", f.spoken[:56])
    check(f.spoken.strip().endswith((".", "!", "?")),
          f"{f.key}: ends on a sentence boundary (Piper streams per sentence)")


# ============================================================ 8. the fallback still works

section("fallback")

empty = route("")
check(empty.intent == "empty", "empty transcript is still handled")
unknown = route("xyzzy plugh")
check(unknown.text == FALLBACK and not unknown.handled,
      "an unknown question still reaches the fallback", f"{unknown.text[:40]!r}")
check(look_up("") is None, "look_up('') is None rather than raising")
check(look_up("completely unrelated words here") is None, "no spurious match on prose")


# ============================================================ report

passed = sum(1 for ok, *_ in RESULTS if ok)
failed = len(RESULTS) - passed

width = 76
last_section = None
for ok, sec, msg, detail in RESULTS:
    if sec != last_section:
        print(f"\n-- {sec} " + "-" * (width - len(sec) - 4))
        last_section = sec
    if not ok:
        print(f"  FAIL  {msg}")
        if detail:
            print(f"        {detail}")

print("\n" + "=" * width)
print(f"{passed}/{len(RESULTS)} checks passed"
      + (f"  ({failed} FAILED)" if failed else "  — all green"))
print(f"{len(FORMULAS)} formulas, {worked_count} with machine-checked arithmetic")
raise SystemExit(1 if failed else 0)
