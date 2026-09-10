#!/usr/bin/env python3
"""
Module:  quiz_grade.py
Purpose: Mark a quiz answer on this machine. No network, no model, no API key, ever.
Author:  LB
Date:    2026-09-02

    python tools/quiz_grade.py "V = I*R" "v equals i times r"      # answer, then his reply

## Why this exists at all

`agents/quiz_agent.evaluate_quiz_answer` sent every single answer to Gemini. On a free tier
counted in REQUESTS at 20 per model name per day (D3), a ten-question quiz spent **half of
LB's daily quota** deciding whether "V = I R" matches "V = I * R". Run the quiz twice before
lunch and the router, the persona and the firmware agent are all out of calls for the day —
so revising for a midterm silently broke the rest of the assistant.

That is not a cost problem, it is a design error. Marking a known answer against a spoken one
is string work, arithmetic and a little algebra. **None of it needs a language model**, and a
grader that runs locally also works on a plane, at 2 a.m. after the quota resets to nothing,
and in the harness with no key set.

LB's rule, in his words: *the question and answer knowledge is internal, so it does not have to
use an outside AI bot unless I ask for a further explanation.* This module is the first half.
`explain_locally` below is most of the second half — the model is reached only when LB asks to
be told MORE than the deck itself knows.

## Four graders, chosen by the item's kind

    mcq      by letter, AND by the text of the option — he is voice-first and will say
             "two x", not "B"
    numeric  by magnitude with a tolerance, and by RANGE when the deck's answer is one
             ("Around 1.8V to 2.0V" is a real answer in his existing bank)
    short    normalised match, then sympy, then term coverage
    prose    content-word coverage, because a philosophy answer is never retyped verbatim

## The three things that were hard, written down so they are not re-derived

**1. Spoken maths does not look like written maths.** Whisper turns "V equals I times R" into
exactly that, and no amount of string comparison makes it equal `V = I * R`. So `_spoken_maths`
rewrites the spoken forms — "equals", "times", "over", "squared", "pi" — before sympy sees it.
Without that step every formula answer LB SAYS is marked wrong while every one he TYPES is
marked right, which would look like the quiz hating his microphone.

**2. `simplify` is unbounded work.** Sympy will happily spend a minute on an expression, on the
turn path, with the microphone shut. So the input is length-capped and character-whitelisted,
`expand` is tried first because it settles almost everything at a fraction of the cost, and
`simplify` is the fallback rather than the plan.

**3. Coverage without a negation check marks "free will exists" correct against "free will does
not exist".** Both contain the same content words; the only difference is the one word a
stopword list would throw away. So negation parity is checked separately and disagreement
downgrades the verdict. This is the check that makes the prose grader safe to point at
philosophy, and it is why `not`, `never` and `cannot` are absent from `_STOPWORDS`.

## What it will not do

It will not mark a wrong answer right to be encouraging, and it will not claim certainty it
does not have. `partial` is a real verdict with its own sentence, because "you have the idea and
you are missing a term" is the true thing to say and rounding it to either pass or fail is a
lie in one direction or the other. `Grade.confidence` says how sure the match was, and the quiz
uses a low confidence on a prose item as its cue to show LB the official answer rather than
just a verdict.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

LOG = logging.getLogger("oddball.quiz")

__all__ = ["Grade", "grade", "explain_locally", "normalise", "looks_like_pass"]

# ---------------------------------------------------------------------------------------
# Thresholds. Collected here rather than buried in four functions, because they are the
# behaviour of this module and they are the thing that gets tuned after LB uses it.
# ---------------------------------------------------------------------------------------

# Fraction of the official answer's content words that must appear for a prose answer to pass.
# 0.6 rather than 0.8: an essay-style answer said out loud carries the ideas and drops the
# connective words, and marking that wrong teaches him to recite rather than to understand.
PROSE_PASS = 0.60
PROSE_PARTIAL = 0.30

# Short answers are terms and formulas, so more of them must be there.
SHORT_PASS = 0.80
SHORT_PARTIAL = 0.50

# `difflib` similarity at which two short strings are the same answer spelled differently.
# "kirchoff" vs "kirchhoff", "capacitence" vs "capacitance" — spelling is not the thing being
# tested, and Whisper's spelling of a technical word is not LB's fault.
FUZZY_PASS = 0.86

# Numeric agreement. The relative one carries almost every case; the absolute one exists so an
# answer of 0 can be matched at all, since everything is within 1% of zero and nothing is.
NUMERIC_REL_TOL = 0.01
NUMERIC_ABS_TOL = 1e-9

# The cap on what reaches sympy. A spoken answer is short; anything longer than this is prose
# that happens to contain an equals sign, and parsing it is a cost with no upside.
MAX_SYMPY_CHARS = 160


@dataclass
class Grade:
    """The verdict on one answer, and everything the quiz needs to speak about it.

    Args:
        verdict:     "correct", "partial" or "incorrect".
        confidence:  0.0-1.0. How well the match held. A LOW confidence on a pass is the quiz's
                     cue to show the official answer as well as the verdict.
        why:         one sentence, already fit to say out loud.
        method:      which grader decided it — "letter", "numeric", "symbolic", "coverage"...
                     Not spoken. It is what makes a wrong mark debuggable from `oddball.log`
                     instead of from a guess.
        missing:     content words the official answer had and his did not. Empty for a pass.
    """

    verdict: str
    confidence: float
    why: str
    method: str = ""
    missing: list = field(default_factory=list)

    @property
    def correct(self) -> bool:
        return self.verdict == "correct"

    @property
    def scored(self) -> float:
        """What this answer is worth. A partial is worth half, and the score says so."""
        return {"correct": 1.0, "partial": 0.5}.get(self.verdict, 0.0)


# ---------------------------------------------------------------------------------------
# Normalising
# ---------------------------------------------------------------------------------------

# Words carrying no content. `not`, `never`, `no`, `cannot` and `without` are deliberately NOT
# in here — see the module docstring. Throwing away a negation is how a grader marks the exact
# opposite of the right answer correct.
_STOPWORDS = frozenset("""
a an the this that these those of in on at to for from by with as is are was were be been
being it its and or but if then than so such there here what which who whom whose when where
how why do does did done have has had will would shall should can could may might must about
into over under again further once i you he she they we me him her them my your his their our
""".split())

# What a spoken formula turns into on the way to sympy. Ordered longest-first inside each group
# so "greater than or equal to" is not eaten by "greater than". Applied on word boundaries, so
# "times" inside "sometimes" is left alone.
_SPOKEN_MATHS: tuple[tuple[str, str], ...] = (
    ("is equal to", "="), ("equals to", "="), ("equal to", "="), ("equals", "="),
    ("multiplied by", "*"), ("times", "*"), ("divided by", "/"), ("over", "/"),
    ("plus or minus", "+-"), ("plus", "+"), ("minus", "-"), ("negative", "-"),
    ("to the power of", "**"), ("squared", "**2"), ("cubed", "**3"),
    ("square root of", "sqrt"), ("the square root of", "sqrt"), ("root of", "sqrt"),
    ("pi", "pi"), ("infinity", "oo"),
    # LAST, and after every phrase that contains it. A bare "is" is how a law is usually
    # stated out loud — "power is voltage times current" — and without it that answer reaches
    # sympy as `p is v * i`, which is a syntax error rather than an equation. Safe to be this
    # greedy because `_looks_algebraic` runs on the RESULT: "resistance is the opposition to
    # current flow" becomes "r = the opposition to i flow", is rejected for its long words, and
    # goes to the text graders where it belongs.
    ("is", "="),
)

# Spoken digits. Whisper writes "two x", never "2x", so without this every coefficient LB SAYS
# fails to parse and the same answer typed passes — the microphone deciding the mark.
#
# "one" is here and "a" is NOT: "a over b" is a fraction, but "a" is also the commonest symbol
# name in an algebra answer, and turning it into 1 would silently rewrite the question.
_NUMBER_WORDS: tuple[tuple[str, str], ...] = (
    ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"), ("four", "4"), ("five", "5"),
    ("six", "6"), ("seven", "7"), ("eight", "8"), ("nine", "9"), ("ten", "10"),
    ("eleven", "11"), ("twelve", "12"), ("half", "(1/2)"), ("third", "(1/3)"),
    ("quarter", "(1/4)"),
)

# Named quantities, as their symbols. **This is the table that makes a SPOKEN law markable.**
#
# `tools/verify_engine.py` found the hole: the answer "voltage equals current times resistance"
# scored zero against a stored `V = I * R`. Every content word had been thrown away by the
# length filter — `v`, `i` and `r` are one character each — so coverage was 0/0 and difflib was
# comparing a nine-character string with a thirty-nine-character one. The old Gemini grader
# handled this case, and losing it would have been a real regression rather than a saving.
#
# Everything is lowercased by `_spoken_maths` before this applies, which quietly solves two
# problems: `R`/`r` and `V`/`v` cannot collide with each other across the two sides, and
# "current" becomes the plain symbol `i` rather than sympy's imaginary unit `I`.
_QUANTITY_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("potential difference", "v"), ("electromotive force", "v"), ("voltage", "v"), ("emf", "v"),
    ("current", "i"), ("resistance", "r"), ("resistivity", "rho"), ("conductance", "g"),
    ("impedance", "z"), ("reactance", "x"), ("capacitance", "c"), ("inductance", "l"),
    ("power", "p"), ("charge", "q"), ("energy", "e"), ("work", "w"),
    ("frequency", "f"), ("period", "t_p"), ("wavelength", "lam"),
    ("time", "t"), ("force", "f_n"), ("mass", "m"), ("acceleration", "a"),
    ("velocity", "u"), ("speed", "u"), ("displacement", "d"), ("distance", "d"),
    ("momentum", "p_m"), ("radius", "r_a"), ("area", "a_r"), ("height", "h"),
)

# Characters sympy is allowed to see. A whitelist rather than a blacklist: this is a parser
# being handed a transcript, and `parse_expr` is only as safe as what reaches it.
_SYMPY_SAFE = re.compile(r"^[A-Za-z0-9_+\-*/^().,= \t]+$")

# The function names an algebraic answer may legitimately spell out. Every OTHER long word is
# evidence that the string is English rather than maths — see `_looks_algebraic`.
_MATH_WORDS = frozenset({"sqrt", "sin", "cos", "tan", "sec", "csc", "cot", "log", "exp",
                         "asin", "acos", "atan", "sinh", "cosh", "tanh", "abs"})


def _looks_algebraic(text: str) -> bool:
    """True when `text` is an expression rather than a sentence.

    **The hyphen is why this function exists.** The first gate here was "contains one of
    `+-*/^=` or a digit", and a hyphen satisfies it — so "Inter-Integrated Circuit" was handed
    to sympy, parsed as `inter - integrated*circuit` by the implicit-multiplication transform,
    compared against a differently-parsed spelling of the same words, and marked WRONG. Every
    hyphenated technical term in the bank would have graded that way.

    So a bare hyphen no longer qualifies (it is punctuation far more often than subtraction),
    and any long word that is not a known function name disqualifies the string outright. What
    is left is maths: `V = I*R`, `x**2/2 + C`, `9.81`.
    """
    if not any(c in text for c in "=+*/^0123456789"):
        return False
    return all(word in _MATH_WORDS for word in re.findall(r"[a-z]{4,}", text.lower()))


def normalise(text: str) -> str:
    """Lowercase, punctuation-stripped, single-spaced. The form everything else compares in."""
    flat = (text or "").lower().replace("’", "'")
    flat = re.sub(r"[^a-z0-9+\-*/^=.<>' ]+", " ", flat)
    return re.sub(r"\s+", " ", flat).strip()


def _stem(word: str) -> str:
    """Crudest possible stemmer: enough that 'derivatives' matches 'derivative'.

    Deliberately not a real stemmer. `nltk` or `snowballstemmer` would be a new dependency and
    a download, for the sake of the four endings that actually turn up when someone answers a
    question out loud. The guard on length is what stops it eating short words — "is" must not
    become "i", and "gas" must not become "ga".
    """
    for suffix in ("ies", "ing", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _content_words(text: str) -> list[str]:
    """The words that carry the meaning, stemmed, in order and deduplicated."""
    seen, out = set(), []
    for word in normalise(text).split():
        if word in _STOPWORDS or len(word) < 2:
            continue
        stem = _stem(word)
        if stem not in seen:
            seen.add(stem)
            out.append(stem)
    return out


_NEGATIONS = frozenset({"not", "no", "never", "cannot", "cant", "isnt", "arent", "doesnt",
                        "dont", "without", "nothing", "none", "neither", "nor", "false"})


def _negation_parity(text: str) -> bool:
    """True when `text` reads as a negation.

    Counted rather than detected, because "it is not untrue" is two negations and one meaning.
    Crude, and crude is the right amount of machinery here: the case worth catching is one
    `not` on one side and none on the other, which is the difference between an answer and its
    opposite.
    """
    words = set(re.sub(r"[^a-z ]+", " ", (text or "").lower()).split())
    return len(words & _NEGATIONS) % 2 == 1


# ---------------------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------------------

_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")

# Multipliers a value can carry. Voice-first, so the spoken word is here beside the symbol —
# "four point seven kilohms" and "4.7k" have to reach the same number.
_SCALES: tuple[tuple[str, float], ...] = (
    ("nano", 1e-9), ("micro", 1e-6), ("milli", 1e-3), ("centi", 1e-2),
    ("kilo", 1e3), ("mega", 1e6), ("giga", 1e9), ("tera", 1e12),
    ("thousand", 1e3), ("million", 1e6), ("billion", 1e9),
)
_SCALE_SYMBOLS: tuple[tuple[str, float], ...] = (
    ("n", 1e-9), ("u", 1e-6), ("µ", 1e-6), ("μ", 1e-6), ("m", 1e-3),
    ("k", 1e3), ("K", 1e3), ("M", 1e6), ("G", 1e9),
)


def _numbers_in(text: str) -> list[float]:
    """Every number in `text`, with its scale prefix applied. Empty when there are none.

    The scale is read from what FOLLOWS the digits, which is where it is in both "4.7k" and
    "4.7 kilohms".

    **A symbol prefix must be followed by a UNIT or by nothing**, and that rule is what stops
    the two failure modes either side of it. Without the "or by nothing", `4.7k` is four point
    seven and `4700` marks as a thousand times out — measured, it was the first bug the harness
    found. Without the "must be a unit", `9.8 m/s^2` becomes 0.0098 and every kinematics answer
    is wrong; so is `5 metres`, whose "m" is a word rather than a prefix. A unit is short — mA,
    kΩ, kohms — so the remainder after the symbol is capped at four characters, which admits
    every real unit and rejects every English word that starts with one of these letters.
    """
    out: list[float] = []
    for match in _NUMBER.finditer(text or ""):
        try:
            value = float(match.group())
        except ValueError:                                       # pragma: no cover - regex
            continue
        tail = (text[match.end():match.end() + 16] or "").strip()
        low = tail.lower()
        for word, factor in _SCALES:
            if low.startswith(word):
                value *= factor
                break
        else:
            for symbol, factor in _SCALE_SYMBOLS:
                if not tail.startswith(symbol):
                    continue
                rest = tail[len(symbol):].strip()
                unit = re.match(r"^[A-Za-zΩµμ°%]{0,4}\b", rest)
                if not rest or (unit and unit.group() and len(rest.rstrip(".,;:")) <= 4):
                    value *= factor
                break
        out.append(value)
    return out


def _close(a: float, b: float) -> bool:
    """Two numbers agreeing to within the tolerance. Handles zero, which relative tolerance
    alone cannot: everything is within 1% of zero and nothing is."""
    return abs(a - b) <= max(NUMERIC_ABS_TOL, NUMERIC_REL_TOL * max(abs(a), abs(b)))


# "1.8 to 2.0", "1.8-2.0 V", "between 1.8 and 2.0", and — the one that matters — "1.8V to 2.0V",
# which is the shape of the LED forward-drop answer already sitting in `quiz_data.json`. The
# unit between the number and the connector is why this is not `\s*`: the first version required
# whitespace there, so the one range answer in LB's actual bank did not match its own regex.
_RANGE = re.compile(r"([+-]?\d+\.?\d*)\s*[A-Za-zΩµμ°%]{0,4}\s*(?:to|-|–|and)\s*"
                    r"([+-]?\d+\.?\d*)", re.I)


def _grade_numeric(official: str, given: str) -> Grade | None:
    """Mark a number against a number, or None when this grader does not apply."""
    theirs = _numbers_in(given)
    if not theirs:
        return None

    span = _RANGE.search(official)
    if span:
        low, high = sorted((float(span.group(1)), float(span.group(2))))
        # Widened by the tolerance at both ends, so an answer AT the boundary passes. "2.0" for
        # a range that stops at 2.0 is right, and floating point should not be what decides it.
        pad = max(NUMERIC_ABS_TOL, NUMERIC_REL_TOL * max(abs(low), abs(high)))
        if any(low - pad <= value <= high + pad for value in theirs):
            return Grade("correct", 0.95, f"That is inside the expected range, {official}.",
                         "numeric-range")
        return Grade("incorrect", 0.95, f"Not quite — the expected range is {official}.",
                     "numeric-range")

    ours = _numbers_in(official)
    if not ours:
        return None

    # Their FIRST number against ours. Not "any of theirs", which would mark "either 3 or 7"
    # correct against an answer of 7 — a guess covering the field is not an answer.
    if _close(theirs[0], ours[0]):
        return Grade("correct", 0.97, f"{official} — that is the number.", "numeric")

    # Right digits, wrong scale. Worth its own sentence: it is the single most common real
    # mistake in an engineering answer, and "incorrect" alone teaches him nothing about which
    # kind of wrong it was.
    if ours[0] and theirs[0]:
        ratio = abs(theirs[0] / ours[0])
        for factor, name in ((1e3, "a thousand"), (1e6, "a million"), (1e-3, "a thousandth"),
                             (1e-6, "a millionth")):
            if _close(ratio, factor):
                return Grade("partial", 0.9,
                             f"The digits are right and the scale is not — you are out by "
                             f"{name}. The answer is {official}.", "numeric-scale")
    return Grade("incorrect", 0.95, f"Not quite. The answer is {official}.", "numeric")


# ---------------------------------------------------------------------------------------
# Algebra
# ---------------------------------------------------------------------------------------

def _spoken_maths(text: str) -> str:
    """Rewrite a spoken formula into something sympy can parse.

    "V equals I times R" -> "V = I * R". See the module docstring for why this is not optional:
    without it, every formula LB SAYS is marked wrong and every one he TYPES is marked right.
    """
    flat = " " + (text or "").lower() + " "
    # Operators, then digits, then quantity names. The order matters only in that the operator
    # words must go first — "times" has to become `*` before anything looks for a symbol next
    # to it — and the quantity table must go last, so it substitutes into an expression that is
    # already shaped like one.
    for word, symbol in _SPOKEN_MATHS + _NUMBER_WORDS + _QUANTITY_SYMBOLS:
        flat = re.sub(rf"(?<![a-z]){re.escape(word)}(?![a-z])", f" {symbol} ", flat)
    return re.sub(r"\s+", " ", flat).strip()


def _sympy_verdict(official: str, given: str) -> str:
    """What sympy makes of two expressions: "equal", "constant", "different", or "" for no idea.

    The empty string is a real answer and not a failure: an unparseable expression falls through
    to the coverage grader, which is a worse grader for algebra and a perfectly good one for
    everything else. Returning "different" instead would mark an answer wrong because SYMPY
    could not read it, which is the machine blaming LB for its own limits.

    **"constant" is the `+ C` case**, and it is here because it is the single most common way to
    be nearly right in a calculus class. `x**2/2` against `x**2/2 + C` differs by a constant:
    that is one specific, nameable mistake and it deserves a partial and a sentence naming it,
    not a flat "incorrect" that makes him re-derive an integral he had already done.
    """
    left, right = _spoken_maths(official), _spoken_maths(given)
    if not left or not right:
        return ""
    if len(left) > MAX_SYMPY_CHARS or len(right) > MAX_SYMPY_CHARS:
        return ""
    if not (_SYMPY_SAFE.match(left) and _SYMPY_SAFE.match(right)):
        return ""
    # BOTH sides must look like maths. An English answer to an algebraic question falls through
    # to the text graders rather than being parsed as a product of single-letter symbols — a
    # verdict from sympy on a sentence it mis-parsed is worse than no verdict at all.
    if not (_looks_algebraic(left) and _looks_algebraic(right)):
        return ""

    try:
        import sympy                                                 # noqa: PLC0415
        from sympy.parsing.sympy_parser import (                     # noqa: PLC0415
            convert_xor, implicit_multiplication_application, parse_expr,
            standard_transformations)
    except Exception:                                                # noqa: BLE001
        LOG.debug("sympy unavailable — falling back to text comparison", exc_info=True)
        return ""

    transforms = standard_transformations + (implicit_multiplication_application, convert_xor)

    def parse(text: str):
        # An equation is graded as the difference of its sides, so "V = I*R" and "I*R = V" and
        # "V - I*R = 0" are one answer. Sympy's `Eq` would compare them structurally and call
        # those three different, which is not what anyone means by a right answer.
        if text.count("=") == 1:
            a, b = text.split("=")
            return parse_expr(a, transformations=transforms) - \
                parse_expr(b, transformations=transforms)
        if "=" in text:
            return None
        return parse_expr(text, transformations=transforms)

    try:
        a, b = parse(left), parse(right)
        if a is None or b is None:
            return ""
        # `expand` first: it settles almost every real case and costs a fraction of `simplify`,
        # which is unbounded work on the turn path with the microphone shut.
        difference = sympy.expand(a - b)
        if difference == 0 or sympy.simplify(a - b) == 0:
            return "equal"

        # An EQUATION is not its difference, and this is where that bites. `V = I*R` becomes
        # `V - I*R`; `R = V/I` becomes `R - V/I`. Those do not subtract to zero and they are
        # the SAME LAW rearranged — which is exactly what a student is supposed to be able to
        # do, so marking it wrong would punish him for knowing the material better.
        #
        # So two equations are compared by what they SOLVE TO. Pick a symbol they share, solve
        # both for it, and compare the solution sets: `V - I*R` and `R - V/I` both give
        # V = I*R. `V - I*R` and `P - I**2*R` do not, and stay wrong.
        if "=" in left and "=" in right:
            shared = sorted(a.free_symbols & b.free_symbols, key=str)
            for symbol in shared[:2]:
                try:
                    ours = {sympy.simplify(s) for s in sympy.solve(a, symbol)}
                    theirs = {sympy.simplify(s) for s in sympy.solve(b, symbol)}
                except Exception:                                    # noqa: BLE001
                    continue
                if ours and ours == theirs:
                    return "equal"

        # Off by a constant and nothing else — the dropped `+ C`. Two shapes, and the second is
        # the one that actually happens: `x**2/2 + C` minus `x**2/2` is the SYMBOL C, not a
        # number, so an `is_number` test alone misses the exact case this branch is for.
        # Required to sit alongside real symbols either side, so `4` against `7` stays a wrong
        # number rather than "you forgot a constant of integration".
        if (a.free_symbols or b.free_symbols) and difference != 0:
            if difference.is_number or difference.is_Symbol or (-difference).is_Symbol:
                return "constant"

        # Both parsed, neither test matched: a genuine disagreement rather than a parser
        # failure, which is the distinction the "" return exists to preserve.
        return "different"
    except Exception:                                                # noqa: BLE001
        LOG.debug("sympy could not compare %r and %r", left, right, exc_info=True)
        return ""


# ---------------------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------------------

def _coverage(official: str, given: str) -> tuple[float, list[str]]:
    """How much of the official answer's meaning is present. Returns (0-1, missing words)."""
    wanted = _content_words(official)
    if not wanted:
        return 0.0, []
    got = set(_content_words(given))
    missing = [w for w in wanted if w not in got]
    return (len(wanted) - len(missing)) / len(wanted), missing


