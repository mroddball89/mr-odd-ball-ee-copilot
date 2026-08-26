#!/usr/bin/env python3
"""
Module:  run_wake.py
Purpose: Build-order steps 3 and 4 — saying his name wakes him, and he answers out loud.
Author:  LB
Date:    2026-08-10

Wires the microphone to the character rig and to his voice. No STT and no language model yet:
he greets you and then listens, and step 5 fills in what happens next.

    python orchestrator/run_wake.py
    ...then open hud/face-preview.html

Flow:  wake word
         -> `startle` gesture                     (instant, while the TTS synthesises)
         -> `speaking` + greeting, mic gated      (his mouth driven by the real audio)
         -> `listening` for listen_s              (where step 5 will capture the request)
         -> back to rest.

Three threads: the event loop owns the rig link and the timers, the microphone runs in its own
because sounddevice's read() blocks, and speech runs in a third because playback blocks too —
speaking on the mic thread would stop frames being pulled and overflow PortAudio's buffer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import random
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np                                             # noqa: E402

from audio.gate import MicGate                                 # noqa: E402
from audio.listen import UtteranceRecorder, build_vad          # noqa: E402
from audio.say import PiperSynth, Speaker                      # noqa: E402
from audio.stt import Transcriber                              # noqa: E402
from audio.stt import build_model as build_whisper             # noqa: E402
from audio.wake import Detection, WakeDetector, build_model, mic_frames  # noqa: E402
from engine import models                                       # noqa: E402
from engine.core import Engine                                 # noqa: E402
from engine.turn import Turn                                   # noqa: E402
from orchestrator.hud_bridge import HudBridge                 # noqa: E402
from orchestrator.settings import load_config                 # noqa: E402

LOG = logging.getLogger("oddball.run")

# How much audio the microphone thread keeps in hand, so a turn can start slightly in the past.
#
# The wake word does not fire the instant you stop saying it — openWakeWord scores a rolling
# window, so the detection lands a few frames later, and by then you are already into the next
# word. Those frames were being fed to the detector and discarded. Measured live: "what time is
# it" arrived as 0.48s of audio and transcribed as "with time." — the answer was right only
# because the router matches keywords.
#
# 0.4s is a compromise. Longer recovers more of the run-up but drags the tail of the wake
# phrase itself into the transcript and costs transcription time; shorter clips the first word
# again.
PREBUFFER_FRAMES = 5        # 5 x 80ms = 0.40s


def _listen_thread(
    detector: WakeDetector,
    device: str,
    on_detect,
    stop: threading.Event,
    gate: MicGate | None = None,
    in_turn: threading.Event | None = None,
    frames_q: "queue.Queue[np.ndarray] | None" = None,
    on_mic_lost=None,
) -> None:
    """Pull frames from the mic and route them, until told to stop.

    Runs off the event loop because sounddevice's read() blocks. Detections cross back via
    HudBridge.broadcast_threadsafe, which is the only thread boundary in the program.

    One microphone, two consumers. Normally frames are scored for the wake word; once a turn
    is under way they are handed to it instead, because `UtteranceRecorder` takes the very
    same 1280-sample frame that `WakeDetector` does. There is no second stream, no second
    device to pick, and no chance of the two disagreeing about the format.

    While the gate is shut, frames are still *pulled* and merely not passed on. Pausing the
    pull instead would back up PortAudio and produce input overflows, which present as a
    flaky microphone rather than as the deliberate mute they would be. This applies to the
    turn as much as to the detector — it is what stops his own answer being captured as your
    next question.
    """
    recent: deque[np.ndarray] = deque(maxlen=PREBUFFER_FRAMES)
    handed_over = False
    try:
        for frame in mic_frames(device):
            if stop.is_set():
                return
            if gate is not None and not gate.is_open():
                continue
            if in_turn is not None and in_turn.is_set():
                if not handed_over:
                    # Hand the turn the moments just BEFORE the wake word fired, or it starts
                    # listening a beat after you already began the question.
                    for held in recent:
                        try:
                            frames_q.put_nowait(held)
                        except queue.Full:
                            break
                    recent.clear()
                    handed_over = True
                try:
                    frames_q.put_nowait(frame)
                except queue.Full:
                    # 200 frames is 16s, well past listen.max_s. If it is full something is
                    # wedged, and dropping the newest is better than blocking the microphone.
                    LOG.warning("utterance buffer full — dropping a frame")
                continue
            handed_over = False
            recent.append(frame)
            det = detector.feed(frame)
            if det is not None:
                LOG.info("wake: %s (%.3f)", det.model, det.score)
                on_detect(det)
    except Exception:
        LOG.exception("microphone stopped")
        # `stop`, not `closing`: this ends the threads that read audio and nothing else. The
        # typed channel is the fallback for exactly this failure and has to outlive it.
        stop.set()
        if on_mic_lost is not None:
            try:
                on_mic_lost()
            except Exception:                                          # noqa: BLE001
                LOG.exception("could not report the microphone loss to the panel")


def _turn_thread(turn, jobs: "queue.Queue[object | None]", on_finished) -> None:
    """Run one full turn per job: capture, transcribe, route, answer.

    `None` is the shutdown signal. The queue holds one item — if a turn is already running,
    a second wake word has nothing sensible to start, and dropping it is honest where
    stacking would queue up answers to questions nobody asked.

    Off the event loop and off the microphone thread because every stage of it blocks:
    capture waits for you to stop talking, transcription is ~1.2s of CPU, and playback runs
    in real time.
    """
    while True:
        job = jobs.get()
        if job is None:
            return
        result = None
        try:
            result = turn.run()
        except Exception:
            LOG.exception("turn failed")
        finally:
            # The Timings are handed on, because whether the conversation stays open depends
            # on what happened in the turn: a dismissal closes it, an answered exchange
            # extends it, and hearing nothing does neither. `None` when the turn raised,
            # which is treated as "heard nothing" — the safe direction.
            on_finished(result)


def _speech_thread(
    speaker: Speaker,
    bridge: HudBridge,
    gate: MicGate,
    tail_s: float,
    jobs: "queue.Queue[str | None]",
    on_finished,
) -> None:
    """Speak whatever is queued, driving his mouth from the audio as it plays.

    Used by `--say` and by `--no-stt`; the full conversational path goes through
    `_turn_thread` instead.

    `None` on the queue is the shutdown signal. The queue holds one item: if he is already
    talking there is nothing sensible to do with a second line, and dropping it is honest
    where stacking would make him monologue.
    """
    while True:
        text = jobs.get()
        if text is None:
            return
        try:
            # `speaking` is entered from on_start, NOT here. Synthesis takes ~0.4s, and
            # announcing the state before it finishes put the rig into its synthetic
            # lip-sync for nearly half a second of silence — his mouth visibly moved before
            # any words came out. He stays in `listening` until sound actually begins, which
            # also reads better: a beat of thought, then the answer.
            #
            # The context manager, rather than paired close/open calls, so an exception
            # raised mid-sentence still reopens the microphone. That failure is total and
            # silent — he would simply never hear anything again.
            with gate.speaking(tail_s):
                secs = speaker.speak(text, on_envelope=bridge.set_mouth,
                                     on_start=lambda: bridge.set_state("speaking"))
            bridge.set_mouth(0.0)
            LOG.info("said %r (%.2fs)", text, secs)
        except Exception:
            LOG.exception("speech failed")
        finally:
            on_finished()


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="wake word -> Mr Odd Ball's face")
    ap.add_argument("--config", default=None, help="path to oddball.toml")
    ap.add_argument("--host", default=None,
                    help="override hud.host. Use 0.0.0.0 to let another machine on the LAN "
                         "watch the face while this box listens — then open the rig with "
                         "?ws=<this-machine-ip>:8765")
    ap.add_argument("--no-mic", action="store_true",
                    help="serve the rig without opening the microphone (bridge smoke test)")
    ap.add_argument("--no-upload", action="store_true",
                    help="do not bind the file-upload endpoint. The paperclip in the chat "
                         "panel stops working; everything else is unaffected.")
    ap.add_argument("--device", default=None,
                    help="input device index or name substring, overriding wake.device. "
                         "Indices come from `python audio/wake.py --list-devices` and are NOT "
                         "stable across reboots, so prefer a name substring for anything lasting.")
    ap.add_argument("--simulate", type=float, metavar="SECONDS", default=None,
                    help="fire a fake wake every SECONDS. Exercises the whole chain except the "
                         "microphone, so the face can be watched with no audio hardware at all "
                         "— and later, on the Pi, without shouting at it.")
    ap.add_argument("--say", metavar="TEXT", default=None,
                    help="say TEXT once, with the rig link live, then exit. The lip-sync demo, "
                         "and the first thing to try on a new box.")
    ap.add_argument("--no-speech", action="store_true",
                    help="step 3 behaviour: wake his face but give him no voice")
    ap.add_argument("--no-stt", action="store_true",
                    help="step 4 behaviour: he greets you on every wake but does not listen "
                         "to the answer. Skips loading whisper, so it starts instantly.")
    ap.add_argument("--no-brain", action="store_true",
                    help="Phase 1 behaviour: skip Tiers 1 and 3 entirely, so an unhandled "
                         "question gets the reflex tier's fallback line. Starts in seconds "
                         "and needs no GGUF — the fastest way to tell a model problem from a "
                         "pipeline problem.")
    ap.add_argument("--no-gate", action="store_true",
                    help="leave the microphone open while he speaks. Only useful for measuring "
                         "what the gate is worth — without it he can hear himself, and any "
                         "greeting containing his name would put him in a loop.")
    ap.add_argument("--save-captures", metavar="DIR", default=None,
                    help="write every captured utterance to DIR as a 16kHz WAV, named after "
                         "what it was transcribed as. A wrong transcript alone cannot tell you "
                         "whether the audio was clipped or simply misheard, and those have "
                         "opposite fixes. Also the start of a command fixture set in a real "
                         "voice, which the accuracy numbers still lack.")
    ap.add_argument("--log", metavar="FILE", default="oddball.log",
                    help="also write the log here, so the per-turn timing line survives the "
                         "terminal being closed. Truncated on each run; \"\" disables it.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Also to a file, because the per-turn timing line is a MEASUREMENT and the terminal is
    # not a place to keep one. Closing the window lost the first real turn he ever took.
    # Truncated per run rather than appended: this is a log of the current session, and an
    # unbounded file on a Pi's SD card is its own problem.
    if args.log:
        try:
            handler = logging.FileHandler(args.log, mode="w", encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s %(message)s"))
            logging.getLogger().addHandler(handler)
            LOG.info("logging to %s", Path(args.log).resolve())
        except OSError as exc:
            LOG.warning("could not open the log file %s: %s", args.log, exc)

    cfg = load_config(args.config) if args.config else load_config()
    wake_cfg, speech_cfg, hud_cfg = cfg["wake"], cfg["speech"], cfg["hud"]
    rest = wake_cfg["resting_state"]

    bridge = HudBridge(args.host or hud_cfg["host"], hud_cfg["port"], rest)
    server = await bridge.start()
    loop = asyncio.get_running_loop()
    await bridge.broadcast({"type": "state", "value": rest})

    # The paperclip's other end. A separate HTTP server on its own port, because the bridge is
    # a WebSocket server and the hook it serves the page from never sees a request BODY —
    # `engine/server.py` makes the whole argument.
    #
    # Wrapped, and the failure is a warning rather than a return: an occupied port must not cost
    # him his voice. The rig's own upload path already reports "is he running?" on a refused
    # connection, so a missing endpoint degrades to a paperclip that says so when pressed —
    # which is the same trade `_quiet()` makes for every other rig call in `engine/turn.py`.
    upload_server = None
    if not args.no_upload:
        try:
            from engine.server import serve as serve_uploads          # noqa: PLC0415

            upload_server = serve_uploads(args.host or hud_cfg["host"], hud_cfg["upload_port"])
        except OSError as exc:
            LOG.warning("no upload endpoint on port %d (%s) — the paperclip will not work",
                        hud_cfg["upload_port"], exc)

        # What is waiting, named at startup.
        #
        # The endpoint accepts a file whether or not anything is listening to the chat, so a
        # `curl` while the service was stopped — or an upload whose "I just uploaded X" line
        # got misrouted — leaves a document in `data/inbox/` that nobody is ever told about.
        # `list_inbox` finds it, but only if LB thinks to ask, and he has no reason to ask
        # about a file he believes was already filed. One line in the log is the cheapest
        # thing that makes a silent backlog visible.
        try:
            from tools.file_manager import inbox_files                # noqa: PLC0415

            waiting = inbox_files()
            if waiting:
                LOG.info("%d file(s) waiting in data/inbox/ — ask him to file %s",
                         len(waiting), ", ".join(p.name for p in waiting[:5]))
        except Exception:                                             # noqa: BLE001
            # A startup convenience is not worth a failed start. Same trade as every rig call
            # in engine/turn.py: the screen is the second channel, and this is a log line.
            LOG.exception("could not read the inbox; carrying on")

    stop = threading.Event()
    # `stop` means "stop pulling frames", and the microphone thread sets it ITSELF when the
    # device goes away — which is right for everything that reads audio and wrong for the one
    # thread that does not.
    #
    # Measured on the Pi 2026-08-24: the C270's microphone stopped enumerating, `mic_frames`
    # raised `no such input device 'C270'` 0.08s after start, and `_listen_thread` set `stop`.
    # The typed channel looped on `while not stop.is_set()` and therefore exited before it had
    # drained a single message. **The one channel built to survive a dead microphone was being
    # killed by the dead microphone** — silently, with the WebSocket still up and the panel
    # still accepting text, which is exactly "typing doesn't always trigger a response".
    #
    # So the two meanings are now two events. This one is only ever set by the shutdown path.
    closing = threading.Event()
    idle_handle: asyncio.TimerHandle | None = None

    # Conversation mode. Monotonic deadline; 0 means no conversation is open. Written by the
    # turn thread and read by the event loop, which is safe for a plain float — every write is
    # a whole assignment and neither side needs a consistent pair of values.
    conversation_s = wake_cfg["conversation_s"]
    conversation_until = 0.0

    def rearm() -> None:
        """Start (or restart) the drift back to rest. Event-loop thread only."""
        nonlocal idle_handle
        if idle_handle is not None:
            idle_handle.cancel()              # a second wake extends the window, not stacks it
        delay = wake_cfg["listen_s"]
        # While a conversation is open his FACE has to stay awake too, or the visible state
        # and the actual state disagree — he would look asleep while still recording, which is
        # the single most misleading thing this rig can do. D41's argument is that his face
        # *is* the interface, so it has to be telling the truth about the microphone.
        remaining = conversation_until - time.monotonic()
        if remaining > delay:
            delay = remaining
        idle_handle = loop.call_later(delay, lambda: bridge.set_state(rest))

    # ---- his voice
    speaker: Speaker | None = None
    jobs: queue.Queue[str | None] = queue.Queue(maxsize=1)
    gate = MicGate()
    speech_thread = None

    if not args.no_speech:
        speaker = Speaker(
            synth=PiperSynth(speech_cfg["voice"]),
            device=speech_cfg["device"],
            volume=speech_cfg["volume"],
            extra_latency_ms=speech_cfg["output_latency_ms"],
            prime_ms=speech_cfg["prime_ms"],
        )

        def on_finished() -> None:
            """Speech ended: he goes back to waiting for the request he just asked for."""
            bridge.set_state("listening")
            loop.call_soon_threadsafe(rearm)

        speech_thread = threading.Thread(
            target=_speech_thread,
            args=(speaker, bridge, gate, speech_cfg["gate_tail_s"], jobs, on_finished),
            name="speech",
            daemon=True,
        )
        speech_thread.start()

    # ---- his ears: capture, transcription and the reflex tier (step 5, Phase 1)
    #
    # `in_turn` switches the microphone thread from scoring the wake word to handing frames
    # to the turn. `frames_q` holds 200 of them — 16s, well past listen.max_s.
    in_turn = threading.Event()
    frames_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
    turn_jobs: queue.Queue[object | None] = queue.Queue(maxsize=1)
    turn_thread = None
    detector: WakeDetector | None = None

    # A turn needs a real microphone: it has to hear the question. Without one, --simulate and
    # --no-mic would set `in_turn` and then block forever on a frame that never arrives, which
    # would look like him freezing on the wake word rather than like a mode that cannot apply.
    full_turn = (speaker is not None and not args.no_stt and args.say is None
                 and not args.no_mic and not args.simulate)
    # ---- his brain: the EE Copilot engine
    #
    # There is no Tier 1 to start and no llama-server to wait for. `router.py` decides who
    # answers and every agent is a network call, so the whole of the old startup block —
    # loading a GGUF, pre-warming its KV cache, checking two tiers were up — is one
    # constructor now.
    #
    # What that cost is written down in docs/DECISIONS.md D1 and D3: the local model was the
    # only part of this system with no quota attached, and dropping it put every joke on the
    # 20-requests-a-day free tier.
    engine = Engine()
    LOG.info("engine up: router=%s agents=%s", models.ROUTER_MODEL, models.AGENT_MODEL)

    if full_turn:
        listen_cfg, stt_cfg = cfg["listen"], cfg["stt"]

        def next_frame():
            """Block until the microphone thread hands over the next frame, or we stop."""
            while not stop.is_set():
                try:
                    return frames_q.get(timeout=0.2)
                except queue.Empty:
                    continue          # the gate is shut while he talks; that is not an end
            return None

        turn = Turn(
            recorder=UtteranceRecorder(
                vad=build_vad(),
                threshold=listen_cfg["threshold"],
                wait_s=listen_cfg["wait_s"],
                hangover_s=listen_cfg["hangover_s"],
                max_s=listen_cfg["max_s"],
            ),
            transcriber=Transcriber(build_whisper(
                stt_cfg["model"], stt_cfg["compute_type"], stt_cfg["cpu_threads"])),
            engine=engine,
            speaker=speaker,
            bridge=bridge,
            gate=gate,
            frames=next_frame,
            greeting=speech_cfg["greeting"],
            gate_tail_s=speech_cfg["gate_tail_s"],
            save_dir=Path(args.save_captures) if args.save_captures else None,
            stall_phrase=cfg.get("brain", {}).get("stall_phrase", ""),
        )

        def turn_finished(result=None) -> None:
            """Close the turn, and decide whether the conversation stays open.

            Args:
                result: the turn's `Timings`, or None if it raised.

            Three outcomes, and the ordering between them is the whole feature:

            **Dismissed** — `intent == "sleep"`. He goes to rest immediately, whatever the
            window says. LB asked for a way to end it; a way that has to wait 25 seconds is
            not one.

            **Answered something** — the window is pushed out, so a conversation lasts as long
            as it lasts rather than expiring mid-thought.

            **Heard nothing** — the window is NOT extended. This is the guard that matters: a
            false wake captures silence, and without this rule one of them would hold the
            microphone open indefinitely by re-triggering itself. D44 is the whole reason to
            be careful here.
            """
            nonlocal conversation_until

            dismissed = result is not None and result.intent == "sleep"
            answered = result is not None and bool(result.heard.strip())
            now = time.monotonic()

            if answered and not dismissed and conversation_s > 0:
                conversation_until = now + conversation_s

            # Stale audio must never open the next exchange, whether that exchange comes from
            # a wake word or from the conversation staying open.
            while True:
                try:
                    frames_q.get_nowait()
                except queue.Empty:
                    break

            if not dismissed and conversation_until > now:
                # Straight into the next exchange, no wake word. `in_turn` stays SET, so there
                # is never a window where the wake detector is fed conversation audio and
                # could fire on it.
                LOG.info("conversation open for %.1fs more — listening without the wake word",
                         conversation_until - now)
                bridge.set_state("listening")
                loop.call_soon_threadsafe(rearm)
                try:
                    turn_jobs.put_nowait(object())
                    return
                except queue.Full:
                    LOG.debug("a turn is already queued; closing the conversation instead")

            if dismissed:
                LOG.info("dismissed — going back to sleep")
            conversation_until = 0.0
            in_turn.clear()
            if detector is not None:
                detector.reset()              # it heard nothing during the turn; do not guess
            bridge.set_state(rest)

        turn_thread = threading.Thread(
            target=_turn_thread, args=(turn, turn_jobs, turn_finished),
            name="turn", daemon=True,
        )
        turn_thread.start()

        # ---- the typed channel
        #
        # This was built and not wired: hud_bridge collected `{"type":"text"}` on its inbound
        # queue and nothing drained it outside a permission gate, so typing into the panel did
        # exactly nothing. LB typed two lines into it and watched them vanish.
        #
        # It matters more than a convenience. Measured on the Pi 2026-08-19, his wake
        # utterances peaked 0.17-0.28 against a 0.76 threshold and mostly did not fire, and the
        # transcripts that did get through were nonsense. Typing is the channel that works when
        # the microphone does not, so it has to be able to do everything the voice can —
        # including the two things that are not questions: waking him, and sending him to bed.
        def _typed_thread() -> None:
            from orchestrator.instant import is_sleep, is_wake      # noqa: PLC0415

            nonlocal conversation_until

            def hand_back(batch: list[dict]) -> None:
                """Return a drained batch to the queue for whoever actually owns it.

                Never raises, and that is the point. `put_nowait` straight onto a full queue was
                doing exactly that: `queue.Full` had nothing around it to catch it, so it
                escaped the message loop AND the while loop and ended this thread. After which
                typing did nothing at all — not intermittently, permanently, for the rest of the
                session — with nothing in the log to say the channel had gone.
                """
                for held in batch:
                    try:
                        bridge.inbound.put_nowait(held)
                    except queue.Full:
                        LOG.warning("inbound full while handing a message back — dropped %r",
                                    str(held.get("value"))[:40])

            # `closing`, NOT `stop` — see where `closing` is defined. A microphone that has
            # gone away is the reason to keep this loop running, not a reason to end it.
            while not closing.is_set():
                try:
                    messages = bridge.drain_inbound()
                    if not messages:
                        time.sleep(0.2)
                        continue

                    # A turn already running owns this queue — `_wait_for_typed_answer` is
                    # draining it for a permission gate, and BOTH an `approve` click and a typed
                    # yes/no belong to that gate rather than to this loop. So the whole batch
                    # goes back untouched.
                    #
                    # This test used to sit below the `type != "text"` filter, which dropped
                    # `approve` on the floor before it could ever be handed back. The gate and
                    # this loop both drain every ~0.2s, so whenever this loop won the race the
                    # click vanished and the gate ran to its timeout — and a timed-out gate
                    # declines. That is an Approve button that silently does nothing, some of
                    # the time, on the path that runs shell commands.
                    if in_turn.is_set():
                        hand_back(messages)
                        # A typed line must not have to wait out the conversation window.
                        # `in_turn` stays SET for the whole of it — `turn_finished` re-arms a
                        # listening pass every `listen.wait_s` of silence rather than clearing
                        # it — so without this, a question typed just after a spoken one bounced
                        # between here and the queue for up to `conversation_s` (25s) before it
                        # was looked at. Closing the window makes the pass in flight the last
                        # one, which bounds the wait to a single capture.
                        #
                        # Typing IS the decision to stop talking, so ending the voice window on
                        # it is the honest reading and not just an expedient one. It does mean a
                        # gate answered by typing leaves him at rest rather than in conversation.
                        if any(m.get("type") == "text" for m in messages):
                            conversation_until = 0.0
                        time.sleep(0.2)
                        continue

                    for i, msg in enumerate(messages):
                        # Re-checked per message, not only per batch: the wake word can fire
                        # between two messages of one drain, and the rest of the batch then
                        # belongs to that turn.
                        if in_turn.is_set():
                            hand_back(messages[i:])
                            break
                        if msg.get("type") != "text":
                            continue              # an `approve` with no gate open is nothing
                        text = str(msg.get("value", "")).strip()
                        if not text:
                            continue

                        if is_wake(text):
                            LOG.info("typed wake: %r", text)
                            bridge.say_line("you", text)
                            bridge.play_gesture("startle")
                            bridge.set_state("listening")
                            # Open the conversation window, so typing the wake phrase leaves him
                            # awake and listening exactly as the spoken one does.
                            if conversation_s > 0:
                                conversation_until = time.monotonic() + conversation_s
                                loop.call_soon_threadsafe(rearm)
                            continue

                        if is_sleep(text):
                            LOG.info("typed dismissal: %r", text)
                            bridge.say_line("you", text)
                            conversation_until = 0.0
                            if detector is not None:
                                detector.reset()
                            bridge.set_state(rest)
                            continue

                        # Anything else is a question, and it is answered whether or not he is
                        # awake. Typing is already deliberate — making it require a wake phrase
                        # first would be ceremony, not safety.
                        LOG.info("typed question: %r", text)
                        try:
                            in_turn.set()         # claim the gate drain for this turn
                            turn.answer_typed(text)
                        except Exception:         # noqa: BLE001
                            LOG.exception("typed turn failed")
                        finally:
                            in_turn.clear()
                            bridge.set_state(rest if conversation_until <= time.monotonic()
                                             else "listening")
                except Exception:                 # noqa: BLE001
                    # This loop is the channel that works when the microphone does not, so it
                    # does not get to die of one bad message. Log what happened and take the
                    # next one — a thread that exits here takes typing with it and says nothing.
                    LOG.exception("typed channel raised — carrying on")
                    time.sleep(0.2)

        threading.Thread(target=_typed_thread, name="typed", daemon=True).start()

    def on_detect(_det) -> None:
        """Called from the audio thread."""
        # Startle first and unconditionally: whatever follows takes a moment, and a face that
        # reacts instantly is what makes that read as a pause for breath rather than a lag.
        bridge.play_gesture("startle")
        bridge.set_state("listening")

        if full_turn:
            try:
                turn_jobs.put_nowait(object())
            except queue.Full:
                LOG.debug("a turn is already running — ignoring this wake")
                return
            in_turn.set()                     # the mic thread now feeds the turn, not the wake
            return

        # Step 3/4 behaviour: no ears, so fall back to the timer and the unconditional greeting.
        loop.call_soon_threadsafe(rearm)
        if speaker is not None:
            try:
                jobs.put_nowait(random.choice(speech_cfg["greeting"]))
            except queue.Full:
                LOG.debug("already speaking — dropping this greeting")

    thread = None
    if args.say is not None:
        if speaker is None:
            LOG.error("--say and --no-speech contradict each other")
            return 2
        LOG.info("open hud/face-preview.html now if you want to watch his mouth — 3s")
        await asyncio.sleep(3.0)
        jobs.put(args.say)
        jobs.put(None)
        speech_thread.join()
        await asyncio.sleep(0.2)          # let the last mouth message reach the rig
        await bridge.broadcast({"type": "state", "value": rest})
        server.close()
        if upload_server is not None:
            upload_server.shutdown()
            upload_server.server_close()
        return 0

    if args.simulate:
        LOG.info("--simulate: firing a fake wake every %.1fs, microphone not opened",
                 args.simulate)

        async def fake_wakes() -> None:
            while True:
                await asyncio.sleep(args.simulate)
                LOG.info("wake: simulated")
                on_detect(Detection(model="simulated", score=1.0, at_s=0.0))

        asyncio.create_task(fake_wakes())
    elif args.no_mic:
        LOG.info("--no-mic: serving the rig only, nothing is listening")
    else:
        detector = WakeDetector(                        # noqa: F841 — closed over by turn_finished
            model=build_model(wake_cfg["model"], wake_cfg["framework"]),
            threshold=wake_cfg["threshold"],
            refractory_s=wake_cfg["refractory_s"],
        )
        # Flush the model when the mic reopens: its rolling buffer still holds the tail of
        # his own sentence, which would otherwise score after the gate had already shut for it.
        gate.on_open = detector.reset
        device = args.device if args.device is not None else wake_cfg["device"]
        if device not in ("", None):
            try:
                device = int(device)
            except ValueError:
                pass                        # a name substring; sounddevice resolves it
        if args.no_gate:
            LOG.warning("--no-gate: the microphone stays open while he speaks. He can hear "
                        "himself; this is for measurement only.")
        def mic_lost() -> None:
            """Say on the panel that the ears are gone, and that typing still reaches him.

            A microphone that vanishes is invisible from the chat panel: the WebSocket stays up,
            the box still takes text, his face still animates, and he simply never answers
            anything you say. Measured 2026-08-24 — the C270 stopped enumerating and the only
            trace was a traceback in a log file nobody was tailing.

            This is the shape of failure this project keeps rediscovering; `turn.py`'s
            GATE_RETRY_LINE exists for the same reason, and its note is the rule: a silent
            failure is indistinguishable from a crash. Three lines end it.
            """
            bridge.set_state(rest)
            bridge.say_line("oddball", "My microphone has gone away, so I cannot hear you. "
                                       "Type to me instead — everything works that way.")

        thread = threading.Thread(
            target=_listen_thread,
            args=(detector, device, on_detect, stop, None if args.no_gate else gate,
                  in_turn if full_turn else None, frames_q if full_turn else None, mic_lost),
            name="mic",
            daemon=True,
        )
        thread.start()
        LOG.info("say the wake word — %r at threshold %s",
                 wake_cfg["model"], wake_cfg["threshold"])
        if full_turn:
            LOG.info("...then just keep talking. He only greets you if you go quiet.")

    LOG.info("open hud/face-preview.html to watch him. ctrl-c to stop.")

    # SIGTERM has to reach the `finally` below, or llama-server is orphaned: it is a child
    # process holding 1.5GB and port 8080, and Python does not run cleanup handlers for a
    # signal it was never told about. Measured on the Pi 2026-08-13 — `pkill -f run_wake`
    # left a server running, and the next start silently attached to it.
    #
    # ctrl-c already worked (SIGINT becomes KeyboardInterrupt); this is for `pkill`, systemd,
    # and anything else that stops a service the normal way.
    shutdown = asyncio.Event()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown.set)
    except NotImplementedError:
        # Windows has no add_signal_handler. ctrl-c still raises KeyboardInterrupt there, and
        # the Pi is where the child process actually matters.
        pass

    try:
        await shutdown.wait()
        LOG.info("shutting down.")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Both, and only here. `stop` ends the audio threads; `closing` ends the typed channel,
        # which has no other reason to stop and must outlive a microphone failure.
        stop.set()
        closing.set()
        server.close()
        if upload_server is not None:
            # `shutdown()` blocks until serve_forever has returned, which is why the server was
            # given `daemon_threads = True`: without it an upload still in flight would hold
            # this open, and `systemctl restart oddball` would hang for as long as a 60 MB file
            # takes to arrive.
            upload_server.shutdown()
            upload_server.server_close()
        for worker, q in ((speech_thread, jobs), (turn_thread, turn_jobs)):
            if worker is None:
                continue
            try:
                q.put_nowait(None)
            except queue.Full:
                pass                        # mid-turn; the daemon thread dies with us
            worker.join(timeout=2.0)
        if thread is not None:
            thread.join(timeout=1.0)
        # Nothing to tear down where Tier 1 used to be: the engine owns no child process, only
        # network calls. The llama-server shutdown that lived here went with it.
        LOG.info("stopped.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
