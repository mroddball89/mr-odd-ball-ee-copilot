#!/usr/bin/env python3
"""
Module:  turn.py
Purpose: One conversational turn — from the wake word to a spoken answer.
Author:  LB
Date:    2026-08-12 (rebuilt around Engine 2026-08-19)

Kept out of the entry point so that file stays an entry point instead of acquiring a third job.

    wake fires
      -> startle, state=listening, MIC STAYS OPEN
      -> capture (Silero VAD decides when you stopped talking)
           |- you kept talking  -> transcribe
           |- you went quiet    -> he asks "What's up LB?", then captures again
      -> state=thinking         (the rig's thinking pose)
      -> transcribe -> Engine.ask()
           |- cards to the rig, speech to Piper
           |- pending? -> capture again -> Engine.ask(answer)
      -> state=speaking, mic gated, lip-synced
      -> rest

**What changed in the merge.** This file used to hold the tier ladder — Tier 0's router, then
`classify()`, then Tier 1 streaming or Tier 3 whole. All of it is now one call to
`Engine.ask()`, and that is the point of the merge: `router.py` decides who answers, and this
file goes back to being about audio and nothing else. `_escalate` and `_act` are gone; what is
left is capture, transcribe, show, speak.

**Why the greeting is conditional.** It used to play on every wake, and the microphone is
muted while he talks — so "Hey Mr Odd Ball, what time is it?" in one breath was impossible:
the question landed while he was deaf. His face changing *is* the acknowledgment, which is the
premise of the whole project. The spoken greeting is now what he does when you wake him and
then say nothing, which is the only time it adds anything.

Runs on the speech thread, so it may block. Every rig update crosses back through HudBridge's
threadsafe wrappers, exactly as before.
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from audio.listen import Outcome, UtteranceRecorder
from orchestrator.classify_yes import is_yes

LOG = logging.getLogger("oddball.turn")

# `_ask_permission` used to live here, and it is gone on purpose. It asked the yes/no question
# AND decided what the answer meant AND performed the consequence — three jobs, and the middle
# one is now `orchestrator/classify_yes.is_yes` while the outer two are `Engine`'s. What is
# left in this file is `_answer`, which captures the reply and hands it straight back to the
# Engine. One place decides what a "yes" does; this file only supplies the ears.

# End of the sentence queue. A sentinel object rather than None, because None is a legitimate
# thing for a buggy producer to push and the two must not be confused.
_DONE = object()


@dataclass
class Timings:
    """Where a turn's seconds went.

    PLAN.md asks for exactly this — "instrument each stage (wake -> STT -> route -> infer ->
    TTS -> audio) and log per turn" — because the exit criterion is a number, and "feels fast"
    is how a number quietly regresses.
    """

    capture_s: float = 0.0        # wake -> you stopped talking (includes the hangover)
    stt_s: float = 0.0
    route_s: float = 0.0
    synth_s: float = 0.0          # measured inside speak(), before the first sample plays
    speak_s: float = 0.0          # the whole utterance, playback included
    brain_s: float = 0.0          # Tier 1 or 3: transcript in -> FIRST sentence out
    greeted: bool = False
    heard: str = ""
    intent: str = ""
    tier: str = ""                # "", "local", "cloud" — which brain answered
    why: str = ""                 # the classifier rule that chose it, for the log
    extras: list[str] = field(default_factory=list)

    @property
    def answer_s(self) -> float:
        """End of your speech to the start of his — the number PLAN.md caps at 2s.

        Deliberately excludes the hangover, which is time you spent not talking, and excludes
        playback, which is him talking. It is the gap you actually experience as a delay.

        `brain_s` is inside it from Phase 2 onward: on a Tier 1 or Tier 3 turn the model is
        part of the wait, and leaving it out would report a number LB does not experience.
        """
        return self.stt_s + self.route_s + self.brain_s + self.synth_s

    def line(self) -> str:
        return (f"turn: capture {self.capture_s:.2f}s | stt {self.stt_s:.2f}s | "
                f"route {self.route_s * 1000:.0f}ms"
                + (f" | {self.tier} {self.brain_s:.2f}s" if self.tier else "")
                + f" | synth {self.synth_s:.2f}s"
                + f" => answered in {self.answer_s:.2f}s"
                + (" | greeted" if self.greeted else "")
                + (f" | {self.why}" if self.why else "")
                + (f" | {', '.join(self.extras)}" if self.extras else ""))


class Turn:
    """Runs one turn. Holds no audio device; everything it needs is handed to it.

    Args:
        recorder:   audio.listen.UtteranceRecorder
        transcriber:audio.stt.Transcriber
        engine:     engine.core.Engine — the one thing that decides who answers
        speaker:    audio.say.Speaker
        bridge:     orchestrator.hud_bridge.HudBridge
        gate:       audio.gate.MicGate
        frames:     a callable returning the next 1280-sample frame, or None to abandon.
                    Injected rather than owned so the microphone stays in one place.
        greeting:   lines to choose from when you wake him and then say nothing.
        gate_tail_s / thinking_state: as configured.
    """

    def __init__(self, recorder: UtteranceRecorder, transcriber, engine, speaker, bridge,
                 gate, frames, greeting: list[str], gate_tail_s: float,
                 thinking_state: str = "thinking", save_dir: "Path | None" = None,
                 stall_phrase: str = "") -> None:
        self._rec = recorder
        self._stt = transcriber
        self._engine = engine
        self._speaker = speaker
        self._bridge = bridge
        self._gate = gate
        self._frames = frames
        self._greeting = list(greeting)
        self._gate_tail_s = gate_tail_s
        self._thinking = thinking_state
        self._save_dir = save_dir
        # Empty by default. D31 introduced a stall phrase to hide a cloud round trip that was
        # ASSUMED slow; measured 2026-08-13 it was 0.66-0.72s, so speaking a stall line made
        # the exchange longer rather than shorter.
        #
        # The merge may have changed that arithmetic and it has not been remeasured: a turn now
        # costs a router call PLUS an agent call, and D3's model split put routing on flash-lite
        # at ~890ms. If a HARDWARE or FIRMWARE turn lands past ~2.5s, this is the knob — set it
        # in config and measure the perceived wait, not the total.
        self._stall = stall_phrase

    def _save(self, audio, heard: str) -> None:
        """Write the captured utterance to a WAV, for looking at rather than guessing about.

        A transcript that came out wrong tells you almost nothing on its own: the audio may
        have been clipped, or complete and simply misheard, and those have opposite fixes.
        Keeping the recording turns that into something measurable — and the files double as
        the beginnings of a command fixture set in LB's own voice, which is what the accuracy
        numbers still lack.
        """
        if self._save_dir is None or audio.size == 0:
            return
        try:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            slug = "".join(c if c.isalnum() else "-" for c in heard.lower())[:40] or "empty"
            path = self._save_dir / f"{stamp}_{slug}.wav"
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16_000)
                wf.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
            LOG.info("saved the capture to %s", path)
        except Exception:
            LOG.exception("could not save the capture")

    def _capture(self):
        """Pump frames into the recorder until it decides the utterance is over."""
        self._rec.reset()
        while True:
            frame = self._frames()
            if frame is None:
                return None                     # shutting down
            done = self._rec.feed(frame)
            if done is not None:
                return done

    def _say(self, text: str, timings: Timings) -> None:
        """Speak, with the mic gated and his mouth driven by the audio."""
        self._bridge.set_state("speaking")
        t0 = time.monotonic()
        synth_started = [0.0]

        def on_start() -> None:
            synth_started[0] = time.monotonic() - t0

        with self._gate.speaking(self._gate_tail_s):
            self._speaker.speak(text, on_envelope=self._bridge.set_mouth, on_start=on_start)
        self._bridge.set_mouth(0.0)
        timings.synth_s = synth_started[0]
        timings.speak_s = time.monotonic() - t0

    def _say_streaming(self, produce, timings: Timings) -> None:
        """Speak sentences as a brain produces them, instead of waiting for the whole reply.

        `produce(emit)` runs on a worker thread and calls `emit(sentence)` as each sentence
        closes. This is the entire point of streaming: Piper starts on sentence one while the
        model is still generating sentence two, which is what turns a 4s reply into a ~1s wait.

        Two things here are load-bearing:

        **The mic gate is held across the WHOLE utterance, not per sentence.** Releasing it
        between sentences would open the microphone in the gaps while he is plainly still
        talking, and he can wake himself — D28 measured self-wake as unreachable on the WI-4's
        quiet speaker, but the Bose is much louder and that measurement has never been redone.

        **`synth_s` is taken from the FIRST sentence only.** It is the delay LB experiences;
        later sentences are him already talking.
        """
        sentences: "queue.Queue" = queue.Queue()

        def worker() -> None:
            try:
                produce(sentences.put)
            except Exception:                                          # noqa: BLE001
                LOG.exception("the brain failed mid-answer")
            finally:
                sentences.put(_DONE)     # in a finally, or a crash hangs the speech thread

        threading.Thread(target=worker, name="brain", daemon=True).start()

        t0 = time.monotonic()
        synth_started = [0.0]
        spoken = 0

        def on_start() -> None:
            if spoken == 0:
                synth_started[0] = time.monotonic() - t0

        self._bridge.set_state("speaking")
        with self._gate.speaking(self._gate_tail_s):
            while True:
                item = sentences.get()
                if item is _DONE:
                    break
                if not str(item).strip():
                    continue
                self._speaker.speak(str(item), on_envelope=self._bridge.set_mouth,
                                    on_start=on_start)
                spoken += 1
        self._bridge.set_mouth(0.0)
        timings.synth_s = synth_started[0]
        timings.speak_s = time.monotonic() - t0
        if not spoken:
            timings.extras.append("nothing spoken")


    def run(self) -> Timings:
        """One turn, from just after the wake word to just after his answer."""
        t = Timings()
        began = time.monotonic()

        self._bridge.set_state("listening")
        capture = self._capture()
        if capture is None:
            return t

        # You woke him and said nothing. NOW the greeting earns its place.
        if capture.outcome is Outcome.SILENT:
            t.greeted = True
            self._say(random.choice(self._greeting), t)
            self._bridge.set_state("listening")
            capture = self._capture()
            if capture is None:
                return t
            if capture.outcome is Outcome.SILENT:
                LOG.info("nothing said after the greeting — going back to rest")
                t.extras.append("no answer")
                t.capture_s = time.monotonic() - began
                return t

        t.capture_s = time.monotonic() - began
        if capture.outcome is Outcome.TOO_LONG:
            t.extras.append("hit max_s")

        # The thinking pose is set BEFORE transcription, because transcription is the part
        # that takes a second and it is the only moment he has nothing else to show.
        self._bridge.set_state(self._thinking)
        heard = self._stt.transcribe(capture.audio)
        t.stt_s = heard.took_s
        t.heard = heard.text
        self._save(capture.audio, heard.text)

        t0 = time.monotonic()
        self._answer(heard.text, t)
        t.route_s = time.monotonic() - t0
        LOG.info("%s", t.line())
        return t

    def _answer(self, heard: str, t: Timings) -> None:
        """Hand the transcript to the Engine, show the cards, say the speech.

        The whole of the old Tier 0 / classify / brains branch collapses into `Engine.ask()`.
        That is the merge: one dispatcher, and this file goes back to being about audio.
        """
        self._show_line("you", heard)
        response = self._engine.ask(heard)
        self._deliver(response, t, typed=False)

    def answer_typed(self, text: str) -> Timings:
        """Answer a line that was TYPED into the chat panel. No microphone involved.

        The whole point of the typed channel is that it works when the microphone does not —
        measured on the Pi 2026-08-19, wake utterances peaked 0.17-0.28 against a 0.76
        threshold. So this path must not reach for audio anywhere, including at a permission
        gate, which is the one place `_answer` does.
        """
        t = Timings()
        t.heard = text
        began = time.monotonic()

        self._bridge.set_state(self._thinking)
        response = self._engine.ask(text)
        t.intent = response.route
        t.extras.append("typed")
        t.extras.extend(self._engine.last.extras)
        t.route_s = time.monotonic() - began

        self._deliver(response, t, typed=True)
        self._quiet(lambda: self._bridge.set_mode(self._engine.mode))
        LOG.info("%s", t.line())
        return t

    def _deliver(self, response, t: Timings, typed: bool) -> None:
        """Show the cards, speak the sentence, and resolve a gate if one opened.

        Shared by the spoken and typed paths so the two cannot drift — the only difference is
        where the gate's yes/no comes from, and that is the `typed` flag.
        """
        t.intent = response.route
        t.extras.extend(self._engine.last.extras)

        # Cards go up BEFORE he opens his mouth. For a permission gate that ordering is the
        # safety property — the exact command has to be on screen while the question is being
        # asked, not after it has been answered.
        self._show(response)
        self._say(response.speech, t)

        # A gate. The Engine is holding the action; the next thing said resolves it, and
        # silence resolves it too — as a no.
        if response.pending is not None:
            t.extras.append(f"gate {response.pending.kind}")
            self._quiet(lambda: self._bridge.ask_approval(response.pending))

            answer = ""
            if typed:
                # No microphone on this path — that is the whole point of it. Wait for the
                # Approve/Deny buttons or a typed yes/no instead, and time out into a decline,
                # which is the same default silence gets on the spoken path.
                answer = self._wait_for_typed_answer(t)
            else:
                answer = self._spoken_gate_answer(t)

            self._quiet(self._bridge.clear_pending)

            # The thinking pose, BEFORE the action runs. This line is the whole of the
            # "he falls asleep while running a command" bug.
            #
            # `Engine.ask(answer)` on an approved gate is not a question — it is
            # `resume_os_action()`, which spawns a subprocess and waits on it. The state at
            # that moment was last set to "listening" (the spoken path, two blocks up) or was
            # never moved off "speaking" (the typed path), and `run_voice.py` has an idle timer
            # that drops the face to the RESTING state — `sleeping` — after a delay it does not
            # know is about to be spent working. So the one part of a turn that visibly takes
            # time was the one part showing no sign of life.
            #
            # Set unconditionally rather than only for `kind == "os"`: a web search is a network
            # round trip on the same path, and a rule that names one route is a rule the next
            # route forgets.
            self._bridge.set_state(self._thinking)

            # Empty string is deliberate and load-bearing: Engine.ask("") declines the pending
            # action AND closes the gate. Anything short of a clear yes is a no.
            outcome = self._engine.ask(answer)
            self._show(outcome)
            self._say(outcome.speech, t)

        self._quiet(lambda: self._bridge.set_mode(self._engine.mode))

    # What he says when a gate got no answer he could read. Measured 2026-08-22: LB asked for
    # Firefox four times and it never opened, because his COMMANDS came in at mic RMS 0.28-0.33
    # and his confirmations at 0.017-0.020 — 15 to 20 times quieter. Whisper returned "" for
    # every one of them, `is_yes("")` is None, and None declines. In silence.
    #
    # A silent decline is indistinguishable from a crash, which is why he asked four times.
    # This sentence is the difference between "it is broken" and "it did not hear me".
    GATE_RETRY_LINE = ("I didn't catch that. Please give a thumbs up to the camera "
                       "or say yes clearly.")

    # One retry, not a loop. A gate that keeps asking is a gate that eventually gets a yes out
    # of a television, and the safe default is already a decline.
    GATE_ATTEMPTS = 2

    def _spoken_gate_answer(self, t: Timings) -> str:
        """Read a yes or no from voice, camera or a click. **Anything unclear is a no.**

        Three channels, deliberately in this order:

        1. **Voice**, because it is instant and it is how LB actually answers.
        2. **The camera**, ONLY when voice was unreadable. A thumbs up costs a subprocess and
           ~2.2 seconds (D15 measured it), so checking it every time would tax every approval
           to rescue the few that need rescuing. It is the fallback, not the default.
        3. **A click** on the HUD, which beats both because it is unambiguous.

        Gesture approval existed, worked, and was wired only into `run_os_agent()` — the old
        blocking terminal function whose own docstring says it is NOT the path the voice loop
        takes. Built and not wired, the same failure D6 records for the typed channel.

        Returns:
            The answer as text. "" means nothing readable arrived, and `Engine.ask("")`
            declines the pending action. Never returns "yes" on a guess.
        """
        from tools.gesture_control import get_gesture

        for attempt in range(1, self.GATE_ATTEMPTS + 1):
            self._bridge.set_state("listening")
            answer = ""

            capture = self._capture()
            if capture is not None and capture.outcome is not Outcome.SILENT:
                got = self._stt.transcribe(capture.audio)
                t.stt_s += got.took_s          # a second capture is a second transcription
                answer = got.text
                LOG.info("gate: heard %r -> %s", answer, is_yes(answer))
            else:
                t.extras.append("no answer to the gate")

            # A click beats what was heard, because it is unambiguous and a transcript never
            # is. Checked AFTER the capture so LB can answer by voice OR mouse without a race.
            for msg in self._quiet(lambda: self._bridge.drain_inbound()) or []:
                if msg.get("type") == "approve":
                    answer = "yes" if msg.get("value") else "no"
                    t.extras.append(f"gate answered by click: {answer}")

            if is_yes(answer) is not None:
                return answer                  # a clear yes OR a clear no. Both are answers.

            # Voice said nothing readable. Look at the camera before giving up.
            self._bridge.set_state(self._thinking)
            try:
                seen = self._quiet(get_gesture) or "NO_CAMERA"
            except Exception:                                          # noqa: BLE001
                LOG.exception("gate: the camera check failed; treating it as no gesture")
                seen = "NO_CAMERA"

            t.extras.append(f"gate camera: {seen}")
            if seen == "THUMBS_UP":
                LOG.info("gate: approved by thumbs up")
                return "yes"

            if attempt < self.GATE_ATTEMPTS:
                # THE line. Say what went wrong and what to do about it, then listen again.
                LOG.info("gate: nothing readable (heard %r, camera %s) — asking again",
                         answer, seen)
                t.extras.append("gate retry")
                self._say(self.GATE_RETRY_LINE, t)

        t.extras.append("gate unanswered -> declined")
        return ""

    # How long a typed gate waits for Approve/Deny before declining. Generous, because reading
    # a shell command and deciding is slower than saying "yes" — and short enough that a gate
    # LB has walked away from does not hold the panel open indefinitely.
    TYPED_GATE_TIMEOUT_S = 90.0

    def _wait_for_typed_answer(self, t: Timings) -> str:
        """Block until Approve/Deny is clicked, or yes/no is typed, or it times out.

        Returns the answer as text, or "" for a timeout — and "" is not a neutral value here:
        `Engine.ask("")` declines the pending action and closes the gate. Walking away is a no,
        exactly as silence is on the spoken path.
        """
        deadline = time.monotonic() + self.TYPED_GATE_TIMEOUT_S
        while time.monotonic() < deadline:
            for msg in self._quiet(lambda: self._bridge.drain_inbound()) or []:
                kind = msg.get("type")
                if kind == "approve":
                    answer = "yes" if msg.get("value") else "no"
                    t.extras.append(f"gate answered by click: {answer}")
                    return answer
                if kind == "text":
                    typed = str(msg.get("value", ""))
                    t.extras.append(f"gate answered by typing: {typed[:24]!r}")
                    return typed
            time.sleep(0.15)

        t.extras.append("typed gate timed out — declined")
        return ""

    def _quiet(self, fn):
        """Run a rig call, swallowing anything it throws.

        A rig that is not connected, or a browser that has just been closed, must never cost
        him his voice. The HUD is the second channel; the answer is the first, and it is
        already synthesised and waiting by the time any of these are called.
        """
        try:
            return fn()
        except Exception:                                              # noqa: BLE001
            LOG.exception("rig call failed — carrying on without the screen")
            return None

    def _show_line(self, role: str, text: str) -> None:
        self._quiet(lambda: self._bridge.say_line(role, text))

    def _show(self, response) -> None:
        """Push a Response's visual half to the rig."""
        self._quiet(lambda: self._bridge.set_route(response.route))
        self._show_line("oddball", response.speech)
        for card in response.cards:
            self._quiet(lambda c=card: self._bridge.show_card(c))
