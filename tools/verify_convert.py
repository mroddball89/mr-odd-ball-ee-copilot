#!/usr/bin/env python3
"""
Module:  verify_convert.py
Purpose: Prove unit conversion is arithmetically right, dimensionally honest, and that it
         refuses everything that is not a conversion.
Author:  LB
Date:    2026-08-14

    venv/bin/python tools/verify_convert.py

Twelfth harness, same contract as the other eleven: exit 0 = all passed, no microphone, no
model, no network, and every claim measured rather than asserted.

## What earns its place here

**The raw-transcript rule, which is the whole D42 hazard again.** `router.normalise()` deletes
the decimal point, so a converter reading normalised text turns "3.3 volts in millivolts" into
33 volts and answers 33000. That is out by a factor of ten, spoken confidently, and invisible
in review because the arithmetic is flawless. Pinned directly: the expected value is 3300 and
the harness asserts the wrong answer is *not* produced by name.

**The dimensional check.** "5 volts in meters" names two real units and has no answer. The
failure mode if this guard breaks is not a crash — it is a number. Every cross-category pair
is swept, not spot-checked.

**Round-tripping every unit.** 1 of any unit converted to its category's base and back must
return 1. This catches a transposed factor in a way a table of expected values cannot, because
it needs no second copy of the number to compare against.

**Temperature, separately and by hand.** It is the only affine category — the one place a
scale-only implementation gives plausible wrong answers rather than obvious ones. 0 C is 32 F,
100 C is 212 F, minus 40 is minus 40, and 0 K is minus 273.15 C.

**That it refuses.** `convert()` returning None is the common case and the safe one: the
router falls through to the tiers that already work. A conversion tier that claims "set a
timer for five minutes" would break a Tier 0 command that already works, and a claimed
question never escalates.
"""

from __future__ import annotations

import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from orchestrator import convert as C                                       # noqa: E402
from orchestrator.convert import UNITS, Unit, convert                       # noqa: E402
from orchestrator.formulas import unspeakable                               # noqa: E402
from orchestrator.instant import FALLBACK, Router, normalise                 # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


FIXED_CLOCK = datetime(2026, 8, 14, 9, 47)
router = Router(now=lambda: FIXED_CLOCK)


def route(question: str):
    return router.route(question)


# ============================================================ 1. the unit table

section("unit table")

check(len(UNITS) >= 50, "the unit table is substantial", f"{len(UNITS)} units")
check(bool(UNITS), "UNITS is not empty (guards every check below)")

keys = [u.key for u in UNITS]
check(len(keys) == len(set(keys)), "every unit key is unique",
      f"{len(keys)} keys, {len(set(keys))} distinct")

for u in UNITS:
    check(u.factor > 0, f"{u.key}: factor is positive", f"{u.factor!r}")
    check(bool(u.spoken) and bool(u.plural), f"{u.key}: has singular and plural forms")
    check(bool(u.names), f"{u.key}: has at least one spelling")
    check(all(n == n.lower().strip() for n in u.names),
          f"{u.key}: spellings are normalised")
    # The offset is the affine part, and temperature is the ONLY category entitled to one.
    # A stray offset anywhere else silently shifts every conversion in that category.
    if u.offset:
        check(u.category == "temperature",
              f"{u.key}: only temperature carries an offset", f"category={u.category}")

categories = {u.category for u in UNITS}
check(len(categories) >= 15, "many dimensions covered", f"{len(categories)} categories")

# Each category should carry the unit its factors are measured against.
#
# **Mass is the documented exception, and it is a real one.** The SI base is the *kilogram*,
# which is itself a prefixed gram — so storing both would put one factor in the table twice,
# free to drift. `convert.py` stores the gram at 1e-3 and lets the prefix machinery build
# kilograms, milligrams and micrograms from it. Stated here rather than silently tolerated,
# because a missing base in any OTHER category would be a genuine bug.
BASE_IS_PREFIXED = {"mass"}
for category in sorted(categories):
    bases = [u for u in UNITS if u.category == category and u.factor == 1.0 and not u.offset]
    if category in BASE_IS_PREFIXED:
        check(not bases, f"{category}: base unit is a prefixed form, by design",
              f"unexpected factor-1 entries: {[u.key for u in bases]}")
    else:
        check(len(bases) >= 1, f"{category}: has a base unit (factor 1)",
              f"{[u.key for u in bases]}")

