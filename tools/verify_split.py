#!/usr/bin/env python3
"""
Module:  verify_split.py
Purpose: Prove the speech/visual split — and prove it can fail.
Author:  LB
Date:    2026-08-19

    python tools/verify_split.py
    python tools/verify_split.py --probe     # reintroduce the bug, expect RED

No model, no audio, no network. Every input here is a literal string, so this runs in
milliseconds and can be run on every save.

## Section 4 is the one that matters

A filter that accepts everything passes every reachability test ever written for it. So
section 4 is **negatives only**: things that must NOT be spoken. If `is_speakable()` were
replaced with `return None` — accept anything — sections 1-3 would stay green and section 4
would go red. That is the property being bought, and `--probe` demonstrates it rather than
asserting it, because a claim that a harness bites is worth exactly as much as the last time
somebody checked.

This is the same discipline as `verify_actions.py` section 4, and for the same reason: the
consequence of a false accept here is Piper reading a 400-line stack trace at LB while he is
holding a soldering iron.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# This harness prints the very characters it exists to catch — τ, Ω, μ, ° — and a Windows
# console defaults to cp1252, which cannot encode them. Without this the probe dies partway
# through with a UnicodeEncodeError and looks like a failure of the code under test.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from engine.response import CardKind                        # noqa: E402
from engine.split import expand_symbols, is_speakable, split   # noqa: E402

PASSED = 0
FAILED = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"   PASS  {what}")
    else:
        FAILED += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def section(name: str) -> None:
    print(f"\n  {name}")


# =========================================================================================
section("1. the agent's own SPOKEN line is preferred")
# =========================================================================================

REPLY_FIRMWARE = """\
To configure GPIO 13 as an output on the ESP32 you set the corresponding bit in the
GPIO enable register.

```c
// Set GPIO 13 as output
REG_WRITE(GPIO_ENABLE_REG, BIT13);
```

SPOKEN: Set bit thirteen of the GPIO enable register to make that pin an output.
"""

r = split(REPLY_FIRMWARE, route="firmware")
check(r.speech == "Set bit thirteen of the GPIO enable register to make that pin an output.",
      "the SPOKEN line becomes the speech", r.speech)
check("SPOKEN" not in r.speech, "the SPOKEN: marker itself is stripped", r.speech)
check(any(c.kind == CardKind.CODE for c in r.cards), "the C block becomes a code card",
      f"{[c.kind for c in r.cards]}")
code = [c for c in r.cards if c.kind == CardKind.CODE][0]
check(code.lang == "c" and "REG_WRITE" in code.body, "the card keeps the language and the code",
      f"lang={code.lang!r}")
check("REG_WRITE" not in r.speech and "```" not in r.speech,
      "no code reaches the speech channel", r.speech)

# A model that quotes the instruction back must not have its example taken as the answer.
r = split("SPOKEN: <one sentence>\n\nReal answer here.\nSPOKEN: The capacitor is ten microfarads.",
          route="general")
check(r.speech == "The capacitor is ten microfarads.", "the LAST SPOKEN line wins", r.speech)

# =========================================================================================
section("2. extraction takes over when the line is missing")
# =========================================================================================

r = split("The cutoff frequency is 15.92 hertz for that filter.", route="math")
check("15.92" in r.speech, "a clean sentence is extracted verbatim", r.speech)
check(len(r.speech.split()) <= 40, "extraction respects the 40-word budget",
      f"{len(r.speech.split())} words")

r = split("", route="general")
check(r.speech and is_speakable(r.speech) is None, "an empty reply still says something",
      r.speech)

r = split("```c\nint x = 1;\n```", route="firmware")
check(r.speech == "I've put the code and the register details on the screen.",
      "a code-only reply falls back to the route's line", r.speech)
check(len(r.cards) == 1 and r.cards[0].kind == CardKind.CODE,
      "and the code is still shown", f"{[c.kind for c in r.cards]}")

# =========================================================================================
section("3. tables and tool output become cards, not speech")
# =========================================================================================

REPLY_HARDWARE = """\
For 5 A on 2 oz internal copper with a 20 C rise:

