#!/usr/bin/env python3
"""
Module:  formulas.py
Purpose: Tier 0 — the formulas and constants LB asks for, answered correctly and instantly.
Author:  LB
Date:    2026-08-13

    python orchestrator/formulas.py "what is the time constant of an rc circuit"

## Why this exists

D30 measured every candidate local model answering first-year electronics questions
**fluently and wrongly**. Asked for the RC time constant: "add the product of resistance and
capacitance, then divide by the frequency". Asked for the LM358 pinout: "14 pins" (it has 8).
Asked what happens to cutoff frequency: "depends on the ratio of resistance to capacitance".

A 1.2B model cannot be relied on for this. A lookup table cannot be wrong. D31 sends factual
questions to Gemini, but Gemini costs a network round trip and ~2.1s — and for the two dozen
things an electronics student asks most, both the latency and the network are unnecessary.

**So this is Tier 0, and it is the same argument as D2**: the cheapest tier that can serve the
question should serve it. Instant, offline, auditable, and correct by construction.

## What belongs here, and what does not

**Here:** relationships with one right answer that LB might act on — formulas, unit
relationships, a handful of physical constants. Being wrong about these costs him a burnt LED
or a wrong lab result.

**Not here:** open questions ("why is the sky blue", "how far away is the moon"). Those have
right answers too, but they are unbounded, they are not things a table can keep up with, and
being approximately right is fine. They go to Tier 3.

The test for adding an entry: *would LB act on this number, and would a wrong answer cost him
real time?*

## Speakability is a hard constraint

Every `spoken` string is read aloud by Piper. That rules out the notation this subject is
normally written in:

- No symbols: **tau**, not `τ`. **ohms**, not `Ω`. **pi**, not `π`. **squared**, not `²`.
- No operators: "times", "divided by", "the square root of" — never `*`, `/`, `√`.
- **Digits are fine and are deliberate.** espeak-ng expands numerals, and keeping them as
  digits is what lets `tools/verify_formulas.py` recompute each worked example and assert the
  number in the sentence is the number the arithmetic produces. Spelling them out would make
  the examples unverifiable, which is the one thing this file cannot afford.

`tools/verify_formulas.py` enforces all of the above.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Characters that survive the router's normalise() but that Piper reads badly or not at all.
# Enforced by the verifier rather than trusted to reviewer attention.
UNSPEAKABLE = "τΩμµπ√²³×÷≈°Δω∞±≤≥"


@dataclass(frozen=True)
class Worked:
    """A worked example whose arithmetic the verifier recomputes.

    The point is that the number in the spoken sentence is not typed by hand and trusted —
    it is derived here, and `tools/verify_formulas.py` asserts that `str(round(value))`
    actually appears in the text LB hears. A slip in the example fails the harness rather
    than teaching him the wrong number.
    """

    describe: str       # what is being computed, for the failure message
    value: float        # the answer, IN THE UNIT THE SENTENCE USES — see below
    unit: str = ""      # that unit, spelled exactly as `spoken` says it

    # `value` is expressed in the same unit the sentence speaks, not in SI base units, and the
    # verifier asserts "<value> <unit>" appears verbatim. The first version compared bare
    # numbers and passed a sentence saying "5 kilohms" against a value of 5000 ohms — the
    # arithmetic was right and the check was meaningless. Pairing the number with its spoken
    # unit is also what stops a single-digit value matching some unrelated digit in the text.


@dataclass(frozen=True)
class Formula:
    """One thing he can answer with no model.

    Args:
        key:      stable id, used by fixtures so tests do not assert on wording.
        triggers: OR of ANDs. Each inner tuple is a set of phrases that must ALL appear;
                  the formula fires if ANY tuple is fully satisfied. Phrases are matched as
                  whole words against already-normalised text.
        spoken:   exactly what he says. One to three short sentences, speakable.
        worked:   optional arithmetic the verifier recomputes against `spoken`.
    """

    key: str
    triggers: tuple[tuple[str, ...], ...]
    spoken: str
    worked: tuple[Worked, ...] = field(default_factory=tuple)


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word phrase match against already-normalised text.

    Padding both sides is what makes this whole-word without a regex per phrase: normalise()
    has already collapsed whitespace and stripped punctuation, so " rc " cannot match inside
    "arc" and " time constant " cannot match "times constantly".
    """
    return f" {phrase} " in f" {text} "