# And the exception still has to be arithmetically right, which the round trip proves for
# every unit including the gram — so nothing is lost by not having a factor-1 row.
gram = next(u for u in UNITS if u.key == "gram")
check(math.isclose(C._to_si(1.0, gram, 1e3), 1.0),
      "1 kilogram is 1 SI mass unit (the prefixed base resolves correctly)",
      f"got {C._to_si(1.0, gram, 1e3)!r}")


# ============================================================ 2. round-trip every unit

section("round trip")

# 1 of anything, into SI and back, must be 1. This needs no second copy of the factor to
# compare against, which is exactly why it catches a transposed one.
for u in UNITS:
    si = C._to_si(1.0, u, 1.0)
    back = C._from_si(si, u, 1.0)
    check(math.isclose(back, 1.0, rel_tol=1e-9, abs_tol=1e-12),
          f"{u.key}: 1 -> SI -> 1 round trips", f"got {back!r} (si={si!r})")

# And with a prefix on both ends, which is where the multiplier could be applied twice.
for u in [x for x in UNITS if x.prefixable]:
    si = C._to_si(1.0, u, 1e-3)
    back = C._from_si(si, u, 1e-3)
    check(math.isclose(back, 1.0, rel_tol=1e-9),
          f"{u.key}: 1 milli -> SI -> 1 milli round trips", f"got {back!r}")


# ============================================================ 3. KNOWN CONVERSIONS

section("arithmetic")

# Expected values typed from reference tables, independently of the factors in convert.py.
# (question, expected value in the destination unit, tolerance)
KNOWN = [
    # THE D42 CASE. Normalised text would make this 33 volts and answer 33000.
    ("how many millivolts in 3.3 volts",                3300.0,        1e-9),
    ("how many ohms in 4.7 kilohms",                    4700.0,        1e-9),
    ("convert 2.2 megohms to ohms",                     2_200_000.0,   1e-9),
    ("convert 500 milliamps to amps",                   0.5,           1e-9),
    ("how many microfarads in 1 farad",                 1_000_000.0,   1e-9),
    ("convert 100 nanoseconds to microseconds",         0.1,           1e-9),
    ("how many meters in a mile",                       1609.344,      1e-9),
    ("how many centimeters in 6 inches",                15.24,         1e-9),
    ("how many grams in a pound",                       453.59237,     1e-9),
    ("how many feet in 100 meters",                     328.0839895,   1e-6),
    ("convert 60 miles per hour to meters per second",  26.8224,       1e-9),
    ("how many radians in 180 degrees",                 math.pi,       1e-9),
    ("how many degrees in 1 radian",                    57.29577951,   1e-6),
    ("how many seconds in a minute",                    60.0,          1e-12),
    ("how many minutes in 2 hours",                     120.0,         1e-12),
    ("how many joules in 1 electron volt",              1.602176634e-19, 1e-9),
    ("convert 1 atmosphere to psi",                     14.69594878,   1e-6),
    ("how many liters in 1 gallon",                     3.785411784,   1e-9),
    ("how many bytes in 4 kilobytes",                   4000.0,        1e-12),
    ("convert 1 horsepower to watts",                   745.6998716,   1e-6),
    ("how many kilocalories in 500 joules",             0.11950286,    1e-6),
]
for question, expected, tol in KNOWN:
    got = convert(question)
    check(got is not None, f"{question!r} is recognised as a conversion")
    if got:
        check(math.isclose(got.value, expected, rel_tol=tol),
              f"{question!r} = {expected!r}", f"got {got.value!r}")
        # The number LB HEARS must be the number computed, not a second rendering of it.
        spoken_number = C._speak_number(got.value)
        check(spoken_number in got.spoken,
              f"{question!r}: the spoken sentence carries the computed value",
              f"{spoken_number!r} not in {got.spoken!r}")

# THE D42 REGRESSION, stated as a refusal of the wrong answer rather than only as the right
# one. If normalise() ever creeps into this module, 3.3 becomes 33 and this goes red by name.
three_three = convert("how many millivolts in 3.3 volts")
check(three_three is not None and not math.isclose(three_three.value, 33_000.0),
      "3.3 volts is NOT read as 33 volts (the decimal point survives)",
      f"got {three_three.value if three_three else None!r}")
check(three_three is not None and math.isclose(three_three.amount, 3.3),
      "the amount kept its decimal point", f"{three_three.amount if three_three else None!r}")

# Negative numbers survive too — normalise() deletes the sign as well as the point.
minus_forty = convert("convert -40 celsius to fahrenheit")
check(minus_forty is not None and math.isclose(minus_forty.amount, -40.0),
      "a negative amount keeps its sign",
      f"{minus_forty.amount if minus_forty else None!r}")


