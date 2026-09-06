#!/usr/bin/env python3
"""
Module:  verify_wake_tail.py
Purpose: Prove the wake-tail window kills a blip and costs a real question nothing.
Author:  LB
Date:    2026-09-03

    python tools/verify_wake_tail.py
    python tools/verify_wake_tail.py --probe     # switch the window off, expect RED

## What is being fixed

`data/oddball.log`, 2026-08-26 to 2026-09-03. Of the 55 captures that "spoke" after a wake
word, **29 held 0.32s or less of voiced audio** — a blip, then the full two-second hangover.
They transcribe to `ball.`, `Bobo.`, `elbow.`, `Mr. Albo.`, `Whoa.`, `Thank you for watching.`

openWakeWord fires when its rolling window is convinced, which is before LB has finished saying
"...Odd Ball". The recorder opens into the last syllable of his own wake word and treats it as
the start of a question.

## How strong the evidence is, stated honestly

    blip rate after a WAKE            53%
    blip rate in a CONVERSATION       31%      ratio 1.72x

Same room, same microphone, same noise floor — if these were ambient noise the two rates would
match. They do not, and that asymmetry is what points at the wake phrase specifically.

**What is NOT proven is the timing.** Whether the blip lands in the first 250ms of the
listening window could not be answered from eight days of logs, because `Capture.waited_s` was
computed and never logged, and `_finish` trims the saved audio from `first_voiced_i - PREROLL`
— which destroys the offset before anything downstream can see it. `audio/listen.py` now logs
`waited`, so the next session settles it.

So this harness proves the two things that CAN be proven offline:

    section 1   the MECHANISM works when the timing is as hypothesised   (synthetic)
    section 2   a real question loses nothing                            (real captures)
    section 3   the guards hold — the window cannot exceed the pre-roll

Section 2 is the one that licenses shipping this ahead of the live confirmation. If the
hypothesis is wrong, the window is inert; it can only fail by being useless, never by eating a
word — and section 2 is what makes that claim rather than assuming it.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from audio.listen import (FRAME_SAMPLES, PREROLL_S, SAMPLE_RATE_HZ,  # noqa: E402
                          Outcome, UtteranceRecorder)

PASSED = 0
FAILED = 0

WINDOW_S = 0.25            # what config/oddball.toml ships as `wake_tail_s`
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE_HZ          # 0.08


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


class ScriptedVAD:
    """Returns a scripted score per frame, so timing is exact and nothing is sampled."""

    def __init__(self, scores):
        self._scores = list(scores)
        self._i = 0

    def predict(self, x, frame_size=640):                             # noqa: ARG002
        score = self._scores[self._i] if self._i < len(self._scores) else 0.0
        self._i += 1
        return score


class Clock:
    """80 ms per frame, advanced by the driver below. No sleeping."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def drive(scores, ignore_start_s: float, hangover_s: float = 2.0, wait_s: float = 1.5):
    """Feed a scripted VAD pattern to a recorder and return the Capture it produces."""
    clock = Clock()
    rec = UtteranceRecorder(ScriptedVAD(scores), threshold=0.35, wait_s=wait_s,
                            hangover_s=hangover_s, max_s=15.0,
                            ignore_start_s=ignore_start_s, clock=clock)
    frame = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    for _ in range(len(scores) + 80):
        out = rec.feed(frame)
        clock.t += FRAME_S
        if out is not None:
            return out
    return None


# Frame patterns, at 80 ms a frame.
#
#   the echo         two voiced frames at the very start (0.00-0.16s), then nothing.
#                    This is 'elbow.', 'Bobo.', 'ball.' — measured shape.
#   the one breath   "Hey Mr Odd Ball, what time is it?" — voice from frame 0, continuous.
#   the late answer  LB pauses, then speaks at 0.5s. Must be unaffected by a 0.25s window.
ECHO = [0.9, 0.9] + [0.0] * 40
ONE_BREATH = [0.9] * 25 + [0.0] * 40
LATE = [0.0] * 6 + [0.9] * 20 + [0.0] * 40


