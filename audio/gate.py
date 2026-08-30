#!/usr/bin/env python3
"""
Module:  gate.py
Purpose: Half-duplex mic gating — stop Mr Odd Ball waking himself on the sound of his own voice.
Author:  LB
Date:    2026-08-11

He speaks through a speaker that his microphone can hear. Without a gate, "Hey Mr Odd Ball"
coming out of his own mouth is indistinguishable from LB saying it, and any greeting containing
his name would put him in a loop.

This is the deliberate, guaranteed-working half of the echo problem (PLAN.md Phase 0, D11):
**mute capture while he talks, plus a short tail.** Acoustic echo cancellation — which would
allow interrupting him mid-sentence — is the better answer and is explicitly deferred; it needs
a stable clock domain (D25) and measurement that has not happened yet.

Two rules, and the second is the one that is easy to get wrong:

1. While gated, the caller must **keep pulling frames from the microphone and stop feeding
   them to the detector.** Stopping the pull backs up PortAudio's buffer and produces input
   overflows, which look like a broken microphone.
2. On reopening, the detector must be **reset**. openWakeWord's frontend holds a rolling
   buffer several frames deep, so the tail of his own sentence is still inside the model at
   the moment the gate opens — it would score, and fire, after the gate was already shut for
   it. This is the same failure that made a single utterance re-trigger in step 3.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

LOG = logging.getLogger("oddball.gate")


class MicGate:
    """A one-way valve on the microphone, open by default.

    Args:
        on_open: called once each time the gate reopens, from whichever thread noticed.
                 This is where the detector gets reset — see rule 2 above. It is a plain
                 public attribute because the detector is usually built after the gate;
                 assigning `gate.on_open = detector.reset` later is expected.
        clock:   monotonic seconds source; injectable so the verifier can test the tail
                 without sleeping through it.

    Thread-safe: `close()` is called from the speech thread and `is_open()` from the mic
    thread, which are the only two threads in the program that touch it.
    """

    def __init__(
        self,
        on_open: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_open = on_open
        self._clock = clock
        self._lock = threading.Lock()
        self._closed = False
        self._reopen_at: float | None = None
        self.blocked_frames = 0     # how much audio was discarded, for the logs

    def close(self) -> None:
        """Shut the mic now, with no scheduled reopening."""
        with self._lock:
            if not self._closed:
                LOG.debug("mic gate closed")
            self._closed = True
            self._reopen_at = None

    def open_after(self, tail_s: float) -> None:
        """Reopen `tail_s` from now.

        The tail covers the room's reverberation and the sound card's buffered-but-unplayed
        audio, both of which outlive the last sample written.
        """
        if tail_s < 0:
            raise ValueError(f"tail_s must be >= 0, got {tail_s}")
        with self._lock:
            if self._closed:
                self._reopen_at = self._clock() + tail_s

    def open_now(self) -> None:
        """Reopen immediately, firing `on_open`. Mainly for shutdown paths."""
        with self._lock:
            self._reopen_at = self._clock()
        self.is_open()

    def is_open(self) -> bool:
        """True if audio should reach the detector.

        Evaluates the pending reopen, so `on_open` fires exactly once per closure, on the
        first call after the tail expires. Callers poll this once per frame.
        """
        fire = False
        with self._lock:
            if not self._closed:
                return True
            if self._reopen_at is not None and self._clock() >= self._reopen_at:
                self._closed = False
                self._reopen_at = None
                fire = True
            else:
                self.blocked_frames += 1
                return False
        if fire and self.on_open is not None:
            LOG.debug("mic gate open (%d frames discarded)", self.blocked_frames)
            self.on_open()
        return True

    def speaking(self, tail_s: float) -> "_Speaking":
        """Context manager: closed for the duration of the block, reopening `tail_s` after.

        Used rather than paired close()/open_after() calls so that an exception raised
        mid-sentence still reopens the gate. The failure it prevents is total and silent —
        he would simply never hear anything again.
        """
        return _Speaking(self, tail_s)


class _Speaking:
    """The context manager returned by MicGate.speaking()."""

    def __init__(self, gate: MicGate, tail_s: float) -> None:
        self._gate = gate
        self._tail_s = tail_s

    def __enter__(self) -> MicGate:
        self._gate.close()
        return self._gate

    def __exit__(self, *exc) -> None:
        self._gate.open_after(self._tail_s)
