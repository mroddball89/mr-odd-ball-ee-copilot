#!/usr/bin/env python3
"""
Module:  math_notation.py
Purpose: Render maths as symbols for the SCREEN and as English for PIPER. No model.
Author:  LB
Date:    2026-09-12

    python -m orchestrator.math_notation                     # the quiz decks, both ways
    python -m orchestrator.math_notation "cos^2(theta)/x"    # one string

## Why this exists

LB asked, 2026-09-12: *"it's hard for me to understand the trig questions because of how it's
displayed and spoken"*. Both halves of that are one problem — a question is stored as flat
ASCII and neither channel does anything with it:

    stored   the limit of cos^2(theta) / (1 - sin(theta)) as theta approaches pi/2
    screen   the limit of cos^2(theta) / (1 - sin(theta)) as theta approaches pi/2
    Piper    "the limit of cos carat two theta slash one minus sin theta..."

`sin` is the worst of it out loud. Piper reads it as the English noun, so a trig question
arrives as a sentence about wrongdoing.

Two functions, because the two channels want opposite things. The screen wants FEWER words —
`θ`, `²`, `√` — and the ear wants MORE: "cosine squared of theta, over the quantity one minus
sine of theta". A single "prettified" string would be a compromise that is wrong for both.

## The anchor, which is the whole safety argument

D38 again — **the danger is never the rule that fails to match, it is the one that matches too
much** — and the decks prove it in their own answer text. Two shapes, described rather than
quoted because `data/quiz/**` is gitignored and this file is not:

    a word with "pi" inside it       e.g. "picture", "capital", "pint"
    a hyphenated proper noun         e.g. a theorem named "<Something>-Sided"

A bare `pi` -> `π` turns the first into "πcture". So every word here is matched with
`\\b` word boundaries, and `-` is only read as "minus" when it is SPACED (` - `), which is how
this repo's decks write arithmetic and is never how they write a hyphenated name.

`sin`, `cos` and `tan` are worse than `pi`, because they hide in ordinary words this project
uses constantly — "u**sin**g", "**sin**ce", "impor**tan**t", "cons**tan**t", "**cos**t". They
are therefore matched ONLY where a function call is unambiguous: immediately followed by `(`
or by `^n(`. `sin` standing alone in prose is left exactly as it was.

## The screen does not need the function names spelled out

`sin(x)` IS the mathematical notation; it is already right on screen and only wrong in the ear.
So `to_unicode` touches Greek names, exponents, roots, arrows and comparisons — and leaves
`sin`, `cos` and `tan` alone. That is why the unicode pass needs no function-call anchor and
the speech pass does.
"""

from __future__ import annotations

import re

__all__ = ["to_unicode", "to_speech", "GREEK", "FUNCTIONS"]

# Greek spelled out -> the letter. Word-bounded, always: "pi" lives inside "picture" and
# "capital", and "phi" inside "graphic".
GREEK: dict[str, str] = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "pi": "π",
}

# Capitalised forms that are genuinely different letters rather than a capitalised word.
# Only the ones whose uppercase is used in engineering: Ω for resistance, Δ for change,
# Σ for a sum. "Pi" at the start of a sentence is still π, so it is folded by the caller.
_GREEK_UPPER: dict[str, str] = {"Omega": "Ω", "Delta": "Δ", "Sigma": "Σ", "Phi": "Φ"}

_SUPERSCRIPT = str.maketrans("0123456789+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ")

# The trig and log functions, spoken. `sin` -> "sine" is the single biggest win here.
FUNCTIONS: dict[str, str] = {
    "sin": "sine", "cos": "cosine", "tan": "tangent",
    "csc": "cosecant", "sec": "secant", "cot": "cotangent",
    "arcsin": "arc sine", "arccos": "arc cosine", "arctan": "arc tangent",
    "sinh": "hyperbolic sine", "cosh": "hyperbolic cosine", "tanh": "hyperbolic tangent",
    "log": "log", "ln": "natural log", "exp": "e to the",
    # Here rather than in a pass of its own, so "sqrt(2)" reads "square root of 2" and not
    # "square root of THE QUANTITY 2" — a root's bracket is an argument, like a sine's.
    "sqrt": "square root",
}