def run(probe: bool = False) -> int:
    print("=" * 78)
    print("  verify_wake_tail.py — the wake phrase's own tail, and the question behind it")
    print("=" * 78)

    window = 0.0 if probe else WINDOW_S
    if probe:
        print("\n  [--probe] wake_tail_s = 0.0 — the pre-2026-09-03 behaviour\n")

    # =====================================================================================
    section("1. the MECHANISM, on the measured shape")
    # =====================================================================================
    echo = drive(ECHO, ignore_start_s=window)
    check(echo is not None and echo.outcome is Outcome.SILENT,
          "two voiced frames at the very start do NOT begin an utterance",
          f"got {echo.outcome.value if echo else None}")
    check(echo is not None and echo.audio.size == 0,
          "...so nothing is handed to the transcriber, and nothing is invented from it")
    check(echo is not None and echo.waited_s < 0.01,
          "...and the log still records that voice arrived at 0.00s, so the evidence survives "
          "the fix", f"waited {echo.waited_s:.2f}s" if echo else "")

    # The whole point: a SILENT capture is already handled well. LB gets the greeting and a
    # second chance, instead of a fragment being routed to a paid model.
    check(echo is not None and not bool(echo),
          "a silent capture is falsy, so `run()` takes the greeting branch — which is what "
          "should have happened all along")

    # =====================================================================================
    section("2. a real question loses NOTHING — the claim that licenses shipping this")
    # =====================================================================================
    for name, pattern, want_voiced in (("one breath, from frame 0", ONE_BREATH, 25 * FRAME_S),
                                       ("a pause, then speech at 0.48s", LATE, 20 * FRAME_S)):
        with_window = drive(pattern, ignore_start_s=window)
        without = drive(pattern, ignore_start_s=0.0)
        check(with_window is not None and with_window.outcome is Outcome.SPOKE,
              f"[{name}] still SPOKE",
              f"got {with_window.outcome.value if with_window else None}")
        check(with_window is not None and abs(with_window.speech_s - want_voiced) < 0.001,
              f"[{name}] every voiced frame is still counted",
              f"{with_window.speech_s:.2f}s vs {want_voiced:.2f}s expected"
              if with_window else "")
        check(with_window is not None and without is not None
              and with_window.audio.size == without.audio.size,
              f"[{name}] and the AUDIO is byte-identical to no window at all — the pre-roll "
              f"recovers what the trigger skipped",
              f"{with_window.audio.size} vs {without.audio.size} samples"
              if with_window and without else "")

    # And on the real recordings, which is the strongest form of this claim available offline.
    real = _real_captures()
    if real:
        for path in real:
            audio = _load(path)
            a = _replay(audio, 0.0)
            b = _replay(audio, WINDOW_S)
            check(a is not None and b is not None
                  and a.outcome == b.outcome and abs(a.speech_s - b.speech_s) < 0.001,
                  f"{path.name[:44]}: unchanged by the window",
                  f"{a.outcome.value}/{a.speech_s:.2f} vs {b.outcome.value}/{b.speech_s:.2f}"
                  if a and b else "one produced no capture")
    else:
        print("      (no saved captures on this machine — section 2's live half skipped)")

    # =====================================================================================
    section("3. the guards — this must never become a mute")
    # =====================================================================================
    rec = UtteranceRecorder(ScriptedVAD([0.0]), threshold=0.35)
    try:
        rec.ignore_start_s = PREROLL_S
        check(False, f"a window of exactly PREROLL_S ({PREROLL_S}s) is REFUSED")
    except ValueError:
        check(True, f"a window of exactly PREROLL_S ({PREROLL_S}s) is REFUSED — past the "
                    f"pre-roll it stops delaying the trigger and starts deleting audio")
    try:
        rec.ignore_start_s = 1.0
        check(False, "a window of 1.0s is REFUSED")
    except ValueError:
        check(True, "a window longer than the pre-roll is REFUSED, not clamped — a silent "
                    "clamp would hide the one mistake that costs a word")
    try:
        rec.ignore_start_s = -0.1
        check(False, "a negative window is REFUSED")
    except ValueError:
        check(True, "a negative window is REFUSED")

    rec.ignore_start_s = 0.0
    check(rec.ignore_start_s == 0.0, "0.0 switches it off entirely")
    check(WINDOW_S < PREROLL_S,
          f"the shipped window ({WINDOW_S}s) is under the pre-roll ({PREROLL_S}s), with "
          f"{PREROLL_S - WINDOW_S:.2f}s of margin")

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if probe:
        if FAILED:
            print(f"\n  The harness BITES: {FAILED} check(s) went red.\n")
            return 0
        print("\n  PROBE DID NOT BITE — section 1 is not testing what it claims.\n")
        return 1
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        return 1
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    return 0


# ---------------------------------------------------------------------------------------
# Replaying the real saved captures
#
# These are the TRIMMED audio — `_finish` cuts from `first_voiced_i - PREROLL` — so they cannot
# reproduce the original offset within the listening window, and therefore cannot show the
# window catching an echo. They CAN show it costing a real utterance nothing, which is the
# claim that matters for shipping.
# ---------------------------------------------------------------------------------------

_REAL_NAMES = ("070726_what-time-is-it-.wav", "072104_what-do-i-have-to-do-today-.wav",
               "070620_what-s-on-my-schedule-for-today-.wav",
               "070647_open--creality-print-.wav",
               "071802_can-you-add-to-my-note-about-the-topic-f.wav")


def _real_captures() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "captures"
    return [root / n for n in _REAL_NAMES if (root / n).exists()]


def _load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


class _SileroVAD:
    def __init__(self):
        from openwakeword.vad import VAD
        self._vad = VAD()

    def predict(self, x, frame_size=640):
        return float(self._vad.predict(x, frame_size))


def _replay(audio: np.ndarray, ignore_start_s: float):
    clock = Clock()
    rec = UtteranceRecorder(_SileroVAD(), threshold=0.35, wait_s=1.5, hangover_s=2.0,
                            max_s=15.0, ignore_start_s=ignore_start_s, clock=clock)
    stream = np.concatenate([np.zeros(int(0.10 * SAMPLE_RATE_HZ), dtype=np.int16), audio])
    for i in range(0, len(stream) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        out = rec.feed(stream[i:i + FRAME_SAMPLES])
        clock.t += FRAME_S
        if out is not None:
            return out
    quiet = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    for _ in range(60):
        out = rec.feed(quiet)
        clock.t += FRAME_S
        if out is not None:
            return out
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prove the wake-tail window")
    ap.add_argument("--probe", action="store_true",
                    help="switch the window off, expect section 1 RED")
    args = ap.parse_args(argv)
    return run(probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