def _similar(official: str, given: str) -> float:
    """difflib ratio on the normalised strings. Catches a misspelling; catches nothing else."""
    return difflib.SequenceMatcher(None, normalise(official), normalise(given)).ratio()


def _grade_text(official: str, given: str, prose: bool) -> Grade:
    """Mark words against words. The fallback every other grader falls through to."""
    left, right = normalise(official), normalise(given)
    if left and left == right:
        return Grade("correct", 1.0, "That is exactly it.", "exact")

    ratio = _similar(official, given)
    if not prose and ratio >= FUZZY_PASS:
        return Grade("correct", ratio,
                     f"Right — spelled a little differently to my copy: {official}.", "fuzzy")

    covered, missing = _coverage(official, given)
    passes = PROSE_PASS if prose else SHORT_PASS
    partial = PROSE_PARTIAL if prose else SHORT_PARTIAL
    score = max(covered, ratio)
    method = "coverage"

    # The check that makes this safe to point at philosophy. "Free will exists" and "free will
    # does not exist" share every content word; the only difference is the word a stopword list
    # throws away. A disagreement here can never be a pass.
    if _negation_parity(official) != _negation_parity(given):
        if score >= partial:
            return Grade("partial", 0.5,
                         f"You have the right ideas but you have stated the opposite of the "
                         f"answer. It is: {official}", "negation")
        return Grade("incorrect", 0.8, f"Not quite — that is the opposite. The answer is: "
                     f"{official}", "negation")

    if score >= passes:
        note = "" if not missing else f" You did not mention {', '.join(missing[:3])}."
        return Grade("correct", score, f"Correct.{note}", method, missing)
    if score >= partial:
        return Grade("partial", score,
                     f"Part of it. The answer is: {official}", method, missing)
    return Grade("incorrect", 1.0 - score, f"Not quite. The answer is: {official}",
                 method, missing)


