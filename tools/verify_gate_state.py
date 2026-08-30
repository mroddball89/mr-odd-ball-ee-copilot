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

import numpy as np                                                    # noqa: E402

from audio.listen import SAMPLE_RATE_HZ, Capture, Outcome             # noqa: E402
from engine.response import Card, CardKind, Pending, Response         # noqa: E402
from engine.turn import Turn                                          # noqa: E402
from orchestrator.classify_yes import is_yes                          # noqa: E402

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
    """Records every sentence, so the retry line is asserted rather than assumed."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text, on_envelope=None, on_start=None):
        self.said.append(text)
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


def build(bridge: FakeBridge, engine, speaker=None) -> Turn:
    return Turn(recorder=None, transcriber=None, engine=engine,
                speaker=speaker or FakeSpeaker(),
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


# =========================================================================================
section("5. the SPOKEN gate — LB's Firefox failure, and the two ways out")
# =========================================================================================

from engine.turn import Timings                                       # noqa: E402


class VoiceGateHarness:
    """Drives `_spoken_gate_answer` with scripted transcripts.

    Reproduces the measured 2026-08-22 failure. LB's COMMANDS transcribed (mic rms 0.28-0.33)
    and his CONFIRMATIONS did not (0.017-0.020 — Whisper returned ""). `is_yes("")` is None,
    None declines, and the decline was SILENT, which is why he asked for Firefox four times
    and concluded the launcher was broken. It was not; it was never approved.
    """

    def __init__(self, transcripts):
        self.bridge = FakeBridge(approve=None)          # no HUD clicks on this path
        self.speaker = FakeSpeaker()
        self.engine = GateEngine(self.bridge)
        self.turn = build(self.bridge, self.engine, self.speaker)
        self._scripted = list(transcripts)
        self.captures = 0

        harness = self

        class _Stt:
            def transcribe(self, _audio):
                harness.captures += 1
                text = harness._scripted.pop(0) if harness._scripted else ""

                class _Heard:
                    pass
                h = _Heard()
                h.text, h.took_s = text, 0.1
                return h

        # **The REAL `Capture`, not a hand-rolled stand-in.** This was a local class with
        # `outcome = "SPOKE"` and `audio = None`, on the reasoning that the transcriber is
        # faked so nothing reads them. That stopped being true on 2026-08-29, when
        # `_spoken_gate_answer` started asking the capture how much of it was voiced — and the
        # double had no answer, so this whole section died with an AttributeError.
        #
        # The numbers are LB's own, from `oddball.log` 07:06:54: 0.40s of voice in 2.72s of
        # audio, which is what a genuine spoken "Yes." measures. They have to be a REAL
        # confirmation, because `orchestrator/credible.py` now refuses one that is not, and a
        # fixture that could not clear that floor would test the floor instead of the gate.
        self.turn._capture = lambda: Capture(
            outcome=Outcome.SPOKE,
            audio=np.zeros(int(2.72 * SAMPLE_RATE_HZ), dtype=np.float32),
            speech_s=0.40, waited_s=0.0)
        self.turn._stt = _Stt()

    def run(self):
        return self.turn._spoken_gate_answer(Timings())


# --- a clear spoken yes still works --------------------------------------------------------
h = VoiceGateHarness(["yes"])
got = h.run()
check(is_yes(got) is True, "a clear spoken 'yes' approves", f"got {got!r}")
check(h.captures == 1, "...on the first capture, with nothing else consulted",
      f"{h.captures} capture(s)")

h = VoiceGateHarness(["no"])
check(is_yes(h.run()) is False, "a clear spoken 'no' declines")

# --- THE BUG, and what answers it now ------------------------------------------------------
#
# The camera used to be the rescue here: a thumbs up approved a yes Whisper could not hear.
# It was removed 2026-08-29 and the retry is the rescue on its own. That is a WEAKER promise
# than the camera made, and stating it plainly is the point of this block — the gate does not
# recover an unheard yes by itself, it asks again and lets LB answer again.
h = VoiceGateHarness(["", "yes"])
check(is_yes(h.run()) is True, "an unheard 'yes' is rescued by the RETRY, not by a camera")

# --- the audible decline -------------------------------------------------------------------
h = VoiceGateHarness(["", ""])
got = h.run()
check(got == "", "nothing readable still DECLINES — the gate never defaults open", f"got {got!r}")
check(any("didn't catch that" in said for said in h.speaker.said),
      "...and he SAYS SO out loud instead of failing silently", f"said: {h.speaker.said}")
check(any("say yes or no" in said.lower() for said in h.speaker.said),
      "...naming what to do about it", f"said: {h.speaker.said}")
check(not any("thumbs" in said.lower() or "camera" in said.lower()
              for said in h.speaker.said),
      "...and never offers a camera he no longer has", f"said: {h.speaker.said}")
check(h.captures == 2, "he listens again after the retry line, exactly once",
      f"{h.captures} capture(s); GATE_ATTEMPTS={Turn.GATE_ATTEMPTS}")


# =========================================================================================
section("6. the gesture system is GONE — and gone means not imported")
# =========================================================================================
#
# Deleting files is easy to do most of the way. What is left behind is an import of a module
# that no longer exists, which does not fail until the one code path that reaches it runs —
# and in this repo that path is a security gate, which only runs when LB asks for something
# dangerous. So this is checked rather than assumed.
#
# **Parsed with `ast`, never grepped.** A textual search over this repo reads the PROSE, and
# the prose still discusses the camera at length, correctly: `engine/turn.py` explains why it
# was removed and `agents/os_agent.py` explains what did not change when it went. Those
# paragraphs are the record and must not make this go red. An import is a syntax node, so ask
# the syntax.

import ast                                                            # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REMOVED_MODULES = {"tools.gesture_control", "tools.gesture_pointer", "tools.win_input",
                   "gesture_control", "gesture_pointer", "win_input"}

live_imports: list[str] = []
scanned = 0
for path in sorted(REPO.rglob("*.py")):
    if any(part in (".venv", "__pycache__", "media", "raw_downloads") for part in path.parts):
        continue
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    scanned += 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in REMOVED_MODULES:
                    live_imports.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "") in REMOVED_MODULES:
                live_imports.append(f"{path.name}:{node.lineno} from {node.module}")

check(not live_imports, f"no module still imports the deleted gesture code ({scanned} files)",
      "; ".join(live_imports) or "clean")

for name in ("tools/gesture_control.py", "tools/gesture_pointer.py", "tools/win_input.py",
             "tools/verify_gestures.py", "tools/verify_pointer.py",
             "tools/live_test_gestures.py"):
    check(not (REPO / name).exists(), f"{name} is deleted")

# The face rig's own `startle` is a DIFFERENT thing wearing the same word, and it stays.
# `hud_bridge.play_gesture` animates the cartoon ball; it never touched a camera.
from orchestrator.hud_bridge import HudBridge                         # noqa: E402

check(hasattr(HudBridge, "play_gesture"),
      "the rig's face animation is untouched — 'gesture' there means the ball, not a hand")


print("\n" + "=" * 78)
print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
print("=" * 78)
if FAILED:
    print(f"\n  {FAILED} RED\n")
    raise SystemExit(1)
print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
