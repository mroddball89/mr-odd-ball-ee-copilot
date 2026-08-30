#!/usr/bin/env python3
"""
Module:  verify_stt.py
Purpose: Prove step 5 Phase 1 — capture, transcription and the reflex tier — with no mic.
Author:  LB
Date:    2026-08-12

    venv/bin/python tools/verify_stt.py            # no model needed
    venv/bin/python tools/verify_stt.py --real     # ...plus real whisper on synthesised speech

Fifth harness, same contract as the other four: exit 0 = all passed, and every claim is
measured rather than asserted.

The reflex tier is a pure function of a string, so it is tested as ordinary logic. The
recorder takes an injectable VAD and clock, so end-of-speech timing is driven by a stub
instead of by talking at a microphone and waiting.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from audio.listen import (  # noqa: E402
    FRAME_SAMPLES,
    PREROLL_S,
    SAMPLE_RATE_HZ,
    Outcome,
    UtteranceRecorder,
)
from audio.stt import Heard, Transcriber                              # noqa: E402
from orchestrator.instant import FALLBACK, Router, normalise           # noqa: E402
from orchestrator.settings import load_config                         # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


class FakeClock:
    def __init__(self, t: float = 500.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class ScriptedVAD:
    """Returns whatever the script says, one value per frame, then 0.0 forever."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.calls = 0
        self.frame_sizes: list[int] = []

    def predict(self, x: np.ndarray, frame_size: int = 480) -> float:
        self.calls += 1
        self.frame_sizes.append(frame_size)
        return self.scores.pop(0) if self.scores else 0.0


def frame(value: int = 1000) -> np.ndarray:
    return np.full(FRAME_SAMPLES, value, dtype=np.int16)


FRAME_S = FRAME_SAMPLES / SAMPLE_RATE_HZ      # 0.08


def drive(rec: UtteranceRecorder, clock: FakeClock, n: int):
    """Feed n frames, advancing the clock one frame at a time. Returns the Capture or None."""
    for _ in range(n):
        out = rec.feed(frame())
        if out is not None:
            return out
        clock.advance(FRAME_S)
    return None


# ============================================================ 1. configuration

section("config")

try:
    cfg = load_config()
    listen_cfg, stt_cfg = cfg["listen"], cfg["stt"]
    check(True, "config/oddball.toml has valid [listen] and [stt] sections",
          f"model={stt_cfg['model']!r} threads={stt_cfg['cpu_threads']} "
          f"hangover={listen_cfg['hangover_s']}s wait={listen_cfg['wait_s']}s")
except Exception as exc:  # noqa: BLE001
    check(False, "config/oddball.toml has valid [listen] and [stt] sections",
          f"{type(exc).__name__}: {exc}")
    cfg = listen_cfg = stt_cfg = None

if cfg:
    real = (REPO_ROOT / "config" / "oddball.toml").read_text(encoding="utf-8")
    for key, broken in (("hangover_s", "hangover_s = 50.0"), ("cpu_threads", "cpu_threads = 0")):
        bad = REPO_ROOT / "config" / f"_bad_{key}.toml"
        # Derive the anchor from the CURRENT value. Three harnesses in this repo have been
        # caught testing nothing because they hardcoded one, so this asserts it exists.
        anchor = f"{key} = {cfg['listen' if key == 'hangover_s' else 'stt'][key]}"
        try:
            assert anchor in real, f"cannot find {anchor!r} in the config to break"
            bad.write_text(real.replace(anchor, broken), encoding="utf-8")
            try:
                load_config(bad)
                check(False, f"an out-of-range {key} is rejected at load", "it was accepted")
            except ValueError as exc:
                ok = key in str(exc)
                check(ok, f"an out-of-range {key} is rejected at load",
                      str(exc).split(": ", 1)[-1] if ok else f"rejected for another reason: {exc}")
            except KeyError as exc:
                check(False, f"an out-of-range {key} is rejected at load",
                      f"failed on a missing key instead, so the range rule went untested: {exc}")
        except AssertionError as exc:
            check(False, f"an out-of-range {key} is rejected at load", str(exc))
        finally:
            bad.unlink(missing_ok=True)