| Parameter | Value |
|---|---|
| Trace width | 35.12 mils |
| Cross section | 62.3 sq mils |

SPOKEN: You need a trace at least thirty five mils wide.
"""
r = split(REPLY_HARDWARE, route="hardware")
check(any(c.kind == CardKind.TABLE for c in r.cards), "a markdown table becomes a table card",
      f"{[c.kind for c in r.cards]}")
check("|" not in r.speech, "no pipes reach the speech channel", r.speech)
table = [c for c in r.cards if c.kind == CardKind.TABLE][0]
check("35.12 mils" in table.body, "the table card keeps its numbers")

r = split("Terminal Output:\n45000\ntotal 12\ndrwxr-xr-x 2 pi pi", route="os")
check(any(c.kind == CardKind.LOG for c in r.cards), "terminal output becomes a log card",
      f"{[c.kind for c in r.cards]}")
check("drwxr-xr-x" not in r.speech, "no directory listing reaches the speech channel",
      r.speech)

r = split("Python Sandbox Execution Result:\nCut-off frequency: 15.92 Hz", route="math")
check(any(c.kind == CardKind.LOG for c in r.cards), "REPL output becomes a log card")

# Prose that is not the spoken line is still worth showing.
r = split("A long paragraph of reasoning about impedance matching that goes on for a while "
          "and explains the whole derivation step by step in detail.\n"
          "SPOKEN: Match the source impedance to the load.", route="general")
check(any(c.kind == CardKind.MARKDOWN for c in r.cards),
      "leftover prose is kept as a markdown card", f"{[c.kind for c in r.cards]}")

# =========================================================================================
section("4. NEGATIVES — what must never be spoken. This is the section that bites.")
# =========================================================================================

MUST_REJECT = [
    ("```c\nint x;\n```",                              "fenced code"),
    ("| a | b |\n|---|---|\n| 1 | 2 |",                "a markdown table"),
    ("See https://example.com/datasheet.pdf for more", "a URL"),
    ("It is at /sys/class/thermal/thermal_zone0/temp", "a unix path"),
    ("Open C:\\Users\\ironi\\notes.txt",                "a windows path"),
    ("Write 0x3F to the control register",             "a hex literal"),
    ("Traceback (most recent call last):\n  File \"x\"", "a traceback"),
    ("Call REG_WRITE with the enable mask",            "a code identifier"),
    ("Set GPIO_ENABLE_REG appropriately",              "an underscored identifier"),
    ("The time constant is 10 ms where τ = RC",        "an unspeakable glyph (tau)"),
    ("Resistance is 4.7 kΩ across the divider",        "an unspeakable glyph (ohm)"),
    ("A capacitance of 100 μF is needed here",         "an unspeakable glyph (mu)"),
    ("The area is π r squared for that circle",        "an unspeakable glyph (pi)"),
    ("Temperature rose by 20° over the run",           "an unspeakable glyph (degree)"),
    ("The value is ≈ 15 hertz after rounding",         "an unspeakable glyph (approx)"),
    ("word " * 45,                                     "over the 40-word budget"),
    ("",                                               "an empty string"),
    ("   \n  ",                                        "whitespace only"),
]

for text, why in MUST_REJECT:
    got = is_speakable(text)
    # `is not None`, NOT a truth test: Rejection.__bool__ is False by design, so `if got`
    # would report every correctly-rejected input as "ACCEPTED". A falsey result object is
    # convenient at the call site and a trap in the harness that judges it.
    check(got is not None, f"is_speakable REJECTS {why}",
          f"got {got.reason!r}" if got is not None else "ACCEPTED — this is the bug")

# ...and the same inputs, driven through split(), must never surface as speech.
for text, why in MUST_REJECT:
    r = split(f"SPOKEN: {text}" if text.strip() else text, route="general")
    check(is_speakable(r.speech) is None,
          f"split() never emits {why} as speech",
          f"speech={r.speech[:60]!r}")

MUST_ACCEPT = [
    "Set bit thirteen of the GPIO enable register.",
    "You need a trace at least thirty five mils wide.",
    "The cutoff frequency is 15.92 hertz.",
    "It's Wednesday, August 19.",
    "I'm Mr Odd Ball. I live on the Pi and I do what I can.",
    "That's 4.7 kilohms across the divider.",
    "The time constant is resistance times capacitance.",
]
for text in MUST_ACCEPT:
    got = is_speakable(text)
    check(got is None, f"is_speakable ACCEPTS {text[:44]!r}",
          f"rejected as {got.reason}: {got.detail}" if got else "")

# =========================================================================================
section("5. the split is lossless — nothing is silently dropped")
# =========================================================================================

r = split(REPLY_FIRMWARE, route="firmware")
check(r.raw == REPLY_FIRMWARE, "the unsplit reply is kept on the Response")
check(r.route == "firmware", "the route is recorded")
shown = " ".join(c.body for c in r.cards)
check("REG_WRITE(GPIO_ENABLE_REG, BIT13);" in shown,
      "every line of the code survives into a card")
check("GPIO enable register" in shown or "GPIO enable register" in r.speech,
      "the explanation survives somewhere")

r = split(REPLY_HARDWARE, route="hardware")
check("35.12" in " ".join(c.body for c in r.cards),
      "a number that cannot be spoken is still written down")

# =========================================================================================
section("6. symbols become words, so a correct sentence is not thrown away")
# =========================================================================================

# TWO different failures, and they are worth separating because only one of them loses words.
#
# (a) REJECTION. `is_speakable()` refuses text containing Ω μ ° ± τ, and a refused sentence is
#     replaced wholesale by "I've put it on the screen." Writing the answer in correct
#     engineering notation was the reliable way to stop it being said AT ALL. These go from
#     dropped to spoken, and that is the fix that matters.
for original, expected_word in [
    ("The trace needs 0.9 mm for a 20°C rise.",        "degrees Celsius"),
    ("Use a 10 kΩ resistor.",                          "kilohms"),
    ("That is 5 Ω.",                                   "ohms"),
    ("A 100 μF capacitor.",                            "microfarads"),
    ("Tolerance is ±5 percent.",                       "plus or minus"),
    ("The time constant τ is 1 ms.",                   "tau"),
    ("The area is 4 mm².",                             "squared"),
]:
    check(is_speakable(original) is not None,
          f"{original[:32]!r:36} was REJECTED before", "if this fails the premise is stale")
    expanded = expand_symbols(original)
    check(is_speakable(expanded) is None and expected_word in expanded,
          f"{original[:32]!r:36} -> now spoken", f"expanded: {expanded}")

# (b) MISPRONUNCIATION. `&` and `%` are not in the rejected set — they were always spoken, just
#     badly, because Piper renders them as a stumble or as nothing. Expanding them changes how
#     the sentence SOUNDS, not whether it is said. Asserted separately so the two are not
#     confused: an earlier version of this harness claimed these were rejected, which was
#     simply untrue and would have quietly overstated what the fix does.
for original, expected_word in [("Efficiency is 85%.", "percent"),
                                ("Set R1 & R2 the same.", "and")]:
    check(is_speakable(original) is None,
          f"{original[:32]!r:36} was already speakable", "not a rejection case")
    check(expected_word in expand_symbols(original),
          f"{original[:32]!r:36} -> pronounced properly", expand_symbols(original))

# Prefix before bare unit. This ordering is the whole reason _EXPANSIONS is a tuple and not a
# dict comprehension over a set — "kΩ" must be matched before "Ω".
check(expand_symbols("10 kΩ") == "10 kilohms" and expand_symbols("10 Ω") == "10 ohms",
      "a prefixed unit is not split into 'k ohms'",
      f"{expand_symbols('10 kΩ')!r} / {expand_symbols('10 Ω')!r}")
check(expand_symbols("20°C") == "20 degrees Celsius",
      "°C is matched before the bare degree sign", expand_symbols("20°C"))

# Punctuation must survive. The first version of this deleted it: the space-trimming regex
# was written with a backreference that got eaten, so "5 Ω." became "5 ohms" with the full
# stop gone — and a sentence with no terminator changes how Piper lands the prosody.
check(expand_symbols("That is 5 Ω.").endswith("ohms."),
      "the full stop survives expansion", expand_symbols("That is 5 Ω."))
check(expand_symbols("At 5 Ω, it works") == "At 5 ohms, it works",
      "a comma survives, with no space before it", expand_symbols("At 5 Ω, it works"))

# Untouched when there is nothing to do — the common case must not be reformatted.
plain = "The cutoff frequency is 15.92 hertz."
check(expand_symbols(plain) == plain, "text with no symbols is returned unchanged")
check(expand_symbols("") == "" and expand_symbols(None) is None,
      "empty and None are safe")

# And end to end through split(), which is what actually runs on the turn path.
r = split("SPOKEN: The rise is 20°C at 4.7 kΩ.", route="hardware")
check("degrees Celsius" in r.speech and "kilohms" in r.speech,
      "split() speaks an expanded SPOKEN: line", r.speech)
check("I've put" not in r.speech,
      "...instead of falling back to 'it's on the screen'", r.speech)

# The CARD keeps the symbols. It is read, not heard, and "20°C" is correct on screen.
r = split("The rise is 20°C at 4.7 kΩ.", route="hardware")
shown = " ".join(c.body for c in r.cards)
check("°C" in shown or "20°C" in shown,
      "the card keeps the original notation — expansion is for the ear only", shown[:70])

# =========================================================================================


def probe() -> int:
    """Reintroduce the bug this harness exists to catch, and confirm section 4 goes red.

    The bug: `is_speakable` accepts everything — the shape a filter takes when somebody
    "simplifies" it, or short-circuits it while debugging and forgets to put it back.

    **This drives the real `split()`, not the patched predicate.** Asserting that a lambda
    returning None returns None would be a tautology dressed as a probe, and a probe that
    cannot fail is worth less than no probe at all, because it buys false confidence. What is
    measured here is whether forbidden content actually reaches `Response.speech` — the thing
    Piper would read out — once the filter stops filtering.
    """
    import engine.split as mod

    real = mod.is_speakable
    print("\n  PROBE: is_speakable() accepts everything; driving the real split()\n")
    mod.is_speakable = lambda text, max_words=mod.MAX_WORDS: None

    leaked = 0
    try:
        for text, why in MUST_REJECT:
            if not text.strip():
                continue                      # empty in, fallback out — not a leak either way
            spoke = mod.split(f"SPOKEN: {text}", route="general").speech
            # Judged with the REAL filter, which is the only opinion that counts.
            verdict = real(spoke)
            if verdict is not None:
                leaked += 1
                print(f"   LEAKED  {why:<34} -> {spoke[:52]!r}")
    finally:
        mod.is_speakable = real

    testable = sum(1 for t, _ in MUST_REJECT if t.strip())
    print(f"\n  {leaked}/{testable} forbidden inputs reached the speech channel with the bug in.")
    if leaked == testable:
        print("  The harness BITES: every negative in section 4 goes red.\n")
        return 0
    if leaked:
        print(f"  PARTIAL: {testable - leaked} negative(s) would stay green. Those checks are\n"
              "  passing for some other reason and are not pulling their weight.\n")
        return 1
    print("  The harness is VACUOUS: nothing leaked with the filter removed.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the speech/visual split")
    ap.add_argument("--probe", action="store_true",
                    help="reintroduce the bug and confirm the negatives catch it")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(0)