# ============================================================ 4. TEMPERATURE IS AFFINE

section("temperature")

# The only category with an origin as well as a scale, and the only one where a scale-only
# implementation gives plausible answers instead of obviously broken ones.
TEMPS = [
    ("convert 0 celsius to fahrenheit",     32.0),
    ("convert 100 celsius to fahrenheit",   212.0),
    ("convert -40 celsius to fahrenheit",  -40.0),
    ("convert 98.6 fahrenheit to celsius",  37.0),
    ("convert 0 kelvin to celsius",        -273.15),
    ("convert 25 celsius to kelvin",        298.15),
    ("convert 300 kelvin to celsius",       26.85),
    ("convert 212 fahrenheit to kelvin",    373.15),
]
for question, expected in TEMPS:
    got = convert(question)
    check(got is not None, f"{question!r} is recognised")
    if got:
        check(math.isclose(got.value, expected, abs_tol=1e-6),
              f"{question!r} = {expected}", f"got {got.value!r}")

# A scale-only converter would answer 0 here, which is the failure this section exists for.
zero_c = convert("convert 0 celsius to fahrenheit")
check(zero_c is not None and not math.isclose(zero_c.value, 0.0),
      "0 celsius is NOT 0 fahrenheit (the offset is applied)",
      f"got {zero_c.value if zero_c else None!r}")


# ============================================================ 5. THE DIMENSIONAL CHECK

section("dimensions")

# Every cross-category pair, not a spot check. The failure mode if this guard breaks is a
# NUMBER, not a crash — which is the single most dangerous shape of bug in this repo.
by_category: dict[str, Unit] = {}
for u in UNITS:
    by_category.setdefault(u.category, u)

pairs = 0
for src_cat, src in sorted(by_category.items()):
    for dst_cat, dst in sorted(by_category.items()):
        if src_cat == dst_cat:
            continue
        pairs += 1
        question = f"convert 5 {src.plural} to {dst.plural}"
        got = convert(question)
        check(got is None, f"refuses {src_cat} -> {dst_cat}",
              f"{question!r} answered {got.spoken if got else None!r}")

check(pairs > 100, "the cross-dimension sweep is broad", f"{pairs} pairs tried")

# Named cases, so a failure reads as itself in the log.
for question in ("what is 5 volts in meters", "convert 3 kilograms to seconds",
                 "how many joules in 4 amps", "convert 10 hertz to liters"):
    check(convert(question) is None, f"{question!r} has no answer and is refused",
          f"got {convert(question)!r}")

# Converting a unit to itself is not a question either.
for question in ("how many volts in 5 volts", "convert 3 meters to meters"):
    check(convert(question) is None, f"{question!r} is not a conversion",
          f"got {convert(question)!r}")


# ============================================================ 6. IT REFUSES

section("refusals")

# Every one of these belongs to a tier that already works. Claiming one would break a Tier 0
# command, and a claimed question never escalates.
NOT_CONVERSIONS = [
    "what time is it", "whats the time", "what day is it today", "what is the date",
    "set a timer for five minutes", "remind me in ten minutes", "who are you",
    "hello", "thanks", "stop", "tell me a joke", "what do you think of capacitors",
    "whats five plus five", "what is 15 percent of 80", "whats 3.5 plus 2",
    "what is 2 to the power of 8", "what is the square root of 144",
    "what is the time constant of an rc circuit", "whats the cutoff frequency",
    "what is an eigenvalue", "define entropy", "how far away is the moon",
    "how many people live in china", "how many days until christmas",
    "whats the weather going to be", "i ran five miles today",
    "the resistor is 10 kilohms", "it took three hours",
]
for question in NOT_CONVERSIONS:
    got = convert(question)
    check(got is None, f"{question!r} is not claimed by the conversion tier",
          f"got {got.spoken if got else None!r}")

check(convert("") is None, "an empty string is refused rather than raising")
check(convert("   ") is None, "whitespace is refused rather than raising")


# ============================================================ 7. router integration

section("router integration")

# Every fixture that worked before must still work. `convert` sits above `calc` and `timer`.
UNTOUCHED = [
    ("what time is it",              "time"),
    ("whats the time",               "time"),
    ("what day is it today",         "date"),
    ("what is the date",             "date"),
    ("set a timer for five minutes", "timer"),
    ("remind me in ten minutes",     "timer"),
    ("who are you",                  "identity"),
    ("hello",                        "hello"),
    ("thanks",                       "thanks"),
    ("stop",                         "stop"),
    ("whats five plus five",         "calc"),
    ("whats 3.5 plus 2",             "calc"),
    ("what is 15 percent of 80",     "calc"),
    ("what is the time constant of an rc circuit", "formula"),
    ("what is an eigenvalue",        "define"),
    ("whats the charge on an electron", "constant"),
]
for question, want in UNTOUCHED:
    reply = route(question)
    check(reply.intent == want, f"{question!r} still routes to {want}",
          f"got {reply.intent!r} -> {reply.text[:44]!r}")