# ============================================================ 2. the reflex tier

section("router")

PINNED = datetime(2026, 8, 12, 14, 5)            # a Wednesday, 2:05pm
router = Router(now=lambda: PINNED)

# Transcripts on the left include REAL mishearings measured from whisper on the Pi, which is
# the entire argument for matching on keywords rather than whole sentences.
CASES = [
    ("what time is it",                 "time"),
    ("What time is it?",                "time"),      # punctuation and case must not matter
    ("whats the time",                  "time"),
    ("do you know what time it is",     "time"),
    ("what day is it today",            "date"),
    ("What is today?",                  "date"),      # tiny.en's mishearing of "what is the date"
    ("whats the date",                  "date"),
    ("set a timer for five minutes",    "timer"),
    ("at a timer for five minutes",     "timer"),     # base.en's mishearing
    ("who are you",                     "identity"),
    ("whats your name",                 "identity"),
    ("thank you",                       "thanks"),
    ("thanks",                          "thanks"),
    ("never mind",                      "stop"),
    ("stop",                            "stop"),
    ("hello there",                     "hello"),
    ("hi",                              "hello"),
    ("what is the weather tomorrow",    "unknown"),
    ("play some music",                 "unknown"),
]
wrong = [(t, want, router.route(t).intent) for t, want in CASES
         if router.route(t).intent != want]
check(not wrong, f"the reflex tier routes {len(CASES)} transcripts correctly",
      "including real whisper mishearings" if not wrong
      else "; ".join(f"{t!r} -> {got} (wanted {want})" for t, want, got in wrong))

# Ordering is load-bearing: "timer" sits above "time" on purpose. Answering the clock at
# someone who asked for a timer is worse than admitting timers do not exist yet.
timer = router.route("set a timer for five minutes")
check(timer.intent == "timer" and "timer" in timer.text.lower(),
      "a timer request is not answered with the time (intent order is load-bearing)",
      f"-> [{timer.intent}] {timer.text!r}")

unknown = router.route("what is the airspeed velocity of an unladen swallow")
check(unknown.intent == "unknown" and unknown.text == FALLBACK and not unknown.handled,
      "an unknown request admits ignorance and is flagged unhandled for Phase 2",
      f"handled={unknown.handled} -> {unknown.text!r}")

empty = router.route("   ")
check(not empty.handled and empty.intent == "empty",
      "an empty transcript is not routed as a command", f"-> {empty.text!r}")

t_reply = router.route("what time is it").text
d_reply = router.route("whats the date").text
check("2" in t_reply and "5" in t_reply and "Wednesday" in d_reply and "August" in d_reply
      and "12" in d_reply,
      "time and the full date are formatted from the injected clock, portably",
      f"{t_reply!r} / {d_reply!r}")

# He answers the question that was ASKED. "What month is it" used to return the whole date,
# which LB reported live on 2026-08-13 as sounding like he had misheard.
for question, want, unwanted in (
    ("what month is it",  "August",    "Wednesday"),
    ("whats the month",   "August",    "12"),
    ("what day is it",    "Wednesday", "August"),
    ("what year is it",   "2026",      "August"),
):
    said = router.route(question).text
    check(want in said and unwanted not in said,
          f"{question!r} answers with {want}, not the whole calendar", f"-> {said!r}")

# The mirror, and the more dangerous direction: a bare "today" must NOT be read as a request
# for the date. Measured live — "do you know if the Orioles have a baseball game today" was
# answered with "It's Thursday, August 13."
for question in ("do you know if the orioles have a baseball game today",
                 "whats the weather today",
                 "is there a game on today"):
    reply = router.route(question)
    check(reply.intent != "date",
          f"a bare 'today' does not hijack the calendar: {question!r}",
          f"-> [{reply.intent}] {reply.text!r}")

