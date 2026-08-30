#!/usr/bin/env python3
"""
Module:  calc.py
Purpose: Tier 0 — arithmetic, answered locally, offline, and correctly.
Author:  LB
Date:    2026-08-13

    python -m orchestrator.calc "whats five plus five"

(`-m`, not a path, for the same reason `classify.py` says so: this imports from the
`orchestrator` package, and running it as a plain script puts `orchestrator/` on sys.path
instead of the repo root.)

## Why this exists

He was sending sums to Google. Measured live on 2026-08-13, both of these classified as
`ask (no rule matched)`, which means he spoke "I'd have to look that one up online. Want me
to?", captured a second utterance, transcribed it, and only then made a network round trip:

    'with five plus five.'            -> cloud -> 'That is ten, LB!'       0.70s
    "It's been 1000 less than 1000."  -> cloud -> 'That sounds like zero'  5.80s

**He asked permission to go online to add five and five.** That is the exact case D2 and D32
exist to kill — the cheapest tier that can serve a question should serve it. Arithmetic has one
right answer, needs no model, no clock and no network, and it is the cheapest thing in the
building. It is also a privacy leak: every sum LB says out loud currently leaves the house.

## This parses the RAW transcript, and that is not a preference

`router.normalise()` strips everything outside `[a-z0-9 ]`, which is right for intent matching
and fatal for arithmetic. Measured:

    '3.5 plus 2'   -> '35 plus 2'    the decimal point is DELETED: 3.5 becomes 35
    '5+5'          -> '55'
    '2^8'          -> '28'
    '15% of 80'    -> '15 of 80'
    '-7 plus 2'    -> '7 plus 2'

A calculator built on normalised text answers `3.5 + 2` as **37**: fluently, instantly and
wrongly, which is the precise D30/D31 failure this whole architecture is built to prevent. So
this module takes the raw string and does its own preparation. `normalise()` is left alone —
475 checks depend on its current behaviour and its own job is unaffected.

## Refusing is a feature

`evaluate()` returns **None** for anything that is not arithmetic, and the router falls through
to the tiers that already work. The rule that makes this safe is that **an operator is
required**: "set a timer for five minutes" has a number and no operator, so `timer` still wins.
Claiming a question it cannot answer would be worse than useless, because a claimed question
never escalates.

## Never eval()

The expression is evaluated by walking an `ast` tree with a strict node whitelist — no `Call`,
no `Name`, no `Attribute`, no subscript. `sqrt` is a prefix transform rather than a function
call precisely so `Call` never has to be allowed. `Pow` is bounded *before* evaluation, because
`9**9**9` does not raise, it just never comes back.
"""

from __future__ import annotations

import ast
import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache

LOG = logging.getLogger("oddball.calc")


@dataclass(frozen=True)
class Result:
    """One computed answer.

    `value` and `expression` are not decoration: `tools/verify_calc.py` recomputes the
    expression independently and asserts it agrees with `value`, and that the number in
    `spoken` is the number the arithmetic produced. That is the same contract
    `formulas.Worked` has, for the same reason — a hand-typed answer in a table teaches LB
    the wrong number without anything going red.
    """

    spoken: str          # exactly what he says
    value: float         # the answer
    expression: str      # the canonical infix form that produced it, for the log


# --------------------------------------------------------------------------------------
# Numbers. Whisper emits words, digits, and mixtures of both — "with five plus five" is
# words, "It's been 1000 less than 1000" is digits, and both are real transcripts from the
# same evening. Everything below has to survive either.
# --------------------------------------------------------------------------------------

_UNITS = {
    "zero": 0, "oh": 0, "nought": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}

# Spoken fractions, as multipliers. "a half" and "one half" both reach here.
_FRACTIONS = {"half": 0.5, "halves": 0.5, "third": 1 / 3, "thirds": 1 / 3,
              "quarter": 0.25, "quarters": 0.25, "fourth": 0.25}

_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_SCALES)