# Longest first, so "arcsin" is not eaten by "sin" and "cosh" not by "cos".
_FUNC_NAMES = sorted(FUNCTIONS, key=len, reverse=True)

# A function call, and ONLY a function call: the name, an optional exponent, then "(".
# The `(` is the anchor that keeps "using" and "important" out of this.
_CALL = re.compile(r"\b(" + "|".join(_FUNC_NAMES) + r")(?:\^(\d+))?\s*\(")

_ORDINAL_POWER = {"2": "squared", "3": "cubed"}


def _word_sub(text: str, table: dict[str, str]) -> str:
    """Replace whole words only. The `picture`/`pi` guard, applied in one place."""
    if not table:
        return text
    pattern = r"\b(" + "|".join(sorted(map(re.escape, table), key=len, reverse=True)) + r")\b"
    return re.sub(pattern, lambda m: table[m.group(1)], text)


# =============================================================================================
# the screen
# =============================================================================================

def to_unicode(text: str) -> str:
    """Maths as symbols, for the chat card. Never raises; returns "" for empty input.

    Deliberately NOT a general LaTeX renderer. It does the substitutions that make a stored
    question readable at a glance and nothing that needs a layout engine — there is no fraction
    bar here, because a fraction bar needs two dimensions and this returns a string.

    `sin`, `cos` and `tan` are left alone: they are already correct notation on a screen.
    """
    if not text:
        return ""

    out = text
    out = _word_sub(out, _GREEK_UPPER)
    out = _word_sub(out, GREEK)

    # sqrt(...) -> √(...). The parentheses stay, because without a vinculum they are the only
    # thing saying where the root ends.
    out = re.sub(r"\bsqrt\s*\(", "√(", out)
    out = re.sub(r"\bsqrt\b", "√", out)

    # x^2 -> x², x^-1 -> x⁻¹, x^n -> xⁿ. Bare digits/sign/n only: "x^(a+b)" has no superscript
    # spelling and is left as typed rather than half-converted.
    out = re.sub(r"\^\(?([0-9+\-n]+)\)?", lambda m: m.group(1).translate(_SUPERSCRIPT), out)

    for src, dst in (("<=", "≤"), (">=", "≥"), ("!=", "≠"), ("->", "→"), ("=>", "⇒"),
                     ("+-", "±"), ("...", "…")):
        out = out.replace(src, dst)

    out = _word_sub(out, {"infinity": "∞", "inf": "∞", "degrees": "°", "degree": "°",
                          "ohms": "Ω", "ohm": "Ω", "micro": "µ"})

    # "*" -> "·", but ONLY between two operands. `_question_card` wraps this result in
    # "**Q:** ...", and a PDF import can carry markdown of its own, so a doubled "*" must
    # survive: in "**bold**" each star is adjacent to another star rather than to an operand,
    # and the lookarounds below refuse it. "2*pi*R*C" and "theta * tan(theta)" both convert.
    #
    # `\w` rather than `[A-Za-z0-9]`, because by this line the Greek pass has already run and
    # the operands are no longer ASCII: "2*pi*R*C" is "2*π*R*C" here, and an ASCII-only
    # lookaround converted the "R*C" and left the "2*π" — half a conversion, which looks like
    # a typo rather than a rule. `\w` is unicode-aware in Python 3 and matches π and θ.
    out = re.sub(r"(?<=[\w)])\s*\*\s*(?=[\w(])", "·", out)
    return out


# =============================================================================================
# the ear
# =============================================================================================

# A character that cannot appear in a question, marking a bracket THIS module opened as a
# function's argument. An earlier version tested for the literal text "of (" instead, and the
# trig deck broke it in one line: "the denominator of (1 - cos(x))/x" has an ENGLISH "of"
# in front of a grouping bracket, so the group lost its "the quantity" and was read as
# "the denominator of 1 minus cosine of x over x" — which says something different.
_ARG = "\x00"


def _speak_calls(text: str) -> str:
    """`cos^2(theta)` -> `cosine squared of ⟨arg⟩theta)`. Brackets are the next pass's job."""
    def swap(m: re.Match) -> str:
        name, power = m.group(1), m.group(2)
        spoken = FUNCTIONS[name]
        if power:
            spoken += " " + _ORDINAL_POWER.get(power, f"to the power of {power}")
        return f"{spoken} of {_ARG}"

    previous = None
    out = text
    # Repeated because a call can nest: sin(cos(x)). Bounded, never a while-True.
    for _ in range(4):
        if out == previous:
            break
        previous, out = out, _CALL.sub(swap, out)
    return out