# ---------------------------------------------------------------------------------------
# Multiple choice
# ---------------------------------------------------------------------------------------

# "B", "b)", "letter B", "the answer is B", "I'll go with C". Anchored on a WORD boundary so
# the "a" in "a capacitor" is not read as option A — which it was, in the first version, and it
# marked every worded answer as a guess of A.
_LETTER = re.compile(r"(?:^|\b)(?:option\s+|letter\s+|answer\s+is\s+|choice\s+)?"
                     r"([a-eA-E])\s*[).:,]?(?:\s|$)")


def _chosen_letter(given: str, choices: dict) -> str:
    """Which option LB picked, or "" when he did not name one.

    Two ways in, and the second matters more than the first for a voice-first quiz. He will
    read the OPTION back — "the derivative is two x" — far more often than he will say "B", and
    a grader that only understands letters marks the better answer wrong.
    """
    text = (given or "").strip()

    # A bare letter, or a letter with the usual decoration. Only when the utterance is short:
    # in a full sentence, a stray "a" or "i" is a word, not a choice.
    if len(text.split()) <= 4:
        match = _LETTER.search(text)
        if match and match.group(1).upper() in choices:
            return match.group(1).upper()

    # "option b", "letter c", "answer is d" — safe to look for anywhere, because those words
    # do not appear by accident.
    labelled = re.search(r"\b(?:option|letter|choice)\s+([a-eA-E])\b", text, re.I)
    if labelled and labelled.group(1).upper() in choices:
        return labelled.group(1).upper()

    # An option that is ALGEBRAICALLY what he said. This is the case the string scorers below
    # cannot reach: "two x" and "2x" share two characters out of five, so difflib rates them at
    # 0.57 and coverage at zero — yet they are the same answer, and reading the option aloud is
    # what a voice-first user does instead of saying "B".
    for letter, option in choices.items():
        if _sympy_verdict(option, text) == "equal":
            return letter

    # By the text of the option. Scored against every choice and the BEST one wins, rather than
    # the first over a threshold — options in a real exam are deliberately similar, and taking
    # the first match would hand him whichever distractor happened to be listed earliest.
    best, best_score = "", 0.0
    for letter, option in choices.items():
        covered, _ = _coverage(option, text)
        score = max(covered, _similar(option, text))
        if score > best_score:
            best, best_score = letter, score
    return best if best_score >= 0.75 else ""


