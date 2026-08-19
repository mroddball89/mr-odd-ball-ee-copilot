#!/usr/bin/env python3
"""
Module:  verify_speakable.py
Purpose: Prove the summariser extracts rather than invents, refuses rather than guesses, and
         never lets a number into his mouth that was not in the source.
Author:  LB
Date:    2026-08-14

    venv/bin/python tools/verify_speakable.py

Fourteenth harness, same contract as the rest: exit 0 = all passed, no model, no network.

## The check that carries the file

**A generated summary containing a number the source does not have is rejected.** That is
`formulas.Worked`'s contract applied to prose, and it is the reason this can be allowed to use
a model at all. D30 measured every candidate model stating electronics relationships fluently
and wrongly; the answer here is not to trust a better prompt, it is to make the failure
mechanically detectable and reject it.

## The cross-check that makes the verifier credible

`define.py` and `formulas.py` hold **209 strings hand-written to this exact standard** over the
last two days — 40 words or fewer, speakable, ASCII, ending on a sentence boundary. Every one
of them is run through `verify()` here.

If the verifier and the hand-written corpus disagree, one of them is wrong, and finding out
which costs nothing. It is the cheapest possible validation of a new rule: a large body of
independently-produced examples that should already satisfy it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from memory.speakable import (                                       # noqa: E402
    MAX_WORDS, Speakable, extract, make_speakable, sentences, verify,
)
from orchestrator.define import TERMS                                # noqa: E402
from orchestrator.formulas import FORMULAS, UNSPEAKABLE              # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


# ============================================================ 1. THE GROUNDING CHECK

section("numbers must come from the source")

SOURCE = ("The time constant of an RC circuit is the resistance multiplied by the "
          "capacitance. With 10 kilohms and 1 microfarad the result is 10 milliseconds.")

check(verify("The time constant is 10 milliseconds.", SOURCE) == [],
      "a number that IS in the source passes")

bad = verify("The time constant is 47 milliseconds.", SOURCE)
check(any("not in the source" in p for p in bad),
      "a number that is NOT in the source is rejected", f"{bad}")

bad = verify("Use 10 kilohms and 2 microfarads for 20 milliseconds.", SOURCE)
check(any("not in the source" in p for p in bad),
      "SOME invented numbers among real ones is still rejected", f"{bad}")

check(verify("The time constant is resistance times capacitance.", SOURCE) == [],
      "prose with no numbers at all is fine")

# Decimals and signs are numbers too — "3.5" must not pass because "35" appears somewhere.
bad = verify("It settles at 3.5 volts.", "It settles at 35 volts.")
check(any("not in the source" in p for p in bad),
      "3.5 is not grounded by 35 being present", f"{bad}")


# ============================================================ 2. it refuses what Piper cannot say

section("speakability")

LONG = " ".join(["word"] * (MAX_WORDS + 5)) + "."
check(any("over the" in p for p in verify(LONG, "")), "too long is rejected")
check(any("sentence boundary" in p for p in verify("no full stop here", "")),
      "no sentence boundary is rejected")
check(any("Piper cannot say" in p for p in verify("The value is 5 Ω.", "The value is 5 Ω.")),
      "a character Piper cannot say is rejected")
check(any("non-ASCII" in p for p in verify("Café current.", "Café current.")),
      "non-ASCII is rejected")
check(any("operator" in p for p in verify("It is V = I * R.", "It is V = I * R.")),
      "bare operators are rejected")
check(verify("", "source") == ["it is empty"], "empty is rejected with one clear reason")


# ============================================================ 3. it refuses fragments

section("fragments and cross-references")

for opener in ("Substituting this into the previous result gives the answer.",
               "Therefore the current is unchanged.",
               "This gives the required value.",
               "Rearranging, we find the answer.",
               "Combining the two expressions yields the result."):
    problems = verify(opener, opener)
    check(any("mid-argument" in p for p in problems),
          f"{opener[:38]!r}... is a fragment", f"{problems}")

for ref in ("The result is shown in Figure 3.", "Values are listed in Table 2.1.",
            "See equation 7 for the derivation."):
    problems = verify(ref, ref)
    check(any("cross-reference" in p for p in problems),
          f"{ref!r} names something the listener cannot see", f"{problems}")

# The mirror: an ordinary definition must NOT be caught by either rule.
for good in ("Capacitance is the charge stored per volt applied.",
             "Thevenin resistance is what you see looking back into the network.",
             "A mole is Avogadro's number of anything."):
    check(verify(good, good) == [], f"{good[:38]!r}... is not a fragment",
          f"{verify(good, good)}")


# ============================================================ 4. extraction

section("extraction")

PASSAGE = ("Kirchhoff's current law states that the algebraic sum of currents entering a "
           "node is zero. Substituting this into the loop equation above and rearranging "
           "gives the result shown in Figure 4.2, which we use throughout the chapter.")
got = extract(PASSAGE)
check(got is not None, "a passage with one good sentence yields something")
if got:
    check(got.method == "extract", "and it is extracted, not invented", got.method)
    check(got.text in PASSAGE, "the text is VERBATIM from the source — nothing invented",
          got.text)
    check("Substituting" not in got.text, "the mid-derivation sentence is not chosen",
          got.text)
    check(verify(got.text, PASSAGE) == [], "and the result verifies")

# Nothing speakable at all -> None, which is a normal outcome, not a failure.
UNSPEAKABLE_PASSAGE = ("Substituting this into the above and rearranging as before, we "
                       "obtain the expression given in Table 6.1 for all such cases.")
check(extract(UNSPEAKABLE_PASSAGE) is None,
      "a passage that is entirely fragments returns None (speakable = '')",
      f"{extract(UNSPEAKABLE_PASSAGE)}")

check(extract("") is None, "empty source returns None")
check(extract("   ") is None, "whitespace source returns None")

ONE_LONG = " ".join(["word"] * 200) + "."
check(extract(ONE_LONG) is None, "a single over-long sentence returns None rather than cutting it")

check(len(sentences("A. B! C? D")) == 4, "sentence splitting handles all terminators",
      f"{sentences('A. B! C? D')}")


# ============================================================ 5. generation is fenced

section("generation is the last resort, and fenced")

FRAGMENTS = ("Substituting this into the previous expression and rearranging as before, "
             "we obtain the standard form used in Table 3.")


def honest(_source: str) -> str:
    return "The current through the node sums to zero."


def liar(_source: str) -> str:
    return "The time constant is 47 milliseconds."


def crasher(_source: str) -> str:
    raise RuntimeError("model went away")


check(make_speakable(FRAGMENTS, "theory", generator=None) is None,
      "with no generator, an unspeakable passage is simply refused")

got = make_speakable(FRAGMENTS, "theory", generator=honest)
check(got is not None and got.method == "generate",
      "a generator is used when extraction fails", f"{got}")

check(make_speakable(FRAGMENTS, "theory", generator=liar) is None,
      "a generator that invents a number is REJECTED, not stored")

check(make_speakable(FRAGMENTS, "theory", generator=crasher) is None,
      "a generator that raises is treated as no answer, not as a crash")

# THE D30 FENCE. `formula` and `table` are exactly what every model was measured mangling, so
# for those two types extraction is the only path and refusing is the fallback.
for locked in ("formula", "table"):
    check(make_speakable(FRAGMENTS, locked, generator=honest) is None,
          f"chunk_type={locked!r} is NEVER generated, however good the generator looks")
for open_type in ("definition", "theory", "worked_example", "procedure"):
    check(make_speakable(FRAGMENTS, open_type, generator=honest) is not None,
          f"chunk_type={open_type!r} may be generated")

# Extraction is preferred even when a generator is available.
got = make_speakable(PASSAGE, "theory", generator=honest)
check(got is not None and got.method == "extract",
      "when extraction works, the generator is not called", f"{got}")


# ============================================================ 6. THE CROSS-CHECK

section("it agrees with 209 hand-written strings")

# define.py and formulas.py were written to this standard by hand over two days, before this
# verifier existed. If the rule and the corpus disagree, one is wrong.
checked = 0
disagreements: list[str] = []
for term in TERMS:
    problems = verify(term.spoken, term.spoken)
    checked += 1
    if problems:
        disagreements.append(f"define.{term.key}: {problems}")
for formula in FORMULAS:
    problems = verify(formula.spoken, formula.spoken)
    checked += 1
    if problems:
        disagreements.append(f"formulas.{formula.key}: {problems}")

check(checked >= 200, "the cross-check ran against the whole corpus", f"{checked} strings")
check(not disagreements,
      "every hand-written spoken string satisfies the new verifier",
      "; ".join(disagreements[:4]))

# And they are all extractable from themselves, which is the degenerate case the corpus builder
# will hit constantly — a chunk that is already short enough to say.
extractable = sum(1 for t in TERMS if extract(t.spoken) is not None)
check(extractable >= len(TERMS) * 0.9,
      "at least 90% of the glossary extracts cleanly from itself",
      f"{extractable}/{len(TERMS)}")


# ============================================================ 7. it never raises

section("never raises")

for junk in ("", " ", "\n", "." * 500, "?" * 200, "a" * 5000, "3.14 " * 300,
             "No terminator", "\t\r\n", "-- -- --"):
    try:
        extract(junk)
        verify(junk, junk)
        make_speakable(junk, "theory", generator=honest)
        ok, detail = True, ""
    except Exception as exc:                                       # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check(ok, f"survives {junk[:22]!r}", detail)

check(isinstance(verify("x.", "x."), list), "verify always returns a list")
check(extract("Hello there.") is None or isinstance(extract("Hello there."), Speakable),
      "extract returns a Speakable or None, never anything else")
check(UNSPEAKABLE, "UNSPEAKABLE is non-empty (guards the speakability checks above)")


# ============================================================ report

passed = sum(1 for ok, *_ in RESULTS if ok)
failed = len(RESULTS) - passed

width = 76
last = None
for ok, sec, msg, detail in RESULTS:
    if sec != last:
        print(f"\n-- {sec} " + "-" * max(3, width - len(sec) - 4))
        last = sec
    if not ok:
        print(f"  FAIL  {msg}")
        if detail:
            print(f"        {detail}")

print("\n" + "=" * width)
print(f"{passed}/{len(RESULTS)} checks passed"
      + (f"  ({failed} FAILED)" if failed else "  — all green"))
print(f"cross-checked against {checked} hand-written spoken strings")
raise SystemExit(1 if failed else 0)
