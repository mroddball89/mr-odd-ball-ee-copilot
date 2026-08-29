#!/usr/bin/env python3
"""
Module:  verify_deafness.py
Purpose: Prove the microphone thread stops queueing audio nobody is listening to.
Author:  LB
Date:    2026-08-29

    python tools/verify_deafness.py

No microphone. `engine.run_voice.mic_frames` is replaced with a generator of canned frames and
the real `_listen_thread` is run against it, so what is under test is the shipping loop rather
than a description of it.

## The morning this comes from

2026-08-29. A turn whose router leg took 286 seconds, from `oddball.log`:

    07:25  738 frames dropped     07:27  750 frames dropped
    07:26  750 frames dropped     07:28  750 frames dropped

**8,530 warning lines in one morning**, at 12.5 a second, one per frame, for the whole time the
turn was thinking. That is the visible half.

The invisible half is worse. `frames_q` holds 200 frames — 16 seconds — and it was being filled
throughout, with audio recorded while the turn was NOT listening. When the turn reached its next
`_capture()` — a permission gate, asking whether to run a shell command — the recorder was
handed that backlog and consumed it at memory speed. `UtteranceRecorder` measures `max_s`
against the wall clock, so a 15-second cap never fired on 16 seconds of buffered audio:

    07:24:10  capture spoke: 18.08s audio, 0.96s voiced  ->  "Yes. Yes."  ->  gate: True

A capture longer than the configured maximum, holding audio from before its own question was
asked, approving a command. `orchestrator/credible.py` now refuses that transcript; this file
is about the other end of it — the capture should never have contained that audio at all.

## The distinction the fix rests on

`in_turn` means a turn is running. It does NOT mean he is listening, and a turn spends most of
its wall clock thinking or speaking. `capturing` is the new event, set for exactly as long as
`Turn._capture` is reading, and it is what now gates the handover.

Section 4 checks the accounting is still honest: dropping frames is still reported, because a
wedged queue is a real fault and silence about it would be the opposite mistake.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# BEFORE any `engine.` import. `core.ROUTER_DEADLINE_S` is read at import time, so setting
# this further down rebinds nothing and section 6 would sit through the real 20-second wait to
# assert that a 0.4-second one works. The first version of this file did exactly that, and the
# check it broke — "the deadline is configurable" — is the check that caught it.
os.environ["ODDBALL_ROUTER_DEADLINE_S"] = "0.4"
os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key-but-long-enough-to-pass")
os.environ["ODDBALL_SELF_CONTEXT"] = "0"

import engine.run_voice as rv                                        # noqa: E402

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


FRAME = np.zeros(1280, dtype=np.int16)


class Detector:
    """Scores nothing and never wakes. Records how many frames it was offered."""

    def __init__(self) -> None:
        self.fed = 0

    def feed(self, frame):
        self.fed += 1
        return None


def drive(n_frames: int, *, in_turn_set: bool, capturing_set: bool,
          maxsize: int = 200) -> tuple[queue.Queue, Detector]:
    """Run the real `_listen_thread` over `n_frames` canned frames and hand back what it did."""
    frames_q: queue.Queue = queue.Queue(maxsize=maxsize)
    detector = Detector()
    in_turn, capturing, stop = threading.Event(), threading.Event(), threading.Event()
    if in_turn_set:
        in_turn.set()
    if capturing_set:
        capturing.set()

    real = rv.mic_frames
    rv.mic_frames = lambda device: (FRAME for _ in range(n_frames))
    try:
        rv._listen_thread(detector, "fake", lambda det: None, stop,
                          None, in_turn, frames_q, None, capturing)
    finally:
        rv.mic_frames = real
    return frames_q, detector


# =========================================================================================
section("1. thinking or speaking — the queue stays EMPTY")
# =========================================================================================
#
# The turn is running but not listening. Every frame here is audio of LB waiting, and none of
# it is his next sentence.

frames_q, detector = drive(500, in_turn_set=True, capturing_set=False)
check(frames_q.qsize() == 0,
      "500 frames arrive while the turn is thinking and NONE is queued",
      "" if frames_q.qsize() == 0 else
      f"{frames_q.qsize()} queued — this is the 16s backlog the gate then read as live audio")
check(detector.fed == 0,
      "and none reaches the wake detector either — a wake word inside a turn is a loop",
      f"detector saw {detector.fed}")

# =========================================================================================
section("2. listening — frames flow, and the run-up comes with them")
# =========================================================================================

frames_q, detector = drive(50, in_turn_set=True, capturing_set=True)
check(frames_q.qsize() == 50 + rv.PREBUFFER_FRAMES or frames_q.qsize() == 50,
      f"50 frames arrive while capturing and {frames_q.qsize()} are queued",
      "the prebuffer is empty on this run because nothing preceded the turn")
check(frames_q.qsize() >= 50,
      "every frame spoken while he is listening is handed over",
      f"only {frames_q.qsize()} of 50 arrived")

# =========================================================================================
section("3. no turn at all — the frames go to the wake detector")
# =========================================================================================

frames_q, detector = drive(40, in_turn_set=False, capturing_set=False)
check(detector.fed == 40, "all 40 frames are scored for the wake word",
      f"detector saw {detector.fed}")
check(frames_q.qsize() == 0, "and none is queued for a turn that is not running")

# =========================================================================================
section("4. a wedged queue is still reported — but once a second, not once a frame")
# =========================================================================================
#
# The opposite mistake would be to silence this. A full queue means a consumer is stuck, which
# is a real fault; what was wrong was the volume, not the fact.


class Counter(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            self.lines.append(record.getMessage())


counter = Counter()
rv.LOG.addHandler(counter)
try:
    # Capturing, so frames flow — but the queue only holds 10, so 300 of them are dropped.
    frames_q, _ = drive(310, in_turn_set=True, capturing_set=True, maxsize=10)
finally:
    rv.LOG.removeHandler(counter)

drops = [line for line in counter.lines if "utterance buffer full" in line]
check(bool(drops),
      "a wedged queue says so — at least one line, even for a burst shorter than the interval",
      "" if drops else
      "SILENT. The first version of this throttle started its clock at `now`, so a burst that "
      "ended inside one second logged nothing at all — indistinguishable from a healthy mic")
check(len(drops) <= 2,
      f"but {len(drops)} line(s) for 300 dropped frames, not 300",
      "" if len(drops) <= 2 else
      f"still roughly one line per frame: {len(drops)} — this is what buried the log")
check(any("dropped" in line and any(ch.isdigit() for ch in line) for line in drops),
      "and the line carries the COUNT, so the size of the problem stays legible",
      f"said: {drops[0]!r}" if drops else "no line to carry it")

# =========================================================================================
section("5. Turn._capture raises and lowers the flag, and drains what it did not hear")
# =========================================================================================
#
# The other half of the contract. If `capturing` is left set, section 1 silently stops holding;
# if it is never set, section 2 does. Both are checked against the real `Turn._capture`.

from audio.listen import Capture, Outcome                            # noqa: E402
from engine.turn import Turn                                         # noqa: E402


class Recorder:
    """Ends the capture after one frame, so `_capture` returns promptly."""

    def __init__(self):
        self.saw_flag_while_reading = None

    def reset(self):
        pass

    def feed(self, frame):
        return Capture(outcome=Outcome.SPOKE, audio=np.zeros(16000, dtype=np.float32),
                       speech_s=1.0, waited_s=0.0)


capturing = threading.Event()
drained = []
seen_during = []


def frames_one():
    seen_during.append(capturing.is_set())
    return FRAME


turn = Turn(recorder=Recorder(), transcriber=None, engine=None, speaker=None, bridge=None,
            gate=None, frames=frames_one, greeting=[], gate_tail_s=0.0,
            capturing=capturing, drain=lambda: drained.append(1))

check(not capturing.is_set(), "the flag starts clear")
turn._capture()
check(seen_during == [True],
      "it is SET while _capture is reading frames", f"saw {seen_during}")
check(not capturing.is_set(),
      "and CLEAR again the moment the capture ends",
      "a flag left set is the original bug with extra steps")
check(drained == [1],
      "and anything queued while he was not listening is thrown away first",
      f"drain called {len(drained)}x")


# The finally clause, checked rather than trusted: an exception out of the recorder must still
# put the microphone back, or one bad capture deafens the rest of the session.
class Exploding(Recorder):
    def feed(self, frame):
        raise RuntimeError("recorder blew up mid-capture")


turn2 = Turn(recorder=Exploding(), transcriber=None, engine=None, speaker=None, bridge=None,
             gate=None, frames=lambda: FRAME, greeting=[], gate_tail_s=0.0,
             capturing=capturing, drain=None)
try:
    turn2._capture()
except RuntimeError:
    pass
check(not capturing.is_set(),
      "a capture that raises still lowers the flag",
      "otherwise one exception leaves him queueing audio nobody will ever read")


# =========================================================================================
section("6. a hung router leg is abandoned, not waited on")
# =========================================================================================
#
# 2026-08-29, from `oddball.log` — four route calls that all returned HTTP 200 in the end:
#
#     "organize the STL files"       90,984 ms
#     "add to my note"             126,844 ms
#     "read me back the notes"     162,453 ms
#     "tell me what notes"         285,985 ms
#
# Two other routes the same morning took 766 ms and 875 ms. Nothing errored, nothing retried,
# `LLM_MAX_RETRIES` was already 0 — so not one existing protection applied. The deadline is a
# wall clock because that is the only thing that describes the fault.

import time as _time                                                 # noqa: E402

import engine.core as core                                           # noqa: E402
from engine.response import Response                                 # noqa: E402
from router import AgentRoute, RouteDecision                         # noqa: E402

check(core.ROUTER_DEADLINE_S == 0.4,
      f"the deadline is configurable ({core.ROUTER_DEADLINE_S}s for this harness)",
      "a hard-coded timeout cannot be tested without waiting out the real one")

# A router that never returns, exactly like the 286-second call.
started = threading.Event()


def hung_router(query):
    started.set()
    _time.sleep(30)
    raise AssertionError("this result must never be used")


core.router_agent = hung_router
dispatched = []
eng = core.Engine(confirm_gates=True)
eng._dispatch = lambda dest, text, t: (dispatched.append(dest) or
                                       Response(speech="ok", route=dest.value, raw="ok"))

began = _time.monotonic()
eng.ask("something no free tier can answer about quantum widgets")
took = _time.monotonic() - began

check(started.is_set(), "the router really was called",
      "" if started.is_set() else "a free path answered, so this proved nothing")
# The ROUTE LEG, not the whole turn. `ask()` also runs the free tier and loads the sentence
# encoder for the corpus pass, and folding those into the assertion measured the wrong thing —
# it failed at 5.44s on a deadline that had fired correctly at 0.4s.
leg = eng.last.route_s
check(leg < core.ROUTER_DEADLINE_S + 0.5,
      f"the router leg is cut off at {leg:.2f}s rather than running to 286",
      "" if leg < core.ROUTER_DEADLINE_S + 0.5 else
      f"the deadline did not fire: {leg:.2f}s against a {core.ROUTER_DEADLINE_S}s limit")
check(took < 30.0,
      f"and the turn as a whole finishes in {took:.2f}s instead of blocking on the hung call",
      "" if took < 30.0 else "the turn waited for the router thread after all")
check(dispatched == [AgentRoute.GENERAL],
      "and the turn lands on GENERAL rather than dying",
      "" if dispatched == [AgentRoute.GENERAL] else f"dispatched to {dispatched}")
check(any("deadline" in e for e in eng.last.extras),
      "with the fallthrough recorded, so it cannot be mistaken for a real GENERAL route",
      f"extras: {eng.last.extras}")

# A router that answers in time is untouched — a deadline must not become a second router.
core.router_agent = lambda q: RouteDecision(destination=AgentRoute.MATH, reasoning="fast")
dispatched.clear()
eng2 = core.Engine(confirm_gates=True)
eng2._dispatch = lambda dest, text, t: (dispatched.append(dest) or
                                        Response(speech="ok", route=dest.value, raw="ok"))
eng2.ask("integrate x squared from nought to three")
check(dispatched == [AgentRoute.MATH],
      "a router that answers in time is obeyed exactly as before",
      "" if dispatched == [AgentRoute.MATH] else f"dispatched to {dispatched}")



print("\n" + "=" * 78)
print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
print("=" * 78)
if FAILED:
    print(f"\n  {FAILED} RED\n")
    raise SystemExit(1)
print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