# --------------------------------------------------------------------------------------
# The table.
#
# ORDER MATTERS, and it is the same rule router.py's INTENTS follows: the most specific
# trigger sets sit above the more general ones. `rc_cutoff` must precede `capacitive_reactance`
# because "cutoff frequency" questions mention capacitance too.
# --------------------------------------------------------------------------------------

FORMULAS: tuple[Formula, ...] = (

    # ABOVE rc_time_constant, and that ordering is the fix. "Time constant of an RL circuit"
    # contains the phrase "time constant", so the RC entry claimed it and answered "resistance
    # times capacitance" — a confidently wrong formula handed to an EE student, which is the
    # exact failure this table was built to prevent (D30). Found 2026-08-15; the entry-level
    # collision section of tools/verify_formulas.py is what now holds it.
    Formula(
        key="rl_time_constant",
        triggers=(("rl", "constant"), ("time constant", "inductor"),
                  ("time constant", "inductance"), ("l over r",)),
        spoken=("For an RL circuit the time constant, tau, is inductance divided by "
                "resistance. 100 millihenries with 50 ohms gives you 2 milliseconds. "
                "Bigger resistance makes an inductor settle faster, not slower."),
        worked=(Worked("100mH / 50 ohms", 0.1 / 50 * 1000, "milliseconds"),),
    ),

    # --- the one that started this. D30: every model got it wrong. ---
    Formula(
        key="rc_time_constant",
        triggers=(("time constant",), ("rc", "constant"), ("tau",)),
        spoken=("The time constant, tau, is just resistance times capacitance. "
                "10 kilohms with 1 microfarad gives you 10 milliseconds. "
                "After one time constant you're at 63 percent of the way there."),
        worked=(Worked("10k ohms x 1uF", 10_000 * 1e-6 * 1000, "milliseconds"),),
    ),

    # ABOVE rc_cutoff, same reason rl_time_constant sits above rc_time_constant: a waveguide
    # has a cutoff too, and it is not 1/(2 pi R C). Without this the RC corner answered it.
    Formula(
        key="waveguide_cutoff",
        triggers=(("waveguide",),),
        spoken=("A waveguide's cutoff frequency is set by its cross section and the mode, "
                "not by any resistor or capacitor. Below it the wave does not propagate "
                "down the guide at all."),
    ),

    Formula(
        key="rc_cutoff",
        triggers=(("cutoff", "frequency"), ("corner", "frequency"), ("cutoff",),
                  ("3 db", "point"), ("break", "frequency")),
        spoken=("The cutoff frequency is 1 divided by 2 pi R C. "
                "So 10 kilohms and 1 microfarad puts the corner at about 16 hertz. "
                "Double the capacitance and you halve the frequency."),
        worked=(Worked("1/(2 pi x 10k x 1uF)", 1 / (2 * 3.141592653589793
                                                    * 10_000 * 1e-6), "hertz"),),
    ),

    # --- the other one it got wrong: it said 14 pins, and garbled the arithmetic ---
    Formula(
        key="led_resistor",
        triggers=(("led", "resistor"), ("resistor", "led"), ("current", "limiting"),
                  ("series", "resistor", "led")),
        spoken=("Take your supply voltage, subtract the LED's forward voltage, "
                "then divide by the current you want. "
                "5 volts, a 2 volt red LED, 20 milliamps, gives you 150 ohms."),
        worked=(Worked("(5V - 2V) / 20mA", (5.0 - 2.0) / 0.020, "ohms"),),
    ),

    Formula(
        key="ohms_law",
        triggers=(("ohms law",), ("ohm law",), ("voltage", "current", "resistance")),
        spoken=("Ohm's law: voltage equals current times resistance. "
                "So current is voltage over resistance, and resistance is voltage over current."),
    ),

    Formula(
        key="power",
        triggers=(("power", "formula"), ("watts", "formula"), ("calculate", "power"),
                  ("power", "dissipated"), ("power", "resistor")),
        spoken=("Power equals voltage times current. "
                "If you only know one of them, it's also current squared times resistance, "
                "or voltage squared divided by resistance."),
    ),

    Formula(
        key="voltage_divider",
        # The bare ("divider",) used to be here and it claimed "what is a current divider",
        # answering with the voltage ratio — which is the INVERTED one, so the number LB would
        # have acted on was wrong, not merely unhelpful. D38 again: never a bare keyword.
        triggers=(("voltage divider",),),
        spoken=("Output equals input times the bottom resistor, "
                "divided by both resistors added together. "
                "Two equal resistors give you half your input."),
    ),

    Formula(
        key="current_divider",
        triggers=(("current divider",), ("current", "divides"), ("splits", "current")),
        spoken=("A current divider is the mirror image of a voltage divider: each branch "
                "takes the OTHER resistor over the total. 10 milliamps into 1 kilohm "
                "beside 3 kilohms puts 7.5 milliamps through the 1 kilohm branch."),
        worked=(Worked("10mA x 3k/(1k+3k), the 1k branch", 10 * 3 / (1 + 3), "milliamps"),),
    ),

    Formula(
        key="resistors_series_parallel",
        triggers=(("resistors", "parallel"), ("resistors", "series"),
                  ("parallel", "resistance"), ("combine", "resistors")),
        spoken=("In series you just add them up. "
                "In parallel it's product over sum for two of them, "
                "so two 10 kilohm resistors in parallel give you 5 kilohms."),
        worked=(Worked("10k || 10k, spoken in kilohms", (10_000 * 10_000) / (10_000 + 10_000) / 1000, "kilohms"),),
    ),

    Formula(
        key="capacitors_series_parallel",
        triggers=(("capacitors", "parallel"), ("capacitors", "series"),
                  ("combine", "capacitors")),
        spoken=("Capacitors are the opposite of resistors. "
                "In parallel you add them up, in series it's product over sum. "
                "That trips people up constantly, so it's worth saying out loud."),
    ),

    Formula(
        key="capacitive_reactance",
        triggers=(("capacitive", "reactance"), ("reactance", "capacitor"),
                  ("impedance", "capacitor")),
        spoken=("Capacitive reactance is 1 divided by 2 pi f C. "
                "It falls as frequency rises, so a capacitor looks like a short "
                "to high frequencies and an open circuit to DC."),
    ),

    Formula(
        key="inductive_reactance",
        triggers=(("inductive", "reactance"), ("reactance", "inductor"),
                  ("impedance", "inductor")),
        spoken=("Inductive reactance is 2 pi f L. "
                "It rises with frequency, which is exactly backwards from a capacitor."),
    ),

    Formula(
        key="resonant_frequency",
        # NOT a bare ("resonance",). That trigger predates `define.py` and shadowed the
        # physics entry for it — "what is resonance" is a PHYS 205 concept question and was
        # being answered with an LC formula. D38 again: the subject has to be the circuit.
        triggers=(("resonant", "frequency"), ("resonance", "circuit"), ("resonance", "lc"),
                  ("lc", "frequency"), ("tank", "circuit")),
        spoken=("Resonant frequency is 1 divided by 2 pi times the square root of L C. "
                "That's where the inductive and capacitive reactances cancel out."),
    ),

    Formula(
        key="capacitor_energy",
        triggers=(("energy", "capacitor"), ("joules", "capacitor"),
                  ("energy", "stored")),
        spoken=("Energy in a capacitor is a half C V squared. "
                "1000 microfarads at 12 volts holds about 72 millijoules."),
        worked=(Worked("0.5 x 1000uF x 12V squared",
                       0.5 * 1000e-6 * 12 ** 2 * 1000, "millijoules"),),
    ),

    Formula(
        key="rms",
        triggers=(("rms",), ("root mean square",), ("peak", "average")),
        spoken=("For a sine wave, RMS is the peak divided by the square root of 2, "
                "which is about 0.707 times the peak. "
                "Mains at 120 volts RMS is about 170 volts peak."),
        worked=(Worked("120V RMS x sqrt(2)", 120 * 2 ** 0.5, "volts"),),
    ),

    Formula(
        key="decibels",
        triggers=(("decibels",), ("decibel",), ("db", "formula"), ("db", "calculate")),
        spoken=("For voltage it's 20 times the log of the ratio. "
                "For power it's 10 times the log. "
                "So double the voltage is about 6 dB, and double the power is about 3."),
    ),

    Formula(
        key="opamp_gain",
        triggers=(("op amp", "gain"), ("opamp", "gain"), ("amplifier", "gain"),
                  ("non inverting",), ("inverting", "gain")),
        spoken=("Non inverting gain is 1 plus feedback over input resistor. "
                "Inverting is minus feedback over input. "
                "The non inverting one can never go below a gain of 1."),
    ),

    Formula(
        key="nyquist",
        # A bare ("nyquist",) used to be here. Harry Nyquist has at least four things named
        # after him and this entry is only ONE of them — it was answering the Nyquist
        # stability criterion (control), the Nyquist plot (control) and Johnson-Nyquist noise
        # with the sampling theorem. Qualify the name; never match it alone.
        triggers=(("nyquist", "rate"), ("nyquist", "frequency"), ("nyquist", "limit"),
                  ("sampling", "rate"), ("sample", "rate"),
                  ("aliasing",), ("sampling", "theorem")),
        spoken=("You need to sample at more than twice your highest frequency. "
                "Anything above half your sample rate folds back down and masquerades "
                "as a lower frequency, and once it's in there you can't get it out."),
    ),

    Formula(
        key="kirchhoff",
        triggers=(("kirchhoff",), ("kirchoff",), ("current law",), ("voltage law",)),
        spoken=("Current into a node equals current out of it. "
                "And voltages around any loop add up to zero. "
                "Those two get you through most of circuit analysis."),
    ),

    Formula(
        key="wavelength",
        triggers=(("wavelength",), ("wave length",)),
        spoken=("Wavelength is the speed of light divided by frequency. "
                "So 100 megahertz is about 3 meters."),
        worked=(Worked("c / 100MHz", 299_792_458 / 100e6, "meters"),),
    ),

    Formula(
        key="thermal_noise",
        triggers=(("johnson", "noise"), ("thermal", "noise"), ("noise", "resistor")),
        spoken=("Johnson noise is the square root of 4 k T R B. "
                "A 10 kilohm resistor at room temperature over 20 kilohertz "
                "gives you about 1.8 microvolts."),
    ),

    # ======================================================================================
    # MATH AND SCIENCE — added 2026-08-14 on LB's instruction to keep "all prerequisite and
    # electives relevant to math and science". Scoped against his FALL 2026 degree audit:
    # MATH 241/242/243, MATH 340, EEGR 331, PHYS 205/206, CHEM 110, plus linear algebra,
    # geometry and trigonometry.
    #
    # The D32 test still applies unchanged: **would LB act on this number, and would a wrong
    # answer cost him real time?** So what lands here is the computational entries — the ones
    # with an arithmetic result. Vocabulary goes to `define.py`, which is a different question
    # ("what does this word mean") with a different shape of answer.
    #
    # These sit BELOW the electronics entries, which is the same most-specific-first rule the
    # rest of the table follows: the EE triggers are the narrower phrases.
    # ======================================================================================

    # --- algebra and coordinate geometry ---
    Formula(
        key="quadratic_formula",
        triggers=(("quadratic formula",), ("quadratic",), ("solve", "quadratic")),
        spoken=("Minus b, plus or minus the square root of b squared minus 4 a c, "
                "all divided by 2 a. "
                "If what's under the root goes negative, your roots are complex."),
    ),
    Formula(
        key="slope",
        triggers=(("slope", "line"), ("slope formula",), ("rise over run",),
                  ("gradient", "line")),
        spoken=("Slope is the change in y divided by the change in x. Rise over run. "
                "A steeper line has a bigger number, and a flat line is zero."),
    ),
    Formula(
        key="distance_formula",
        triggers=(("distance formula",), ("distance", "two points"), ("midpoint",)),
        spoken=("Distance is the square root of the change in x squared "
                "plus the change in y squared. "
                "From the origin to the point 3, 4 is 5 units. "
                "It's Pythagoras with coordinates."),
        worked=(Worked("sqrt(3^2 + 4^2)", (3 ** 2 + 4 ** 2) ** 0.5, "units"),),
    ),
    Formula(
        key="geometric_series",
        triggers=(("geometric series",), ("infinite series",), ("sum", "series")),
        spoken=("An infinite geometric series sums to the first term divided by "
                "1 minus the ratio, and only when that ratio is smaller than 1. "
                "Otherwise it diverges."),
    ),

    # --- geometry. LB asked for this explicitly on 2026-08-14. ---
    Formula(
        key="circle_area",
        triggers=(("area", "circle"), ("area of a circle",)),
        spoken=("Area of a circle is pi r squared. "
                "A radius of 5 gives about 78.5 square meters. "
                "Double the radius and the area goes up four times."),
        worked=(Worked("pi x 5^2", 3.141592653589793 * 25, "square meters"),),
    ),
    Formula(
        key="circle_circumference",
        triggers=(("circumference", "circle"), ("perimeter", "circle")),
        spoken=("Circumference is 2 pi r, or pi times the diameter. "
                "A radius of 5 gives about 31.4 meters."),
        worked=(Worked("2 pi x 5", 2 * 3.141592653589793 * 5, "meters"),),
    ),
    Formula(
        key="triangle_area",
        triggers=(("area", "triangle"), ("area of a triangle",)),
        spoken=("Area of a triangle is a half base times height. "
                "A base of 10 and a height of 6 gives 30 square meters. "
                "The height has to be perpendicular to the base."),
        worked=(Worked("0.5 x 10 x 6", 0.5 * 10 * 6, "square meters"),),
    ),
    Formula(
        key="sphere_volume",
        triggers=(("volume", "sphere"), ("surface area", "sphere")),
        spoken=("A sphere's volume is four thirds pi r cubed, "
                "and its surface area is 4 pi r squared. "
                "A radius of 3 holds about 113.1 cubic meters."),
        worked=(Worked("4/3 pi x 3^3", 4 / 3 * 3.141592653589793 * 27, "cubic meters"),),
    ),
    Formula(
        key="cylinder_volume",
        triggers=(("volume", "cylinder"), ("volume", "cone"), ("volume", "prism")),
        spoken=("A cylinder is the base area times the height, so pi r squared h. "
                "Radius 2 and height 10 gives about 125.7 cubic meters. "
                "A cone is a third of that."),
        worked=(Worked("pi x 2^2 x 10", 3.141592653589793 * 4 * 10, "cubic meters"),),
    ),

    # --- calculus. MATH 241 through 243. ---
    Formula(
        key="power_rule",
        triggers=(("power rule",), ("derivative", "x to the n")),
        spoken=("Bring the exponent down in front, then knock one off it. "
                "So the derivative of x cubed is 3 x squared. "
                "Integrating runs it backwards: raise the power, then divide by the new one."),
    ),

    # --- physics: PHYS 205 mechanics ---
    Formula(
        key="kinematics",
        triggers=(("kinematic equations",), ("equations of motion",), ("suvat",),
                  ("constant acceleration",)),
        spoken=("Final velocity is initial plus a t. "
                "Distance is initial v t plus a half a t squared. "
                "Dropped from rest for 3 seconds you're doing about 29.4 meters per second."),
        worked=(Worked("9.81 x 3", 9.81 * 3, "meters per second"),),
    ),
    Formula(
        key="pendulum_period",
        triggers=(("pendulum",), ("period", "pendulum")),
        spoken=("A pendulum's period is 2 pi times the square root of length over gravity. "
                "A 1 meter pendulum swings in about 2 seconds. "
                "The mass makes no difference at all."),
        worked=(Worked("2 pi sqrt(1/9.81)",
                       2 * 3.141592653589793 * (1 / 9.81) ** 0.5, "seconds"),),
    ),
    Formula(
        key="universal_gravitation",
        triggers=(("universal gravitation",), ("law of gravitation",),
                  ("gravitational force",)),
        spoken=("The force is big G times both masses, divided by the distance squared. "
                "Double the distance and the force drops to a quarter."),
    ),

    # --- physics: PHYS 206 electricity, magnetism, waves and optics ---
    Formula(
        key="photon_energy",
        triggers=(("energy", "photon"), ("photon energy",), ("planck", "einstein")),
        spoken=("Photon energy is Planck's constant times frequency, "
                "or h c over the wavelength. "
                "Green light at 500 nanometers carries about 2.5 electron volts."),
        worked=(Worked("hc/500nm in eV",
                       6.62607015e-34 * 299_792_458 / 500e-9 / 1.602176634e-19,
                       "electron volts"),),
    ),
    Formula(
        key="lens_equation",
        triggers=(("thin lens",), ("lens equation",), ("focal length",)),
        spoken=("1 over the focal length equals 1 over the object distance "
                "plus 1 over the image distance. "
                "A 10 centimeter lens with the object at 30 images at 15 centimeters."),
        worked=(Worked("1/(1/10 - 1/30)", 1 / (1 / 10 - 1 / 30), "centimeters"),),
    ),

    # --- chemistry: CHEM 110 ---
    Formula(
        key="dilution",
        triggers=(("dilution",), ("m1v1",), ("dilute", "solution")),
        spoken=("Molarity one times volume one equals molarity two times volume two. "
                "Take 50 millilitres of 2 molar up to 100 millilitres "
                "and you've got 1 molar."),
        worked=(Worked("2 x 50 / 100", 2 * 50 / 100, "molar"),),
    ),

    # --- probability and statistics: EEGR 331 ---
    Formula(
        key="z_score",
        triggers=(("z score",), ("standard score",), ("standardise",)),
        spoken=("A z score is your value minus the mean, divided by the standard deviation. "
                "Scoring 85 when the mean is 70 and the deviation is 10 "
                "puts you 1.5 standard deviations up."),
        worked=(Worked("(85 - 70) / 10", (85 - 70) / 10, "standard deviations"),),
    ),
    Formula(
        key="combinations",
        triggers=(("n choose k",), ("combinations formula",), ("how many combinations",),
                  ("binomial coefficient",)),
        spoken=("N factorial, divided by k factorial times n minus k factorial. "
                "5 choose 2 is 10 ways. "
                "Drop the k factorial and you're counting permutations instead."),
        worked=(Worked("5! / (2! 3!)", 10, "ways"),),
    ),

    # --- constants: cheap, and impossible to be wrong about ---
    Formula(
        key="speed_of_light",
        triggers=(("speed", "light"), ("fast", "light")),
        spoken="The speed of light is 299792458 meters per second, so near enough 3 times 10 to the 8.",
    ),

    Formula(
        key="speed_of_sound",
        triggers=(("speed", "sound"), ("fast", "sound")),
        spoken="Sound does about 343 meters per second in air at room temperature.",
    ),

    Formula(
        key="gravity",
        triggers=(("gravity",), ("acceleration", "gravity"), ("9 8",)),
        spoken="Gravity is 9.81 meters per second squared.",
    ),
)