REACHES_CONVERT = [
    "how many millivolts in 3.3 volts",
    "convert 25 degrees celsius to fahrenheit",
    "how many meters in a mile",
    "how many radians in 180 degrees",
    "convert 500 milliamps to amps",
    "how many ohms in 4.7 kilohms",
]
for question in REACHES_CONVERT:
    reply = route(question)
    check(reply.intent == "convert" and reply.text != FALLBACK,
          f"{question!r} reaches the conversion tier",
          f"got [{reply.intent}] {reply.text[:44]!r}")

# Things with no answer anywhere must still fall through to the model rather than be absorbed.
for question in ("why is the sky blue", "how far away is the moon", "tell me a joke"):
    reply = route(question)
    check(not reply.handled, f"{question!r} still escalates (handled=False)",
          f"[{reply.intent}] handled={reply.handled}")


# ============================================================ 8. alias resolution

section("aliases")

# Longest-first, and mutation testing corrected what that is actually protecting.
#
# The glued prefixes — "millimeter", "kilohm", "microfarad" — are safe WITHOUT the sort,
# because the matcher's word boundaries already refuse to stop mid-token: "meter" cannot
# match inside "millimeter" when the next character is a letter. Reversing the sort left all
# eight of those green.
#
# What the sort genuinely protects is the MULTI-WORD aliases, where the boundaries do not
# help: "miles per hour" contains "miles" and "hour" as complete tokens, so shortest-first
# resolves it to plain miles and answers a speed question in units of length. Those are the
# cases below the line, and reversing the sort turns them red.
LONGEST_WINS = [
    # boundary-protected: correct with or without the sort, kept as regression cover
    ("millimeter", "meter", 1e-3),
    ("kilohm", "ohm", 1e3),
    ("megohm", "ohm", 1e6),
    ("microfarad", "farad", 1e-6),
    ("nanosecond", "second", 1e-9),
    ("milliamp", "ampere", 1e-3),
    ("kilogram", "gram", 1e3),
    ("kilometer", "meter", 1e3),
    # SORT-PROTECTED: multi-word aliases whose parts are units in their own right
    ("miles per hour", "mileperhour", 1.0),
    ("meters per second", "meterpersecond", 1.0),
    ("kilometers per hour", "kilometerperhour", 1.0),
    ("cubic meter", "cubicmeter", 1.0),
    ("square foot", "squarefoot", 1.0),
    ("electron volt", "electronvolt", 1.0),
    ("fluid ounce", "fluidounce", 1.0),
    ("kilo ohm", "ohm", 1e3),
    ("atomic mass unit", "amu", 1.0),
]
for alias, want_key, want_multiplier in LONGEST_WINS:
    found = C._find_unit(alias)
    check(found is not None, f"{alias!r} matches a unit")
    if found:
        unit, multiplier, _, _ = found
        check(unit.key == want_key and math.isclose(multiplier, want_multiplier),
              f"{alias!r} is {want_multiplier:g} x {want_key}",
              f"got {multiplier:g} x {unit.key}")

# THE ONE REAL COLLISION, resolved by table order and pinned here so it stays a decision.
pa = C._find_unit("5 pa")
check(pa is not None and pa[0].key == "pascal",
      "'pa' resolves to pascals, not picoamperes (table order decides, deliberately)",
      f"got {pa[0].key if pa else None!r}")

# Single-letter and word-shaped aliases need a number in front, or ordinary English becomes
# a unit. "a mile" must not parse the article as amperes.
for text in ("a mile", "give it to us", "in a moment", "m"):
    found = C._find_unit(text)
    check(found is None or found[0].key not in ("ampere", "second", "meter")
          or text.strip()[0].isdigit(),
          f"{text!r} does not parse an English word as a unit",
          f"got {found[0].key if found else None!r}")

check(C._find_unit("5 a") is not None and C._find_unit("5 a")[0].key == "ampere",
      "'5 a' DOES parse as amperes — the digit is what makes it a unit")


# ============================================================ 9. speakable

section("speakable")

