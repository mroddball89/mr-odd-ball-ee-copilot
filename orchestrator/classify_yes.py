#!/usr/bin/env python3
"""
Module:  classify_yes.py
Purpose: Did LB say yes?
Author:  LB
Date:    2026-08-12 (extracted from orchestrator/classify.py 2026-08-19)

The tier system is gone from this repo — `router.py` decides who answers now, and
`classify.py` went with it. **This function did not**, because it has nothing to do with
tiering: it reads a spoken yes or no, and the permission gates need exactly that.

It was proven against the cloud boundary in the standalone assistant, where the cost of
getting it wrong was spending the network without being asked. Here the cost is running a
shell command on the Pi without being asked, which is strictly worse, so it arrives unchanged.

## None is not False

`is_yes` returns three things, and the third one matters:

    True   a clear yes            -> go ahead
    False  a deliberate no        -> worth acknowledging out loud
    None   neither                -> also do not go ahead, but say nothing about it

Callers treat False and None identically for the decision — **anything short of a clear yes is
a no** — and differently for the reply. A refusal deserves "no problem"; a mumble does not
deserve a lecture about being unclear.
"""

from __future__ import annotations

import re

__all__ = ["is_yes", "normalise", "approve_at_keyboard"]

# **`_NO` may be generous; `_YES` must be strict.** The costs are not symmetric: a false "no"
# costs one repeated question, and a false "yes" runs a shell command LB refused. The module
# docstring has always said "anything short of a clear yes is a no" — these two lists did not
# honour it until 2026-09-02.
#
# Three bare words are GONE from `_YES` and each one was a hole:
#   "do"      -> "What does that do?", "Do I need to do that?"  both approved execution
#   "course"  -> "Of course not"                                approved execution
#   "please"  -> "please read that back to me"                  approved execution
# The compound forms they appear in are unambiguous and stay: "do it", "please do", "of course".
_YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go on", "go ahead", "please do",
        "do it", "run it", "go for it", "why not", "affirmative", "of course",
        "definitely", "yes please")

# Checked FIRST, so anything here beats anything in `_YES`. That is what lets "of course" stay
# a yes while "of course not" is a no, and it is why this list can afford to be wide.
#
# "hold on" and "wait" are refusals HERE even though they are not refusals in English: at a gate
# they mean "not yet", and a gate that hears "not yet" and runs the command has not listened.
_NO = ("no", "nope", "nah", "dont", "do not", "never mind", "nevermind", "forget it",
       "leave it", "cancel", "stop", "no thanks", "negative", "dont bother", "skip it",
       "abort", "no way", "of course not", "course not", "rather not", "better not",
       "not now", "not yet", "hold on", "hold off", "wait", "dont run", "dont do")

# Words that can only ever begin a question. `do`, `does`, `is`, `can` and `should` are
# deliberately ABSENT: "do it" is a yes, and a list that cannot tell those apart would refuse
# the commonest approval there is. The `?` check below carries the rest.
_INTERROGATIVE = ("what", "whats", "why", "how", "hows", "when", "where", "which", "who",
                  "whose", "whom")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Whisper punctuates and capitalises; none of that should change the answer.

    **Apostrophes are removed, not replaced with a space**, and that one character is the whole
    reason this function had a bug in front of shell execution for three weeks. The docstring
    always claimed it — *"so `don't` and `dont` are the same word"* — and the code did the
    opposite: `[^a-z0-9 ]+ -> " "` turned `don't` into `don t`, which is two words and neither
    of them is `dont`. So `_NO` never matched an apostrophised refusal, and
    `is_yes("Don't run it")` fell through to `_YES`, matched "run it", and **returned True**.

    Both apostrophes are handled. Whisper emits the curly `’` and a keyboard emits `'`;
    `engine/core.py` normalises the same pair for the same reason.
    """
    stripped = text.lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", stripped)).strip()


def is_question(transcript: str) -> bool:
    """True when `transcript` is asking rather than answering.

    A question is not consent in any language, and nothing in this module knew that:
    "What does that do?" — the single most reasonable thing to say to a proposed shell command —
    matched the bare "do" in `_YES` and approved it.

    Takes the RAW text, before `normalise`, because the question mark is the strongest signal
    available and normalising destroys it.
    """
    raw = (transcript or "").strip()
    if "?" in raw:
        return True
    first = normalise(raw).split(" ")[0] if normalise(raw) else ""
    return first in _INTERROGATIVE


def _matches(text: str, phrases: tuple[str, ...]) -> str | None:
    """The first phrase present as whole words, or None.

    Whole-word matching, not substring: "no" must not match inside "know", and this is the
    function standing between a model's proposed shell command and it running.
    """
    for phrase in phrases:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text):
            return phrase
    return None


def is_yes(transcript: str) -> bool | None:
    """Did LB agree?

    Args:
        transcript: what was heard or typed.

    Returns:
        True for a clear yes, False for a clear no, None when it is neither.

    The ORDER of these checks is the safety property, not style:

    1. **A WHOLE utterance that is exactly a listed phrase is an answer, not a question.**
       "why not" is agreement and begins with an interrogative; without this it would be read
       as a question and silently decline. Only an exact, complete match qualifies, so nothing
       longer can smuggle itself past the question rule below.
    2. **A question is neither yes nor no.** "What does that do?" is asking, and the caller
       treats None as a decline — so the gate closes and LB asks again. That is the right trade
       against the alternative, which was running the command he was asking about. (The nicer
       behaviour is to answer the question and re-offer the gate; that is on the board, not in
       this change.)
    3. **"no" beats "yes".** "no thanks" contains no yes word, but "ok no" contains both, and
       "of course not" contains "of course". A refusal must never be read as consent.
    4. Only then, a clear yes.
    """
    text = normalise(transcript)
    if not text:
        return None
    if text in _NO:
        return False
    if text in _YES:
        return True
    if is_question(transcript):
        return None
    if _matches(text, _NO):
        return False
    if _matches(text, _YES):
        return True
    return None


def approve_at_keyboard(prompt: str = "   Allow execution? (y/n): ") -> bool:
    """Ask for approval at the terminal. **Only a typed `y` returns True.**

    The blocking terminal gates in `agents/os_agent.py` and `agents/web_agent.py` both used to
    call `gesture_control.approve_by_gesture_or_keyboard`, which tried the camera first and
    fell through to this. The camera is gone (2026-08-29) and what was left was identical in
    both files, so it lives here once instead of twice.

    Deliberately stricter than `is_yes` and it stays that way. `is_yes` reads a SPOKEN answer,
    where "sure" and "go ahead" are what a person actually says and refusing them would make
    the gate feel broken. This reads a KEYSTROKE, where the only reason to type anything other
    than `y` is that the answer is not yes.

    Args:
        prompt: what to print at the terminal.

    Returns:
        True if approved.
    """
    try:
        return input(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        # No stdin, or ctrl-C at the prompt. Both are declines. A gate that defaults open
        # under an unexpected condition is not a gate.
        print()
        return False


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["yes", "no thanks", "ok no", "go ahead", "hmm", ""]:
        print(f"  {arg!r:20} -> {is_yes(arg)}")