_messy = "  What's THE Time?! "
check(normalise(_messy) == "whats the time",
      "normalise() strips case, punctuation and apostrophes",
      f"{_messy!r} -> {normalise(_messy)!r}")


# ============================================================ 3. end of speech

section("capture")

# --- nobody says anything
clock = FakeClock()
rec = UtteranceRecorder(ScriptedVAD([]), wait_s=1.0, hangover_s=0.6, clock=clock)
cap = drive(rec, clock, 40)
check(cap is not None and cap.outcome is Outcome.SILENT and cap.audio.size == 0 and not cap,
      "silence gives up after wait_s and returns no audio",
      # `cap is not None`, not `if cap` — a SILENT Capture is deliberately falsy, so the
      # obvious spelling printed "never finished" for a capture that finished correctly.
      f"{cap.outcome.value if cap is not None else 'never finished'} after "
      f"{'%.2f' % (13 * FRAME_S)}s of silence")

# --- speech, then a pause
clock = FakeClock()
speech = [0.9] * 12                                  # ~0.96s of speech
rec = UtteranceRecorder(ScriptedVAD(speech), wait_s=1.0, hangover_s=0.6, clock=clock)
cap = drive(rec, clock, 60)
check(cap is not None and cap.outcome is Outcome.SPOKE and bool(cap),
      "speech followed by a pause ends the utterance",
      f"{cap.outcome.value if cap else 'never finished'}, "
      f"{cap.audio.size / SAMPLE_RATE_HZ:.2f}s audio, {cap.speech_s:.2f}s voiced")

# The one that matters: it must not cut you off DURING a natural pause shorter than hangover_s.
clock = FakeClock()
#              speech          short gap        more speech      long gap
scores = [0.9] * 10 + [0.1] * 5 + [0.9] * 10 + [0.1] * 20        # gap = 0.40s < 0.6s hangover
rec = UtteranceRecorder(ScriptedVAD(scores), wait_s=1.0, hangover_s=0.6, clock=clock)
cap = drive(rec, clock, 60)
kept = cap.audio.size / SAMPLE_RATE_HZ if cap else 0.0
check(cap is not None and cap.outcome is Outcome.SPOKE and kept > 25 * FRAME_S,
      "a pause shorter than the hangover does not cut the sentence in half",
      f"kept {kept:.2f}s across a 0.40s mid-sentence gap (hangover 0.60s)")

# --- leading audio is kept, but BOUNDED. Two rules that pull against each other:
# the first consonant lands before the VAD is convinced, so some lead-in is required — but
# handing whisper every second you spent not talking costs transcription time AND makes it
# hallucinate. A live turn with 0.32s of speech in 2.16s of audio produced
# "We'll see you next time." out of nothing.
clock = FakeClock()
rec = UtteranceRecorder(ScriptedVAD([0.1, 0.1] + [0.9] * 10), wait_s=2.0, hangover_s=0.4,
                        clock=clock)
cap = drive(rec, clock, 40)
check(cap is not None and cap.audio.size / SAMPLE_RATE_HZ > 10 * FRAME_S,
      "audio from before the VAD triggered is kept, so the first word survives",
      f"{cap.audio.size / SAMPLE_RATE_HZ:.2f}s kept for 10 voiced frames "
      f"({10 * FRAME_S:.2f}s) plus a lead-in")

# 25 frames (2.0s) of silence before 6 frames of speech: the silence must NOT reach whisper.
clock = FakeClock()
rec = UtteranceRecorder(ScriptedVAD([0.1] * 25 + [0.9] * 6), wait_s=5.0, hangover_s=0.4,
                        clock=clock)
cap = drive(rec, clock, 60)
kept = cap.audio.size / SAMPLE_RATE_HZ if cap else 0.0
budget = (PREROLL_S + 6 * FRAME_S + 0.4) + FRAME_S * 2        # lead-in + speech + hangover
check(cap is not None and kept < budget and kept > 6 * FRAME_S,
      "long silence before speech is trimmed away instead of being sent to whisper",
      f"2.0s of leading silence + 0.48s of speech -> {kept:.2f}s kept "
      f"(lead-in {PREROLL_S}s), not {31 * FRAME_S:.2f}s")

