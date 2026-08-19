#!/usr/bin/env python3
"""
Module:  autotune.py
Purpose: Move the wake threshold toward the room, using what each wake turned out to be.
Author:  LB
Date:    2026-08-14

    python -m audio.autotune --replay media/data/2026-08-14-oddball-session.log

## Where this came from, and how the idea changed on contact with a measurement

Read across from PiSugar's `whisplay-ai-chatbot` (`src/device/voice-detect.ts`, GPL-3.0 —
**read, not copied**; nothing here is derived from their source). Their design samples 0.35 s
of ambient audio every 30 s with `sox stat`, takes the RMS, and floats a detection level at
`ambient + margin`, smoothed and clamped. For an RMS-triggered recorder that is exactly right.

**Ported directly it would have made this worse, and the measurement said so before it was
written.** Scored frame by frame through our own wake model on 2026-08-14:

| recording | p95 | max |
|---|---|---|
| this room, idle | 0.0308 | 0.1264 |
| this room, under four-core load | 0.0017 | 0.0046 |
| `negative/ambient-01.wav` (WI-4) | 0.0026 | 0.0035 |
| `negative/speech-01.wav`, 60 s | 0.0006 | 0.0606 |

The steady floor scores **two orders of magnitude below the 0.50 threshold**. Sampling it would
drive the threshold *down*. What actually fires him are rare transients that a six-second
sample never sees — and D27 already explains why level does not transfer: openWakeWord's
melspectrogram frontend **normalises** it, so a louder room does not mean a higher score.

**And the obvious repair does not work either.** Tracking a high percentile of the live score
stream fails because LB's real wake words are *also* rare high-scoring frames. The threshold
would chase them upward until he stopped being able to wake it — the failure mode is silent and
looks exactly like a broken microphone.

## So it is tuned by OUTCOME, which this project already records

A wake is followed by a capture, and the capture says which kind of wake it was. Measured on
the Pi, 2026-08-14:

    false wakes    0.08 s, 0.16 s of voiced audio
    LB speaking    2.56 s, 3.44 s

That is a supervision signal, free, already logged, and — importantly — **it does not depend on
the transcript.** Whisper is currently returning nothing for LB's real speech, so a rule built
on "did it transcribe" would classify his genuine wakes as false and ratchet the threshold up
until he could not use it. Voiced *duration* survives that, because the VAD still sees him.

## The safety properties, which matter more than the tuning

1. **It only ever moves between a floor and a ceiling**, both configured. It cannot tune itself
   deaf and it cannot tune itself into the noise.
2. **It needs `min_events` observations before it moves at all.** One odd capture is not data.
3. **It is an EMA, so no single event can move it far.** A misclassification costs a nudge.
4. **`value` is the CONFIGURED threshold until it has enough evidence**, so a fresh start
   behaves exactly like today.
5. **It is a pure function of the events it is given** — no clock, no audio, no I/O, no model.
   That is deliberate: it makes the whole thing testable as ordinary logic, and it is why
   `tools/verify_autotune.py` needs no microphone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

LOG = logging.getLogger("oddball.autotune")

__all__ = ["AdaptiveThreshold", "Outcome", "classify_wake",
           "POSITION_MIN", "POSITION_MAX", "POSITION_TARGET", "MARGIN_MIN"]


# --------------------------------------------------------------------------------------
# Where a threshold belongs inside the fixture band. ONE definition, three consumers:
# `tools/verify_wake.py` enforces it, `tools/tune_threshold.py` recommends against it, and
# `tools/verify_autotune.py` asserts the three agree. That is the D37 pattern — three places,
# one number — applied after the same rule was nearly typed out twice.
# --------------------------------------------------------------------------------------

# A band has two edges and a threshold has to respect both. 0.30 sat at 40% of the way up and
# passed a mere "is it inside the band" check for two days while being the D44 bug; too close
# to the positives and a quiet call is ignored instead.
POSITION_MIN, POSITION_MAX = 0.45, 0.85

# Calibrated rather than chosen. On the D44 fixture set (worst positive 0.6227, loudest
# negative 0.0885) two independent methods already landed: **0.50** picked by hand from the
# removes-40-of-57 table (77% of the band), and **0.531** where the replay below settled
# (83%). A target of 0.75 recommends 0.49 — between them, and inside the position rule.
POSITION_TARGET = 0.75

# Headroom under the quietest recorded positive, so a quiet call still fires.
MARGIN_MIN = 0.15


class Outcome:
    """What a wake turned out to be. Strings, so they log as themselves."""

    REAL = "real"          # a capture with enough voiced audio to be a person talking
    FALSE = "false"        # a wake that captured effectively nothing
    UNKNOWN = "unknown"    # in between: not used as evidence in either direction


def classify_wake(voiced_s: float, real_above_s: float = 0.60,
                  false_below_s: float = 0.30) -> str:
    """Was this wake a person, or the room?

    Args:
        voiced_s: seconds of the capture the VAD called voiced (`Capture.speech_s`).
        real_above_s: at or above this, treat it as a person.
        false_below_s: below this, treat it as a false wake.

    Returns:
        One of `Outcome.REAL`, `Outcome.FALSE`, `Outcome.UNKNOWN`.

    **The gap between the two bounds is deliberate and is the whole reason there are two of
    them.** The measured populations are 0.08-0.16 s against 2.56-3.44 s, which is a wide
    separation — but one real question in the same log, *"I want to go."*, carried only 0.16 s.
    A single cutoff would have to call that one wrong in one direction or the other. Two
    cutoffs let the ambiguous middle be evidence for **nothing**, which is the honest answer
    and costs only a slower tune.
    """
    if voiced_s >= real_above_s:
        return Outcome.REAL
    if voiced_s < false_below_s:
        return Outcome.FALSE
    return Outcome.UNKNOWN


@dataclass
class AdaptiveThreshold:
    """A wake threshold that moves toward the room it is in.

    Args:
        configured: the threshold from `config/oddball.toml`. Used until there is evidence,
                    and never itself exceeded downward past `floor`.
        floor:      it may never go below this, however many real wakes are seen.
        ceiling:    it may never go above this, however much noise is seen.
        margin:     how far above a false wake's score to aim, as a fraction.
        smoothing:  EMA weight kept on the old value. 0.9 = move a tenth of the way.
        min_events: observations required before `value` departs from `configured`.

    Not frozen, because it accumulates — but every field that changes is private, and the
    public surface is `observe()` and `value`.
    """

    configured: float
    floor: float
    ceiling: float
    margin: float = 0.15
    smoothing: float = 0.85
    min_events: int = 6

    _value: float = field(init=False, default=0.0)
    _events: int = field(init=False, default=0)
    _false_seen: int = field(init=False, default=0)
    _real_seen: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if not 0.0 < self.floor <= self.ceiling <= 1.0:
            raise ValueError(
                f"need 0 < floor <= ceiling <= 1, got floor={self.floor} "
                f"ceiling={self.ceiling}")
        if not 0.0 <= self.smoothing < 1.0:
            raise ValueError(f"smoothing must be in [0, 1), got {self.smoothing}")
        if self.min_events < 1:
            raise ValueError(f"min_events must be >= 1, got {self.min_events}")
        self._value = self._clamp(self.configured)

    def _clamp(self, value: float) -> float:
        return min(max(value, self.floor), self.ceiling)

    @property
    def value(self) -> float:
        """The threshold to use right now.

        The CONFIGURED value until `min_events` observations have arrived, so a freshly
        started service behaves exactly as it did before this existed. That is what makes the
        feature safe to enable without a separate migration story.
        """
        if self._events < self.min_events:
            return self._clamp(self.configured)
        return self._value

    @property
    def adapting(self) -> bool:
        return self._events >= self.min_events

    def observe(self, score: float, outcome: str) -> float:
        """Record one wake and what it turned out to be. Returns the threshold to use next.

        Args:
            score:   the wake score that fired.
            outcome: `Outcome.REAL`, `Outcome.FALSE` or `Outcome.UNKNOWN`.

        A **false** wake aims the threshold just above the score that produced it — that exact
        score must not fire again. A **real** wake aims just below, because a threshold LB has
        to shout past is the failure this must not tune itself into. **Unknown** moves nothing
        and is not even counted as evidence.
        """
        if outcome == Outcome.UNKNOWN:
            return self.value

        self._events += 1
        if outcome == Outcome.FALSE:
            self._false_seen += 1
            target = score * (1.0 + self.margin)
        else:
            self._real_seen += 1
            target = score * (1.0 - self.margin)

        target = self._clamp(target)
        self._value = self._clamp(
            self._value * self.smoothing + target * (1.0 - self.smoothing))

        LOG.info("autotune: %s wake at %.3f -> target %.3f, threshold now %.3f "
                 "(%d events: %d false, %d real)",
                 outcome, score, target, self.value,
                 self._events, self._false_seen, self._real_seen)
        return self.value

    def summary(self) -> str:
        return (f"threshold {self.value:.3f} "
                f"({'adapting' if self.adapting else 'configured'}; "
                f"{self._events} events, {self._false_seen} false, {self._real_seen} real; "
                f"bounds {self.floor:.2f}-{self.ceiling:.2f})")


def _replay(path: str) -> int:
    """Replay a session log through the tuner and report where it would have settled.

    The point of a replay is that it answers "what would this have done" with the session that
    prompted it, rather than with an argument. `media/data/2026-08-14-oddball-session.log` is
    the 127 minutes that made LB say he kept waking up.
    """
    import re

    wake_re = re.compile(r"oddball\.run wake: \S+ \(([\d.]+)\)")
    cap_re = re.compile(r"capture (\w+): [\d.]+s audio, ([\d.]+)s voiced")

    tuner = AdaptiveThreshold(configured=0.30, floor=0.30, ceiling=0.80)
    pending: float | None = None
    seen = 0

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = wake_re.search(line)
            if m:
                pending = float(m.group(1))
                seen += 1
                continue
            m = cap_re.search(line)
            if m and pending is not None:
                voiced = float(m.group(2))
                tuner.observe(pending, classify_wake(voiced))
                pending = None

    print(f"\nreplayed {seen} wakes from {path}")
    print(f"  {tuner.summary()}")
    print(f"  started at 0.300, settled at {tuner.value:.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="adaptive wake threshold")
    ap.add_argument("--replay", metavar="LOG", help="replay an oddball.log through the tuner")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.replay:
        return _replay(args.replay)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