def _join_number_words(tokens: list[str]) -> list[str]:
    """Collapse runs of number words into digit strings.

    "twenty three" -> "23", "three hundred and five" -> "305", "a thousand" -> "1000".

    Written as an explicit accumulator rather than a regex because English number words are
    positional: "hundred" and "thousand" multiply what came before them, and "twenty three"
    adds while "two hundred" multiplies. A regex cannot see that structure.
    """
    out: list[str] = []
    current = 0          # the group being built, e.g. "three hundred"
    total = 0            # groups already closed by a scale word, e.g. the 3000 in "3005"
    active = False       # are we inside a run of number words?

    def flush() -> None:
        nonlocal current, total, active
        if active:
            out.append(str(total + current))
        current, total, active = 0, 0, False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # "a"/"an" only counts as 1 when a scale word follows it: "a thousand" is 1000, but
        # the "a" in "a plus b" is filler. Looking ahead is what tells them apart.
        if tok in ("a", "an") and i + 1 < len(tokens) and tokens[i + 1] in _SCALES:
            current, active = 1, True
            i += 1
            continue
        if tok in _UNITS:
            current += _UNITS[tok]
            active = True
        elif tok in _TENS:
            current += _TENS[tok]
            active = True
        elif tok in _SCALES:
            scale = _SCALES[tok]
            if not active:              # "hundred" with nothing in front of it means 100
                current = 1
                active = True
            if scale >= 1000:
                total = (total + current) * scale
                current = 0
            else:
                current *= scale
        elif tok == "and" and active:
            # "three hundred and five" — swallow the joiner, keep accumulating. A trailing
            # "and" that turns out not to continue a number is restored by the flush below.
            if i + 1 < len(tokens) and tokens[i + 1] in _NUMBER_WORDS:
                i += 1
                continue
            flush()
            out.append(tok)
        else:
            flush()
            out.append(tok)
        i += 1
    flush()
    return out


