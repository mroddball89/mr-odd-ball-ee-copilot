#!/usr/bin/env python3
"""
Module:  credible.py
Purpose: Decide whether a transcript is backed by enough real speech to act on.
Author:  LB
Date:    2026-08-29

    python -m orchestrator.credible

## What this is for

`base.en` does not return nothing when handed near-silence. **It invents a sentence.** From
`data/oddball.log`, all measured, all with the capture's own numbers beside them:

    2.48s audio, 0.08s voiced   ->  "Thank you for watching."
    3.12s audio, 0.32s voiced   ->  "I don't know what it is."
    15.52s audio, 0.48s voiced  ->  "Come on!"
    18.08s audio, 0.96s voiced  ->  "Yes. Yes."

The first cost a routed turn and a paid API call for a sentence nobody said. **The last one
approved an OS command.** `engine/turn.py` handed "Yes. Yes." to `classify_yes.is_yes`, which
correctly reported a yes, and a command ran — on the strength of 0.96 seconds of voiced audio
spread through eighteen seconds of room tone. `is_yes` was not wrong; it was asked the wrong
question. It reads what the words mean, and nothing was reading whether there had been words.

This module is that second question, kept separate for the same reason `classify_yes` is
separate: one job, no audio, no model, testable from numbers.

## The three tests, and why one of them is not enough

Each catches a shape the others miss. The thresholds come from the 52 real captures in
`tools/verify_credible.py`, which is every capture the log has, not a sample.

1. **A voiced-seconds floor.** Below `MIN_VOICED_S` there is not enough audio to have said
   anything at all. One 80ms frame is a click, not a word.

2. **A voiced-ratio floor.** How much of the capture was actually speech. This is the test
   that catches "Yes. Yes." — 2.1 words per voiced second is a perfectly ordinary rate, and
   the only thing wrong with it is that the voiced audio was 5% of the recording. A capture
   runs from the first voiced frame to the last, so a single noise blip late in the window
   stretches it: scattered voicing and continuous speech look identical to a rate test and
   completely different to this one.

3. **A words-per-voiced-second ceiling.** A physical impossibility test, and the only one that
   catches a hallucination whose ratio is ordinary. "I don't know what it is." is six words
   from 0.32 seconds — 18.8 words per second — at a 10.3% ratio that test 2 lets through.

## The numbers are measured, and the margins are stated because they are thin

From the 52 captures, separating what LB really said from what the room produced:

    voiced ratio   genuine LOW  11.1%   |  rejected HIGH   6.5%   -> floor 8%
    words/voiced   genuine HIGH  15.0   |  rejected LOW    18.8   -> ceiling 16.0

**Both gaps are narrow, and test 3's is narrower than it looks.** The genuine 15.0 is
"Can you write a note and see what you hope for me?" — twelve words the VAD only heard 0.80
seconds of, because LB was speaking quietly. That reading is not a fast talker; it is an
undercount, and undercounts are exactly what push a real utterance toward the hallucination
side. So the ceiling is set above it deliberately rather than tightened to look safer.

The two tests are complementary rather than redundant, which is what makes thin margins
acceptable: every rejected case in the corpus fails at least one of them by a wide margin, and
the one that only just fails test 2 fails test 3 by a factor of ten.

## Every rejection is fail-SAFE, and that is what buys the right to be strict

Nothing here decides what LB meant. It decides whether to believe there were words at all, and
being wrong about that costs him one repetition:

    on the gate      not a clear answer -> the existing retry, then decline. A decline is
                     already what silence means there, so a false reject cannot run anything.
    on dispatch      treated as silence -> no route, no agent, no API call.

That asymmetry is the whole argument for these thresholds. A false accept at the gate runs a
command nobody approved; a false reject asks LB to say "yes" again.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Verdict", "assess", "MIN_VOICED_S", "MIN_VOICED_RATIO", "MAX_WORDS_PER_VOICED_S"]

# Two 80ms frames. Below this the capture holds a click or a chair, not a syllable — the
# quietest thing LB really said in the corpus is 0.16s ("elbow"), so this floor is set AT it
# rather than above it, and the ratio test is left to catch that one.
MIN_VOICED_S = 0.16

# Voiced seconds as a fraction of the capture. Genuine speech in the corpus never falls below
# 11.1%; nothing that needs rejecting on this test rises above 6.5%.
MIN_VOICED_RATIO = 0.08

# Words the transcript may claim per second of voiced audio. See the margin note above: the
# genuine maximum is 15.0 and the lowest hallucination is 18.8.
MAX_WORDS_PER_VOICED_S = 16.0


@dataclass(frozen=True)
class Verdict:
    """Whether to believe the transcript.

    Args:
        credible: True when there was enough real speech to act on.
        reason:   "" when credible; otherwise which test failed, WITH THE NUMBERS IN IT. The
                  numbers are the point — a rejection logged as "not credible" teaches nobody
                  anything, and these thresholds will need retuning against a different
                  microphone or a different room.
    """

    credible: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.credible


def assess(text: str, speech_s: float, audio_s: float, *,
           min_voiced_s: float = MIN_VOICED_S,
           min_voiced_ratio: float = MIN_VOICED_RATIO,
           max_words_per_voiced_s: float = MAX_WORDS_PER_VOICED_S) -> Verdict:
    """Is `text` backed by enough voiced audio to be believed? **Never raises.**

    Args:
        text:     what the transcriber returned.
        speech_s: seconds of the capture that scored above the VAD threshold.
        audio_s:  seconds of audio handed to the transcriber.

    Returns:
        A `Verdict`. The thresholds are arguments so a caller — or a harness sweeping them —
        can override without reaching into module state.
    """
    words = len((text or "").split())
    if not words:
        return Verdict(False, "nothing transcribed")

    if speech_s < min_voiced_s:
        return Verdict(False, f"only {speech_s:.2f}s voiced, under the {min_voiced_s:.2f}s floor")

    if audio_s > 0:
        ratio = speech_s / audio_s
        if ratio < min_voiced_ratio:
            return Verdict(False, f"{ratio:.1%} of {audio_s:.2f}s was voiced, under "
                                  f"{min_voiced_ratio:.0%} — mostly room tone")

    rate = words / speech_s if speech_s > 0 else float("inf")
    if rate > max_words_per_voiced_s:
        return Verdict(False, f"{words} words from {speech_s:.2f}s voiced is {rate:.1f} a "
                              f"second, over {max_words_per_voiced_s:.0f} — nobody talks "
                              f"that fast")

    return Verdict(True)


def main() -> int:
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    demo = [
        ("Thank you for watching.", 0.08, 2.48),
        ("Yes. Yes.", 0.96, 18.08),
        ("I don't know what it is.", 0.32, 3.12),
        ("Come on!", 0.48, 15.52),
        ("Yes.", 0.40, 2.72),
        ("What's on my schedule for today?", 2.00, 4.32),
        ("Can you write a note and see what you hope for me?", 0.80, 7.20),
    ]
    for text, voiced, audio in demo:
        v = assess(text, voiced, audio)
        mark = "believed" if v.credible else "REJECTED"
        print(f"  {mark}  {text!r:52} {v.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
