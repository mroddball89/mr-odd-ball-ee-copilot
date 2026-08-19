#!/usr/bin/env python3
"""
Module:  convert.py
Purpose: Tier 0 — unit conversion, answered locally, offline, and correctly.
Author:  LB
Date:    2026-08-14

    python -m orchestrator.convert "how many millivolts in 3.3 volts"

(`-m`, not a path, for the same reason `calc.py` says so: this imports from the `orchestrator`
package, and running it as a plain script puts `orchestrator/` on sys.path instead of the
repo root.)

## Why this exists

The same argument as `calc.py`, one rung up. A conversion has exactly one right answer, needs
no model, no clock and no network, and today every one of them either reaches Gemini or dies in
`classify()`'s ASK branch — the measured 5.89 s-to-nowhere failure. "How many millivolts in
3.3 volts" is a multiplication by 1000 and it currently costs a network round trip.

It is also the class of question a lookup table cannot serve, because the *number* is part of
the question. `formulas.py` can hold "the cutoff frequency is 1 over 2 pi R C"; it cannot hold
every value of every unit pair. So this is a small computation, not a bigger table.

## It reads the RAW transcript, and that is load-bearing

Exactly the D42 rule. `router.normalise()` strips everything outside `[a-z0-9 ]`, which deletes
the decimal point:

    '3.3 volts in millivolts'   -> '33 volts in millivolts'    3.3 becomes 33
    '-40 celsius in fahrenheit' -> '40 celsius in fahrenheit'  the sign is gone

A converter built on normalised text answers 3.3 V as 33000 mV — instantly, confidently and
wrongly, and a factor of ten in a spoken answer is exactly what D30 exists to prevent. So this
module takes the raw string and does its own preparation, reusing `calc`'s number machinery
rather than growing a second copy of it.

## Refusing is a feature, and it refuses in three distinct ways

`convert()` returns **None** for anything that is not a conversion, and the router falls
through to the tiers that already work. Three separate guards, because they fail differently:

1. **No unit pair** — "how many people are in China" names no units. Not ours.
2. **Different dimensions** — "5 volts in meters" names two real units that cannot be
   converted. This returns None rather than a number, because inventing a dimensional bridge
   is the confidently-wrong failure in its purest form.
3. **No number, and no "how many X in a Y" frame** — a bare "meters and feet" is a topic, not
   a question.

Claiming a question it cannot answer would be worse than useless, because a claimed question
never escalates.

## Temperature is affine, and that is why `Unit` carries an offset

Every other unit here is a pure scale factor, so a two-column table would have done — except
Celsius and Fahrenheit have an origin as well as a scale. Storing the offset for all units and
setting it to zero for most is what stops "convert 0 celsius to fahrenheit" answering 0.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache

from orchestrator.calc import _apply_fractions, _join_number_words
from orchestrator.constants import SI_PREFIXES, SI_SYMBOLS

LOG = logging.getLogger("oddball.convert")

__all__ = ["Conversion", "convert", "UNITS"]


@dataclass(frozen=True)
class Conversion:
    """One converted quantity.

    `value`, `from_unit` and `to_unit` are not decoration: `tools/verify_convert.py`
    recomputes the conversion independently, in SI, and asserts it agrees with `value` and
    that the number in `spoken` is the number the arithmetic produced. Same contract as
    `calc.Result` and `formulas.Worked`, for the same reason.
    """

    spoken: str          # exactly what he says
    value: float         # the answer, in `to_unit`
    amount: float        # what was asked about, in `from_unit`
    from_unit: str       # canonical key
    to_unit: str         # canonical key


@dataclass(frozen=True)
class Unit:
    """One unit, as a scale and an origin relative to its category's SI base.

    Args:
        key:      canonical id.
        category: only units sharing one may be converted. This IS the dimensional check.
        factor:   multiply by this to reach the SI base unit.
        offset:   add this AFTER scaling to reach the SI base. Non-zero only for temperature.
        spoken:   singular form, as Piper should say it.
        plural:   plural form. "feet", not "foots".
        names:    every spelling a transcript might contain, longest matched first.
        prefixable: may an SI prefix be attached? True for SI units, False for feet and pounds.
    """

    key: str
    category: str
    factor: float
    spoken: str
    plural: str
    names: tuple[str, ...]
    offset: float = 0.0
    prefixable: bool = False


# --------------------------------------------------------------------------------------
# The unit table.
#
# Scoped to LB's math and science requirements (FALL 2026 audit): MATH 241/242/243, MATH 340,
# EEGR 331, PHYS 205/206, CHEM 110, plus geometry and trigonometry. Everything an engineering
# problem set states its givens in.
#
# `names` order does not matter within a unit — the matcher sorts every alias by length across
# the whole table, so "millimeter" is always tried before "meter". What matters is that no
# alias is a *word* that means something else: "in" for inch and "s" for second are excluded
# deliberately, because "how many meters in a mile" contains "in" and would parse as inches.
# --------------------------------------------------------------------------------------

UNITS: tuple[Unit, ...] = (

    # --- length. SI base: meter ---
    Unit("meter", "length", 1.0, "meter", "meters",
         ("meter", "metre", "meters", "metres", "m"), prefixable=True),
    Unit("inch", "length", 0.0254, "inch", "inches", ("inch", "inches")),
    Unit("foot", "length", 0.3048, "foot", "feet", ("foot", "feet", "ft")),
    Unit("yard", "length", 0.9144, "yard", "yards", ("yard", "yards")),
    Unit("mile", "length", 1609.344, "mile", "miles", ("mile", "miles")),
    Unit("mil", "length", 2.54e-5, "mil", "mils", ("mil", "mils", "thou")),
    Unit("angstrom", "length", 1e-10, "angstrom", "angstroms", ("angstrom", "angstroms")),

    # --- mass. SI base: kilogram, but the PREFIXABLE unit is the gram ---
    # "kilogram" is itself a prefixed gram, so the table stores the gram and lets the prefix
    # machinery build kilograms, milligrams and micrograms from it. Storing both would give
    # two routes to "kilogram" with one factor typed twice.
    Unit("gram", "mass", 1e-3, "gram", "grams", ("gram", "gramme", "grams", "grammes", "g"),
         prefixable=True),
    Unit("pound", "mass", 0.45359237, "pound", "pounds", ("pound", "pounds", "lb", "lbs")),
    Unit("ounce", "mass", 0.028349523125, "ounce", "ounces", ("ounce", "ounces", "oz")),
    Unit("tonne", "mass", 1000.0, "tonne", "tonnes", ("tonne", "tonnes", "metric ton")),
    Unit("amu", "mass", 1.66053906660e-27, "atomic mass unit", "atomic mass units",
         ("atomic mass unit", "atomic mass units", "amu", "dalton", "daltons")),

    # --- time. SI base: second ---
    # "s" earns its place because it is what builds "ms", "us" and "ns" — the three time units
    # LB actually converts. Bare "s" is guarded by `_NEEDS_A_NUMBER`; bare "us" is guarded by
    # name, because it is also a pronoun.
    Unit("second", "time", 1.0, "second", "seconds",
         ("second", "seconds", "sec", "secs", "s"), prefixable=True),
    Unit("minute", "time", 60.0, "minute", "minutes", ("minute", "minutes", "min", "mins")),
    Unit("hour", "time", 3600.0, "hour", "hours", ("hour", "hours", "hr", "hrs")),
    Unit("day", "time", 86400.0, "day", "days", ("day", "days")),
    Unit("week", "time", 604800.0, "week", "weeks", ("week", "weeks")),
    Unit("year", "time", 31557600.0, "year", "years", ("year", "years")),

    # --- temperature. The ONLY affine category, and the reason `offset` exists. ---
    # SI base: kelvin. celsius: K = C + 273.15. fahrenheit: K = (F - 32) x 5/9 + 273.15,
    # which factors to F x 5/9 + 255.372..., and that constant is DERIVED below rather than
    # typed, so it cannot be typed wrongly.
    Unit("kelvin", "temperature", 1.0, "kelvin", "kelvin", ("kelvin", "kelvins")),
    Unit("celsius", "temperature", 1.0, "degree celsius", "degrees celsius",
         ("celsius", "centigrade", "degrees celsius", "degree celsius", "degrees c"),
         offset=273.15),
    Unit("fahrenheit", "temperature", 5.0 / 9.0, "degree fahrenheit", "degrees fahrenheit",
         ("fahrenheit", "degrees fahrenheit", "degree fahrenheit", "degrees f"),
         offset=273.15 - 32.0 * 5.0 / 9.0),

    # --- angle. Trigonometry, at LB's request. SI base: radian ---
    Unit("radian", "angle", 1.0, "radian", "radians",
         ("radian", "radians", "rad"), prefixable=True),
    Unit("degree", "angle", math.pi / 180.0, "degree", "degrees", ("degree", "degrees", "deg")),
    Unit("gradian", "angle", math.pi / 200.0, "gradian", "gradians", ("gradian", "gradians")),
    Unit("revolution", "angle", 2 * math.pi, "revolution", "revolutions",
         ("revolution", "revolutions", "turn", "turns", "rev", "revs")),
    Unit("arcminute", "angle", math.pi / 10800.0, "arcminute", "arcminutes",
         ("arcminute", "arcminutes", "arc minute", "arc minutes")),

    # --- energy. SI base: joule ---
    Unit("joule", "energy", 1.0, "joule", "joules", ("joule", "joules", "j"), prefixable=True),
    Unit("calorie", "energy", 4.184, "calorie", "calories", ("calorie", "calories", "cal")),
    Unit("kilocalorie", "energy", 4184.0, "kilocalorie", "kilocalories",
         ("kilocalorie", "kilocalories", "kcal", "food calorie", "food calories")),
    Unit("electronvolt", "energy", 1.602176634e-19, "electron volt", "electron volts",
         ("electron volt", "electron volts", "electronvolt", "electronvolts", "ev")),
    Unit("watthour", "energy", 3600.0, "watt hour", "watt hours",
         ("watt hour", "watt hours", "wh"), prefixable=True),
    Unit("btu", "energy", 1055.05585262, "b t u", "b t u",
         ("btu", "british thermal unit", "british thermal units")),

    # --- power. SI base: watt ---
    Unit("watt", "power", 1.0, "watt", "watts", ("watt", "watts", "w"), prefixable=True),
    Unit("horsepower", "power", 745.6998715822702, "horsepower", "horsepower",
         ("horsepower", "hp")),

    # --- force. SI base: newton ---
    Unit("newton", "force", 1.0, "newton", "newtons",
         ("newton", "newtons", "n"), prefixable=True),
    Unit("poundforce", "force", 4.4482216152605, "pound force", "pounds force",
         ("pound force", "pounds force", "lbf")),
    Unit("dyne", "force", 1e-5, "dyne", "dynes", ("dyne", "dynes")),

    # --- pressure. SI base: pascal ---
    Unit("pascal", "pressure", 1.0, "pascal", "pascals",
         ("pascal", "pascals", "pa"), prefixable=True),
    Unit("bar", "pressure", 1e5, "bar", "bars", ("bar", "bars"), prefixable=True),
    Unit("atmosphere", "pressure", 101325.0, "atmosphere", "atmospheres",
         ("atmosphere", "atmospheres", "atm")),
    Unit("psi", "pressure", 6894.757293168, "p s i", "p s i",
         ("psi", "pounds per square inch")),
    Unit("torr", "pressure", 133.32236842105263, "torr", "torr",
         ("torr", "millimeter of mercury", "millimeters of mercury", "mmhg")),

    # --- volume. SI base: cubic meter, but the PREFIXABLE unit is the liter ---
    Unit("liter", "volume", 1e-3, "liter", "liters",
         ("liter", "litre", "liters", "litres", "l"), prefixable=True),
    Unit("cubicmeter", "volume", 1.0, "cubic meter", "cubic meters",
         ("cubic meter", "cubic meters", "cubic metre", "cubic metres")),
    Unit("gallon", "volume", 3.785411784e-3, "gallon", "gallons", ("gallon", "gallons")),
    Unit("quart", "volume", 9.46352946e-4, "quart", "quarts", ("quart", "quarts")),
    Unit("pint", "volume", 4.73176473e-4, "pint", "pints", ("pint", "pints")),
    Unit("fluidounce", "volume", 2.95735295625e-5, "fluid ounce", "fluid ounces",
         ("fluid ounce", "fluid ounces", "fl oz")),
    Unit("cup", "volume", 2.365882365e-4, "cup", "cups", ("cup", "cups")),

    # --- area. SI base: square meter ---
    Unit("squaremeter", "area", 1.0, "square meter", "square meters",
         ("square meter", "square meters", "square metre", "square metres")),
    Unit("squarefoot", "area", 0.09290304, "square foot", "square feet",
         ("square foot", "square feet")),
    Unit("squareinch", "area", 6.4516e-4, "square inch", "square inches",
         ("square inch", "square inches")),
    Unit("acre", "area", 4046.8564224, "acre", "acres", ("acre", "acres")),
    Unit("hectare", "area", 1e4, "hectare", "hectares", ("hectare", "hectares")),

    # --- speed. SI base: meter per second ---
    Unit("meterpersecond", "speed", 1.0, "meter per second", "meters per second",
         ("meter per second", "meters per second", "metre per second", "metres per second",
          "mps")),
    Unit("kilometerperhour", "speed", 1000.0 / 3600.0, "kilometer per hour",
         "kilometers per hour",
         ("kilometer per hour", "kilometers per hour", "kilometre per hour",
          "kilometres per hour", "kph", "kmh")),
    Unit("mileperhour", "speed", 1609.344 / 3600.0, "mile per hour", "miles per hour",
         ("mile per hour", "miles per hour", "mph")),
    Unit("knot", "speed", 1852.0 / 3600.0, "knot", "knots", ("knot", "knots")),

    # --- frequency. SI base: hertz ---
    Unit("hertz", "frequency", 1.0, "hertz", "hertz",
         ("hertz", "hz", "cycle per second", "cycles per second"), prefixable=True),
    Unit("rpm", "frequency", 1.0 / 60.0, "r p m", "r p m",
         ("rpm", "revolution per minute", "revolutions per minute")),

    # --- the electrical units. Prefixed forms are the whole point: milliamps, kilohms,
    #     microfarads, nanoseconds. This is the category LB converts most often. ---
    Unit("volt", "voltage", 1.0, "volt", "volts", ("volt", "volts", "v"), prefixable=True),
    Unit("ampere", "current", 1.0, "amp", "amps",
         ("ampere", "amperes", "amp", "amps", "a"), prefixable=True),
    Unit("ohm", "resistance", 1.0, "ohm", "ohms", ("ohm", "ohms"), prefixable=True),
    Unit("farad", "capacitance", 1.0, "farad", "farads",
         ("farad", "farads", "f"), prefixable=True),
    Unit("henry", "inductance", 1.0, "henry", "henries",
         ("henry", "henries", "henrys", "h"), prefixable=True),
    Unit("coulomb", "charge", 1.0, "coulomb", "coulombs",
         ("coulomb", "coulombs", "c"), prefixable=True),
    Unit("weber", "flux", 1.0, "weber", "webers", ("weber", "webers", "wb"), prefixable=True),
    Unit("tesla", "fluxdensity", 1.0, "tesla", "teslas",
         ("tesla", "teslas"), prefixable=True),
    Unit("gauss", "fluxdensity", 1e-4, "gauss", "gauss", ("gauss",)),

    # --- data. Not on the audit, but EEGR 161/322 state everything in these. ---
    Unit("bit", "data", 1.0, "bit", "bits", ("bit", "bits"), prefixable=True),
    Unit("byte", "data", 8.0, "byte", "bytes", ("byte", "bytes"), prefixable=True),
)

_BY_KEY: dict[str, Unit] = {u.key: u for u in UNITS}


# --------------------------------------------------------------------------------------
# Matching a unit in text.
# --------------------------------------------------------------------------------------

def _build_aliases() -> tuple[dict[str, tuple[Unit, float]], list[str]]:
    """Every spelling a unit can arrive as, mapped to (unit, prefix multiplier).

    Returns:
        (map, aliases sorted longest first).

    **Longest-first protects the multi-word aliases**, and mutation testing narrowed that
    claim to what is actually true. The glued forms — "millimeter", "kilohm", "microfarad" —
    are already safe without it, because `_ALIAS_RE`'s word boundaries refuse to stop
    mid-token: "meter" cannot match inside "millimeter" when a letter follows.

    Where the boundaries do not help is **"miles per hour"**, whose parts are complete tokens
    and units in their own right. Shortest-first resolves it to plain miles, and a speed
    question comes back in units of length — fluently and wrongly. Same for "cubic meter",
    "square foot" and "electron volt". `tools/verify_convert.py` separates the two groups so
    the fixtures say which property they are actually testing.

    **Collisions resolve by table order, first writer wins, and there is a real one:** `pa` is
    both pascals and picoamperes. Pascals sit higher in `UNITS`, which is the answer a person
    means far more often. `tools/verify_convert.py` pins that specific pair, so the resolution
    is a decision rather than an accident of ordering.
    """
    table: dict[str, tuple[Unit, float]] = {}

    def add(alias: str, unit: Unit, multiplier: float) -> None:
        table.setdefault(alias, (unit, multiplier))

    for unit in UNITS:
        for alias in unit.names:
            add(alias, unit, 1.0)
            if not unit.prefixable:
                continue
            for prefix, multiplier in SI_PREFIXES.items():
                add(prefix + alias, unit, multiplier)
                # "kilo ohm", "milli amps" — whisper inserts the space about half the time.
                add(prefix + " " + alias, unit, multiplier)
                # VOWEL ELISION, and it is not a nicety: the standard spellings are
                # "kilohm" and "megohm", NOT "kiloohm" and "megaohm". Without this the
                # single most common conversion an electronics student asks for —
                # "5 kilohms in ohms" — is refused outright. Caught by smoke-testing the
                # CLI, which is exactly the class of bug review does not see.
                if prefix[-1] in "aeiou" and alias[0] in "aeiou":
                    add(prefix[:-1] + alias, unit, multiplier)
            # Single-letter prefixes attach ONLY to single-letter unit symbols: mv, ka, uf,
            # ns, ms, kg. Attaching them to whole words would turn "min" into milli-inches
            # and "cup" into centi-something. Length 1 is the guard, and it is deliberate.
            if len(alias) == 1:
                for symbol, multiplier in SI_SYMBOLS.items():
                    add(symbol + alias, unit, multiplier)

    return table, sorted(table, key=lambda a: (-len(a), a))


_ALIAS_MAP, _ALIAS_ORDER = _build_aliases()

# Aliases that are also ordinary English words are matched ONLY with a number in front of
# them. Every single letter qualifies — "a" is amperes and an article, "m" is meters and a
# stutter — and so does "us", which is microseconds and a pronoun. Requiring an adjacent digit
# is what tells "5 a" from "a mile", and it is why "in" is not an alias for inch at all:
# "how many meters in a mile" would parse as inches, and no digit rule can save that one.
_NEEDS_A_NUMBER = {a for a in _ALIAS_MAP if len(a) == 1} | {"us"}

# ONE compiled alternation rather than ~4,000 separate regexes. Python's `re` alternation
# takes the first alternative that matches at the earliest position, so feeding it aliases
# already sorted longest-first gives leftmost-longest for free, in a single pass. The naive
# loop-and-finditer version compiled a pattern per alias per turn and blew the 512-entry
# pattern cache on every call.
_ALIAS_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(a) for a in _ALIAS_ORDER)
                       + r")(?![a-z0-9])")


def _find_unit(text: str) -> tuple[Unit, float, int, int] | None:
    """The first unit mentioned in `text`, preferring the longest spelling.

    Args:
        text: prepared text — lowercase, digits, letters, dots and minus signs.

    Returns:
        (unit, prefix multiplier, start, end) or None if no unit is named.
    """
    for match in _ALIAS_RE.finditer(text):
        alias = match.group(1)
        if alias in _NEEDS_A_NUMBER and not re.search(r"\d$", text[:match.start()].rstrip()):
            continue
        unit, multiplier = _ALIAS_MAP[alias]
        return unit, multiplier, match.start(), match.end()
    return None


# --------------------------------------------------------------------------------------
# Parsing the question.
# --------------------------------------------------------------------------------------

# The frames a conversion actually arrives in. Whisper punctuates and capitalises; `_prepare`
# has already dealt with that. `normalise()` is NOT used — see the module docstring.
_FRAMES: tuple[re.Pattern, ...] = (
    # "convert 5 volts to millivolts" / "5 volts in millivolts" / "5 volts as millivolts"
    re.compile(r"(?P<amount>-?\d+(?:\.\d+)?)\s*(?P<src>.+?)\s+(?:in|into|to|as|equals|is)\s+"
               r"(?P<dst>.+)$"),
    # "how many millivolts in 3.3 volts" — DESTINATION FIRST, and this is the frame that makes
    # the reversal bug possible. `calc._REVERSED` exists for exactly this hazard in arithmetic.
    # `an?` before `one`, and BOTH before the amount: Python's alternation is first-match, so
    # writing `a|an` matches the "a" of "an" and leaves a stray "n" glued to the unit.
    re.compile(r"how many\s+(?P<dst>.+?)\s+(?:in|are in|is|per|make|makes|to)\s+"
               r"(?:(?:an?|one)\s+)?(?P<amount>-?\d+(?:\.\d+)?)?\s*(?P<src>.+)$"),
)

# Words that carry no conversion and are safe to drop before unit matching. Same idea as
# `calc._FILLER` and deliberately a SUPERSET of nothing — the two lists serve different
# grammars and merging them would drop "of", which `calc` needs and this module must keep out
# of unit names.
_FILLER = ("whats", "what is", "what", "how much", "tell me", "please", "the", "there",
           "exactly", "about", "roughly", "hey mr odd ball", "hey mr oddball", "mr odd ball",
           "oddball", "do you know", "can you", "could you", "convert", "change", "express",
           "give me", "work out", "calculate", "value of", "worth of", "equal to", "equals")


def _prepare(raw: str) -> str:
    """Lowercase and strip punctuation, KEEPING the decimal point and the sign.

    Deliberately unlike `router.normalise()`, and for the identical reason `calc._prepare`
    is: the decimal point and the minus sign are the whole reason this module reads raw text.
    """
    text = raw.lower().strip()
    text = text.replace("−", "-").replace("º", " degrees ").replace("°", " degrees ")
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)        # 1,000 is one number
    text = re.sub(r"[^a-z0-9\.\- ]+", " ", text)
    text = re.sub(r"\.(?!\d)", " ", text)                  # a decimal point needs a digit after
    # A hyphen between words is a separator ("kilo-ohm"); a hyphen before a digit is a sign.
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_filler(text: str) -> str:
    for phrase in _FILLER:
        text = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _numbers_to_digits(text: str) -> str:
    """"twenty three volts" -> "23 volts", reusing calc's parser rather than repeating it.

    `calc._join_number_words` is positional — it knows "three hundred" multiplies and
    "twenty three" adds — and rewriting that here would be a second implementation of the
    hardest part of number parsing, free to drift. Same argument as SI_PREFIXES living in
    `constants.py`.
    """
    tokens = _join_number_words(text.split())
    tokens = _apply_fractions(tokens)
    joined = " ".join(tokens)
    # `_apply_fractions` emits "(1/2)", which is arithmetic calc can evaluate and this module
    # cannot. Convert the only forms it produces back to a decimal.
    def _fraction(match: re.Match) -> str:
        numerator, denominator = float(match.group(1)), float(match.group(2))
        return f"{numerator / denominator:.10g}" if denominator else match.group(0)

    joined = re.sub(r"\((\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\)", _fraction, joined)
    return re.sub(r"(?<![a-z0-9])negative\s+(\d)", r"-\1", joined)


# --------------------------------------------------------------------------------------
# The conversion itself.
# --------------------------------------------------------------------------------------

def _to_si(amount: float, unit: Unit, prefix: float) -> float:
    """Scale then offset. Order matters and is the affine part: F -> K is F x 5/9 + 255.37."""
    return amount * prefix * unit.factor + unit.offset


def _from_si(si: float, unit: Unit, prefix: float) -> float:
    """The exact inverse of `_to_si`. Offset removed BEFORE unscaling."""
    return (si - unit.offset) / (unit.factor * prefix)


def _prefix_name(multiplier: float) -> str:
    """The spoken prefix for a multiplier, or "". 0.001 -> "milli"."""
    for name, value in SI_PREFIXES.items():
        if math.isclose(value, multiplier, rel_tol=1e-12):
            return name
    return ""


def _speak_number(value: float) -> str:
    """Render the answer the way Piper should read it.

    Four significant figures, the same choice `calc._speak_number` makes and for the same
    reason: "about 33.3333 millivolts" is a mouthful and no more useful than "33.33". The
    house rule that digits stay digits is what lets the harness assert the spoken number is
    the computed one.
    """
    if math.isnan(value) or math.isinf(value):
        return ""
    if value == int(value) and abs(value) < 1e12:
        return str(int(value))
    if value != 0 and (abs(value) >= 1e12 or abs(value) < 1e-4):
        exponent = math.floor(math.log10(abs(value)))
        mantissa = round(value / 10 ** exponent, 3)
        text = f"{mantissa:g}"
        sign = "minus " if exponent < 0 else ""
        return f"{text} times 10 to the {sign}{abs(exponent)}"
    rounded = float(f"{value:.4g}")
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def _unit_words(unit: Unit, prefix: float, value: float) -> str:
    """"millivolts" — prefix plus the singular or plural form, chosen by the value.

    The ohm elides the prefix's final vowel: **kilohms and megohms**, which is both the
    standard spelling and what Piper needs to say it as one word rather than "kilo oh ohms".

    It is deliberately restricted to the ohm rather than applied to every vowel-vowel join,
    because the general rule turns "milliamps" into "millamps" — right-looking, and wrong.
    """
    stem = unit.spoken if abs(value) == 1 else unit.plural
    if prefix == 1.0:
        return stem
    name = _prefix_name(prefix)
    if stem.startswith("ohm") and name and name[-1] in "aeiou":
        name = name[:-1]
    return f"{name}{stem}"


@lru_cache(maxsize=256)
def convert(raw: str) -> Conversion | None:
    """Convert a spoken quantity, or None if this is not a conversion question.

    Args:
        raw: the transcript exactly as heard or typed. **Not** normalised — see the module
             docstring; normalising deletes the decimal point.

    Returns:
        A `Conversion`, or None. None is the common case and is not a failure: it means the
        question belongs to another tier, and the router falls through to it.

    Never raises. A malformed question is a None, not an exception, because Tier 0 sits on the
    turn's critical path and an exception there is a turn that never answers.
    """
    if not raw or not raw.strip():
        return None
    try:
        return _convert(raw)
    except Exception:                                    # noqa: BLE001 — see docstring
        LOG.exception("convert failed on %r; treating as not-a-conversion", raw)
        return None


def _convert(raw: str) -> Conversion | None:
    text = _numbers_to_digits(_strip_filler(_prepare(raw)))
    if not text:
        return None

    for frame in _FRAMES:
        match = frame.search(text)
        if not match:
            continue

        source_text, dest_text = match.group("src"), match.group("dst")
        found_src = _find_unit(source_text)
        found_dst = _find_unit(dest_text)
        if not found_src or not found_dst:
            continue

        src_unit, src_prefix, _, _ = found_src
        dst_unit, dst_prefix, _, _ = found_dst

        # THE DIMENSIONAL CHECK. "5 volts in meters" names two real units and has no answer.
        # Returning None rather than a number is the whole of guard 2 in the docstring: a
        # converter that bridges dimensions is the confidently-wrong failure in its purest
        # form, and it would be invisible in review because the arithmetic still runs.
        if src_unit.category != dst_unit.category:
            LOG.info("refusing %s -> %s: %s is not %s", src_unit.key, dst_unit.key,
                     src_unit.category, dst_unit.category)
            return None

        # Converting a unit to itself is not a question. "how many meters in a meter" and,
        # more usefully, "how many volts in 5 volts" both land here.
        if src_unit.key == dst_unit.key and math.isclose(src_prefix, dst_prefix):
            return None

        raw_amount = match.groupdict().get("amount")
        amount = float(raw_amount) if raw_amount else 1.0

        value = _from_si(_to_si(amount, src_unit, src_prefix), dst_unit, dst_prefix)
        number = _speak_number(value)
        if not number:
            return None

        src_words = _unit_words(src_unit, src_prefix, amount)
        dst_words = _unit_words(dst_unit, dst_prefix, value)
        said_amount = _speak_number(amount)
        spoken = f"{said_amount} {src_words} is {number} {dst_words}."

        LOG.info("convert %s %s -> %s %s", amount, src_unit.key, value, dst_unit.key)
        return Conversion(spoken=spoken, value=value, amount=amount,
                          from_unit=src_unit.key, to_unit=dst_unit.key)

    return None


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    questions = args or [
        "how many millivolts in 3.3 volts",
        "convert 25 degrees celsius to fahrenheit",
        "whats 60 miles per hour in meters per second",
        "how many radians in 180 degrees",
        "what time is it",
    ]
    for question in questions:
        hit = convert(question)
        print(f"  {question!r:52} -> {hit.spoken if hit else '(not a conversion)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