def _apply_fractions(tokens: list[str]) -> list[str]:
    """"two thirds" -> "(2/3)", "a half" -> "(1/2)", "half of 80" -> "(1/2)*80".

    Fractions are turned into parenthesised division rather than a decimal so the arithmetic
    stays exact for as long as possible — 1/3 as 0.333 would round twice.
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _FRACTIONS:
            denominator = round(1 / _FRACTIONS[tok])
            numerator = "1"
            # A number immediately before it is the numerator: "two thirds".
            if out and re.fullmatch(r"-?\d+(\.\d+)?", out[-1]):
                numerator = out.pop()
            elif out and out[-1] in ("a", "an", "one"):
                out.pop()
            out.append(f"({numerator}/{denominator})")
        else:
            out.append(tok)
        i += 1
    return out


# --------------------------------------------------------------------------------------
# Operators.
# --------------------------------------------------------------------------------------

# Straightforward infix forms: "a plus b" -> "a + b". Longest phrases first, because
# "divided by" must be consumed before a bare "by" and "to the power of" before "power".
_INFIX: tuple[tuple[str, str], ...] = (
    ("to the power of", "**"), ("raised to the power", "**"), ("to the power", "**"),
    ("multiplied by", "*"), ("divided by", "/"), ("divide by", "/"),
    ("times by", "*"), ("over", "/"), ("times", "*"), ("multiplied", "*"),
    ("plus", "+"), ("add", "+"), ("added to", "+"), ("and", "+"),
    ("minus", "-"), ("subtract", "-"), ("take away", "-"), ("less", "-"),
    ("modulo", "%%"), ("mod", "%%"), ("remainder of", "%%"),
    ("squared", "**2"), ("cubed", "**3"),
)

# Postfix/prefix forms handled before the infix table.
_SQRT = ("square root of", "the square root of", "square root", "root of")

# THE REVERSED FORMS. "5 less than 20" is 20 - 5, not 5 - 20, and getting this backwards is
# the single most likely way this module ships a confident wrong answer.
#
# It is not hypothetical: LB's real transcript on 2026-08-13 was "It's been 1000 less than
# 1000" — symmetric, so a sign error would have been invisible there. "What's 5 less than 20"
# is where it shows, and tools/verify_calc.py asserts BOTH the value and its sign.
#
# (regex, replacement) with the operands swapped. Applied before _INFIX, because "less than"
# has to be consumed before the bare "less" in the infix table matches it.
# `_NUM` carries exactly ONE capturing group, so two of them in a pattern are \1 and \2. The
# first draft wrote \3 (counting the non-capturing `(?:\.\d+)?` as if it captured) and every
# substitution raised "invalid group reference" — caught immediately because Tier 0 swallows
# the exception and returns None, which looks exactly like "not arithmetic".
_NUM = r"(-?\d+(?:\.\d+)?|\([^()]*\))"
_REVERSED: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(rf"{_NUM}\s+less than\s+{_NUM}"), r"(\2 - \1)"),
    (re.compile(rf"{_NUM}\s+fewer than\s+{_NUM}"), r"(\2 - \1)"),
    (re.compile(rf"{_NUM}\s+more than\s+{_NUM}"), r"(\2 + \1)"),
    (re.compile(rf"{_NUM}\s+greater than\s+{_NUM}"), r"(\2 + \1)"),
    (re.compile(rf"{_NUM}\s+subtracted from\s+{_NUM}"), r"(\2 - \1)"),
    (re.compile(rf"{_NUM}\s+percent off\s+{_NUM}"), r"(\2 - \2 * \1 / 100)"),
    (re.compile(rf"{_NUM}\s+off\s+{_NUM}"), r"(\2 - \1)"),
)

# "15 percent of 80" -> 15/100 * 80. Runs after _REVERSED so "20 percent off 50" is a
# discount rather than a multiplication.
_PERCENT_OF = re.compile(rf"{_NUM}\s+percent of\s+{_NUM}")
_PERCENT = re.compile(rf"{_NUM}\s*percent")

# "two thirds of ninety" and "half of 80" multiply; "5 out of 10" divides. Both are checked
# after the percent rules, so "15 percent of 80" never reaches them.
_OUT_OF = re.compile(rf"{_NUM}\s+out of\s+{_NUM}")
_X_OF_Y = re.compile(rf"{_NUM}\s+of\s+{_NUM}")

# Words that carry no arithmetic and are safe to drop. Anything NOT in here and not part of an
# expression makes evaluate() refuse — that is the guard that stops calc claiming questions it
# has no business answering.
_FILLER = {
    "what", "whats", "what's", "is", "are", "the", "of", "a", "an", "to", "be", "does",
    "do", "you", "know", "tell", "me", "please", "calculate", "compute", "work", "out",
    "equal", "equals", "result", "answer", "much", "many", "how", "give", "get", "im",
    "i", "its", "it", "thats", "that", "with", "been", "so", "then", "there", "hey",
    "mr", "odd", "ball", "oddball", "question", "quick", "just", "can", "could", "would",
}

# The operator tokens that must survive for something to count as arithmetic.
_OPERATOR_CHARS = set("+-*/%")


def _prepare(raw: str) -> str:
    """Lowercase and drop punctuation that is not arithmetic.

    Keeps digits, the math symbols, and letters. Deliberately unlike `router.normalise()`:
    the decimal point and the sign are the whole reason this module exists.
    """
    text = raw.lower().strip()
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    # A literal '%' is ALWAYS "percent" here, never Python's modulo. Whisper writes "15% of
    # 80" for spoken "fifteen percent of eighty", and the first draft let the symbol survive
    # into the expression as modulo: "15 % 80" evaluated to 15 and he said "That's 15" to a
    # question whose answer is 12. Modulo has its own spoken triggers ("mod", "modulo"), so
    # nothing is lost by claiming the symbol outright.
    text = text.replace("%", " percent ")
    # Apostrophes are DELETED, not turned into spaces, for the same reason `normalise()`
    # drops them: "what's" has to become "whats", one filler word. Replacing with a space
    # leaves a stray "s" that is not filler and not a number, so the whole expression is
    # refused — which is exactly what LB's real transcript "It's been 1000 less than 1000."
    # did until tools/verify_calc.py ran it.
    text = text.replace("'", "").replace("’", "")
    # Thousands separators: "1,000" is one number, not two. Done before punctuation stripping
    # so the comma cannot become a space and split the number in half.
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    # Trailing sentence punctuation and anything else Piper/whisper sprinkled in.
    text = re.sub(r"[^a-z0-9\.\+\-\*/%\(\)\s]", " ", text)
    # A decimal point needs a digit AFTER it; anything else is a full stop.
    #
    # The first draft required "not preceded by a digit AND not followed by one", which keeps
    # the period in "1000." — preceded by a digit, so not a full stop by that rule — and the
    # expression came out as "(1000 - 1000)." and failed to parse. Whisper ends most
    # sentences on a number, so this was not an edge case: it broke LB's real transcript.
    text = re.sub(r"\.(?!\d)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_expression(prepared: str) -> str | None:
    """Turn prepared text into an infix expression, or None if it is not arithmetic.

    Order is load-bearing throughout, and it is the same rule `router.INTENTS` and
    `formulas.FORMULAS` follow: the most specific pattern must be consumed first.
    """
    text = prepared

    # 1. sqrt, before anything else can eat the word "of".
    for phrase in _SQRT:
        text = text.replace(phrase, " sqrt ")

    # 2. Number words -> digits, so the regex operand patterns below have digits to match.
    tokens = _join_number_words(text.split())
    tokens = _apply_fractions(tokens)
    text = " ".join(tokens)

    # 2a. "negative seven" is one operand, not an operator and a number. Done here, after the
    #     words became digits and before anything tries to match an operand.
    text = re.sub(r"(?<![a-z0-9])negative\s+(\d)", r"-\1", text)

    # 3. Reversed operands, BEFORE the infix table — "less than" must not be seen as "less".
    for pattern, replacement in _REVERSED:
        prev = None
        while prev != text:                 # repeat for chained forms
            prev = text
            text = pattern.sub(replacement, text)

    # 4. Percentages.
    text = _PERCENT_OF.sub(r"(\1 / 100 * \2)", text)
    text = _PERCENT.sub(r"(\1 / 100)", text)

    # 4a. "of" between two operands multiplies — "two thirds of ninety", "half of 80". It runs
    #     AFTER the percent rules so "15 percent of 80" is already gone, and "out of" is
    #     claimed first because it divides: "5 out of 10" is 0.5, not 50.
    text = _OUT_OF.sub(r"(\1 / \2)", text)
    text = _X_OF_Y.sub(r"(\1 * \2)", text)

    # 5. Plain infix operators, longest phrase first.
    for phrase, symbol in _INFIX:
        text = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", f" {symbol} ", text)
    text = text.replace("%%", "%")

    # 6. sqrt(x) -> x ** 0.5. A prefix transform rather than a function call, so the AST
    #    whitelist never has to allow a Call node.
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"sqrt\s*\(([^()]*)\)", r"((\1) ** 0.5)", text)
        text = re.sub(r"sqrt\s+(-?\d+(?:\.\d+)?)", r"((\1) ** 0.5)", text)

    # 7. Drop filler. Anything left that is not part of an expression is a question this
    #    module has no business answering, so it refuses rather than guessing.
    kept = [t for t in text.split() if t not in _FILLER]
    text = " ".join(kept)
    if re.search(r"[a-z]", text):
        return None

    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    # An operator is REQUIRED. "set a timer for five minutes" reduces to a bare number, and a
    # bare number is not a question for the calculator — this single rule is what keeps calc
    # from swallowing Tier 0 commands that already work.
    if not any(c in _OPERATOR_CHARS for c in text.lstrip("-")):
        return None
    if not any(c.isdigit() for c in text):
        return None
    return text


# --------------------------------------------------------------------------------------
# Evaluation. Never eval().
# --------------------------------------------------------------------------------------

_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant)
_ALLOWED_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
                ast.USub, ast.UAdd)

# `2 ** 4096` is a number with 1234 digits and Python computes it happily; `9 ** 9 ** 9` is a
# number with 369 million digits and the turn never comes back. The guard is on the exponent
# and it runs on the TREE, before any arithmetic happens, because by the time you could catch
# an exception the Pi has already stopped answering.
_MAX_EXPONENT = 100


class _Unsafe(Exception):
    """The expression contains something the whitelist does not allow."""


def _check(node: ast.AST) -> None:
    """Walk the tree and reject anything outside the whitelist. Raises `_Unsafe`."""
    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES + _ALLOWED_OPS):
            raise _Unsafe(f"{type(child).__name__} is not allowed")
        if isinstance(child, ast.Constant) and not isinstance(child.value, (int, float)):
            raise _Unsafe(f"{type(child.value).__name__} constant is not allowed")
        if isinstance(child, ast.BinOp) and isinstance(child.op, ast.Pow):
            exponent = child.right
            if isinstance(exponent, ast.UnaryOp) and isinstance(exponent.op, ast.USub):
                exponent = exponent.operand
            if not isinstance(exponent, ast.Constant):
                raise _Unsafe("only a literal exponent is allowed")
            if abs(exponent.value) > _MAX_EXPONENT:
                raise _Unsafe(f"exponent {exponent.value} is too large")


def _safe_eval(expression: str) -> float:
    """Evaluate a whitelisted arithmetic expression. Raises on anything else."""
    tree = ast.parse(expression, mode="eval")
    _check(tree)
    return eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})  # noqa: S307


# --------------------------------------------------------------------------------------
# Speaking the answer.
# --------------------------------------------------------------------------------------

DIVIDE_BY_ZERO = "You can't divide by zero. Nobody can."
TOO_BIG = "That number's too big for me to say out loud."

# Above this, digits stop being speakable and scientific notation is kinder to Piper. It also
# matches the house style already in formulas.py: "3 times 10 to the 8".
_SCIENTIFIC_ABOVE = 1e12
_SCIENTIFIC_BELOW = 1e-4


def _trim(value: float) -> str:
    """Render a float without trailing zeros. 2.0 -> "2", 1.414 -> "1.414"."""
    if value == int(value):
        return str(int(value))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def _speak_number(value: float) -> str:
    """Render a number the way Piper should read it.

    Digits are deliberate and are the house rule (`formulas.py`): espeak-ng expands numerals,
    and keeping them as digits is what lets the harness assert the number in the sentence is
    the number the arithmetic produced.
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""

    # An exact integer is spoken as one, whether it arrived as int or float. 10.0 is "10".
    if value == int(value) and abs(value) < _SCIENTIFIC_ABOVE:
        return str(int(value))

    if value != 0 and (abs(value) >= _SCIENTIFIC_ABOVE or abs(value) < _SCIENTIFIC_BELOW):
        exponent = math.floor(math.log10(abs(value)))
        mantissa = _trim(round(value / 10 ** exponent, 2))
        return f"about {mantissa} times 10 to the {exponent}"

    # SIGNIFICANT figures, not decimal places, because this is going to be spoken. Four
    # decimals turns "100 divided by 3" into "about 33.3333", which is a mouthful and no more
    # useful than "about 33.33". Four significant figures gives 33.33, 1.414 and 0.6667 —
    # each the number a person would actually say out loud.
    rounded = float(f"{value:.4g}")
    if rounded == int(rounded):
        return str(int(rounded))
    # "about" is earned: it appears only when rounding actually lost information, so that a
    # hedge in his voice always means something. 0.1 plus 0.2 is exactly "0.3", not "about".
    return f"{'about ' if abs(rounded - value) > 1e-12 else ''}{_trim(rounded)}"