def look_up(normalised: str) -> Formula | None:
    """Find the formula this question is asking for, or None.

    A pure function of a string — no clock, no model, no network. Takes text that
    `router.normalise()` has already lowercased, de-punctuated and whitespace-collapsed.

    Args:
        normalised: the transcript, normalised.

    Returns:
        The first Formula whose trigger set is fully satisfied, in table order.
    """
    if not normalised:
        return None
    for formula in FORMULAS:
        for group in formula.triggers:
            if all(_has_phrase(normalised, phrase) for phrase in group):
                return formula
    return None


def unspeakable(text: str) -> str:
    """The characters in `text` that Piper cannot say. Empty string means it is safe."""
    return "".join(sorted({c for c in text if c in UNSPEAKABLE}))


def keys() -> tuple[str, ...]:
    return tuple(f.key for f in FORMULAS)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from orchestrator.router import normalise

    questions = sys.argv[1:] or [
        "what is the time constant of an rc circuit",
        "what resistor do i need for an led",
        "whats the cutoff frequency",
        "what time is it",
    ]
    for q in questions:
        hit = look_up(normalise(q))
        print(f"  {q!r:52} -> {hit.key if hit else '(no formula)'}")
        if hit:
            print(f"      {hit.spoken}")
    if not re.search(r"\w", "".join(questions)):
        raise SystemExit(1)
