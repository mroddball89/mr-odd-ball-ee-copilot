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

__all__ = ["is_yes", "normalise"]

# Anything short of a clear yes declines, so the "no" list exists only to distinguish a
# deliberate refusal (worth a brief acknowledgement) from silence or a misheard mumble.
_YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go on", "go ahead", "please do",
        "do it", "run it", "go for it", "why not", "affirmative", "course", "of course",
        "definitely", "please", "yes please", "do")
_NO = ("no", "nope", "nah", "dont", "do not", "never mind", "nevermind", "forget it",
       "leave it", "cancel", "stop", "no thanks", "negative", "dont bother", "skip it",
       "abort", "no way")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Whisper punctuates and capitalises; none of that should change the answer. Apostrophes are
    dropped rather than kept so "don't" and "dont" are the same word.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


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

    "no" is checked FIRST. "no thanks" contains no yes word, but "ok no" contains both — and a
    refusal must never be read as consent. Ordering is the safety property here, not style.
    """
    text = normalise(transcript)
    if not text:
        return None
    if _matches(text, _NO):
        return False
    if _matches(text, _YES):
        return True
    return None


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["yes", "no thanks", "ok no", "go ahead", "hmm", ""]:
        print(f"  {arg!r:20} -> {is_yes(arg)}")
