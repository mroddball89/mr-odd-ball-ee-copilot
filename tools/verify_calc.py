#!/usr/bin/env python3
"""
Module:  verify_calc.py
Purpose: Prove the Tier 0 calculator is correct, safe, bounded, and does not over-reach.
Author:  LB
Date:    2026-08-13

    venv/bin/python tools/verify_calc.py

Eighth harness, same contract as the other seven: exit 0 = all passed, every claim measured
rather than asserted, and no microphone, no model and no network anywhere in it. `calc` is a
pure function of a string, so all of it is testable as ordinary logic.

## What earns its place here

**The arithmetic, recomputed.** Every expected value below is computed by Python in this file,
never typed as a literal. A hand-typed expectation is just a second chance to make the same
mistake, and `formulas.py` exists because D30 caught models being fluently and wrongly
confident — a calculator that shipped a wrong answer would be the same failure with a shorter
excuse.

**The reversed operands.** "5 less than 20" is 15, not -15. LB's real transcript on 2026-08-13
was "It's been 1000 less than 1000", which is symmetric and would have hidden a sign error
completely. The asymmetric cases are checked in both directions, values *and* signs.

**The decimal point.** `router.normalise()` turns "3.5 plus 2" into "35 plus 2". This asserts
the answer is 5.5 and explicitly that it is **not 37** — which is what a calculator reading
normalised text would say. That check is the reason `calc` takes the raw string, so it is
pinned rather than left to a comment.

**That it refuses.** The more dangerous direction, exactly as in `verify_formulas.py`: an
over-eager calculator that claims "what time is it" would break Tier 0 commands that already
work, and a claimed question never escalates. Every existing router fixture is re-run.

**That it cannot be made to execute anything.** The expression goes through an `ast` whitelist,
so the harness tries to get past it.

**That it terminates.** `9 ** 9 ** 9` does not raise — it just never comes back. The guard is
checked with a clock on it.
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

from orchestrator import calc                                          # noqa: E402
from orchestrator.calc import DIVIDE_BY_ZERO, Result, evaluate         # noqa: E402
from orchestrator.formulas import unspeakable                          # noqa: E402
from orchestrator.instant import Router, normalise                      # noqa: E402

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


def value_of(question: str) -> float | None:
    hit = evaluate(question)
    return None if hit is None else hit.value


# ==================================================== 1. the arithmetic, recomputed here

section("arithmetic")

# (spoken question, the value THIS FILE computes). The right-hand side is an expression, not a
# literal, so the harness and the module have to agree on the answer independently.
SUMS: tuple[tuple[str, float], ...] = (
    ("whats five plus five", 5 + 5),
    ("what is 5 plus 5", 5 + 5),
    ("what is 12 minus 4", 12 - 4),
    ("whats 6 times 7", 6 * 7),
    ("what is 6 multiplied by 7", 6 * 7),
    ("whats 100 divided by 4", 100 / 4),
    ("what is 100 over 4", 100 / 4),
    ("whats twenty three times four", 23 * 4),
    ("what is three hundred and five minus 5", 305 - 5),
    ("a thousand times a thousand", 1000 * 1000),
    ("what is 2 to the power of 10", 2 ** 10),
    ("whats 12 squared", 12 ** 2),
    ("what is 5 cubed", 5 ** 3),
    ("the square root of 144", 144 ** 0.5),
    ("whats the square root of 2", 2 ** 0.5),
    ("what is 17 mod 5", 17 % 5),
    ("whats 15 percent of 80", 15 / 100 * 80),
    ("what is 20 percent of 50", 20 / 100 * 50),
    ("20 percent off 50", 50 - 50 * 20 / 100),
    ("whats 10 percent", 10 / 100),
    ("what is two thirds of ninety", (2 / 3) * 90),
    ("half of 80", 0.5 * 80),
    ("what is 5 out of 10", 5 / 10),
    ("whats 0.1 plus 0.2", 0.1 + 0.2),
    ("3.5 plus 2", 3.5 + 2),
    ("what is 2.5 times 4", 2.5 * 4),
    ("what is negative 7 plus 2", -7 + 2),
    ("whats 8 times 7 plus 2", 8 * 7 + 2),
    ("what is 100 divided by 3", 100 / 3),
    ("1,000 plus 1,000", 1000 + 1000),
    ("what is 1000000 divided by 4", 1000000 / 4),
    ("whats 99 plus 1", 99 + 1),
    ("what is 7 times 6 minus 2", 7 * 6 - 2),
    ("2 times 10 to the power of 30", 2 * 10 ** 30),
    ("whats 45 divided by 9", 45 / 9),
    ("what is 2 plus 2", 2 + 2),
    ("nine times nine", 9 * 9),
    ("what is eleven plus twelve", 11 + 12),
    ("whats fifty divided by two", 50 / 2),
    ("what is 5 subtracted from 30", 30 - 5),
    # SYMBOLIC forms. Whisper writes "15% of 80" for spoken "fifteen percent of eighty", and
    # the typed chat window (docs/STATE.md, next) will send these directly. The '%' case was
    # a real confidently-wrong bug: the symbol survived into the expression as Python's
    # modulo, so "15% of 80" evaluated to 15 and he said "That's 15" to a question whose
    # answer is 12. Mutation testing found the harness had no check for it at all.
    ("15% of 80", 15 / 100 * 80),
    ("what is 50% of 30", 50 / 100 * 30),
    ("5+5", 5 + 5),
    ("2^8", 2 ** 8),
    ("100/4", 100 / 4),
    ("3.5+2", 3.5 + 2),
)

for question, expected in SUMS:
    got = value_of(question)
    check(got is not None, f"{question!r} is recognised as arithmetic")
    if got is not None:
        check(math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-12),
              f"{question!r} == {expected}", f"got {got}")

check(len(SUMS) >= 35, "enough expressions are covered", f"{len(SUMS)} expressions")

# The number he SAYS has to be the number the arithmetic produced. Same contract as
# formulas.Worked — a correct value rendered into a wrong sentence is still a wrong answer.
section("spoken value matches the arithmetic")

for question, expected in SUMS:
    hit = evaluate(question)
    if hit is None or math.isnan(hit.value):
        continue
    # Reconstruct what the digits in the sentence should be, from the value, and require them
    # to appear verbatim in what he says.
    if expected == int(expected) and abs(expected) < 1e12:
        needle = str(int(expected))
        check(needle in hit.spoken, f"{question!r}: says {needle}", hit.spoken)
    else:
        rounded = float(f"{expected:.4g}")
        if abs(expected) < 1e12:
            needle = (str(int(rounded)) if rounded == int(rounded)
                      else f"{rounded:.10f}".rstrip("0").rstrip("."))
            check(needle in hit.spoken, f"{question!r}: says {needle}", hit.spoken)


# ============================================ 2. reversed operands — the sign-error trap

section("reversed operands")

# THE section of this file. "X less than Y" is Y - X. Every case here is ASYMMETRIC, because a
# symmetric one (LB's real "1000 less than 1000") cannot tell a correct answer from a flipped
# sign. Both the value and the sign are asserted.
REVERSED: tuple[tuple[str, float], ...] = (
    ("what is 5 less than 20", 20 - 5),
    ("whats 3 less than 10", 10 - 3),
    ("what is 5 more than 20", 20 + 5),
    ("whats 100 more than 1", 1 + 100),
    ("what is 7 subtracted from 30", 30 - 7),
    ("whats 2 fewer than 9", 9 - 2),
    ("what is 4 greater than 10", 10 + 4),
)

for question, expected in REVERSED:
    got = value_of(question)
    check(got is not None, f"{question!r} is recognised")
    if got is not None:
        check(math.isclose(got, expected), f"{question!r} == {expected} (operands reversed)",
              f"got {got}")
        check(got > 0, f"{question!r} has the RIGHT SIGN — not the operands the other way up",
              f"got {got}, which is what a naive left-to-right parse produces")

# LB's actual transcript. Symmetric, so it proves reachability rather than the sign.
#
# Bound to a NAME rather than repeated inline. The first draft rebuilt it in the detail
# message with chr(73) + chr(116) + "s been..." to dodge quote-escaping, which silently
# dropped the apostrophe — so a failing check reported "got 0.0" from a *different* string
# that parsed fine. A diagnostic that lies is worse than no diagnostic.
LB_TRANSCRIPT = "It's been 1000 less than 1000."
check(value_of(LB_TRANSCRIPT) == 0,
      f"LB's real transcript {LB_TRANSCRIPT!r} answers 0",
      f"got {value_of(LB_TRANSCRIPT)}")


# ================================================= 3. real STT fixtures, warts and all

section("real stt fixtures")

# These are verbatim from oddball.log on 2026-08-13 — what `tiny.en` actually produced from
# LB's voice, question stem mangled and all. They are the strongest checks in this file
# because they are the only degraded input that was not invented here.
FIXTURES: tuple[tuple[str, float], ...] = (
    ("with five plus five.", 10),
    ("It's been 1000 less than 1000.", 0),
)

for question, expected in FIXTURES:
    got = value_of(question)
    check(got is not None, f"real transcript {question!r} is still recognised")
    if got is not None:
        check(math.isclose(got, expected), f"real transcript {question!r} == {expected}",
              f"got {got}")


# ======================================== 4. the decimal point — why this parses raw text

section("decimal integrity")

# `router.normalise()` deletes the decimal point: "3.5 plus 2" becomes "35 plus 2". This is
# the check that pins WHY calc takes the raw string, so the reason survives a refactor.
check(normalise("3.5 plus 2") == "35 plus 2",
      "normalise() really does destroy the decimal point (the premise still holds)",
      f"normalise('3.5 plus 2') == {normalise('3.5 plus 2')!r}")
check(value_of("3.5 plus 2") == 5.5, "3.5 plus 2 == 5.5")
check(value_of("3.5 plus 2") != 37,
      "3.5 plus 2 is NOT 37 — the answer a calculator on normalised text would give")
check(value_of("what is 2.5 times 4") == 10.0, "2.5 times 4 == 10")
check(value_of("0.5 plus 0.25") == 0.75, "0.5 plus 0.25 == 0.75")


# ================================================================ 5. it refuses politely

section("refuses non-arithmetic")

# The mirror of the formula tier's "no over-reach" section, and the more dangerous direction:
# a claimed question never escalates, so an over-eager calculator silently removes Tier 1 and
# Tier 3 from the answer path.
MUST_REFUSE = (
    "what time is it", "whats the time", "do you know what time it is",
    "what day is it today", "whats the date", "what month is it", "what year is it",
    "set a timer for five minutes", "set an alarm for 7 30", "remind me in 5 minutes",
    "who are you", "whats your name", "what are you",
    "thanks", "thank you", "hello", "hi there", "good morning",
    "stop", "cancel", "never mind",
    "what is the time constant of an rc circuit", "whats the cutoff frequency",
    "what resistor do i need for an led", "whats ohms law", "how do i combine resistors",
    "whats the weather in baltimore today", "whats the weather for friday in maryland",
    "do you know if the orioles have a baseball game today",
    "how far away is the moon", "tell me a joke", "how are you doing",
    "what is the pinout of a 555 timer", "how many pins does an lm358 have",
    "play track 5 of 10", "what channel is the game on", "my favourite number is 7",
    "five", "what is 7", "seven", "xyzzy", "", "   ", "whats 2 plus",
    "of all search google and find a weather for baltimore maryland today",
)

for question in MUST_REFUSE:
    hit = evaluate(question)
    check(hit is None, f"{question!r} is NOT claimed by the calculator",
          f"claimed it and said {hit.spoken!r}" if hit else "")

check(len(MUST_REFUSE) >= 40, "the refusal set is substantial",
      f"{len(MUST_REFUSE)} questions")

# The rule that makes all of the above work, stated as its own check so a regression names
# itself rather than showing up as forty unrelated failures.
check(evaluate("five") is None and evaluate("what is 7") is None,
      "a bare number is not arithmetic — an OPERATOR is required")
check(evaluate("5 plus 5") is not None,
      "...but a number with an operator is (guards the check above from passing vacuously)")


# ==================================================== 6. the router still routes everything

section("router integration")

check(router.route("what is 5 plus 5").intent == "calc", "a sum reaches the calc intent")
check(router.route("what is 5 plus 5").handled is True, "a sum is handled by Tier 0")
check("10" in router.route("what is 5 plus 5").text, "and he says the answer",
      router.route("what is 5 plus 5").text)

# Every intent that worked before must still work. This is the check that would have caught
# the Query refactor if it had gone wrong.
STILL_ROUTES = (
    ("what time is it", "time"),
    ("whats the time", "time"),
    ("what day is it today", "date"),
    ("whats todays date", "date"),
    ("set a timer for five minutes", "timer"),
    ("who are you", "identity"),
    ("thanks", "thanks"),
    ("hello", "hello"),
    ("stop", "stop"),
    ("what is the time constant of an rc circuit", "formula"),
    ("whats the cutoff frequency", "formula"),
    ("whats ohms law", "formula"),
)

for question, want in STILL_ROUTES:
    got = router.route(question)
    check(got.intent == want, f"{question!r} still routes to {want}", f"got {got.intent}")

# And the things that must still escalate must still escalate — the calculator must not have
# quietly absorbed Tier 1 and Tier 3's traffic.
for question in ("whats the weather in baltimore today", "how far away is the moon",
                 "tell me a joke", "xyzzy"):
    got = router.route(question)
    check(not got.handled, f"{question!r} still escalates (handled=False)",
          f"intent={got.intent}")

# The clock must survive the one collision that has bitten this repo three times.
check(router.route("what is the time constant of an rc circuit").intent == "formula",
      "the formula tier still outranks calc AND the clock")
check("9 47" not in router.route("what is the time constant of an rc circuit").text,
      "...and no clock reading leaks into it")


# =========================================================== 7. safety — never eval()

section("safety")

# The expression is walked with an ast whitelist. These are the things that must not get past
# it. Each is fed as a raw transcript, the way a hostile or garbled one would arrive.
ATTACKS = (
    "__import__('os').system('id')",
    "1 .__class__",
    "().__class__.__bases__",
    "open('/etc/passwd').read()",
    "exec('x=1')",
    "eval('1+1')",
    "[1,2,3]",
    "{'a': 1}",
    "lambda: 1",
    "x + 1",
    "print(1)",
    "1 if 1 else 2",
    "(1).bit_length()",
)

for attack in ATTACKS:
    hit = evaluate(attack)
    check(hit is None, f"refuses {attack!r}",
          f"returned {hit.spoken!r}" if hit else "")

# --- and now the SAME attacks against the whitelist ON ITS OWN.
#
# Mutation testing caught this section passing vacuously: every attack above dies at
# `_to_expression`, which refuses anything containing a letter, so the ast whitelist never
# ran. Widening `_ALLOWED_NODES` to include Call, Name and Attribute left the harness fully
# green — the safety checks were measuring the letter filter and nothing else.
#
# There are two layers here on purpose, and each has to be proven separately, or a change to
# the first silently removes all evidence that the second works.
for attack in ATTACKS + ("9 ** 9 ** 9", "'a'", "1 < 2", "(lambda: 1)()", "1j"):
    # `_Unsafe` (the whitelist did its job) or a parse error are the only acceptable outcomes.
    # Catching broadly matters: with Call allowed, `__import__(...)` raises NameError from the
    # empty builtins instead — which is "safe by accident", not safe by design, and it also
    # took the harness down with it rather than reporting. That must read as a FAIL.
    try:
        calc._safe_eval(attack)
        rejected, why = False, "evaluated without complaint"
    except calc._Unsafe as exc:
        rejected, why = True, str(exc)
    except SyntaxError:
        rejected, why = True, "rejected by the parser"
    except Exception as exc:  # noqa: BLE001
        rejected = False
        why = (f"got past the whitelist and raised {type(exc).__name__} instead — "
               "blocked by luck, not by design")
    check(rejected, f"the ast whitelist itself rejects {attack!r}", why)

# The mirror, so the whitelist cannot pass by refusing everything.
for safe in ("1 + 1", "2 ** 10", "(20 - 5)", "15 / 100 * 80", "-7 + 2", "((144) ** 0.5)"):
    try:
        got = calc._safe_eval(safe)
        ok = isinstance(got, (int, float))
    except Exception as exc:  # noqa: BLE001
        ok, got = False, f"{type(exc).__name__}: {exc}"
    check(ok, f"the ast whitelist still ALLOWS {safe!r}", str(got))

# The guard has to be on the tree, not on an exception, because this does not raise — it
# simply never returns. A clock is the only honest way to check that.
began = time.monotonic()
bomb = evaluate("9 to the power of 9 to the power of 9")
took = time.monotonic() - began
check(bomb is None, "refuses a power tower rather than computing it")
check(took < 1.0, "...and refuses it FAST (the tree is checked before any arithmetic)",
      f"took {took:.3f}s")

began = time.monotonic()
evaluate("2 to the power of 999999999")
took = time.monotonic() - began
check(took < 1.0, "a huge literal exponent is bounded too", f"took {took:.3f}s")

check(evaluate("2 to the power of 10") is not None,
      "...but an ordinary power still works (the bound is not just 'refuse everything')")


# ==================================================== 8. speakable, and never raises

section("speakable")

for question, _ in SUMS + REVERSED:
    hit = evaluate(question)
    if hit is None:
        continue
    bad = unspeakable(hit.spoken)
    check(not bad, f"{question!r}: no characters Piper cannot say", f"found {bad!r}")
    check(hit.spoken.isascii(), f"{question!r}: pure ASCII", hit.spoken)
    check(hit.spoken.strip().endswith((".", "!", "?")),
          f"{question!r}: ends like a sentence", hit.spoken)
    check(len(hit.spoken.split()) <= 20, f"{question!r}: short enough to speak",
          hit.spoken)
    for symbol in ("*", "/", "^", "+", "=", "%"):
        check(symbol not in hit.spoken, f"{question!r}: no bare {symbol!r} operator",
              hit.spoken)

# Float noise is the specific ugliness this has to avoid: 0.1 + 0.2 is 0.30000000000000004 in
# IEEE 754, and Piper would read every one of those digits out loud.
noisy = evaluate("whats 0.1 plus 0.2")
check(noisy is not None and "0.30000000000000004" not in noisy.spoken,
      "0.1 plus 0.2 does not speak IEEE 754 noise",
      noisy.spoken if noisy else "")
check(noisy is not None and "0.3" in noisy.spoken, "...it says 0.3",
      noisy.spoken if noisy else "")
check(noisy is not None and "about" not in noisy.spoken,
      "...and does not hedge, because nothing was lost", noisy.spoken if noisy else "")

thirds = evaluate("what is 100 divided by 3")
check(thirds is not None and "about" in thirds.spoken,
      "a genuinely rounded answer DOES hedge", thirds.spoken if thirds else "")
check(thirds is not None and len(thirds.spoken) < 30,
      "...and stays short (significant figures, not 4 decimals)",
      thirds.spoken if thirds else "")

zero = evaluate("5 divided by 0")
check(zero is not None and zero.spoken == DIVIDE_BY_ZERO,
      "dividing by zero is answered, in character, from Tier 0",
      zero.spoken if zero else "None")


section("never raises")

# Anything can arrive here: a garbled transcript, an empty capture, a wall of punctuation.
# Tier 0 returning an exception into a turn would take the whole assistant down.
random.seed(20260813)
ALPHABET = "0123456789 abcdefghijklmnopqrstuvwxyz+-*/%().,^ "
crashes = []
for _ in range(500):
    junk = "".join(random.choice(ALPHABET) for _ in range(random.randint(0, 40)))
    try:
        result = evaluate(junk)
        if result is not None and not isinstance(result, Result):
            crashes.append((junk, "returned a non-Result"))
    except Exception as exc:  # noqa: BLE001 — that is the whole point of the check
        crashes.append((junk, f"{type(exc).__name__}: {exc}"))

check(not crashes, "500 fuzzed strings, no exceptions and no bad return types",
      f"{len(crashes)} failures, first: {crashes[0] if crashes else ''}")

# The same, through the router, since that is the path a real turn takes.
router_crashes = []
for _ in range(200):
    junk = "".join(random.choice(ALPHABET) for _ in range(random.randint(0, 40)))
    try:
        router.route(junk)
    except Exception as exc:  # noqa: BLE001
        router_crashes.append((junk, f"{type(exc).__name__}: {exc}"))

check(not router_crashes, "200 fuzzed strings through the router, no exceptions",
      f"{len(router_crashes)} failures, first: {router_crashes[0] if router_crashes else ''}")


section("purity")

# The cache key is the whole input, so the same string must always give the same answer and a
# different string must be free to give a different one. A cache keyed on less than what
# derives the answer is how a confident wrong answer gets served twice.
# Read through value_of(), which returns None rather than raising. Dereferencing `.value`
# directly crashed the whole harness when a mutation broke the parser — 50 real failures
# above were never printed because the run died here. A harness that explodes instead of
# reporting is one that cannot be trusted to tell you what went wrong.
check(value_of("what is 5 plus 5") is not None
      and value_of("what is 5 plus 5") == value_of("what is 5 plus 5"),
      "the same question gives the same answer (cache is keyed on the whole input)")
check(value_of("what is 5 plus 5") != value_of("what is 5 plus 6"),
      "a different question is not served from the same cache entry")
check(calc.evaluate.cache_info().maxsize is not None, "evaluate() is actually cached")


# ============================================================================== report

passed = sum(1 for ok, *_ in RESULTS if ok)
failed = len(RESULTS) - passed

width = 76
last_section = None
for ok, sec, msg, detail in RESULTS:
    if sec != last_section:
        print(f"\n-- {sec} " + "-" * max(0, width - len(sec) - 4))
        last_section = sec
    if not ok:
        print(f"  FAIL  {msg}")
        if detail:
            print(f"        {detail}")

print("\n" + "=" * width)
print(f"{passed}/{len(RESULTS)} checks passed"
      + (f"  ({failed} FAILED)" if failed else "  - all green"))
print(f"{len(SUMS)} expressions recomputed, {len(REVERSED)} reversed-operand forms, "
      f"{len(MUST_REFUSE)} refusals, {len(ATTACKS)} attacks, 700 fuzzed strings")
raise SystemExit(1 if failed else 0)