SPOKEN_SAMPLES = [q for q, _e, _t in KNOWN] + [q for q, _e in TEMPS]
for question in SPOKEN_SAMPLES:
    got = convert(question)
    if not got:
        continue
    bad = unspeakable(got.spoken)
    check(not bad, f"{question!r}: no characters Piper cannot say", f"found {bad!r}")
    check(got.spoken.isascii(), f"{question!r}: pure ASCII", got.spoken[:48])
    for symbol in ("*", "/", "^", "=", "<", ">", "_", "e-", "e+"):
        check(symbol not in got.spoken, f"{question!r}: no bare {symbol!r}", got.spoken[:56])
    check(got.spoken.strip().endswith("."),
          f"{question!r}: ends on a sentence boundary", got.spoken[:56])
    check(len(got.spoken.split()) <= 20, f"{question!r}: short enough to speak",
          f"{len(got.spoken.split())} words")

# Singular and plural are chosen by the value, not fixed.
one = convert("how many meters in 1 yard")
check(one is not None and "1 yard is" in one.spoken,
      "a value of 1 uses the singular", f"{one.spoken if one else None!r}")


# ============================================================ 10. it never raises

section("never raises")

random.seed(20260814)
alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 .,-%()'"
crashes = []
for _ in range(700):
    junk = "".join(random.choice(alphabet) for _ in range(random.randint(0, 60)))
    try:
        convert(junk)
    except Exception as exc:                                        # noqa: BLE001
        crashes.append((junk, f"{type(exc).__name__}: {exc}"))
check(not crashes, "700 fuzzed strings, none raised",
      "; ".join(f"{j!r} -> {e}" for j, e in crashes[:3]))

for junk in ("", " ", "\n", "?" * 500, "convert " + "x" * 400 + " to y",
             "convert 1e400 meters to feet", "convert 0 meters to feet",
             "how many meters in", "convert to", "in in in in"):
    try:
        convert(junk)
        ok = True
        detail = ""
    except Exception as exc:                                        # noqa: BLE001
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    check(ok, f"convert survives {junk[:28]!r}", detail)


# ============================================================ 11. it is cheap

section("latency")

# Tier 0 sits on the turn's critical path and `convert` is called on EVERY turn as a matcher,
# whether or not the question is a conversion. The refusal path is the one that has to be
# free, so it is the one measured.
convert.cache_clear()
questions = ["what time is it", "tell me a joke", "who are you", "whats five plus five"]
start = time.perf_counter()
for i in range(400):
    convert(f"{questions[i % len(questions)]} {i}")     # unique, so the cache never helps
elapsed_ms = (time.perf_counter() - start) * 1000 / 400
check(elapsed_ms < 5.0, "a refusal is cheap enough to run on every turn",
      f"{elapsed_ms:.3f} ms per call, cold cache, over 400 calls")

convert.cache_clear()
start = time.perf_counter()
for i in range(400):
    convert(f"how many millivolts in {i}.5 volts")
convert_ms = (time.perf_counter() - start) * 1000 / 400
check(convert_ms < 8.0, "a real conversion is cheap too",
      f"{convert_ms:.3f} ms per call, cold cache, over 400 calls")


# ============================================================ 12. purity

section("purity")

# A pure function of a string: same input, same output, no clock, no network, no state.
first = convert("how many millivolts in 3.3 volts")
convert.cache_clear()
second = convert("how many millivolts in 3.3 volts")
check(first == second, "the same question gives the same answer with a cold cache",
      f"{first!r} vs {second!r}")
check(convert(normalise("HOW MANY MILLIVOLTS IN 3 VOLTS")) is not None,
      "case does not matter")


# ============================================================ report

passed = sum(1 for ok, *_ in RESULTS if ok)
failed = len(RESULTS) - passed

width = 76
last_section = None
for ok, sec, msg, detail in RESULTS:
    if sec != last_section:
        print(f"\n-- {sec} " + "-" * max(3, width - len(sec) - 4))
        last_section = sec
    if not ok:
        print(f"  FAIL  {msg}")
        if detail:
            print(f"        {detail}")

print("\n" + "=" * width)
print(f"{passed}/{len(RESULTS)} checks passed"
      + (f"  ({failed} FAILED)" if failed else "  — all green"))
print(f"{len(UNITS)} units across {len(categories)} dimensions, "
      f"{len(C._ALIAS_MAP)} spellings; {len(KNOWN)} conversions recomputed, "
      f"{pairs} cross-dimension refusals, 700 fuzzed strings")
raise SystemExit(1 if failed else 0)
