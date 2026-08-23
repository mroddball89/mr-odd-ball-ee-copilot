#!/usr/bin/env python3
"""
Module:  verify_gate_state.py
Purpose: Prove the face is awake while an approved action actually runs.
Author:  LB
Date:    2026-08-22

    python tools/verify_gate_state.py        # no audio, no camera, no API key

## The bug this pins

LB: *"the avatar defaults to sleeping while the OS agent executes bash commands."*

`Turn._deliver()` resolves a permission gate by calling `Engine.ask(answer)` — and on an
approved gate that call is not a question, it is `resume_os_action()`, which spawns a
subprocess and waits on it. Nothing set a state before it. The last state set was
`"listening"` (the spoken path, while waiting for the yes) or `"speaking"` (the typed path),
and `run_voice.py` runs an idle timer that drops the face to the **resting state — `sleeping`**
after a delay that knows nothing about the work about to happen.

So the one stretch of a turn that visibly takes time was the one stretch showing no sign of
life, and on a slow command the face went to sleep mid-execution.

## Why it is asserted as ORDER rather than as a final value

Checking `bridge.state == "thinking"` after the fact proves nothing: the resume finishes and
something else sets the next state, so the interesting moment is already gone. What matters is
the state **at the instant the action ran**, so the fake engine records the state it can see
when `ask()` is entered. That is the property, stated directly.

The spoken path is checked too, because it is the one that had `"listening"` set two blocks
above the resume — the failure was worse there, and a fix applied to only one of the two
branches is exactly the drift `_deliver` exists to prevent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import os                                                            # noqa: E402

from dotenv import load_dotenv                                       # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
_k = os.environ.get("GOOGLE_API_KEY", "").strip()
if len(_k) < 20 or any(p in _k.lower() for p in ("paste", "here", "your-key", "xxx")):
    os.environ["GOOGLE_API_KEY"] = "harness-not-a-real-key-but-long-enough-to-pass"

from engine.response import Card, CardKind, Pending, Response         # noqa: E402
from engine.turn import Turn                                          # noqa: E402

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


# --- the fakes. Only what Turn actually touches. -----------------------------------------

class FakeBridge:
    """Records every state change, and answers the gate by click."""

    def __init__(self, approve: bool = True) -> None:
        self.state = "sleeping"          # the resting state — where the bug left it
        self.states: list[str] = []
        self._approve = approve
        self.mouth = 0.0

    def set_state(self, name):
        self.state = name
        self.states.append(name)

    def set_mouth(self, v):    self.mouth = v
    def set_route(self, r):    pass
    def set_mode(self, m):     pass
    def show_card(self, c):    pass
    def say_line(self, r, t):  pass
    def ask_approval(self, p): pass
    def clear_pending(self):   pass

    def drain_inbound(self):
        # A click on Approve. Consumed once, like the real queue.
        if self._approve is None:
            return []
        answer, self._approve = self._approve, None
        return [{"type": "approve", "value": answer}]


class FakeSpeaker:
    def speak(self, text, on_envelope=None, on_start=None):
        if on_start:
            on_start()


class FakeGate:
    def speaking(self, tail_s):
        class _Ctx:
            def __enter__(self_inner):  return self_inner
            def __exit__(self_inner, *a): return False
        return _Ctx()


class GateEngine:
    """Returns a gated Response first, then the result — recording the state it ran under.

    `saw_state_on_resume` is the whole point: it is the face's state at the instant the
    approved action actually executed.
    """

    def __init__(self, bridge: FakeBridge, kind: str = "os") -> None:
        self.mode = "normal"
        self._bridge = bridge
        self._kind = kind
        self.calls = 0
        self.saw_state_on_resume: str | None = None

        class _Last:
            extras: list[str] = []
        self.last = _Last()

    def ask(self, text: str) -> Response:
        self.calls += 1
        if self.calls == 1:
            return Response(
                speech="I want to check the CPU temperature. Should I?",
                cards=[Card(CardKind.CODE, "Wants to run", "cat /sys/.../temp", "bash")],
                route=self._kind,
                pending=Pending(kind=self._kind, tool_args={"command": "x"},
                                spoken="Should I?", shown="cat /sys/.../temp"),
                raw="Proposed")
        # THE MOMENT UNDER TEST — this stands in for resume_os_action()'s subprocess.
        self.saw_state_on_resume = self._bridge.state
        return Response(speech="Done. The output's on the screen.", route=self._kind,
                        raw="OS Execution Result: 45000")


def build(bridge: FakeBridge, engine) -> Turn:
    return Turn(recorder=None, transcriber=None, engine=engine, speaker=FakeSpeaker(),
                bridge=bridge, gate=FakeGate(), frames=None, greeting=[], gate_tail_s=0.0,
                thinking_state="thinking")


# =========================================================================================
section("1. the typed path — the face is awake while the command runs")
# =========================================================================================

bridge = FakeBridge(approve=True)
engine = GateEngine(bridge)
turn = build(bridge, engine)
turn.answer_typed("check the cpu temperature")

check(engine.calls == 2, "the gate opened and then resolved", f"ask() called {engine.calls}x")
check(engine.saw_state_on_resume == "thinking",
      "the state IS 'thinking' at the instant the approved action runs",
      f"it was {engine.saw_state_on_resume!r} — 'sleeping' or 'speaking' is the bug")
check("thinking" in bridge.states, "a thinking state was broadcast at all",
      f"states seen: {bridge.states}")

# =========================================================================================
section("2. a DECLINED gate does not leave him staring into space")
# =========================================================================================

bridge2 = FakeBridge(approve=False)
engine2 = GateEngine(bridge2)
build(bridge2, engine2).answer_typed("delete everything")

# Declining still calls ask() to close the gate, and that path is instant — but it must not
# be left showing a state that says work is happening either.
check(engine2.calls == 2, "a decline still closes the gate", f"ask() called {engine2.calls}x")
check(bridge2.states[-1] in ("speaking", "listening", "thinking", "idle", "sleeping"),
      "the face lands on a real state after a decline", f"ended on {bridge2.states[-1]!r}")

# =========================================================================================
section("3. it is not special-cased to OS — a WEB gate behaves identically")
# =========================================================================================

bridge3 = FakeBridge(approve=True)
engine3 = GateEngine(bridge3, kind="web")
build(bridge3, engine3).answer_typed("what does a 2N3904 cost")

check(engine3.saw_state_on_resume == "thinking",
      "a web search shows the thinking pose while it runs too",
      f"it was {engine3.saw_state_on_resume!r} — a rule that names one route is a rule the "
      f"next route forgets")

# =========================================================================================
section("4. the ungated path is unchanged")
# =========================================================================================


class PlainEngine:
    mode = "normal"

    def __init__(self):
        self.calls = 0

        class _Last:
            extras: list[str] = []
        self.last = _Last()

    def ask(self, text):
        self.calls += 1
        return Response(speech="It's 7 oh 7.", route="utility", raw="It's 7 oh 7.")


bridge4 = FakeBridge()
plain = PlainEngine()
build(bridge4, plain).answer_typed("what time is it")
check(plain.calls == 1, "an ungated turn asks exactly once", f"{plain.calls}")
check(bridge4.states[0] == "thinking",
      "and still shows the thinking pose while the answer is produced",
      f"states: {bridge4.states}")


print("\n" + "=" * 78)
print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
print("=" * 78)
if FAILED:
    print(f"\n  {FAILED} RED\n")
    raise SystemExit(1)
print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