def _speak(value: float) -> str:
    number = _speak_number(value)
    if not number:
        return TOO_BIG
    return f"That's {number}."


# --------------------------------------------------------------------------------------
# The public surface.
# --------------------------------------------------------------------------------------

@lru_cache(maxsize=16)
def evaluate(raw: str) -> Result | None:
    """Compute the answer to a spoken sum, or None if this is not arithmetic. Never raises.

    Args:
        raw: the transcript, **raw** — not `router.normalise()`d. See the module docstring;
             normalising first deletes the decimal point and turns 3.5 into 35.

    Returns:
        A Result, or None. None means "not for me", and the router falls through to the tiers
        that already work.

    The cache key is the whole input. `evaluate` is a pure function of `raw` — no clock, no
    config, no model — so nothing that changes the answer sits outside the key. The matcher
    and the handler in `router.INTENTS` both call this, and the cache is what makes that
    second call free rather than a second parse.
    """
    if not raw or not raw.strip():
        return None
    try:
        prepared = _prepare(raw)
        expression = _to_expression(prepared)
        if expression is None:
            return None
        try:
            value = _safe_eval(expression)
        except ZeroDivisionError:
            return Result(DIVIDE_BY_ZERO, float("nan"), expression)
        except _Unsafe as exc:
            LOG.info("refusing %r: %s", expression, exc)
            return None
        except (SyntaxError, ValueError, TypeError, OverflowError, MemoryError,
                RecursionError):
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if isinstance(value, complex):      # (-8) ** 0.5
            return None
        if math.isnan(value) or math.isinf(value):
            return Result(TOO_BIG, float(value), expression)
        LOG.info("calc %r -> %s = %s", raw, expression, value)
        return Result(_speak(float(value)), float(value), expression)
    except Exception as exc:  # noqa: BLE001 — Tier 0 never raises into a turn
        LOG.warning("calc failed on %r: %s: %s", raw, type(exc).__name__, exc)
        return None


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    questions = args or [
        "whats five plus five",
        "whats 1000 less than 1000",
        "what is 5 less than 20",
        "3.5 plus 2",
        "whats 15 percent of 80",
        "the square root of 144",
        "what time is it",
    ]
    for question in questions:
        hit = evaluate(question)
        if hit:
            print(f"  {question!r:38} -> {hit.expression:24} {hit.spoken}")
        else:
            print(f"  {question!r:38} -> (not arithmetic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
