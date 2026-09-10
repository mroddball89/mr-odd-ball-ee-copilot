#!/usr/bin/env python3
"""
Module:  verify_define.py
Purpose: Prove the encyclopedia — the glossary and the constants — is correct, reachable,
         speakable, and that it swallows nothing it should not.
Author:  LB
Date:    2026-08-14

    venv/bin/python tools/verify_define.py

Eleventh harness, same contract as the other ten: exit 0 = all passed, no microphone, no
model, no network, and every claim measured rather than asserted. Both tiers under test are
pure functions of a string, so all of it is testable as ordinary logic.

## What earns its place here, and why

**The constants are checked twice, and the second way is the one that matters.** Retyping
CODATA values into a test file proves only that they were typed the same way twice — one
transcription slip and both copies are wrong together. So the second pass checks the
**relationships between them**: epsilon nought must equal 1 over mu nought c squared,
the gas constant must equal Avogadro times Boltzmann, Faraday must equal Avogadro times the
elementary charge, and the thermal voltage must equal k T over q. A typo in any single value
breaks an identity, and no amount of consistent retyping can hide that.

**That the glossary reaches every entry.** 169 entries and ~316 spellings, matched
longest-first across the whole table. Get that sort wrong and "partial derivative" resolves to
"derivative" — the specific entry becomes dead code that still reads as covered in review.

**That it swallows nothing.** This is the dangerous direction and it is why the harness exists
at all. The `define` intent sits *above* `time`, `identity` and everything `classify.py` does,
and it holds single words like "power", "work", "period" and "field". Every existing router
fixture is re-run through the new ordering, and the personality boundary
(`classify.py`'s "an opinion about a capacitor is still an opinion") is asserted directly.

**The adjacency rule.** `look_up` requires the term to sit immediately after the frame.
The first version only required a frame *somewhere* and a term *somewhere*, and
`verify_stt.py` caught it defining "velocity" at "what is the airspeed velocity of an unladen
swallow". Pinned here so it cannot come back.

**That every string is speakable.** `UNSPEAKABLE` is imported from `formulas`, never
re-declared — one rule, one copy, the D35 lesson.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from orchestrator import constants, define                                  # noqa: E402
from orchestrator.constants import CONSTANTS, SI_PREFIXES, say_value        # noqa: E402
from orchestrator.define import TERMS, subjects                             # noqa: E402
from orchestrator.formulas import UNSPEAKABLE, unspeakable                  # noqa: E402
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


# ============================================================ 1. the tables are well-formed

section("tables")

# Assert the inputs before trusting anything derived from them. An empty table would make
# every check below pass vacuously — the failure this repo has now shipped four times, most
# recently as verify-rig.mjs's "0 gestures played to completion".
check(len(TERMS) >= 120, "the glossary is substantial", f"{len(TERMS)} terms")
check(len(CONSTANTS) >= 15, "the constant table is substantial", f"{len(CONSTANTS)} constants")

term_keys = [t.key for t in TERMS]
check(len(term_keys) == len(set(term_keys)), "every glossary key is unique",
      f"{len(term_keys)} keys, {len(set(term_keys))} distinct")
const_keys = [c.key for c in CONSTANTS]
check(len(const_keys) == len(set(const_keys)), "every constant key is unique",
      f"{len(const_keys)} keys, {len(set(const_keys))} distinct")

# A duplicate alias makes one of the two entries permanently unreachable. define._alias_order
# raises on import if this is violated, so reaching this line already proves it — the check
# states the property so a future refactor that removes the guard fails here instead.
aliases = [a for t in TERMS for a in t.terms]
check(len(aliases) == len(set(aliases)), "no glossary alias is claimed twice",
      f"{len(aliases)} aliases, {len(set(aliases))} distinct")

for t in TERMS:
    check(bool(t.spoken.strip()), f"{t.key}: has something to say")
    check(bool(t.terms), f"{t.key}: has at least one spelling")
    check(all(a == a.lower().strip() for a in t.terms),
          f"{t.key}: spellings are normalised (lowercase, trimmed)")
    check(all(a == normalise(a) for a in t.terms),
          f"{t.key}: every spelling survives normalise() unchanged",
          f"{[a for a in t.terms if a != normalise(a)]}")

# ---- COURSE COVERAGE. LB's scope was "all prerequisite and electives relevant to math and
# science", read against his FALL 2026 degree audit. A subject with three entries is a gap
# that reads as covered, so the floor is asserted per subject rather than in aggregate.

REQUIRED_SUBJECTS = {
    "algebra": 8, "geometry": 8, "trigonometry": 8, "calculus": 15, "diffeq": 8,
    "linear_algebra": 8, "statistics": 15, "physics_mechanics": 15, "physics_em": 15,
    "chemistry": 15,
    # D47. The audit's math-and-science rows do not contain LB's own major, so the ten above
    # produced an EE student's encyclopedia with no EE in it — 9 of 74 covered, and no entry
    # for "current" or "resistance". `circuits` is the hand-written seed.
    "circuits": 20,
    # Delivered 2026-08-15 by tools/draft_ee_entries.py — compressed from fetched source
    # passages and entailment-gated against them, then read by LB.
    "electronics": 20, "signals": 20,
    # The remaining six, delivered 2026-08-15 in one ~20 minute run of
    # tools/draft_ee_entries.py --all. The full BSEE scope LB chose is now present.
    "digital_logic": 15, "electromagnetics": 15, "power_systems": 15,
    "control": 15, "communications": 15, "microelectronics": 12,
}

# The rest of the BSEE scope, delivered by training/ee_encyclopedia.ipynb. Declared here so
# the target is visible and the gate is armed: a subject that arrives MUST meet its floor, but
# one that has not arrived yet is reported as pending rather than failed. Move each into
# REQUIRED_SUBJECTS as it lands, and this file stops treating its absence as acceptable.
# Empty: every subject in the agreed BSEE scope has landed. Kept rather than deleted so the
# next widening of scope has an obvious place to declare itself before the entries exist.
INCOMING_SUBJECTS: dict[str, int] = {}

counts = subjects()
for subject, floor in REQUIRED_SUBJECTS.items():
    check(counts.get(subject, 0) >= floor,
          f"{subject}: covered to at least {floor} entries", f"has {counts.get(subject, 0)}")

for subject, floor in INCOMING_SUBJECTS.items():
    n = counts.get(subject, 0)
    if n == 0:
        print(f"   ....  {subject}: not delivered yet (target {floor}) — "
              f"run training/ee_encyclopedia.ipynb")
        continue
    check(n >= floor, f"{subject}: arrived, so it must meet its floor of {floor}", f"has {n}")

check(set(counts) <= set(REQUIRED_SUBJECTS) | set(INCOMING_SUBJECTS),
      "no subject outside the agreed scope",
      f"unexpected: {sorted(set(counts) - set(REQUIRED_SUBJECTS) - set(INCOMING_SUBJECTS))}")
check(set(REQUIRED_SUBJECTS) <= set(counts),
      "every required subject is present",
      f"missing: {sorted(set(REQUIRED_SUBJECTS) - set(counts))}")


# ================================================ 1b. the three ways a bigger table goes wrong

section("scale")

# Everything below exists because D47 roughly doubles this table, and every one of these
# failures is invisible in review and cheap to check.

from orchestrator import formulas as _formulas                            # noqa: E402

# The ten D43 scoped from the audit's math and science rows. Everything else in the table is
# the EE half D47 added, and the bare-word rule below applies only to that half.
MATH_SCIENCE_SUBJECTS = {
    "algebra", "geometry", "trigonometry", "calculus", "diffeq", "linear_algebra",
    "statistics", "physics_mechanics", "physics_em", "chemistry",
}
EE_SUBJECTS = (set(REQUIRED_SUBJECTS) | set(INCOMING_SUBJECTS)) - MATH_SCIENCE_SUBJECTS

# --- 1. duplicate spellings ------------------------------------------------------------
# `define._alias_order()` already raises on a duplicate alias, which is a real guard — it
# caught three collisions the moment ee_terms.py was first wired in ("electric potential",
# "potential difference", "transient", all already owned by physics_em and diffeq).
#
# But it raises at IMPORT time, so the failure arrives as a service that will not start on the
# Pi rather than as a harness that goes red on the desktop. This says the same thing early and
# readably. It can only pass here — if it could fail, the import above would already have.
_seen: dict[str, str] = {}
_dupes: list[str] = []
for t in TERMS:
    for alias in t.terms:
        if alias in _seen:
            _dupes.append(f"{alias!r} in both {_seen[alias]} and {t.key}")
        _seen[alias] = t.key
check(not _dupes, "no spelling is claimed by two entries", "; ".join(_dupes))

# --- 2. shadowing by a higher-priority table -------------------------------------------
# router.INTENTS checks formula, then constant, then define. An entry whose question is
# claimed above it is dead code no matter how good the definition is, and nothing else in this
# repo would ever notice.
_shadowed = []
for t in TERMS:
    probe = normalise(f"what is {t.terms[0]}")
    if _formulas.look_up(probe) is not None:
        _shadowed.append(f"{t.key} -> formulas.{_formulas.look_up(probe).key}")
    elif constants.look_up(probe) is not None:
        _shadowed.append(f"{t.key} -> constants.{constants.look_up(probe).key}")
check(not _shadowed,
      "no entry is shadowed by the formula or constant table",
      "; ".join(_shadowed) + "  (these entries are unreachable — drop them or requalify)")

# --- 3. bare EE words in ordinary sentences --------------------------------------------
# The homonym rule. EE vocabulary overlaps ordinary English harder than calculus does —
# current, ground, load, gain, noise, phase — and a bare single-word spelling that matched
# outside a definition frame would swallow questions `classify.py` deliberately routes to
# personality first. The frame gate is what makes bare words survivable; this proves it.
BARE_CARRIERS = [
    "what do you think of {w}",
    "my {w} homework is due tomorrow",
    "the {w} was a nightmare",
    "tell me a joke about {w}",
    "i need help with my {w} lab",
    "what is the {w} time",
]
_bare = [a for t in TERMS if t.subject in EE_SUBJECTS for a in t.terms if " " not in a]
for word in sorted(_bare):
    for carrier in BARE_CARRIERS:
        probe = normalise(carrier.format(w=word))
        hit = define.look_up(probe)
        check(hit is None,
              f"bare {word!r} does not answer {carrier.format(w=word)!r}",
              f"matched {hit.key}" if hit else "")

check(len(_bare) > 0, "the EE subjects do hold some bare single words",
      f"{len(_bare)}: {sorted(_bare)}")


# ============================================================ 2. THE CONSTANTS, two ways

section("constants — retyped against CODATA")

# Pass one: values typed independently from CODATA 2018 / SI 2019. This catches a wrong digit
# in the table. It does NOT catch the same wrong digit typed into both places, which is what
# section 3 exists for.
CODATA = {
    "elementary_charge": 1.602176634e-19,
    "electron_mass": 9.1093837015e-31,
    "proton_mass": 1.67262192369e-27,
    "planck": 6.62607015e-34,
    "boltzmann": 1.380649e-23,
    "avogadro": 6.02214076e23,
    "gas_constant": 8.314462618,
    "permittivity": 8.8541878128e-12,
    "permeability": 1.25663706212e-6,
    "coulomb_constant": 8.9875517923e9,
    "gravitational_constant": 6.67430e-11,
    "faraday_constant": 96485.33212,
    "atomic_mass_unit": 1.66053906660e-27,
    "stefan_boltzmann": 5.670374419e-8,
    "electron_volt": 1.602176634e-19,
    "absolute_zero": -273.15,
    "pi": math.pi,
    "eulers_number": math.e,
}
by_key = {c.key: c for c in CONSTANTS}
for key, expected in CODATA.items():
    got = by_key.get(key)
    check(got is not None, f"{key}: is in the table")
    if got:
        check(math.isclose(got.value, expected, rel_tol=1e-9),
              f"{key}: matches the published value",
              f"table {got.value!r} vs CODATA {expected!r}")

check(set(CODATA) <= set(by_key), "every reference value names a real entry")


section("constants — the relationships between them")

# PASS TWO, and this is the one that earns its place. Retyping proves consistency of
# transcription; these prove PHYSICS. Break any single value and an identity fails, which no
# amount of consistently-repeated typing can hide.

eps0 = by_key["permittivity"].value
mu0 = by_key["permeability"].value
c_light = 299_792_458.0
q = by_key["elementary_charge"].value
n_a = by_key["avogadro"].value
k_b = by_key["boltzmann"].value

check(math.isclose(eps0, 1 / (mu0 * c_light ** 2), rel_tol=1e-7),
      "epsilon nought equals 1 over mu nought c squared",
      f"{eps0:.6e} vs {1 / (mu0 * c_light ** 2):.6e}")
check(math.isclose(by_key["coulomb_constant"].value, 1 / (4 * math.pi * eps0), rel_tol=1e-7),
      "Coulomb's constant equals 1 over 4 pi epsilon nought",
      f"{by_key['coulomb_constant'].value:.6e} vs {1 / (4 * math.pi * eps0):.6e}")
check(math.isclose(by_key["gas_constant"].value, n_a * k_b, rel_tol=1e-9),
      "the gas constant equals Avogadro times Boltzmann",
      f"{by_key['gas_constant'].value!r} vs {n_a * k_b!r}")
check(math.isclose(by_key["faraday_constant"].value, n_a * q, rel_tol=1e-9),
      "Faraday's constant equals Avogadro times the elementary charge",
      f"{by_key['faraday_constant'].value!r} vs {n_a * q!r}")
check(math.isclose(by_key["thermal_voltage"].value, k_b * 300.0 / q, rel_tol=1e-3),
      "the thermal voltage equals k T over q at 300 kelvin",
      f"{by_key['thermal_voltage'].value!r} vs {k_b * 300.0 / q!r}")
check(math.isclose(by_key["electron_volt"].value, q, rel_tol=1e-12),
      "one electron volt in joules is numerically the elementary charge")
check(math.isclose(by_key["molar_volume"].value,
                   by_key["gas_constant"].value * 273.15 / 101325.0 * 1000.0, rel_tol=1e-4),
      "the molar volume equals R T over P at standard temperature and pressure",
      f"{by_key['molar_volume'].value!r} vs "
      f"{by_key['gas_constant'].value * 273.15 / 101325.0 * 1000.0!r}")
check(math.isclose(by_key["proton_mass"].value / by_key["electron_mass"].value,
                   1836.15267, rel_tol=1e-6),
      "the proton is 1836.15 times the electron",
      f"ratio {by_key['proton_mass'].value / by_key['electron_mass'].value:.5f}")
check(math.isclose(by_key["golden_ratio"].value ** 2,
                   by_key["golden_ratio"].value + 1, rel_tol=1e-12),
      "phi squared equals phi plus 1")
check(math.isclose(by_key["radians_in_a_circle"].value, 2 * math.pi, rel_tol=1e-12),
      "a full circle is 2 pi radians")


section("constants — the sentence is generated from the number")

# The design claim of constants.py: `spoken` is a property over `value`, so the two cannot
# disagree. That is only true if `say_value` is right, so `say_value` is tested directly.
SAY = [
    (299792458.0, 4, "299792458"),
    (1.602176634e-19, 4, "1.602 times 10 to the minus 19"),
    (6.02214076e23, 4, "6.022 times 10 to the 23"),
    (8.314462618, 4, "8.314"),
    (9.80665, 6, "9.80665"),
    (-273.15, 5, "-273.15"),
    (0.025852, 4, "0.02585"),
    (1.25663706212e-6, 4, "1.257 times 10 to the minus 6"),
    (0.0, 4, "0"),
    (10.0, 4, "10"),
    # The renormalisation branch: 9.9999e11 rounds its mantissa to 10.000, which must become
    # 1 times 10 to the 12 rather than "10 times 10 to the 11".
    (9.9999e11, 4, "1 times 10 to the 12"),
]
for value, sig, want in SAY:
    got = say_value(value, sig)
    check(got == want, f"say_value({value!r}, {sig}) is {want!r}", f"got {got!r}")

check("e" not in say_value(1.602176634e-19).replace("times", "").replace("the", ""),
      "scientific notation is spoken, never printed as an 'e' float")

for c in CONSTANTS:
    # The number LB hears IS the number in the table — not a second copy of it.
    check(say_value(c.value, c.sig) in c.spoken,
          f"{c.key}: the spoken sentence carries the table's own value",
          f"{say_value(c.value, c.sig)!r} not in {c.spoken[:70]!r}")
    if c.unit:
        check(c.unit in c.spoken, f"{c.key}: the spoken sentence names its unit", c.unit)


# ============================================================ 3. everything is reachable

section("reachability — glossary")

for t in TERMS:
    for alias in t.terms:
        # The question a person would actually ask, built from the entry's own spelling.
        hit = define.look_up(normalise(f"what is a {alias}"))
        check(hit is not None and hit.key == t.key,
              f"{t.key}: reachable as {alias!r}",
              f"got {hit.key if hit else None!r}")

# Through the ROUTER, which is the claim that actually matters — an entry shadowed by the
# formula or constant intent above it is dead however well look_up() works.
for t in TERMS:
    reply = route(f"what is a {t.terms[0]}")
    check(reply.handled and reply.text != FALLBACK,
          f"{t.key}: the router answers it rather than falling through",
          f"[{reply.intent}] {reply.text[:56]!r}")

section("reachability — constants")

for c in CONSTANTS:
    for group in c.triggers:
        question = normalise("what is the " + " ".join(group))
        hit = constants.look_up(question)
        check(hit is not None and hit.key == c.key,
              f"{c.key}: reachable via {' + '.join(group)!r}",
              f"got {hit.key if hit else None!r} from {question!r}")
    reply = route("what is the " + " ".join(c.triggers[0]))
    check(reply.handled and reply.text != FALLBACK,
          f"{c.key}: the router answers it rather than falling through",
          f"[{reply.intent}] {reply.text[:56]!r}")


# ============================================================ 4. real phrasings

section("phrasing")

# Fixtures assert the KEY, never the wording — the convention verify_stt.py set for intent.
# Wording can be improved without breaking the harness; routing cannot.
PHRASINGS = [
    ("what is an eigenvalue",                       "eigenvalue"),
    ("whats an eigenvector",                        "eigenvalue"),
    ("define entropy",                              "entropy"),
    ("what does a derivative mean",                 "derivative"),
    ("what is a partial derivative",                "partial_derivative"),
    ("explain the law of cosines",                  "law_of_cosines"),
    ("whats the standard deviation",                "standard_deviation"),
    ("what is a mole",                              "mole"),
    ("tell me about the unit circle",               "unit_circle"),
    ("what is the definition of a radian",          "radian"),
    ("remind me what a phasor is",                  "phasor"),
    ("what is simple harmonic motion",              "shm"),
    ("define the central limit theorem",            "central_limit"),
    ("what does electronegativity mean",            "electronegativity"),
    ("whats a null space",                          "null_space"),
    ("what is an integrating factor",               "integrating_factor"),
    ("explain the chain rule",                      "chain_rule"),
    ("what is a hypotenuse",                        "hypotenuse"),
    ("what does the pythagorean theorem mean",      "theorem_pythagoras"),
    ("describe magnetic flux",                      "magnetic_flux"),
    ("what is faradays law",                        "faradays_law"),
    ("define ph",                                   "ph"),
    ("what is a p value",                           "p_value"),
    ("whats a moment of inertia",                   "moment_of_inertia"),
]
for question, want in PHRASINGS:
    hit = define.look_up(normalise(question))
    check(hit is not None and hit.key == want, f"{question!r} -> {want}",
          f"got {hit.key if hit else None!r}")

# Specific entries beat the general ones they contain.
#
# **Corrected after mutation testing.** The comment here used to credit the longest-first
# sort. Reversing that sort left every one of these green, and the reason is the end-anchor
# rule: `look_up` requires `rest == alias`, so "partial derivative" simply is not equal to
# "derivative" and the order never arises. Exact matching is what disambiguates; the sort is
# a backstop for the `alias + trailer` path and for nothing else.
#
# The checks stay — they pin the behaviour LB depends on — but the claim attached to them is
# now the true one. A harness that passes for a reason its comment does not name is how a
# vacuous check survives review.
SPECIFIC_BEATS_GENERAL = [
    ("what is a partial derivative",      "partial_derivative", "derivative"),
    ("what is the standard deviation",    "standard_deviation", "variance"),
    ("what is an electric field",         "electric_field",     "magnetic_field"),
    ("what is a definite integral",       "definite_integral",  "integral"),
]
for question, want, not_want in SPECIFIC_BEATS_GENERAL:
    hit = define.look_up(normalise(question))
    check(hit is not None and hit.key == want,
          f"{question!r} resolves to the specific entry, not {not_want}",
          f"got {hit.key if hit else None!r}")

CONSTANT_PHRASINGS = [
    ("whats the charge on an electron",     "elementary_charge"),
    ("what is avogadros number",            "avogadro"),
    ("whats the permittivity of free space", "permittivity"),
    ("what is plancks constant",            "planck"),
    ("what is pi",                          "pi"),
    ("whats the thermal voltage",           "thermal_voltage"),
    ("what is the gravitational constant",  "gravitational_constant"),
    ("what is absolute zero",               "absolute_zero"),
    ("whats the mass of a proton",          "proton_mass"),
    ("what is the ideal gas constant",      "gas_constant"),
]
for question, want in CONSTANT_PHRASINGS:
    hit = constants.look_up(normalise(question))
    check(hit is not None and hit.key == want, f"{question!r} -> {want}",
          f"got {hit.key if hit else None!r}")


# ============================================================ 5. THE ADJACENCY RULE

section("adjacency — a frame plus a word is not a definition question")

# The bug verify_stt.py caught. The loose version asked "is there a frame anywhere" and "is
# there a term anywhere" and answered yes to both — for a question about a swallow.
NOT_DEFINITIONS = [
    "what is the airspeed velocity of an unladen swallow",
    "what is the weather going to do",
    "what is my mean time to failure looking like",
    "what is the best way to solve this integral",
    "whats the point of all this work",
    "what is on tv tonight",
    "what is taking so long",
]
for question in NOT_DEFINITIONS:
    hit = define.look_up(normalise(question))
    check(hit is None, f"{question!r} is NOT a definition question",
          f"defined {hit.key if hit else None!r}")

check(not route("what is the airspeed velocity of an unladen swallow").handled,
      "the swallow still escalates (handled=False), as verify_stt.py requires")

# And the frame gate itself: a glossary word with no frame at all is not ours.
NO_FRAME = [
    "the derivative was really hard today",
    "i need to integrate this by tomorrow",
    "my work is going badly",
    "power went out again",
    "set a timer for one period",
]
for question in NO_FRAME:
    hit = define.look_up(normalise(question))
    check(hit is None, f"{question!r} carries no frame, so it is not claimed",
          f"defined {hit.key if hit else None!r}")


# ============================================================ 6. IT SWALLOWS NOTHING

section("no over-reach")

# The mirror, and the more dangerous direction. `define` sits above `time`, `date`, `timer`
# and `identity`, and it holds single words like "power", "work", "period" and "field".
UNTOUCHED = [
    ("what time is it",              "time"),
    ("whats the time",               "time"),
    ("what day is it today",         "date"),
    ("what is the date",             "date"),
    ("what is the month",            "date"),
    ("set a timer for five minutes", "timer"),
    ("who are you",                  "identity"),
    ("what are you",                 "identity"),
    ("hello",                        "hello"),
    ("thanks",                       "thanks"),
    ("stop",                         "stop"),
    ("whats five plus five",         "calc"),
    ("what is 15 percent of 80",     "calc"),
    ("what is the time constant of an rc circuit", "formula"),
    ("whats the cutoff frequency",   "formula"),
]
for question, want in UNTOUCHED:
    reply = route(question)
    check(reply.intent == want, f"{question!r} still routes to {want}",
          f"got {reply.intent!r} -> {reply.text[:44]!r}")

# THE PRIVACY ORDERING. classify.py opens by testing personality before subject matter
# precisely so an opinion about electronics reaches Mr Odd Ball rather than an exam board.
# A bare-keyword glossary would eat these three intents earlier, and classify would never
# see them. This is the check that keeps that boundary intact.
#
# Every one of these names a word that IS in the glossary. The first version used EE words
# like "capacitor" that the glossary does not hold at all, so the checks passed without
# exercising the gate — mutation testing caught that: deleting the frame gate left them
# green. A fixture that cannot fail is worth nothing.
OPINIONS = [
    "how do you feel about entropy",
    "what do you think of a derivative",
    "do you like matrices",
    "whats your favourite integral",
    "do you prefer a mole or a gram",
    "what do you make of eigenvalues",
    "do you think momentum is interesting",
]
for question in OPINIONS:
    reply = route(question)
    check(reply.intent not in ("define", "constant"),
          f"{question!r} is NOT answered from the glossary — it is an opinion",
          f"got [{reply.intent}] {reply.text[:44]!r}")
    check(not reply.handled,
          f"{question!r} still escalates so classify.py can route it to personality",
          f"handled={reply.handled}")

# THE PROPERTY THAT MAKES THE INTENT ORDER NOT LOAD-BEARING, which is stronger than an
# ordering that happens to work. Moving `define` above `formula` in router.INTENTS leaves
# every harness green, and this sweep says why: the end-anchor rule means the glossary never
# claims a formula question in the first place. It is asserted rather than assumed, because
# "the ordering saves us" is exactly the kind of belief that is true until an entry is added.
from orchestrator.formulas import FORMULAS                                  # noqa: E402

for f in FORMULAS:
    for group in f.triggers:
        question = normalise("what is the " + " ".join(group))
        stolen = define.look_up(question)
        check(stolen is None,
              f"the glossary does not claim the {f.key} question",
              f"{question!r} -> {stolen.key if stolen else None!r}")


# ============================================================ 7. speakable

section("speakable")

for entry, label in [(t, t.key) for t in TERMS] + [(c, c.key) for c in CONSTANTS]:
    spoken = entry.spoken
    bad = unspeakable(spoken)
    check(not bad, f"{label}: no characters Piper cannot say", f"found {bad!r}")
    check(spoken.isascii(), f"{label}: pure ASCII",
          "".join(ch for ch in spoken if not ch.isascii())[:20])
    for symbol in ("*", "/", "^", "=", "<", ">", "_"):
        check(symbol not in spoken, f"{label}: no bare {symbol!r} operator", spoken[:56])
    # D32's ceiling. Piper runs ~160 words per minute, so 40 words is about 15 seconds, and a
    # definition is the answer to a small question — it should sound like one.
    words = len(spoken.split())
    check(words <= 42, f"{label}: short enough to speak ({words} words)", spoken[:56])
    check(spoken.strip().endswith((".", "!", "?")),
          f"{label}: ends on a sentence boundary (Piper streams per sentence)")

check(UNSPEAKABLE, "UNSPEAKABLE is non-empty (guards every speakability check above)")


# ============================================================ 8. it never raises

section("never raises")

check(define.look_up("") is None, "define.look_up('') is None rather than raising")
check(constants.look_up("") is None, "constants.look_up('') is None rather than raising")
check(define.look_up("completely unrelated words here") is None, "no spurious match on prose")

for junk in ("", " ", "\n", "?" * 200, "what is a " + "x" * 500, "define", "what is a",
             "\t\r", "what is a \u00e9\u00e8\u00ea", "what is a 12345",
             "what is a \u03c4 \u03a9 \u221a"):
    try:
        define.look_up(normalise(junk))
        constants.look_up(normalise(junk))
        ok = True
    except Exception as exc:                                       # noqa: BLE001
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    check(ok, f"look_up survives {junk[:24]!r}", "" if ok else detail)

check(SI_PREFIXES["kilo"] == 1e3 and SI_PREFIXES["milli"] == 1e-3,
      "the SI prefix table is sane (convert.py depends on it)")


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
print(f"{len(TERMS)} glossary entries across {len(subjects())} subjects, "
      f"{sum(len(t.terms) for t in TERMS)} spellings; {len(CONSTANTS)} constants")
raise SystemExit(1 if failed else 0)
