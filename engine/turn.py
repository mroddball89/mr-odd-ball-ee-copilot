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

from audio.listen import SAMPLE_RATE_HZ, Outcome, UtteranceRecorder
from orchestrator import credible
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
        # **"engine", not "route".** `Turnlog.route_s` in engine/core.py is the ROUTER LEG and
        # prints on the line directly above this one; this field is the whole `Engine.ask`,
        # router and agent and reminders together. Both were labelled "route", one line apart,
        # and reading them as the same quantity is how a 2026-08-29 turn looked like a 48-second
        # router call when the router had taken 1.0s and an embedding model had taken 11.
        return (f"turn: capture {self.capture_s:.2f}s | stt {self.stt_s:.2f}s | "
                f"engine {self.route_s * 1000:.0f}ms"
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
                 stall_phrase: str = "",
                 capturing: "threading.Event | None" = None,
                 drain=None, dictation_max_s: float = 0.0,
                 wake_tail_s: float = 0.0, quiz_wait_s: float = 0.0,
                 quiz_max_s: float = 0.0, should_greet=None) -> None:
        self._rec = recorder
        # 0.0 means "leave the cap alone", which is what every harness that does not pass it
        # gets. See `_capture` and `dictation_max_s` in config/oddball.toml.
        self._dictation_max_s = float(dictation_max_s or 0.0)
        # The same bargain again, for the turns where LB is ANSWERING a quiz question rather
        # than asking one. 0.0 leaves both knobs alone. See `_is_quizzing` and `[listen]
        # quiz_wait_s` / `quiz_max_s`.
        self._quiz_wait_s = float(quiz_wait_s or 0.0)
        self._quiz_max_s = float(quiz_max_s or 0.0)
        # "Is there a reason to believe LB actually called him?" A zero-argument callable, and
        # deliberately NOT a reference to the detector: `Turn` has no business knowing what a
        # wake score is, and a harness has to be able to answer the question with a lambda.
        # Defaults to "always", which is the behaviour before 2026-09-06.
        self._should_greet = should_greet if callable(should_greet) else (lambda: True)
        # Applied to the FIRST capture of a turn only — the one that follows the wake word and
        # therefore opens into its tail. See `wake_tail_s` in config/oddball.toml.
        self._wake_tail_s = float(wake_tail_s or 0.0)
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
        # Set for exactly as long as `_capture` is reading, so the microphone thread knows the
        # difference between "a turn is running" and "he is listening right now". Optional, so
        # a harness can build a Turn without one and the behaviour is what it always was.
        self._capturing = capturing
        self._drain = drain

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

    def _capture(self, suppress_start_s: float = 0.0):
        """Pump frames into the recorder until it decides the utterance is over.

        Args:
            suppress_start_s: voice this early may not BEGIN an utterance. Non-zero only for
                the capture that follows a wake word, where the wake phrase's own tail arrives
                first. Nothing is muted — see `UtteranceRecorder.ignore_start_s`.

        **Announces that it is listening, and throws away whatever arrived while it was not.**
        Both halves matter and the second is the one that was a bug: `frames_q` holds 16
        seconds, and a turn that spent 286 seconds in the router came back to a queue full of
        audio recorded before its question was even asked. The recorder consumed that backlog
        at memory speed, so the wall-clock `max_s` never fired, and the permission gate read a
        capture that was 18.08 seconds long with 0.96 seconds of voice scattered through it.
        It transcribed to "Yes. Yes." and a command ran.

        `orchestrator/credible.py` refuses that capture now. This stops it existing.

        **The cap follows the activity**, added 2026-09-03. With a note draft open and waiting
        on content, LB is dictating a paragraph rather than asking a question, and the 15s cap
        that suits the second cut the first in half mid-clause — see `dictation_max_s` in
        `config/oddball.toml` for the recording it ruined. Raised for exactly the turns where
        the Engine is holding an open draft, and put back in `finally` so an exception cannot
        leave the microphone running long for the rest of the session.
        """
        self._rec.reset()

        # Touched ONLY when the cap is actually being raised, and guarded, because `Turn` is
        # built with a real `UtteranceRecorder` in the rig and with stubs in three harnesses.
        # `verify_deafness`'s stub has no `max_s`, and reading it unconditionally took the
        # microphone thread down with an AttributeError — caught by running the harness, which
        # is the entire reason the stub exists. A recorder that cannot be capped simply does
        # not get the longer cap; nothing else about the turn changes.
        was_max_s = None
        if self._dictation_max_s and self._is_dictating():
            try:
                was_max_s = self._rec.max_s
                self._rec.max_s = self._dictation_max_s
                LOG.info("dictation: recording up to %.0fs for this turn", self._rec.max_s)
            except AttributeError:
                was_max_s = None

        # The quiz budget. Same guarded shape, and it must sit AFTER the dictation block so it
        # cannot clobber a `was_max_s` that block already took — the two predicates are
        # mutually exclusive in practice (an open note draft and an open quiz question cannot
        # both be true), but the restore in `finally` must not depend on that being true.
        was_wait_s = None
        if self._quiz_wait_s and self._is_quizzing():
            # **Two attributes, two `try` blocks, and that is not tidiness.** Written as one
            # block, a recorder that has `wait_s` but not `max_s` raises AFTER the wait has
            # already been raised, and the single `except` would then clear `was_wait_s` — so
            # the `finally` restores nothing and the microphone waits thirty seconds on every
            # turn for the rest of the session. That is precisely the leak this whole guard
            # exists to prevent, reintroduced by the guard itself.
            try:
                was_wait_s = self._rec.wait_s
                self._rec.wait_s = self._quiz_wait_s
            except AttributeError:
                # A recorder that cannot be told to wait simply does not wait longer. This is
                # the `verify_deafness` lesson from the dictation block above, and it is the
                # one that has actually bitten: `wait_s` did not exist as a property at all
                # until 2026-09-06, so every stub written before then lacks it.
                was_wait_s = None
            if self._quiz_max_s and was_max_s is None:
                try:
                    was_max_s = self._rec.max_s
                    self._rec.max_s = self._quiz_max_s
                except AttributeError:
                    was_max_s = None
            LOG.info("quiz: %.0fs to begin answering, %.0fs cap on this answer",
                     getattr(self._rec, "wait_s", 0.0), getattr(self._rec, "max_s", 0.0))

        # Same guarded shape, and for the same reason: a stub recorder in a harness has no
        # such attribute, and the microphone thread must not die because of it.
        was_ignore_s = None
        if suppress_start_s:
            try:
                was_ignore_s = self._rec.ignore_start_s
                self._rec.ignore_start_s = suppress_start_s
            except (AttributeError, ValueError):
                was_ignore_s = None
        if self._drain is not None:
            self._drain()
        if self._capturing is not None:
            self._capturing.set()
        try:
            while True:
                frame = self._frames()
                if frame is None:
                    return None                 # shutting down
                done = self._rec.feed(frame)
                if done is not None:
                    return done
        finally:
            # In a `finally` because every exit from that loop — a capture, a shutdown, or an
            # exception out of the recorder — must put the microphone back. A `capturing` flag
            # left set is the original bug with extra steps.
            if self._capturing is not None:
                self._capturing.clear()
            # And the cap, for the same reason. A raised cap left raised is a microphone that
            # records a television for ninety seconds on every turn for the rest of the day.
            if was_max_s is not None:
                self._rec.max_s = was_max_s
            # And the wait, which matters MORE than the cap does. A raised cap only costs
            # anything on a turn that is genuinely still recording; a raised wait is spent on
            # every ordinary turn that hears nothing at all, so leaving it set would make him
            # sit in silence for thirty seconds after every false wake for the rest of the day.
            if was_wait_s is not None:
                self._rec.wait_s = was_wait_s
            # And the suppression window, which left in place would deafen him to the first
            # quarter-second of every answer at every permission gate.
            if was_ignore_s is not None:
                self._rec.ignore_start_s = was_ignore_s

    def _is_dictating(self) -> bool:
        """True when the Engine is holding a note draft that is waiting on CONTENT.

        Not merely "a draft is open": a draft awaiting a NAME is answered with two or three
        words, and giving that ninety seconds would only mean waiting longer for the hangover
        on a turn that never needed it.

        Duck-typed and wrapped, because `Turn` is constructed with a real Engine in the rig and
        with stubs in three different harnesses. A stub without `note_draft` must not crash the
        microphone — it just does not get the longer cap.
        """
        try:
            draft = getattr(self._engine, "note_draft", None)
            return draft is not None and getattr(draft, "awaiting", "") == "content"
        except Exception:                                             # noqa: BLE001
            return False

    def _is_quizzing(self) -> bool:
        """True when a quiz question is on the table and LB owes an answer to it.

        Not merely `mode == "quiz"`: with `quiz.item` None the bank has run dry and there is
        nothing to work out, so a thirty-second wait would only mean waiting longer to be told
        the deck is empty.

        Duck-typed and wrapped for the reason `_is_dictating` gives — `Turn` is built with a
        real Engine in the rig and with stubs in three harnesses, and a stub without `quiz`
        must not take the microphone thread down with an AttributeError. It just does not get
        the longer wait.
        """
        try:
            engine = self._engine
            if getattr(engine, "mode", "") != "quiz":
                return False
            return getattr(getattr(engine, "quiz", None), "item", None) is not None
        except Exception:                                             # noqa: BLE001
            return False

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


    def _silence_line(self, attempt: int) -> "str | None":
        """What to say when a capture came back silent — or None to say nothing and stop.

        Every silent capture used to be answered the same way, with the greeting. Three
        different things produce one, and only the last of them is a greeting's business.

        **Mid-quiz.** He is working the question out. "What's up LB?" arriving in the middle of
        long division is the machine talking over the exact thinking it just asked for, and it
        is what LB reported: *"i need time to answer the question."* The Engine owns these
        words because they are quiz words — see `Engine.quiz_silence_line`, which also decides
        when to shut up entirely because the session is about to close.

        **A wake nobody meant.** `should_greet()` is False: the detector fired below the score
        LB's own calls reach, and nothing has been heard since. Speaking here is the ENTIRE
        audible cost of a false wake — on 2026-08-31 it was twenty-seven greetings addressed to
        an empty room on a day he never touched the machine, and it is what he actually hears
        from another room. His face still changed, instantly and unconditionally; only the
        voice waits for a reason.

        **Everything else.** Unchanged. You woke him, he heard you, and you said nothing — the
        greeting earns its place and gets it.

        Args:
            attempt: 1 after the first silent capture, 2 after the one following the line.

        Returns:
            The line, or None meaning say nothing and end the turn. **Returning None on attempt
            1 also skips the second capture**, which is the other half of what a false wake
            costs: about four seconds of open microphone spent waiting for a room to answer.
        """
        if self._is_quizzing():
            try:
                return self._engine.quiz_silence_line(attempt)
            except Exception:                                         # noqa: BLE001
                # A stub Engine in a harness has no such method. Fall through to the greeting,
                # which is what a Turn without a quiz has always done.
                LOG.debug("engine has no quiz_silence_line; using the greeting")

        if attempt <= 1 and not self._should_greet():
            return None
        if attempt > 1:
            return None                    # the greeting is never repeated; it never was
        return random.choice(self._greeting)

    def run(self) -> Timings:
        """One turn, from just after the wake word to just after his answer."""
        t = Timings()
        began = time.monotonic()

        self._bridge.set_state("listening")
        # **The only capture that gets the suppression window.** This one opens immediately
        # after the wake detector fired, so the tail of "...Odd Ball" is still arriving; the
        # greeting retry below and the permission-gate capture are both preceded by HIS voice,
        # not LB's, and `MicGate` already covers those.
        capture = self._capture(suppress_start_s=self._wake_tail_s)
        if capture is None:
            return t

        # You woke him and said nothing. Whether that earns a spoken line depends on WHY it
        # was silent, and `_silence_line` is where the three reasons are told apart.
        if capture.outcome is Outcome.SILENT:
            line = self._silence_line(attempt=1)
            if line is None:
                LOG.info("silent capture and nothing worth saying — going back to rest")
                t.extras.append("no answer")
                t.capture_s = time.monotonic() - began
                return t
            t.greeted = True
            self._say(line, t)
            self._bridge.set_state("listening")
            capture = self._capture()
            if capture is None:
                return t
            if capture.outcome is Outcome.SILENT:
                second = self._silence_line(attempt=2)
                if second:
                    self._say(second, t)
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

        # **Is there any reason to believe he said that?** `base.en` hands back a sentence for
        # near-silence rather than an empty string, and on 2026-08-29 at 07:06:08 "Thank you
        # for watching." — from 0.08s of voiced audio in 2.48s — was routed to PERSONA and
        # cost a paid call out of a twenty-a-day budget.
        #
        # **The capture is saved BEFORE this check**, deliberately. A rejected turn is exactly
        # the recording worth having: it is the evidence for retuning the floors, and dropping
        # it would leave the thresholds unfalsifiable.
        credit = credible.assess(heard.text, capture.speech_s,
                                 capture.audio.size / SAMPLE_RATE_HZ)
        if not credit:
            # Silence, not an apology. These are turns where NOBODY SPOKE, and answering the
            # room with "sorry, didn't catch that" teaches LB to talk back to his own noise
            # floor. `Outcome.SILENT` already ends a turn without a word; this is that case
            # arriving one step later, after the transcriber invented something.
            LOG.info("not acting on %r — %s", heard.text, credit.reason)
            t.extras.append(f"not credible: {credit.reason}")
            return t

        t0 = time.monotonic()
        self._answer(heard.text, t, truncated=capture.outcome is Outcome.TOO_LONG)
        t.route_s = time.monotonic() - t0
        LOG.info("%s", t.line())
        return t

    def _answer(self, heard: str, t: Timings, truncated: bool = False) -> None:
        """Hand the transcript to the Engine, show the cards, say the speech.

        The whole of the old Tier 0 / classify / brains branch collapses into `Engine.ask()`.
        That is the merge: one dispatcher, and this file goes back to being about audio.

        `truncated` is the one piece of AUDIO knowledge the Engine cannot work out for itself:
        that this transcript is the front of a sentence and the rest was never recorded. It was
        known here all along — `run()` has written `hit max_s` into the turn extras since the
        cap existed — and went no further, so a note written from a capped recording was
        announced as "Added to your note." with no hint that it stopped mid-word.
        """
        self._show_line("you", heard)
        response = self._engine.ask(heard, truncated=truncated)
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
        # "typed" only. The Engine's own extras are copied across by `_deliver`, which BOTH
        # paths go through — doing it here as well appended them twice, and the turn line read
        # `typed, deadline reminder (5), deadline reminder (5)`. Nine turns in the log to
        # 2026-09-06, every one of them typed, because the spoken path never had the second
        # copy. Cosmetic in the end: the card itself is added once, inside `Engine.ask`.
        t.extras.append("typed")
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
        # **The one place the Engine's extras are copied onto the turn.** Both the spoken and
        # the typed path come through here, which is the whole reason `_deliver` exists; a
        # caller that copies them itself as well gets them twice. See `answer_typed`.
        t.extras.extend(self._engine.last.extras)

        # Cards go up BEFORE he opens his mouth. For a permission gate that ordering is the
        # safety property — the exact command has to be on screen while the question is being
        # asked, not after it has been answered.
        self._show(response)
        # **An empty `speech` means say nothing, and that is a real answer.** Quiz mode uses it
        # for a turn that was LB thinking out loud: he has not committed an answer yet, so
        # there is nothing to mark and nothing to reply, and the right response is to keep
        # listening in silence. Handing "" to the Speaker instead would enter the `speaking`
        # state for zero samples and log "nothing to say" once per mutter.
        if response.speech and response.speech.strip():
            self._say(response.speech, t)
        else:
            t.extras.append("said nothing")

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
    GATE_RETRY_LINE = "I didn't catch that. Please say yes or no clearly."

    # One retry, not a loop. A gate that keeps asking is a gate that eventually gets a yes out
    # of a television, and the safe default is already a decline.
    GATE_ATTEMPTS = 2

    def _spoken_gate_answer(self, t: Timings) -> str:
        """Read a yes or no from voice or a click. **Anything unclear is a no.**

        Two channels, deliberately in this order:

        1. **Voice**, because it is instant and it is how LB actually answers.
        2. **A click** on the HUD, which beats voice because it is unambiguous.

        ## The camera used to be the third, and was removed 2026-08-29

        A thumbs up at the camera was the fallback for a yes Whisper could not hear. It worked,
        and it was still the wrong trade: it cost a subprocess and ~2.2 seconds (D15) on the
        turns that needed rescuing, and on 2026-08-29 at 19:52 it cost **20 seconds** on a turn
        that did not — `gesture read timed out after 20s`, on a machine with no camera
        attached, to approve a launch LB then approved by saying "Yes".

        The rescue it offered is available more cheaply from the retry that is already here.
        Saying "yes" again is faster than any camera path, and a decline is what silence means
        at this gate either way. Two channels that cost nothing beat three where the third
        charges every gate for the benefit of a few.

        Returns:
            The answer as text. "" means nothing readable arrived, and `Engine.ask("")`
            declines the pending action. Never returns "yes" on a guess.
        """
        for attempt in range(1, self.GATE_ATTEMPTS + 1):
            self._bridge.set_state("listening")
            answer = ""

            capture = self._capture()
            if capture is not None and capture.outcome is not Outcome.SILENT:
                got = self._stt.transcribe(capture.audio)
                t.stt_s += got.took_s          # a second capture is a second transcription

                # **THE CHECK THAT MATTERS MOST IN THIS FILE.** On 2026-08-29 at 07:24:10 an
                # OS command ran because "Yes. Yes." came back from 0.96 seconds of voiced
                # audio scattered through 18.08 seconds of room tone. The same transcript, at
                # the same voiced duration, appears twice more in the log on 2026-08-28.
                #
                # `is_yes` was not wrong and needs no change: it reads what words MEAN, and it
                # was handed words nobody said. This is the question nothing was asking — were
                # there any words at all — and it belongs in front of `is_yes`, not inside it.
                #
                # Rejecting leaves `answer` as "", which this loop already handles: ask once
                # more, then decline. **A decline is what silence means
                # here**, so a false reject cannot do anything worse than make LB repeat
                # himself, while a false accept runs a command he never approved.
                credit = credible.assess(got.text, capture.speech_s,
                                         capture.audio.size / SAMPLE_RATE_HZ)
                if credit:
                    answer = got.text
                    LOG.info("gate: heard %r -> %s", answer, is_yes(answer))
                else:
                    LOG.info("gate: ignoring %r — %s", got.text, credit.reason)
                    t.extras.append(f"gate not credible: {credit.reason}")
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

            if attempt < self.GATE_ATTEMPTS:
                # THE line. Say what went wrong and what to do about it, then listen again.
                LOG.info("gate: nothing readable (heard %r) — asking again", answer)
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