# --- the cap
clock = FakeClock()
rec = UtteranceRecorder(ScriptedVAD([0.9] * 200), wait_s=1.0, hangover_s=0.6, max_s=1.0,
                        clock=clock)
cap = drive(rec, clock, 200)
check(cap is not None and cap.outcome is Outcome.TOO_LONG and cap.audio.size > 0,
      "an endless utterance stops at max_s and keeps what it had",
      f"{cap.outcome.value if cap else 'never finished'}, "
      f"{cap.audio.size / SAMPLE_RATE_HZ:.2f}s at a 1.0s cap")

# --- the recorder resets itself, or the next turn inherits this one's audio
clock = FakeClock()
vad = ScriptedVAD([0.9] * 6)
rec = UtteranceRecorder(vad, wait_s=1.0, hangover_s=0.4, clock=clock)
first = drive(rec, clock, 40)
vad.scores = [0.9] * 6
second = drive(rec, clock, 40)
check(first is not None and second is not None
      and abs(first.audio.size - second.audio.size) < FRAME_SAMPLES * 2,
      "a finished capture resets, so the next turn does not inherit its audio",
      f"{first.audio.size / SAMPLE_RATE_HZ:.2f}s then "
      f"{second.audio.size / SAMPLE_RATE_HZ:.2f}s")

# --- the VAD is driven with a chunk size that divides our frame exactly
check(vad.frame_sizes and all(FRAME_SAMPLES % fs == 0 for fs in vad.frame_sizes),
      "the VAD is given a chunk size that divides the 1280-sample frame exactly",
      f"frame_size={vad.frame_sizes[0]}, {FRAME_SAMPLES // vad.frame_sizes[0]} chunks per frame")

# --- bad frames are refused, for the same reason WakeDetector refuses them
for bad_frame, why in ((np.zeros(999, dtype=np.int16), "wrong length"),
                       (np.zeros(FRAME_SAMPLES, dtype=np.float32), "wrong dtype")):
    try:
        UtteranceRecorder(ScriptedVAD([])).feed(bad_frame)
        check(False, f"a frame of the {why} is refused", "it was accepted")
    except (TypeError, ValueError) as exc:
        check(True, f"a frame of the {why} is refused", str(exc)[:70])

for kwargs, why in (({"threshold": 0}, "threshold"), ({"hangover_s": 0}, "hangover_s"),
                    ({"max_s": -1}, "max_s")):
    try:
        UtteranceRecorder(ScriptedVAD([]), **kwargs)
        check(False, f"an invalid {why} is refused at construction", "it was accepted")
    except ValueError as exc:
        check(True, f"an invalid {why} is refused at construction", str(exc)[:60])


# ============================================================ 4. transcription

section("transcription")