def _grade_mcq(official: str, given: str, choices: dict) -> Grade:
    """Mark a multiple-choice answer by letter or by the option's own text."""
    want = (official or "").strip().upper()
    if want not in choices:
        # The deck stores the ANSWER TEXT rather than a letter — a legitimate shape, and the
        # one an importer produces when the paper's key spells the answer out. Find its letter.
        match = next((letter for letter, text in choices.items()
                      if normalise(text) == normalise(official)), "")
        want = match or want

    picked = _chosen_letter(given, choices)
    if not picked:
        # No option identified. Fall through to the text graders against the correct option's
        # words, so a right answer phrased in his own words still passes.
        target = choices.get(want, official)
        graded = _grade_text(target, given, prose=False)
        graded.method = f"mcq-{graded.method}"
        # The LETTER, put back. `_grade_text` only ever saw the option's words, so it says "the
        # answer is: Socrates" — true, and missing the one thing he needs to mark his own paper
        # against the sheet in front of him.
        if want in choices and graded.verdict != "correct":
            graded.why = graded.why.replace(f"answer is: {target}", f"answer is {want}, {target}")
        return graded

    if picked == want:
        text = choices.get(want, "")
        return Grade("correct", 0.98, f"Correct — {want}{', ' + text if text else ''}.",
                     "mcq-letter")

    theirs = choices.get(picked, "")
    right = choices.get(want, official)
    return Grade("incorrect", 0.95,
                 f"You went with {picked}{', ' + theirs if theirs else ''}. The answer is "
                 f"{want}{', ' + right if right else ''}.", "mcq-letter")


