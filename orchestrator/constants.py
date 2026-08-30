#!/usr/bin/env python3
"""
Module:  constants.py
Purpose: Tier 0 — the physical and mathematical constants LB's coursework leans on.
Author:  LB
Date:    2026-08-14

    python -m orchestrator.constants "whats the charge on an electron"

(`-m`, not a path, for the same reason `calc.py` and `classify.py` say so: this imports from
the `orchestrator` package, and running it as a plain script puts `orchestrator/` on sys.path
instead of the repo root.)

## Why a separate file from formulas.py

A formula is a *relationship* and its text is prose. A constant is a **number with a unit**,
and that difference is what makes this file verifiable in a way prose is not: the spoken
sentence is **generated from the value**, never typed alongside it.

That is the one lesson `formulas.Worked` exists to enforce, taken one step further. `Worked`
recomputes a hand-typed sentence and asserts they agree — good, but the sentence and the number
are still two things that *can* disagree until the harness runs. Here there is only one thing.
`Constant.spoken` is a property over `value` and `unit`, so a typo in the value changes what he
says, and a sentence that contradicts its own number is **unrepresentable**.

## Scope — LB's math and science requirements, 2026-08-14

Set by LB against his FALL 2026 Morgan State degree audit: math and science prerequisites and
electives are in, general education is out. That is MATH 241/242/243, MATH 340, EEGR 331,
PHYS 205/206, CHEM 110, plus linear algebra, geometry and trigonometry.

## Speakability is the same hard constraint as formulas.py

Every string here is read aloud by Piper, and `tools/verify_define.py` enforces it with
`formulas.UNSPEAKABLE` — **imported, never re-declared**, because two copies of a rule drift.
That is the D35 lesson about `_CHARS_PER_TOKEN`, applied to a second shared rule.

- No symbols: **pi**, not the Greek letter. **ohms**, not the sign. **micro**, not mu.
- Exponents are spoken: "1.602 times 10 to the minus 19", never `1.602e-19`.
- Digits stay as digits. espeak-ng expands numerals, and keeping them as digits is what lets
  `tools/verify_define.py` assert the number in the sentence is the number in the table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from orchestrator.formulas import _has_phrase

__all__ = ["Constant", "CONSTANTS", "SI_PREFIXES", "look_up", "say_value", "keys"]


# --------------------------------------------------------------------------------------
# Speaking a number.
#
# The house style is already set by formulas.py: "3 times 10 to the 8", digits kept as
# digits. This generalises it so that no constant's sentence is ever typed by hand.
# --------------------------------------------------------------------------------------

def say_value(value: float, sig: int = 4) -> str:
    """Render a number the way Piper should read it aloud.

    Args:
        value: the number.
        sig:   significant figures to keep.

    Returns:
        A speakable string — plain digits for ordinary magnitudes, and
        "<mantissa> times 10 to the [minus] <exponent>" outside them. No symbols, no `e`
        notation, nothing Piper reads as silence.

    Whole numbers stay whole: the speed of light is "299792458", not "2.998 times 10 to the 8",
    because LB asked for a constant and the exact integer is both correct and sayable.
    """
    if value == 0:
        return "0"
    if math.isnan(value) or math.isinf(value):
        return ""

    # An exact integer small enough to say is spoken as itself.
    if value == int(value) and abs(value) < 1e10:
        return str(int(value))

    exponent = math.floor(math.log10(abs(value)))

    # Ordinary magnitudes read better as plain decimals: 9.81, 8.314, 0.02585.
    if -3 <= exponent < 6:
        places = max(0, sig - 1 - exponent)
        text = f"{value:.{places}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    mantissa = value / (10.0 ** exponent)
    # Rounding the mantissa can carry it to 10.0 (9.9999 at sig=4). Renormalise, or he says
    # "10 times 10 to the 23", which is right arithmetic and wrong notation.
    rendered = f"{mantissa:.{sig - 1}f}"
    if abs(float(rendered)) >= 10:
        exponent += 1
        mantissa = value / (10.0 ** exponent)
        rendered = f"{mantissa:.{sig - 1}f}"
    rendered = rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    sign = "minus " if exponent < 0 else ""
    return f"{rendered} times 10 to the {sign}{abs(exponent)}"


@dataclass(frozen=True)
class Constant:
    """One number LB might act on, and the sentence he hears for it.

    `spoken` is a **property**, not a field. That is the whole design: the number in the
    sentence cannot disagree with `value`, because there is only one number. `formulas.Worked`
    checks that two hand-written things agree; here they are the same thing.

    Args:
        key:      stable id, so fixtures never assert on wording.
        triggers: OR of ANDs, exactly as `formulas.Formula` defines it — each inner tuple is a
                  set of phrases that must ALL appear, and the constant fires if ANY tuple is
                  fully satisfied.
        name:     how the sentence names it. Speakable.
        value:    the number, in `unit`.
        unit:     the unit, spelled out. "" for a pure number like pi.
        sig:      significant figures spoken.
        note:     one optional extra sentence — where it comes up, or how to remember it.
        symbol:   ASCII symbol, for the log and for `--table`. Never spoken.
    """

    key: str
    triggers: tuple[tuple[str, ...], ...]
    name: str
    value: float
    unit: str = ""
    sig: int = 4
    note: str = ""
    symbol: str = ""

    @property
    def spoken(self) -> str:
        """Exactly what he says. Generated from `value`, so it cannot drift from it."""
        tail = f" {self.unit}" if self.unit else ""
        sentence = f"{self.name} is {say_value(self.value, self.sig)}{tail}."
        return f"{sentence} {self.note}".strip() if self.note else sentence


# --------------------------------------------------------------------------------------
# SI prefixes. Owned here because `convert.py` needs them and a second copy would drift.
#
# Spoken forms only — "micro", never the Greek letter, which is in UNSPEAKABLE. The symbol
# column is what a transcript looks like when whisper hears "5 kilohms" as "5k ohms".
# --------------------------------------------------------------------------------------

SI_PREFIXES: dict[str, float] = {
    "yotta": 1e24, "zetta": 1e21, "exa": 1e18, "peta": 1e15, "tera": 1e12,
    "giga": 1e9, "mega": 1e6, "kilo": 1e3, "hecto": 1e2, "deca": 1e1, "deka": 1e1,
    "deci": 1e-1, "centi": 1e-2, "milli": 1e-3, "micro": 1e-6, "nano": 1e-9,
    "pico": 1e-12, "femto": 1e-15, "atto": 1e-18, "zepto": 1e-21, "yocto": 1e-24,
}

# The single-letter forms whisper actually emits. Deliberately NOT a mirror of the table
# above: "d" for deci and "da" for deca are real SI and are also how "5 d" would be heard in a
# dozen unrelated sentences, so they are left out. A missing prefix costs a refusal; a
# spurious one costs a wrong answer by a factor of ten.
SI_SYMBOLS: dict[str, float] = {
    "t": 1e12, "g": 1e9, "m": 1e-3, "k": 1e3, "c": 1e-2,
    "u": 1e-6, "n": 1e-9, "p": 1e-12,
}


# --------------------------------------------------------------------------------------
# The table.
#
# ORDER MATTERS, same rule as router.INTENTS and formulas.FORMULAS: most specific first.
# `electron_mass` must precede `elementary_charge` because "mass of an electron" mentions
# the electron, and `boltzmann` must precede `gas_constant` because both say "constant".
#
# Values are CODATA 2018 / SI 2019, which is what every current textbook prints.
# --------------------------------------------------------------------------------------

CONSTANTS: tuple[Constant, ...] = (

    # --- pure mathematics ---
    Constant(
        key="pi", symbol="pi",
        # NOT a bare ("pi",). That shipped for about two hours and answered "what's the sleep
        # mode on a Pi" with 3.14159265 — because "Raspberry Pi" is a phrase LB says several
        # times an hour, and `_has_phrase` matches "pi" as a whole word inside it.
        #
        # D38 for the fifth time, and the first time the bare keyword was one *I* added. The
        # subject has to be the number, which in practice means the interrogative sits right
        # against it.
        triggers=(("what is pi",), ("whats pi",), ("value of pi",), ("value", "pi"),
                  ("pi to",), ("digits of pi",)),
        name="Pi", value=math.pi, sig=9,
        note="Circumference over diameter, for every circle there has ever been.",
    ),
    Constant(
        key="eulers_number", symbol="e",
        triggers=(("eulers number",), ("euler number",), ("natural log", "base"),
                  ("base", "natural logarithm")),
        name="Euler's number, e", value=math.e, sig=9,
        note="It is the base of the natural logarithm, and the one function that is its own derivative.",
    ),
    Constant(
        key="golden_ratio", symbol="phi",
        triggers=(("golden ratio",), ("golden section",)),
        name="The golden ratio, phi", value=(1 + 5 ** 0.5) / 2, sig=7,
        note="It is 1 plus the square root of 5, all over 2.",
    ),
    Constant(
        key="radians_in_a_circle",
        # NOT ("how many radians",). That trigger shipped for about ten minutes and swallowed
        # "how many radians in 180 degrees" — a real conversion, answered with the size of a
        # whole circle. The subject has to be the circle, not the word "radians" appearing in
        # a question about something else. Same bare-keyword lesson as D38, third occurrence.
        triggers=(("radians", "circle"), ("radians", "full turn"), ("radians", "one turn")),
        name="A full circle", value=2 * math.pi, unit="radians", sig=7,
        note="That is 360 degrees, so 1 radian is about 57.3 degrees.",
    ),

    # --- electricity and magnetism: PHYS 206, and the ones EE uses weekly ---
    Constant(
        key="electron_mass", symbol="m_e",
        triggers=(("mass", "electron"), ("electron", "mass")),
        name="The mass of an electron", value=9.1093837015e-31, unit="kilograms",
    ),
    Constant(
        key="proton_mass", symbol="m_p",
        triggers=(("mass", "proton"), ("proton", "mass")),
        name="The mass of a proton", value=1.67262192369e-27, unit="kilograms",
        note="That is about 1836 times the electron.",
    ),
    Constant(
        key="elementary_charge", symbol="q",
        triggers=(("elementary charge",), ("charge", "electron"), ("electron", "charge"),
                  ("charge", "proton")),
        name="The elementary charge", value=1.602176634e-19, unit="coulombs",
        note="Every charge you will ever measure is a whole number of these.",
    ),
    Constant(
        key="coulomb_constant", symbol="k_e",
        triggers=(("coulombs constant",), ("coulomb constant",), ("electrostatic constant",)),
        name="Coulomb's constant", value=8.9875517923e9,
        unit="newton meters squared per coulomb squared",
        note="It is 1 over 4 pi epsilon nought.",
    ),
    Constant(
        key="permittivity", symbol="eps0",
        triggers=(("permittivity",), ("epsilon nought",), ("epsilon not",), ("epsilon zero",),
                  ("electric constant",)),
        name="The permittivity of free space, epsilon nought", value=8.8541878128e-12,
        unit="farads per meter",
        # tiny.en hears "not" for "naught" — the same mishearing class as D29's "what" -> "with".
        note="Multiply it by the relative permittivity to get the value inside a material.",
    ),
    Constant(
        key="permeability", symbol="mu0",
        triggers=(("permeability",), ("mu nought",), ("mu not",), ("mu zero",),
                  ("magnetic constant",)),
        name="The permeability of free space, mu nought", value=1.25663706212e-6,
        unit="henries per meter",
        note="It is very close to 4 pi times 10 to the minus 7.",
    ),
    Constant(
        key="thermal_voltage", symbol="V_T",
        triggers=(("thermal voltage",), ("kt over q",), ("k t on q",)),
        name="The thermal voltage at room temperature", value=0.025852, unit="volts",
        note="About 26 millivolts. It sets the slope of every diode equation you will write.",
    ),

    # --- quantum and thermal: PHYS 206, CHEM 110 ---
    Constant(
        key="planck", symbol="h",
        triggers=(("plancks constant",), ("planck constant",), ("plancks",)),
        name="Planck's constant", value=6.62607015e-34, unit="joule seconds",
        note="Energy of a photon is this times its frequency.",
    ),
    # Stefan-Boltzmann sits ABOVE Boltzmann, and that is not a preference — it is the same
    # most-specific-first rule as everywhere else. Boltzmann's trigger is a bare
    # ("boltzmann",), which matches "stefan boltzmann" too, so the other way round the
    # Stefan-Boltzmann entry is unreachable. Caught by the reachability sweep in
    # tools/verify_define.py, not by reading the table.
    Constant(
        key="stefan_boltzmann", symbol="sigma",
        triggers=(("stefan boltzmann",), ("stefan constant",)),
        name="The Stefan Boltzmann constant", value=5.670374419e-8,
        unit="watts per meter squared per kelvin to the fourth",
    ),
    Constant(
        key="boltzmann", symbol="k_B",
        triggers=(("boltzmanns constant",), ("boltzmann constant",), ("boltzmann",)),
        name="Boltzmann's constant", value=1.380649e-23, unit="joules per kelvin",
        note="It is the gas constant divided by Avogadro's number.",
    ),
    Constant(
        key="avogadro", symbol="N_A",
        triggers=(("avogadros number",), ("avogadro number",), ("avogadro",),
                  ("particles", "mole"), ("atoms", "mole")),
        name="Avogadro's number", value=6.02214076e23, unit="per mole",
        note="That is how many particles are in one mole of anything.",
    ),
    Constant(
        key="gas_constant", symbol="R",
        triggers=(("gas constant",), ("universal gas constant",), ("ideal gas", "constant")),
        name="The universal gas constant", value=8.314462618,
        unit="joules per mole kelvin",
        note="It is the R in P V equals n R T.",
    ),
    Constant(
        key="faraday_constant", symbol="F",
        triggers=(("faradays constant",), ("faraday constant",)),
        name="Faraday's constant", value=96485.33212, unit="coulombs per mole",
        note="It is Avogadro's number times the elementary charge.",
    ),
    Constant(
        key="atomic_mass_unit", symbol="u",
        triggers=(("atomic mass unit",), ("unified mass unit",), ("dalton",)),
        name="One atomic mass unit", value=1.66053906660e-27, unit="kilograms",
        note="That is one twelfth of a carbon 12 atom.",
    ),
    Constant(
        key="electron_volt", symbol="eV",
        triggers=(("electron volt",), ("electronvolt",), ("ev", "joules")),
        name="One electron volt", value=1.602176634e-19, unit="joules",
        note="It is the energy an electron picks up crossing 1 volt.",
    ),
    Constant(
        key="absolute_zero",
        triggers=(("absolute zero",), ("zero kelvin",), ("0 kelvin",)),
        name="Absolute zero", value=-273.15, unit="degrees celsius", sig=5,
        note="Nothing gets colder than that.",
    ),
    Constant(
        key="molar_volume",
        triggers=(("molar volume",), ("volume", "mole"), ("22 4",)),
        name="The molar volume of an ideal gas at standard temperature and pressure",
        value=22.41396954, unit="liters per mole", sig=5,
    ),

    # --- mechanics: PHYS 205 ---
    Constant(
        key="gravitational_constant", symbol="G",
        triggers=(("gravitational constant",), ("big g",), ("newtons constant",)),
        name="The gravitational constant, big G", value=6.67430e-11,
        unit="newton meters squared per kilogram squared",
        note="Do not confuse it with little g, which is 9.81.",
    ),
    # Little g is NOT here on purpose. `formulas.gravity` already answers it, and its trigger
    # is a bare ("gravity",) — so any entry here mentioning the word would sit behind the
    # formula intent in `router.INTENTS` and be permanently unreachable. A table entry that
    # cannot be reached is worse than no entry: it reads as covered in review. Same shadowing
    # hazard D38 records for bare keywords, one tier up.
)


def look_up(normalised: str) -> Constant | None:
    """Find the constant this question is asking for, or None.

    A pure function of a string — no clock, no model, no network. Takes text that
    `router.normalise()` has already lowercased, de-punctuated and whitespace-collapsed.

    Args:
        normalised: the transcript, normalised.

    Returns:
        The first Constant whose trigger set is fully satisfied, in table order.
    """
    if not normalised:
        return None
    for constant in CONSTANTS:
        for group in constant.triggers:
            if all(_has_phrase(normalised, phrase) for phrase in group):
                return constant
    return None


def keys() -> tuple[str, ...]:
    return tuple(c.key for c in CONSTANTS)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--table":
        for c in CONSTANTS:
            print(f"  {c.key:24s} {c.symbol:8s} {c.spoken}")
        return 0

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from orchestrator.router import normalise

    questions = args or [
        "whats the charge on an electron",
        "what is avogadros number",
        "whats the permittivity of free space",
        "what time is it",
    ]
    for question in questions:
        hit = look_up(normalise(question))
        print(f"  {question!r:52} -> {hit.key if hit else '(no constant)'}")
        if hit:
            print(f"      {hit.spoken}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