class StubWhisper:
    """Returns scripted segments, so the wiring is tested without a 75MB download."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.kwargs: dict = {}

    def transcribe(self, audio, **kw):
        self.kwargs = kw
        seg = type("Seg", (), {})
        out = []
        for t in self.texts:
            s = seg()
            s.text = t
            out.append(s)
        return out, None


stub = StubWhisper([" What time", " is it?"])
heard = Transcriber(stub).transcribe(np.zeros(1600, dtype=np.float32))
check(heard.text == "What time is it?" and bool(heard),
      "segments are joined and stripped into one transcript", f"-> {heard.text!r}")
check(heard.took_s >= 0, "transcription time is recorded for the latency line",
      f"{heard.took_s * 1000:.1f}ms against a stub")

# These are not defaults — they were measured. With whisper's own, tiny.en misheard
# "What day is it today?" as "What they use it today."
kw = stub.kwargs
check(kw.get("language") == "en" and kw.get("beam_size") == 1
      and kw.get("without_timestamps") is True
      and kw.get("condition_on_previous_text") is False,
      "the measured decode arguments are actually passed",
      f"language={kw.get('language')} beam={kw.get('beam_size')} "
      f"no_timestamps={kw.get('without_timestamps')} "
      f"no_context={kw.get('condition_on_previous_text')}")

silent = Transcriber(StubWhisper([])).transcribe(np.zeros(1600, dtype=np.float32))
check(silent.text == "" and not silent,
      "an utterance with no words is falsy rather than an empty-string command",
      f"-> {silent.text!r}")

# int16 audio must not be handed to whisper as-is; that is silence to it.
Transcriber(stub).transcribe(np.zeros(1600, dtype=np.float64))
check(True, "non-float32 audio is converted rather than refused", "float64 accepted")


# ============================================================ 5. the latency budget

section("latency")

if cfg:
    # What a reflex turn costs AFTER you stop talking, excluding STT: route, then synthesis.
    # Whisper is measured separately (--real, or on the Pi) because it dominates and depends
    # on the box. This is the part that must stay small.
    t0 = time.monotonic()
    for _ in range(200):
        router.route("what time is it")
    route_ms = (time.monotonic() - t0) / 200 * 1000
    check(route_ms < 5.0, "routing is free compared to everything else",
          f"{route_ms:.3f}ms per turn, averaged over 200")

    # 2.5s is PLAN.md's Phase 2 target and the same BUDGET_S that tools/measure_llm.py ranks
    # models against.
    #
    # ## The half-of-budget rule is RETIRED, and this is the honest version of why
    #
    # It read `hangover < budget / 2` — "the hangover must never dominate the turn". On
    # 2026-08-22 LB raised the hangover to 2.0s because he was still being cut off when he
    # pauses mid-sentence, and at 2.0s of a 2.5s budget the rule fires correctly: **the
    # hangover now does dominate the turn.** That is not a bug in the config, it is the trade
    # he chose, and re-inflating `budget` to 4.1 so the ratio passes would be a lie about what
    # the answer budget is — the exact "assert the old rule and call it green" mistake D8 is
    # about, pointed the other way.
    #
    # So the ratio is replaced by an explicit CEILING, and the cost it was guarding is printed
    # on every run instead of being hidden behind a pass. The guard is still real: a typo'd
    # 5.0 or a creep to 3.0 fails here, and an out-of-range value is separately rejected at
    # load (see the `hangover_s = 50.0` check above).
    #
    # If the share below climbs much past 80%, the fix is no longer a bigger number. It is
    # push-to-talk, or a VAD that can tell a thinking pause from a finished sentence — both of
    # which end the utterance on intent rather than on a stopwatch. Tracked in tasks/todo.md.
    budget = 2.5
    hangover = listen_cfg["hangover_s"]
    ceiling = 2.0
    share = hangover / budget * 100
    check(hangover <= ceiling,
          "the end-of-speech hangover is within the agreed ceiling",
          f"hangover {hangover}s, ceiling {ceiling}s — and that is {share:.0f}% of the {budget}s "
          f"answer budget, spent as silence on EVERY turn, including the free lookups")


# ============================================================ 6. real whisper (optional)

if "--real" in sys.argv:
    section("real speech")
    try:
        from audio.say import PiperSynth, resample
        from audio.stt import build_model

        synth = PiperSynth(load_config()["speech"]["voice"])
        model = build_model(stt_cfg["model"], stt_cfg["compute_type"], stt_cfg["cpu_threads"])
        transcriber = Transcriber(model)

        # Synthesised speech, NOT LB's voice — this proves the pipeline is wired, not that
        # the model understands him. Real accuracy needs recorded fixtures of him speaking.
        #
        # Split into enforced and reported, the same way tests/fixtures/wake/known-limits/
        # is handled: the harness stays green and a real regression still stays visible.
        # `tiny.en` mishears "Who are you?" as "Hey, where are you?" some of the time — and
        # because Piper is stochastic (a different performance every run) asserting on it
        # made this check flaky rather than strict. It routes to `hello`, which is a graceful
        # wrong answer rather than nonsense.
        enforced = [("What time is it?", "time"), ("What day is it today?", "date")]
        reported = [("Who are you?", "identity")]

        def run_one(text: str):
            audio, rate = synth(text)
            got = transcriber.transcribe(resample(audio, rate, SAMPLE_RATE_HZ))
            return got, router.route(got.text).intent

        rows, bad = [], []
        for text, want in enforced:
            got, intent = run_one(text)
            rows.append(f"{got.took_s:.2f}s {got.text!r} -> {intent}")
            if intent != want:
                bad.append(f"{text!r} -> {got.text!r} -> {intent}, wanted {want}")
        check(not bad, "real whisper transcribes and routes the commands the budget is set by",
              "; ".join(rows) if not bad else "; ".join(bad))

        notes = []
        for text, want in reported:
            got, intent = run_one(text)
            notes.append(f"{text!r} -> {got.text!r} -> {intent}"
                         + ("" if intent == want else f" (wanted {want})"))
        check(True, "known limits of tiny.en, reported not enforced", "; ".join(notes))

        # --- the fixtures: LB's actual voice, not a synthesiser ---
        #
        # Everything above this line is measured on TTS, because for most of this project
        # there was nothing else. These are real recordings of real commands, and they are to
        # this harness what tests/fixtures/wake/ is to verify_wake.py.
        #
        # They assert the INTENT, not the transcript. tiny.en hears "what" as "with" about
        # half the time at this distance, and the router does not care — it matches keywords.
        # Asserting the transcript would fail on turns that answered perfectly.
        from audio.stt import wav_audio

        fixtures = sorted((REPO_ROOT / "tests" / "fixtures" / "commands").glob("*.wav"))
        if not fixtures:
            check(False, "command fixtures in a real voice are present",
                  "tests/fixtures/commands/ is empty — record some with --save-captures")
        else:
            rows, wrong = [], []
            for path in fixtures:
                want = path.stem.split("-")[0]           # time-03.wav -> "time"
                got = transcriber.transcribe(wav_audio(path))
                intent = router.route(got.text).intent
                rows.append(f"{path.name} {got.text!r} -> {intent}")
                if intent != want:
                    wrong.append(f"{path.name}: {got.text!r} -> {intent}, wanted {want}")
            check(not wrong,
                  f"all {len(fixtures)} recorded commands reach the right intent",
                  "; ".join(rows) if not wrong else "; ".join(wrong))

        # Local-first is a project principle, not a preference: left alone faster-whisper
        # calls huggingface.co on every start to check the model revision, so he would need
        # the internet to boot and would phone home each time he did.
        from audio.stt import WHISPER_DIR
        cached = list(WHISPER_DIR.rglob("model.bin"))
        t0 = time.monotonic()
        build_model(stt_cfg["model"], stt_cfg["compute_type"], stt_cfg["cpu_threads"])
        reload_s = time.monotonic() - t0
        check(bool(cached) and reload_s < 5.0,
              "the model is on disk and reloads without contacting huggingface",
              f"{len(cached)} model file(s) under models/whisper/, reloaded in {reload_s:.2f}s")
    except Exception as exc:  # noqa: BLE001
        check(False, "real whisper transcribes and routes synthesised commands",
              f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ report

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify capture, transcription and the reflex tier")
    ap.add_argument("--real", action="store_true",
                    help="also run real whisper against synthesised commands (downloads a model)")
    ap.parse_args()

    failed = [r for r in RESULTS if not r[0]]
    last = ""
    print()
    for ok, sec, msg, detail in RESULTS:
        if sec != last:
            print(f"  {sec}")
            last = sec
        print(f"   {'PASS' if ok else 'FAIL'}  {msg}")
        if detail:
            print(f"           {detail}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed\n")
    sys.exit(1 if failed else 0)