# ---------------------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------------------

# What "I have no idea" sounds like. Marked incorrect, but with a sentence that does not
# pretend he got it wrong — he said he did not know, and telling him "not quite" in reply is
# the machine not listening.
_DONT_KNOW = ("i dont know", "i do not know", "no idea", "not sure", "dunno", "no clue",
              "i dont remember", "i do not remember", "pass", "skip", "next one", "i give up",
              "give up", "beats me", "haven't a clue", "havent a clue", "cant remember",
              "cannot remember", "forgot", "no clue at all")


def _is_dont_know(text: str) -> bool:
    """True when LB said he does not know, however he phrased it.

    Containment for a SHORT utterance, not a prefix match. "I have no idea" contains "no idea"
    and starts with neither it nor any other entry — the harness caught it being marked as a
    wrong answer to the question, with "Not quite" in reply to a man who had just said he did
    not know. The length cap is what keeps a real answer that happens to contain "not sure"
    from being read as a shrug.
    """
    flat = normalise(text)
    if not flat:
        return False
    if any(flat == phrase for phrase in _DONT_KNOW):
        return True
    return len(flat.split()) <= 6 and any(phrase in flat for phrase in _DONT_KNOW)


def grade(item, given: str) -> Grade:
    """Mark one answer. **Never raises, never touches the network.**

    Args:
        item:  a `quiz_bank.QuizItem`, or any object with `.answer`, `.kind` and `.choices`.
               Duck-typed rather than imported, so the harness can pass a stub and this module
               does not depend on the bank.
        given: what LB said or typed.

    Returns:
        A `Grade`. On an internal error it returns an honest "I could not mark that" rather
        than a verdict, because a made-up mark is worse than an admitted failure — this runs
        inside a turn and an exception here would drop him out of quiz mode entirely.
    """
    official = str(getattr(item, "answer", "") or "").strip()
    kind = str(getattr(item, "kind", "") or "short").lower()
    choices = dict(getattr(item, "choices", {}) or {})
    given = (given or "").strip()

    if not given:
        return Grade("incorrect", 1.0, f"I did not catch an answer. It is: {official}",
                     "empty")
    if not official:
        return Grade("partial", 0.0,
                     "I have that question with no answer stored against it, so I cannot mark "
                     "it.", "no-answer")

    if _is_dont_know(given):
        return Grade("incorrect", 1.0, f"No problem. The answer is: {official}", "dont-know")

    try:
        if choices or kind == "mcq":
            return _grade_mcq(official, given, choices)

        if kind == "numeric":
            numeric = _grade_numeric(official, given)
            if numeric is not None:
                return numeric

        if kind in ("short", "numeric"):
            # Sympy BEFORE the text graders, because "V=IR" and "R times I equals V" are the
            # same answer and share barely a character. After the numeric grader, because
            # "1.8 to 2.0" is a range and not an expression.
            algebra = _sympy_verdict(official, given)
            if algebra == "equal":
                return Grade("correct", 0.99,
                             f"Correct — that is the same as {official}.", "symbolic")
            if algebra == "constant":
                return Grade("partial", 0.85,
                             f"The shape is right and you are out by a constant — check for a "
                             f"dropped term or a missing constant of integration. It is "
                             f"{official}.", "symbolic-constant")
            if algebra == "different":
                # Sympy PARSED both and they are not equal, so it gets the last word here.
                # Letting the text graders overrule it was a measured bug: "V = 0" scores 0.55
                # on difflib against "V = I * R" — five of nine characters — and was marked a
                # partial credit. Character similarity between two formulas is noise.
                return Grade("incorrect", 0.97, f"Not that one. The answer is {official}.",
                             "symbolic")

        return _grade_text(official, given, prose=(kind == "prose"))
    except Exception:                                                # noqa: BLE001
        LOG.exception("grading failed for %r", official[:60])
        return Grade("partial", 0.0,
                     f"Something went wrong marking that, so I will not guess. The answer is: "
                     f"{official}", "error")


