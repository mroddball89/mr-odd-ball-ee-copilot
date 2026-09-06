#!/usr/bin/env python3
"""
Module:  listen.py
Purpose: Capture what you actually said — decide when you started and when you stopped.
Author:  LB
Date:    2026-08-12

Between the wake word firing and transcription, something has to know when the utterance is
over. Fixed-length recording is the naive answer and it is wrong in both directions: too short
truncates the question, too long makes every turn feel slow, because the whole latency budget
is spent waiting for a timer that expired after you had already finished.

`UtteranceRecorder` is fed the **same 1280-sample frames** as the wake detector and uses voice
activity detection to end the recording when you stop talking.

The VAD is **openWakeWord's own** — `openwakeword.vad.VAD`, a Silero wrapper, whose
`silero_vad.onnx` is already on disk because openWakeWord downloads it alongside the feature
models. So this costs no new dependency, no new download, and no new model to keep in step.
Its `predict()` splits the input into `frame_size` chunks, and 1280 / 320 is exactly 4, so our
existing frames divide cleanly into 20ms VAD chunks.

Like `WakeDetector` and `MicGate`, the VAD and the clock are injectable — every rule in here is
tested with no microphone and no waiting.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

import numpy as np

LOG = logging.getLogger("oddball.listen")

SAMPLE_RATE_HZ = 16_000
FRAME_SAMPLES = 1280                  # 80ms, the same frame the wake detector scores
# 40ms, and 1280 / 640 == 2 exactly. This is openWakeWord's own choice (`VAD.__call__` uses
# 160*4) and it is not arbitrary: Silero is a recurrent model with a minimum input it will
# accept at all — 160 raises `Invalid input shape: {4}` outright — and it scores measurably
# worse on short chunks. Measured on tests/fixtures/wake/positive/normal-01.wav:
#
#   frame_size=320   max 0.919   12 frames over 0.5
#   frame_size=640   max 0.993   12 frames over 0.5
#   frame_size=1280  max 0.994   14 frames over 0.5
#
# 320 was the first thing that divided the frame evenly, which is not the same as being right.
VAD_CHUNK = 640

# How much audio to keep BEFORE the first voiced frame. The first consonant of a sentence
# usually lands in the frame before the VAD is convinced, so some lead-in is required — but
# keeping everything from the moment capture opened is worse than useless. Measured live: a
# turn captured 2.16s of audio holding 0.32s of speech, and whisper hallucinated
# "We'll see you next time." out of the silence, which is exactly what it does when given
# mostly nothing. It also spent 1.68s transcribing padding.
PREROLL_S = 0.3


class Outcome(Enum):
    """How a capture ended."""

    SPOKE = "spoke"                   # speech, then a pause — the normal case
    SILENT = "silent"                 # nobody said anything within wait_s
    TOO_LONG = "too_long"             # still going at max_s; kept what we had


@dataclass
class Capture:
    """One attempt at hearing a sentence."""

    outcome: Outcome
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    speech_s: float = 0.0             # how much of it was voiced
    waited_s: float = 0.0             # how long before speech began

    def __bool__(self) -> bool:
        return self.outcome is not Outcome.SILENT and self.audio.size > 0


class VoiceDetector(Protocol):
    """The slice of openwakeword.vad.VAD this needs."""

    def predict(self, x: np.ndarray, frame_size: int = ...) -> float: ...


class UtteranceRecorder:
    """Accumulates frames until the sentence is over.

    Args:
        vad:        anything satisfying VoiceDetector.
        threshold:  VAD score above which a frame counts as speech.
        wait_s:     give up if speech has not started within this long.
        hangover_s: end the utterance after this much silence *following* speech.
        max_s:      hard cap, so a television cannot record forever.
        ignore_start_s: voice inside this long from the start of the capture may not BEGIN an
                    utterance. For the capture that follows a wake word, where the tail of the
                    wake phrase itself arrives first. See the property below.
        clock:      monotonic seconds source; injectable so tests need no sleeping.

    `hangover_s` is the one number you feel. Too short and he interrupts you mid-sentence;
    too long and every answer is late by the difference. It is spent on every single turn,
    so it is part of the latency budget, not separate from it.
    """

    def __init__(
        self,
        vad: VoiceDetector,
        threshold: float = 0.5,
        wait_s: float = 1.5,
        hangover_s: float = 0.6,
        max_s: float = 10.0,
        ignore_start_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")
        for name, value in (("wait_s", wait_s), ("hangover_s", hangover_s), ("max_s", max_s)):
            if value <= 0:
                raise ValueError(f"{name} must be > 0, got {value}")
        self._vad = vad
        self._threshold = float(threshold)
        self._wait_s = float(wait_s)
        self._hangover_s = float(hangover_s)
        self._max_s = float(max_s)
        # 0.0 is "no suppression", which is what every existing caller and harness gets.
        self._ignore_start_s = 0.0
        self.ignore_start_s = ignore_start_s          # through the setter, for its guard
        self._clock = clock
        self.reset()

    @property
    def max_s(self) -> float:
        """The hard cap on one utterance, in seconds. Settable between captures.

        **Why this is writable at all**, added 2026-09-03: dictating a paragraph into a note is
        not the same activity as asking a question, and one number cannot serve both. 15s is
        generous for "what's the trace width for five amps" and it cut LB's English thesis in
        half mid-clause — `data/oddball.log` 17:10:37, and the truncated fragment is still
        sitting in `vault/notes/english research question.md` ending on the word "incentivizes".

        Raising the number globally is the wrong fix and this class's own docstring says why:
        the cap exists *"so a television cannot record forever"*, and there is television in
        that log. So `engine/turn.py` raises it only for the one turn where the Engine is
        holding an open note draft, and puts it back afterwards.

        Set between captures, never during one — `_step` reads it each frame, so changing it
        mid-utterance would move the goalposts on a recording already in progress.
        """
        return self._max_s

    @max_s.setter
    def max_s(self, seconds: float) -> None:
        seconds = float(seconds)
        if seconds <= 0:
            raise ValueError(f"max_s must be > 0, got {seconds}")
        self._max_s = seconds

    @property
    def ignore_start_s(self) -> float:
        """How long, at the very start of a capture, voice may not START an utterance.

        ## The measurement, `data/oddball.log` 2026-08-26 to 2026-09-03

        **55% of the 55 captures opened by a wake word held 0.40s or less of voiced audio** —
        more than half of every time LB woke him. The shape is identical across all of them:

            capture spoke: 2.48s audio, 0.08s voiced      one 80 ms frame, then nothing
            capture spoke: 2.48s audio, 0.16s voiced      two frames
            -> 'elbow.'  'Bobo.'  'ball.'  'Mr. Albo.'  'Thank you for watching.'

        2.48s is not a coincidence, it is arithmetic: `PREROLL_S` 0.3 + a blip + `hangover_s`
        2.0 is **the shortest capture this recorder can produce**. Every one of these is the
        recorder starting an utterance on a fragment and then waiting out the full hangover for
        a sentence that never came.

        The fragment is the tail of the wake phrase. openWakeWord scores a rolling window and
        fires when it is convinced, which is **before LB has finished saying "…Odd Ball"** —
        so the recorder opens into the last syllable of his own wake word and treats it as the
        beginning of a question. "Ball" transcribes as `ball.`, `Bobo.`, `elbow.`, `Mr. Albo.`

        ## Why this suppresses the TRIGGER rather than muting the microphone

        Muting would lose the audio, and LB says "Hey Mr Odd Ball, what time is it?" in one
        breath — the question would lose its first word, which is the failure `_step` already
        keeps a frame of lead-in to avoid.

        Nothing is discarded here. The frame is buffered and counted exactly as before; only
        the decision *"this is where the sentence starts"* waits. Because `PREROLL_S` (0.3s) is
        longer than this window (0.25s), a real utterance that triggers at the far edge still
        carries back every frame from before it — so the one-breath question is complete and
        the wake tail, which is followed by silence rather than speech, never triggers at all
        and the capture correctly comes back SILENT. A silent capture is already handled: LB
        gets "What's up LB?" and a second chance, which is what should have happened all along.

        Settable between captures, like `max_s`, because it applies to the capture that follows
        a WAKE and not to the ones inside an open conversation window — there is no wake phrase
        to echo there, and a fast answer must not be suppressed.
        """
        return self._ignore_start_s

    @ignore_start_s.setter
    def ignore_start_s(self, seconds: float) -> None:
        seconds = float(seconds)
        if seconds < 0:
            raise ValueError(f"ignore_start_s must be >= 0, got {seconds}")
        if seconds >= PREROLL_S:
            # Refused rather than clamped. Past the pre-roll this stops being a delayed trigger
            # and silently becomes a mute: the audio from the suppressed window would no longer
            # be recoverable, and the first word of a one-breath question would be gone with no
            # error to see. That is the exact failure this design exists to avoid.
            raise ValueError(
                f"ignore_start_s must stay under PREROLL_S ({PREROLL_S}s) so the suppressed "
                f"audio is still recovered by the pre-roll; got {seconds}")
        self._ignore_start_s = seconds

    def reset(self) -> None:
        """Forget the utterance in progress, and the VAD's memory of it.

        Silero is recurrent — it carries hidden state between calls. Leaving that to
        accumulate across every turn of a long-running process is the same class of mistake
        as not resetting the wake model on fire, which cost a session in step 3.
        """
        reset_states = getattr(self._vad, "reset_states", None)
        if callable(reset_states):
            reset_states()
        self._peak_rms = 0.0
        self._peak_score = 0.0
        self._first_voiced_i: int | None = None
        self._last_voiced_i: int | None = None
        self._frames: list[np.ndarray] = []
        self._started_at: float | None = None      # clock when capture began
        self._speech_at: float | None = None       # clock when the utterance was TRIGGERED
        # Clock when voice was first seen at all, INCLUDING inside `ignore_start_s`. Separate
        # from `_speech_at` on purpose: with the wake-tail window on, the trigger is delayed,
        # and reporting the delayed time as "waited" would erase the very measurement that
        # says whether the window was needed. This one always tells the truth about when
        # sound arrived, so the log stays diagnostic with the fix switched on.
        self._first_voice_at: float | None = None
        self._last_speech_at: float | None = None
        self._speech_frames = 0
        self.last_score = 0.0

    def feed(self, frame: np.ndarray) -> Capture | None:
        """Score and accumulate one frame. Returns a Capture when the utterance ends.

        Args:
            frame: exactly FRAME_SAMPLES int16 samples at SAMPLE_RATE_HZ — the same frame
                   `WakeDetector.feed` takes, so one microphone loop can drive both.

        Raises:
            TypeError / ValueError: on the wrong dtype or length, for the same reason
            WakeDetector refuses them — a block-size mistake upstream must not degrade
            quietly into "he mishears things".
        """
        frame = np.asarray(frame)
        if frame.dtype != np.int16:
            raise TypeError(f"expected int16 samples, got {frame.dtype}")
        if frame.shape != (FRAME_SAMPLES,):
            raise ValueError(f"expected exactly {FRAME_SAMPLES} samples, got {frame.shape}")

        now = self._clock()
        if self._started_at is None:
            self._started_at = now

        self.last_score = float(self._vad.predict(frame, VAD_CHUNK))
        voiced = self.last_score >= self._threshold
        # Tracked so the capture log can distinguish "no audio arrived" from "audio arrived
        # and the VAD was not convinced". Those look identical from outside and have
        # completely different fixes; guessing between them cost a debugging round.
        self._peak_score = max(self._peak_score, self.last_score)
        self._peak_rms = max(self._peak_rms,
                             float(np.sqrt(np.mean((frame.astype(np.float64) / 32768.0) ** 2))))

        # Keep the frame from the moment capture starts, not from first speech: the leading
        # consonant of a sentence usually lands in the frame *before* the VAD is convinced,
        # and clipping it costs the first word.
        self._frames.append(frame)

        elapsed = now - self._started_at

        # **Voice this early cannot START an utterance** — see `ignore_start_s`. The frame is
        # still buffered above and still counted below, so nothing is thrown away; only the
        # trigger waits. `PREROLL_S` is longer than this window, so when real speech does
        # trigger the utterance, the audio from before the trigger comes back with it.
        may_start = elapsed >= self._ignore_start_s

        if voiced:
            self._speech_frames += 1
            self._last_speech_at = now
            if self._first_voice_at is None:
                self._first_voice_at = now
            self._last_voiced_i = len(self._frames) - 1
            if self._speech_at is None and may_start:
                self._speech_at = now
                self._first_voiced_i = len(self._frames) - 1
                LOG.debug("speech began after %.2fs", elapsed)
            elif self._speech_at is None:
                LOG.debug("ignoring voice at %.2fs — inside the %.2fs wake tail",
                          elapsed, self._ignore_start_s)
        if self._speech_at is None:
            if elapsed >= self._wait_s:
                return self._finish(Outcome.SILENT, now)
            return None

        if now - self._last_speech_at >= self._hangover_s:
            return self._finish(Outcome.SPOKE, now)
        if elapsed >= self._max_s:
            LOG.warning("utterance hit the %.0fs cap — keeping what we have", self._max_s)
            return self._finish(Outcome.TOO_LONG, now)
        return None

    def _finish(self, outcome: Outcome, now: float) -> Capture:
        speech_s = self._speech_frames * FRAME_SAMPLES / SAMPLE_RATE_HZ
        # From `_first_voice_at`, not `_speech_at` — see the note in `reset`. On a SILENT
        # capture that still saw suppressed voice, this reports when that voice arrived, which
        # is exactly the case the wake-tail window exists to produce.
        first = self._first_voice_at if self._first_voice_at is not None else self._speech_at
        waited = (first - self._started_at) if first is not None else 0.0
        if outcome is Outcome.SILENT or self._first_voiced_i is None:
            audio = np.zeros(0, dtype=np.float32)
        else:
            # Trim to the speech, with a bounded lead-in and the hangover left on the end.
            # Handing whisper the full capture means handing it every second you spent not
            # talking — it costs transcription time proportional to the padding, and given
            # mostly silence whisper does not return nothing, it INVENTS something. A live
            # turn with 0.32s of speech in 2.16s of audio produced "We'll see you next time."
            pre = int(round(PREROLL_S / (FRAME_SAMPLES / SAMPLE_RATE_HZ)))
            start = max(0, self._first_voiced_i - pre)
            frames = self._frames[start:]           # the hangover after the last word stays:
            # whisper is happier with a little room than with a sentence that stops dead.
            audio = (np.concatenate(frames).astype(np.float32) / 32768.0)
        # `waited` is in this line as of 2026-09-03, and it was the missing instrument.
        #
        # It has been computed here since the class was written and logged nowhere. It is the
        # ONE number that separates the two explanations for a blip capture — a fragment of
        # voice followed by the full hangover:
        #
        #     waited ~0.0s   the recorder opened INTO the tail of the wake phrase
        #     waited ~0.8s   something in the room made a noise a while after the wake
        #
        # Without it, `capture spoke: 2.48s audio, 0.16s voiced` is compatible with both, and
        # the audio saved to `captures/` cannot settle it either: `_finish` trims from
        # `first_voiced_i - PREROLL`, so the offset within the listening window is destroyed
        # before anything downstream can see it. Eight days of logs, 55 wake captures, and the
        # question could not be answered from any of it.
        LOG.info("capture %s: %.2fs audio, %.2fs voiced, waited %.2fs (peak vad %.3f vs "
                 "threshold %.2f, peak mic rms %.4f)",
                 outcome.value, audio.size / SAMPLE_RATE_HZ, speech_s, waited,
                 self._peak_score, self._threshold, self._peak_rms)
        cap = Capture(outcome=outcome, audio=audio, speech_s=speech_s, waited_s=waited)
        self.reset()
        return cap


def build_vad(threads: int = 1):
    """Load openWakeWord's Silero VAD, downloading it if this is a fresh machine.

    Shares `ensure_feature_models()` with the wake path so the download rule lives in one
    place — that function is what fixed a fresh Pi failing with a bare NO_SUCHFILE.
    """
    from openwakeword.vad import VAD

    from audio.wake import ensure_feature_models

    ensure_feature_models("onnx")
    return VAD(n_threads=threads)
