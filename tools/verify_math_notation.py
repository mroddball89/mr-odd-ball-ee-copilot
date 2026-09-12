#!/usr/bin/env python3
"""
Module:  verify_math_notation.py
Purpose: Prove the maths converters read the decks correctly and leave English alone.
Author:  LB
Date:    2026-09-12

    python tools/verify_math_notation.py

## Why this exists

`orchestrator/math_notation.py` rewrites every quiz question on its way to the screen and to
Piper. That is a substitution pass over text LB is trying to learn from, so the failure that
matters is not "it missed one" — it is **the rule that matched too much** and silently changed
a sentence into a different sentence.

The real decks supply the shapes, and section 2 reproduces those shapes rather than quoting
them — `data/quiz/**` is gitignored (LB's own uploaded PDFs, one of them a third party's
copyrighted guide) and this file is committed to a public repo. The three that bite:

    a word with "pi" inside it         a bare pi -> π rewrites the word
    a hyphenated proper noun           that hyphen is not a minus sign
    an English "of" before a bracket   NOT a function argument, and must keep "the quantity"

Section 3 is the round trip that matters most: every question in every deck must survive both
converters without losing a word of its prose. It reads the genuine decks at RUN time, on the
machine that has them, and says so and passes when they are absent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.harness_lib import bootstrap, check, counts as _tally, section  # noqa: E402

bootstrap()

from orchestrator.math_notation import to_speech, to_unicode                # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DECKS = sorted((REPO / "data" / "quiz").glob("*.json"))


# =========================================================================================
section("1. the screen — symbols, not spelled-out names")
# =========================================================================================

for raw, want in [
    ("theta", "θ"),
    ("pi/2", "π/2"),
    ("cos^2(theta)", "cos²(θ)"),
    ("x^2 + y^2 = r^2", "x² + y² = r²"),
    ("x^-1", "x⁻¹"),
    ("sqrt(2)", "√(2)"),
    ("1/(2*pi*R*C)", "1/(2·π·R·C)"),
    # No spaces around "·", spaced input or not, so "2·π·R·C" and "θ·tan(θ)" agree.
    ("theta * tan(theta)", "θ·tan(θ)"),
    ("5 ohms", "5 Ω"),
    ("x <= y", "x ≤ y"),
    ("x != y", "x ≠ y"),
    ("x -> 0", "x → 0"),
    ("90 degrees", "90 °"),
]:
    got = to_unicode(raw)
    check(got == want, f"screen: {raw!r} -> {want!r}", f"got {got!r}")

# `sin` IS notation on a screen. Spelling it out there would be a regression, not a feature.
check(to_unicode("sin(x)") == "sin(x)",
      "screen: sin/cos/tan are left as written — already correct notation",
      to_unicode("sin(x)"))

# The markdown guard. `_question_card` wraps the result in "**Q:** ...", and an imported PDF
# can carry emphasis of its own.
check(to_unicode("**bold** text") == "**bold** text",
      "screen: markdown emphasis survives — '*' converts between OPERANDS only",
      to_unicode("**bold** text"))


# =========================================================================================
section("2. the ear — English, and the anchors that keep English out of it")
# =========================================================================================

for raw, want in [
    ("sin(x)", "sine of x"),
    ("cos(theta)", "cosine of theta"),
    ("tan(t)/t", "tangent of t, over t"),
    ("cos^2(theta)", "cosine squared of theta"),
    ("x^3", "x cubed"),
    ("x^5", "x to the power of 5"),
    ("sqrt(2)", "square root of 2"),
    ("(1 - cos(x))/x", "the quantity 1 minus cosine of x, over x"),
    ("50%", "50 percent"),
]:
    got = to_speech(raw)
    check(got == want, f"spoken: {raw!r} -> {want!r}", f"got {got!r}")

# THE ANCHOR. Every one of these is prose that hides a function name or "pi" inside an
# ordinary word, and every one must come back byte-identical.
#
# **Written here rather than quoted off the decks, deliberately.** `data/quiz/**` is
# gitignored — those are LB's own uploaded course PDFs, and one of them is a third party's
# copyrighted guide — so a committed file in a PUBLIC repo must not carry their text. These
# reproduce the SHAPE of the traps the real decks contain, which is the part under test.
# Section 3 still reads the genuine decks, at run time, on the machine that has them.
PROSE = [
    "the picture on page two is not precise",          # "pi" inside a word - the real trap
    "a capital letter and a pint of milk",             # "pi" twice more
    "using a constant is important since cost matters",  # sin, tan, tan, cos
    "processing a signal is not the same as discussing it",
    "the Right-Sided Squeeze Theorem",                 # a hyphen that is not a minus sign
    "left-to-right, top-to-bottom",                    # three more of them
]
for line in PROSE:
    for name, fn in (("screen", to_unicode), ("spoken", to_speech)):
        got = fn(line)
        check(got == line, f"{name}: prose is untouched — {line[:46]!r}", f"got {got!r}")

# The English "of" in front of a grouping bracket. This is the bug the sentinel exists for:
# a literal "of (" test read this group as a function argument and dropped "the quantity",
# which changes what the sentence says.
got = to_speech("the denominator of (1 - cos(x))/x")
check("the quantity" in got,
      "spoken: an ENGLISH 'of' before a bracket still gets 'the quantity'", got)

# A spaced hyphen is a minus; an unspaced one is a name.
check("minus" in to_speech("1 - cos(x)"), "spoken: ' - ' is a minus sign")
check("minus" not in to_speech("The Right-Sided Squeeze Theorem"),
      "spoken: 'Right-Sided' is NOT 'Right minus Sided'")

# Empty input is a real case: `QuizItem.question` can be "" on a malformed import, and this
# runs on the turn path where an exception is a failed turn.
for fn in (to_unicode, to_speech):
    check(fn("") == "" and fn(None) == "",             # type: ignore[arg-type]
          f"{fn.__name__}: empty and None return '' rather than raising")


# =========================================================================================
section("3. every real question survives both converters")
# =========================================================================================

# `data/quiz/**` is gitignored, so a fresh clone HAS no decks and this section has nothing to
# read. That is not a failure — the converters are proved by sections 1 and 2, which carry
# their own cases. Going red here would mean this harness is red on every machine but LB's,
# and a check that cannot pass where it is run is one that gets muted.
if not DECKS:
    print("  (no data/quiz/*.json on this machine — sections 1-2 still prove the converters)")

_WORD = __import__("re").compile(r"[A-Za-z]{4,}")

total = 0
for deck in DECKS:
    items = json.loads(deck.read_text(encoding="utf-8"))
    for item in items:
        q = item.get("question", "")
        if not q:
            continue
        total += 1
        u, s = to_unicode(q), to_speech(q)
        check(bool(u) and bool(s), f"{deck.stem}: {q[:40]!r} converts on both channels")

        # No ordinary word may be mangled. Greek names and function names are EXPECTED to
        # change, so they are excused; everything else of four letters or more must survive
        # the trip to the screen intact. This is what would have caught "picture" -> "πcture".
        from orchestrator.math_notation import FUNCTIONS, GREEK
        excused = set(GREEK) | set(FUNCTIONS) | {"sqrt", "ohm", "ohms", "degrees", "degree",
                                                 "infinity"}
        lost = [w for w in _WORD.findall(q)
                if w.lower() not in excused and w not in u]
        check(not lost, f"{deck.stem}: no word is mangled on screen — {q[:36]!r}",
              f"lost: {lost}")

if DECKS:
    check(total >= 20, f"and there were enough of them to mean something ({total} questions)")


if __name__ == "__main__":
    print("\n" + "=" * 78)
    print(f"  {_tally.passed + _tally.failed} checks, {_tally.passed} passed, "
          f"{_tally.failed} failed")
    print("=" * 78)
    if _tally.failed:
        print(f"\n  {_tally.failed} RED\n")
        raise SystemExit(1)
    print(f"\n  {_tally.passed}/{_tally.passed} checks passed — all green\n")