def looks_like_pass(text: str) -> bool:
    """True when LB is asking to move on rather than answering. Used by the quiz turn."""
    return _is_dont_know(text)


# ---------------------------------------------------------------------------------------
# Explaining, without a model
# ---------------------------------------------------------------------------------------

def explain_locally(item, grade_result=None) -> str:
    """Everything the DECK knows about this question, as prose. "" when it knows nothing extra.

    This is the second half of LB's rule. He asked for the knowledge to be internal and for the
    outside model to be reached only when he wants MORE explanation — so "explain that" is
    answered from here first, and only falls through to `agents/quiz_agent.py` when this
    returns "". A practice paper that shipped its worked solutions therefore costs zero API
    calls to revise from, which is the case worth optimising because it is the common one.
    """
    parts: list[str] = []
    answer = str(getattr(item, "answer", "") or "").strip()
    choices = dict(getattr(item, "choices", {}) or {})
    explanation = str(getattr(item, "explanation", "") or "").strip()

    if choices and answer.upper() in choices:
        parts.append(f"The answer is {answer.upper()}: {choices[answer.upper()]}.")
    elif answer:
        parts.append(f"The answer is {answer}.")

    if explanation:
        parts.append(explanation)

    if grade_result is not None and getattr(grade_result, "missing", None):
        missing = list(grade_result.missing)[:5]
        parts.append("What your answer did not mention: " + ", ".join(missing) + ".")

    source = str(getattr(item, "source", "") or "")
    if source:
        page = getattr(item, "page", 0)
        parts.append(f"This one came from {source}" + (f", page {page}." if page else "."))

    # An explanation that is ONLY the answer is not an explanation — the quiz has already said
    # that sentence. Returning "" here is what routes LB to the model, which is exactly what he
    # asked for in the case where the deck genuinely has nothing more to give.
    if not explanation and not (grade_result is not None and getattr(grade_result, "missing",
                                                                     None)):
        return ""
    return " ".join(parts)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse                                                  # noqa: PLC0415
    import sys                                                       # noqa: PLC0415

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="mark one answer, locally")
    ap.add_argument("official", help="the correct answer")
    ap.add_argument("given", help="what the student said")
    ap.add_argument("--kind", default="", choices=["", *("mcq numeric short prose".split())])
    args = ap.parse_args(argv)

    from tools.quiz_bank import infer_kind                           # noqa: PLC0415

    class _Item:
        answer = args.official
        kind = args.kind or infer_kind(args.official)
        choices: dict = {}

    result = grade(_Item(), args.given)
    print(f"\n  {result.verdict.upper()}  (confidence {result.confidence:.2f}, "
          f"via {result.method})")
    print(f"  {result.why}\n")
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