def _speak_groups(text: str) -> str:
    """Bracketed groups -> "the quantity ...", which is how a person reads them aloud.

    A closing bracket becomes a COMMA rather than a space, because the pause is what tells the
    ear where the group ended — "the quantity one minus sine of theta, over two" and
    "the quantity one minus sine of theta over two" are different expressions.
    """
    out = re.sub(rf"{_ARG}\s*", " ", text)              # function arguments: no "quantity"
    out = re.sub(r"\(\s*", " the quantity ", out)       # everything left is a real group
    out = out.replace(")", ", ")
    return out


def to_speech(text: str) -> str:
    """Maths as English, for Piper. Never raises; returns "" for empty input.

    The order matters and is the whole of the function: function calls are resolved BEFORE
    brackets (or "sine of x" loses its "of"), and brackets before operators (or a minus sign
    inside a group is read before the group is named).
    """
    if not text:
        return ""

    # Roots first, and in this order. The trig deck stores "√2√(1 - cos(t))" — one root with a
    # bracket and one without, touching. `√(` becomes a `sqrt(` CALL so it reads "square root
    # of 1 minus...", while a bare `√` has no bracket to delimit it and can only be read as a
    # prefix. The leading space on both is load-bearing: without it "√2√(" collapses to
    # "2sqrt(" and `\bsqrt` no longer matches, because "2s" has no word boundary in it.
    out = text.replace("√(", " sqrt(").replace("√", " the square root of ")

    out = _speak_calls(out)
    out = _speak_groups(out)

    # Exponents that were not part of a call: x^2 -> "x squared".
    out = re.sub(r"\^\(?([0-9n]+)\)?",
                 lambda m: " " + _ORDINAL_POWER.get(m.group(1), f"to the power of {m.group(1)}"),
                 out)

    # Operators. `-` is SPACED-ONLY, so "Right-Sided Squeeze Theorem" survives; `/` and `*`
    # are not, because no English word in these decks contains them.
    out = out.replace("/", " over ")
    out = out.replace("*", " times ")
    out = out.replace(" - ", " minus ")
    out = out.replace(" + ", " plus ")

    for src, dst in ((" <= ", " is less than or equal to "), (" >= ", " is greater than or "
                                                              "equal to "),
                     (" != ", " is not equal to "), (" < ", " is less than "),
                     (" > ", " is greater than "), (" = ", " equals "), (" -> ", " approaches ")):
        out = out.replace(src, dst)

    out = out.replace("%", " percent")
    out = _word_sub(out, {"infinity": "infinity", "inf": "infinity"})

    # Collapse what the substitutions above introduced. Every closing bracket became a comma,
    # so nested groups end in a run of them — "sine of theta,," — and Piper reads a doubled
    # comma as a doubled pause.
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s*,(\s*,)+", ",", out)          # ",," and ", ,"  -> ","
    out = re.sub(r"\s+([,.;:?!])", r"\1", out)      # " ,"             -> ","
    out = re.sub(r",(\s*[.?!])", r"\1", out)        # ",?"             -> "?"
    # A group that closes at the very end leaves a comma with nothing after it — "sine of x,"
    # — and Piper holds the pause before silence, which sounds like he was interrupted.
    out = re.sub(r"\s*,\s*$", "", out)
    return out


if __name__ == "__main__":                                            # pragma: no cover
    import json
    import pathlib
    import sys

    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            print(f"  raw     {arg}")
            print(f"  screen  {to_unicode(arg)}")
            print(f"  spoken  {to_speech(arg)}\n")
        raise SystemExit(0)

    root = pathlib.Path(__file__).resolve().parents[1]
    for deck in sorted((root / "data" / "quiz").glob("*.json")):
        print(f"===== {deck.name} =====")
        for item in json.loads(deck.read_text(encoding="utf-8")):
            q = item.get("question", "")
            screen, spoken = to_unicode(q), to_speech(q)
            if screen == q and spoken == q:
                continue                     # nothing to say about prose
            print(f"  raw     {q}")
            print(f"  screen  {screen}")
            print(f"  spoken  {spoken}\n")
