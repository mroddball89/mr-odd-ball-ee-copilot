#!/usr/bin/env python3
"""
Module:  verify_corrections.py
Purpose: Prove a correction is caught and kept, and that a QUESTION is not mistaken for one.
Author:  LB
Date:    2026-08-25

    python tools/verify_corrections.py
    python tools/verify_corrections.py --probe

No audio, no model, no key. Detection is pure string matching and the ledger is a Markdown file,
which is the whole reason both were built that way — the feature that fires when LB is annoyed
is the one that must not depend on a network or a quota.

## Section 2 is the one that bites

A correction detector earns its keep on the negatives. Every phrase that marks a rebuke also
appears inside an ordinary question — "why was that answer wrong" contains "that answer was
wrong" exactly — and a matcher without an anchor turns that question into a filed rebuke and
answers it with "I've written that down".

Worse, two of the near-misses are features that already work: `agents/persona_agent.py` routes
*"remember that I'm using the 2N3904"* to `save_to_vault`, and `orchestrator/instant.py` treats
*"never mind"* as ordinary speech. A greedy correction matcher does not fail to add a feature —
it silently takes those.

`--probe` removes both anchors and prints how many of section 2 get eaten.

## Section 4 writes to a TEMPORARY ledger

`vault/corrections.md` is LB's real file and this harness must not touch it. `corrections.LEDGER`
and `corrections.VAULT_DIR` are rebound to a temp directory for the duration; the check that
they were actually rebound is itself a check, because a harness that quietly wrote to the real
ledger would pass while corrupting the thing it tests.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from tools import corrections                                        # noqa: E402
from tools.corrections import Correction, detect                     # noqa: E402

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


# Corrections that MUST be caught, paired with the rule that must come out of them.
# "" means a bare rebuke — real, and carrying no rule of its own.
CORRECTIONS: list[tuple[str, str]] = [
    ("That was wrong.",                                     ""),
    ("No, that was wrong.",                                 ""),
    ("that's not right",                                    ""),
    ("You got that wrong.",                                 ""),
    ("That's not what I asked.",                            ""),
    # These two are the ones worth staring at. Both open with a directive marker — `dont` and
    # `stop <gerund>` — and both must come out as a BARE REBUKE with no rule, because the thing
    # they forbid is "that", and "that" has no referent once the turn scrolls away. A standing
    # rule reading "Don't do that" in every future prompt is noise at best.
    ("Don't do that.",                                      ""),
    ("stop doing that",                                     ""),
    # ...while the same markers WITH a real object do yield a rule. This is the pair that pins
    # the distinction down.
    ("Don't use relative paths for that.",                  "Don't use relative paths for that"),
    ("Always use absolute paths instead.",                  "Always use absolute paths instead"),
    ("always ask me before you run anything",               "always ask me before you run anything"),
    ("Never open Firefox without asking me first.",         "Never open Firefox without asking me first"),
    ("Don't use relative paths.",                           "Don't use relative paths"),
    ("Don't ever run apt-get without asking.",              "Don't ever run apt-get without asking"),
    ("From now on, use /home/lb/projects for everything.",  "From now on, use /home/lb/projects for everything"),
    ("from now on I want you to spell out the units",       "from now on I want you to spell out the units"),
    ("next time you should check the datasheet first",      "next time you should check the datasheet first"),
    ("you should always say the units out loud",            "you should always say the units out loud"),
    ("make sure you check the vault first",                 "make sure you check the vault first"),
    ("Stop using the shell for that.",                      "Stop using the shell for that"),
    ("in future, ask before you open anything",             "in future, ask before you open anything"),
    # The combined form — a rebuke AND the rule it is about. This is the shape LB actually
    # uses, and handling only the bare rebuke would file half his corrections with no rule.
    ("no that was wrong, always use absolute paths",        "always use absolute paths"),
    ("That was wrong. Never do that again.",                "Never do that again"),
]

# Lines that must be ANSWERED, not filed. Each is a real thing to say to an engineering copilot,
# and each contains a phrase the detector would match without its anchors.
NOT_CORRECTIONS: list[str] = [
    # questions ABOUT something being wrong
    "why was that answer wrong",
    "explain why that answer was wrong",
    "was my calculation wrong",
    "what did I do wrong on this board",
    "is it wrong to always use absolute paths",
    "why do you always do that",
    "how do I know if the polarity is wrong",
    # questions containing a directive word
    "what does always on mean",
    "does the esp32 always boot into download mode",
    "should I never exceed the gate voltage",
    "do not disturb mode on the pi",
    "how do I stop the motor",
    "what's the never-exceed rating on this part",
    # the two that are OTHER FEATURES, and the reason they matter most
    "remember that I'm using the 2N3904",
    "remember to buy more resistors",
    "never mind",
    "never mind, I've got it",
    "save that to the vault",
    # ordinary traffic
    "stop the quiz",
    "next time I'll do it myself",
    "don't I need a pullup resistor",
    "what time is it",
    "tell me a joke",
    "whats the trace width for 5 amps",
    "open firefox",
    "what's on my screen",
    "sync canvas",
]

# =========================================================================================
section("1. a correction is caught, and the RULE is LB's own words")
# =========================================================================================

for text, want_rule in CORRECTIONS:
    found = detect(text)
    check(found is not None, f"correction: {text!r}",
          "" if found else "this would be routed and answered instead of recorded")
    if found is None:
        continue
    check(found.rule == want_rule, f"...rule is {want_rule!r}", f"got {found.rule!r}")
    check(found.said == text.strip(), "...and what he SAID is kept verbatim", f"got {found.said!r}")

# The property the whole module rests on: the rule is sliced from the RAW text, so a path
# survives. `normalise` strips slashes, and a rule about absolute paths with the slashes
# removed is a rule that says the opposite of what LB meant.
pathy = detect("Always use /home/lb/projects, never a relative path.")
check(pathy is not None and "/home/lb/projects" in pathy.rule,
      "a path in a rule survives — it is sliced from the RAW text, not the normalised one",
      f"got {pathy.rule!r}" if pathy else "not detected at all")

# =========================================================================================
section("2. NEGATIVES — a question that mentions being wrong is a QUESTION")
# =========================================================================================

for text in NOT_CORRECTIONS:
    found = detect(text)
    check(found is None, f"not a correction: {text!r}",
          f"would be filed as a rule: {found.rule!r}" if found else "")

check(detect("") is None, "an empty line is not a correction")
check(detect("   ") is None, "whitespace is not a correction")
check(detect(None) is None, "None is not a correction and does not raise")

# =========================================================================================
section("3. the two features a greedy matcher would silently steal")
# =========================================================================================

check("remember to" not in corrections._DIRECTIVES
      and "remember that" not in corrections._DIRECTIVES,
      "the `remember` family is NOT a directive marker",
      "persona_agent routes 'remember that I'm using the 2N3904' to save_to_vault; "
      "claiming it here would take the vault's traffic")
check("remember" in corrections._REQUEST_OPENERS,
      "...and `remember` is explicitly listed as a request opener")
check("mind" in corrections._NOT_RULES,
      "'never mind' cannot become a standing rule",
      "filing it would put 'never mind' in front of every future answer")
check(corrections._REBUKE_FILLER is not None
      and "mr" in corrections._REBUKE_FILLER,
      "the rebuke filler set is its own, so 'that was wrong, Mr Odd Ball' still counts")

# =========================================================================================
section("4. the ledger round-trips, on a TEMPORARY file")
# =========================================================================================

_real_ledger = corrections.LEDGER
_real_vault = corrections.VAULT_DIR
_tmp = Path(tempfile.mkdtemp(prefix="oddball-corrections-"))
corrections.VAULT_DIR = _tmp
corrections.LEDGER = _tmp / "corrections.md"

check(corrections.LEDGER != _real_ledger,
      "the harness is writing to a temp ledger, NOT to LB's real one",
      f"real: {_real_ledger}")

try:
    check(corrections.clear(), "an empty ledger can be created")
    check(corrections.active_rules() == [], "...and it has no standing rules")
    check(corrections.for_prompt() == "",
          "an empty ledger injects NOTHING into a prompt",
          "a heading with no rules under it teaches the model the section is decoration")

    saved = corrections.record(detect("Always use absolute paths."),
                               context='I had just said: "saved to notes/x.md"')
    check(saved is not None, "a correction is written")
    check(saved.when != "", "...and stamped with a time")

    rules = corrections.active_rules()
    check(len(rules) == 1, "one rule comes back", f"got {len(rules)}")
    check(rules[0].rule == "Always use absolute paths", "...with the rule intact",
          f"got {rules[0].rule!r}")
    check("saved to notes/x.md" in rules[0].context, "...and the context survives the round trip")

    # Given twice, it is ONE rule in the prompt and TWO lines in the record. Both halves matter:
    # the model must not be told the same thing twice, and LB must be able to see that he had
    # to say it twice.
    corrections.record(detect("Always use absolute paths."))
    check(len(corrections.active_rules()) == 1,
          "the same rule given twice is ONE standing rule",
          f"got {len(corrections.active_rules())}")
    check(corrections.LEDGER.read_text(encoding="utf-8").count("- **Rule:**") == 2,
          "...but BOTH times are in the file, because having to repeat it is evidence")

    corrections.record(detect("Never open Firefox without asking."))
    block = corrections.for_prompt()
    check("Always use absolute paths" in block and "Never open Firefox" in block,
          "both rules reach the prompt")
    check("OVERRIDE" in block, "...and they are stated as overriding, not as background")
    check(block.index("Always use absolute paths") < block.index("Never open Firefox"),
          "...in the order they were given, oldest first")

    # A bare rebuke is recorded, and is honest that it carries no rule.
    corrections.record(detect("That was wrong."), context="context here")
    bare = corrections.active_rules()[-1]
    check(bare.rule == "", "a bare rebuke is stored with no rule")
    check(bare.instruction == "That was wrong.",
          "...and falls back to what he said when it reaches a prompt")

    # A hand-edited ledger must not take the feature down — the banner promises LB can edit it.
    corrections.LEDGER.write_text("total nonsense\n## not a timestamp\n- **Rule:** x\n",
                                  encoding="utf-8")
    check(corrections.active_rules() == [], "a hand-mangled ledger reads as empty, not as a crash")
    check(corrections.for_prompt() == "", "...and injects nothing")

    corrections.LEDGER.write_text("", encoding="utf-8")
    check(corrections.active_rules() == [], "an empty file reads as no rules")
    check(corrections.record(detect("Always ask first.")) is not None,
          "...and can still be written to")
finally:
    corrections.LEDGER = _real_ledger
    corrections.VAULT_DIR = _real_vault
    shutil.rmtree(_tmp, ignore_errors=True)

check(corrections.LEDGER == _real_ledger, "the real ledger path was restored afterwards")

# =========================================================================================
section("5. the prompt block is bounded")
# =========================================================================================

check(corrections.MAX_PROMPT_CHARS <= 4000,
      f"the injected block is capped at {corrections.MAX_PROMPT_CHARS} characters",
      "this rides on EVERY agent call; unbounded, it eats the context window a rule at a time")
check(corrections.MAX_RULES <= 60, f"at most {corrections.MAX_RULES} rules reach a prompt")

# =========================================================================================


def probe() -> int:
    """Remove both anchors and count how many questions get eaten.

    The loosened matcher is what a first draft looks like: match a rebuke anywhere in the line,
    and treat a directive word anywhere as a rule. That is the version that files "why was that
    answer wrong" as a correction and steals "remember that I'm using the 2N3904" from the vault.
    """
    print("\n  PROBE: matching anywhere in the line instead of anchored\n")

    import re

    from orchestrator.instant import normalise

    loose_markers = corrections._DIRECTIVES + ("remember to", "remember that", "stop")

    def loose_detect(text: str) -> str | None:
        flat = normalise(text)
        for phrase in corrections._REBUKES:
            if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", flat):
                return f"rebuke {phrase!r}"
        for marker in loose_markers:
            if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", flat):
                return f"directive {marker!r}"
        return None

    caught = 0
    for text in NOT_CORRECTIONS:
        hit = loose_detect(text)
        if hit:
            caught += 1
            print(f"   WOULD FILE   {text!r}   ({hit})")

    print(f"\n  {caught}/{len(NOT_CORRECTIONS)} questions would be filed as corrections "
          f"instead of answered.")
    if caught:
        print("  The harness BITES: section 2 goes red without the anchors.\n")
        return 0
    print("  The harness is VACUOUS: loosening the rule changed nothing.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify correction detection and the ledger")
    ap.add_argument("--probe", action="store_true")
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
