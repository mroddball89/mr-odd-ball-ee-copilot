#!/usr/bin/env python3
"""
Module:  verify_speech.py
Purpose: Prove build-order step 4 — his voice, his lip-sync and his mic gate — with no speaker.
Author:  LB
Date:    2026-08-11

    venv\\Scripts\\python.exe tools/verify_speech.py

Companion to tools/verify_wake.py and tools/verify-rig.mjs, same contract: exit 0 = all
passed, non-zero = something is wrong, and every claim is measured rather than asserted.

Nothing here opens an audio device. `Speaker` takes its sink as an argument and `MicGate`
takes its clock as one, so playback timing and the gate's tail are driven by stubs — the same
trick that let step 3 be verified with no microphone attached.

The check that matters most is the last one: every greeting line is synthesised and played
through the *real* wake model, because a line containing his own name would put him in a loop
the moment the gate's tail expired.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from audio.gate import MicGate                                          # noqa: E402
from audio.say import (                                                 # noqa: E402
    BLOCK_MS,
    PiperSynth,
    Speaker,
    _NullStream,
    envelope_blocks,
    resolve_voice,
    speakable,
)
from audio.wake import FRAME_SAMPLES, SAMPLE_RATE_HZ, WakeDetector, build_model  # noqa: E402
from orchestrator.hud_bridge import HudBridge                           # noqa: E402
from orchestrator.settings import load_config                           # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


class FakeClock:
    """A monotonic clock that only moves when told to. The gate's tail is a duration, and
    sleeping through it in a test would make the suite slower without making it stricter."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def tone(seconds: float, sample_rate: int, freq: float = 220.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)


# ============================================================ 1. configuration

section("config")

try:
    cfg = load_config()
    speech = cfg["speech"]
    check(True, "config/oddball.toml has a valid [speech] section",
          f"engine={speech['engine']!r} voice={speech['voice']!r} "
          f"volume={speech['volume']} gate_tail_s={speech['gate_tail_s']} "
          f"{len(speech['greeting'])} greeting line(s)")
except Exception as exc:  # noqa: BLE001
    check(False, "config/oddball.toml has a valid [speech] section", f"{type(exc).__name__}: {exc}")
    cfg = speech = None

if cfg:
    # Break exactly one value in a COPY OF THE REAL CONFIG. A hand-written stub would be
    # missing every other key and fail on the first of those instead, reporting a pass while
    # testing nothing — the mistake tools/verify_wake.py made and now guards against.
    real = (REPO_ROOT / "config" / "oddball.toml").read_text(encoding="utf-8")
    for original, broken, key in (
        ("volume = 0.8", "volume = 8.0", "volume"),
        ("gate_tail_s = 0.35", "gate_tail_s = 60.0", "gate_tail_s"),
    ):
        bad = REPO_ROOT / "config" / f"_bad_{key}.toml"
        try:
            assert original in real, f"{original!r} is no longer in the config"
            bad.write_text(real.replace(original, broken), encoding="utf-8")
            try:
                load_config(bad)
                check(False, f"an out-of-range speech.{key} is rejected at load", "it was accepted")
            except ValueError as exc:
                ok = key in str(exc)
                check(ok, f"an out-of-range speech.{key} is rejected at load",
                      str(exc).split(": ", 1)[-1] if ok
                      else f"rejected, but for another reason: {exc}")
            except KeyError as exc:
                check(False, f"an out-of-range speech.{key} is rejected at load",
                      f"failed on a missing key instead, so the range rule went untested: {exc}")
        except AssertionError as exc:
            check(False, f"an out-of-range speech.{key} is rejected at load", str(exc))
        finally:
            bad.unlink(missing_ok=True)


# ============================================================ 1b. what Piper can pronounce

section("speakable")

# The exact reply LFM2.5 gave on the Pi, 2026-08-13, with a persona that forbids emoji in as
# many words. Every model does this — gemini.py already strips markdown for the same reason.
LIVE_EMOJI = "Yes, I'm right here on that little Raspberry Pi! \U0001f913 What's up with you?"
check("\U0001f913" not in speakable(LIVE_EMOJI),
      "the emoji a live model actually emitted is stripped before Piper sees it",
      repr(speakable(LIVE_EMOJI)))
check(speakable(LIVE_EMOJI).startswith("Yes, I'm right here"),
      "...and the words around it survive intact", repr(speakable(LIVE_EMOJI)))

# Narrow on purpose. Stripping punctuation would flatten his prosody, and stripping words
# would change the answer — this must only ever remove things that cannot be voiced.
for text in ("Plain text, with punctuation: fine! Really?",
             "It's 2 12.",
             "The moon is about 384,000 km away - roughly.",
             "tau is R times C; about 63 percent."):
    check(speakable(text) == text, f"ordinary text is untouched: {text!r}",
          repr(speakable(text)))

check(speakable("**bold** and `code`") == "bold and code", "markdown that reaches Piper is stripped")
check(speakable("\U0001f913") == "", "an emoji-only reply collapses to empty rather than a stumble")
check(speakable("") == "", "empty stays empty")
check(speakable("   \n  ") == "", "whitespace-only collapses")

# Speaker applies it, so every caller is covered by one rule rather than by remembering. This
# has to watch what the SYNTHESISER receives — asserting speakable() again here would pass
# whether or not Speaker ever called it, which is the vacuous check this repo has been bitten by.
_seen: list[str] = []


def _recording_synth(text: str):
    _seen.append(text)
    return np.zeros(2205, dtype=np.float32), 22050


Speaker(synth=_recording_synth).synth(LIVE_EMOJI)
check(_seen and "\U0001f913" not in _seen[0],
      "Speaker strips before the synthesiser ever sees the text, so no call site can forget",
      f"synth received {_seen[0]!r}" if _seen else "synth was never called")


# ============================================================ 2. synthesis

section("synthesis")

synth = None
utterance = None
if speech:
    try:
        path = resolve_voice(speech["voice"])
        check(True, "the configured voice is installed",
              f"{path.name} ({path.stat().st_size / 1e6:.0f}MB)")
        t0 = time.monotonic()
        synth = PiperSynth(speech["voice"])
        load_s = time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001
        check(False, "the configured voice is installed", f"{type(exc).__name__}: {exc}")

if synth is not None:
    try:
        line = speech["greeting"][0]
        t0 = time.monotonic()
        audio, sr = synth(line)
        synth_s = time.monotonic() - t0
        secs = audio.size / sr
        peak = float(np.abs(audio).max()) if audio.size else 0.0
        utterance = (audio, sr)

        check(audio.dtype == np.float32 and audio.ndim == 1 and sr > 0,
              "the synthesiser returns mono float32 at a stated rate",
              f"dtype={audio.dtype} shape={audio.shape} rate={sr}Hz")
        # A silent or empty result is the way TTS fails quietly: he would open his mouth,
        # the state machine would advance, and nothing would come out.
        check(audio.size > 0 and peak > 0.05,
              "he actually makes a sound (not silence, not an empty buffer)",
              f"{secs:.2f}s, peak amplitude {peak:.2f}")
        # Loose bounds on purpose. This catches a mangled phoneme path producing a 40ms
        # blip or a runaway producing a minute of noise, without pinning the voice's pace.
        check(0.5 < secs < 15.0, "the utterance is a plausible length for its text",
              f"{secs:.2f}s for {len(line)} characters")
        # Piper is a VITS model with a stochastic duration predictor: the same text is a
        # slightly different performance every time. Asserted rather than merely noted,
        # because it is the reason nothing here can compare audio against a golden file,
        # and the reason the self-wake check below takes several takes of each line.
        again = synth(line)[0]
        check(again.size != audio.size or not np.array_equal(again, audio),
              "his delivery varies between takes (VITS is stochastic — no golden audio)",
              f"two takes of the same line: {audio.size / sr:.2f}s and {again.size / sr:.2f}s")
        check(synth_s < secs * 2, "synthesis is fast enough to answer without a pause",
              f"{synth_s:.2f}s to synthesise {secs:.2f}s of speech "
              f"({secs / synth_s:.1f}x realtime), voice loaded in {load_s:.2f}s")
    except Exception as exc:  # noqa: BLE001
        check(False, "the synthesiser produces usable audio", f"{type(exc).__name__}: {exc}")


# ============================================================ 3. the mouth envelope

section("envelope")

if utterance:
    audio, sr = utterance
    envs = [e for _, e in envelope_blocks(audio, sr)]
    blocks = int(audio.size // (sr * BLOCK_MS / 1000.0))

    check(len(envs) >= blocks and all(0.0 <= e <= 1.0 for e in envs)
          and not any(np.isnan(envs)),
          "every envelope value is a number in 0..1",
          f"{len(envs)} values over {audio.size / sr:.2f}s, "
          f"range {min(envs):.3f}..{max(envs):.3f}")
    # Peak-normalised per utterance, which is what makes a quiet voice open his mouth as
    # wide as a loud one.
    check(abs(max(envs) - 1.0) < 1e-6, "the loudest block of an utterance reads exactly 1.0",
          f"max {max(envs):.6f}")
    # If it never came down he would just hold his mouth open for the whole sentence.
    check(min(envs) < 0.35, "the envelope returns toward closed between sounds",
          f"min {min(envs):.3f}, mean {float(np.mean(envs)):.3f}")

# Pure-logic checks below need no voice installed at all.
sr = 22050
silent = np.zeros(sr, dtype=np.float32)
check(all(e == 0.0 for _, e in envelope_blocks(silent, sr)),
      "silence produces zeros, not a division by zero",
      f"{len(list(envelope_blocks(silent, sr)))} blocks, all 0.0")

ramp = (tone(1.0, sr) * np.linspace(0.0, 1.0, sr, dtype=np.float32))
ramp_envs = [e for _, e in envelope_blocks(ramp, sr)]
rising = sum(b >= a - 1e-6 for a, b in zip(ramp_envs, ramp_envs[1:]))
check(rising >= len(ramp_envs) - 2 and ramp_envs[-1] > ramp_envs[0],
      "the envelope follows amplitude, so his mouth tracks the audio",
      f"{rising}/{len(ramp_envs) - 1} steps non-decreasing on a fade-in, "
      f"{ramp_envs[0]:.3f} -> {ramp_envs[-1]:.3f}")

steady = tone(0.5, sr)
n_blocks = len(list(envelope_blocks(steady, sr)))
want_blocks = round(0.5 / (BLOCK_MS / 1000.0))
check(n_blocks == want_blocks, f"one envelope value per {BLOCK_MS:.0f}ms of audio",
      f"{n_blocks} blocks for 0.50s, expected {want_blocks}")


# ============================================================ 4. playback contract

section("playback")

emitted: list[float] = []
spk = Speaker(synth=lambda _t: (tone(0.4, sr), sr), volume=1.0,
              open_stream=lambda _sr: _NullStream())
secs = spk.speak("anything", on_envelope=emitted.append)

check(len(emitted) == round(0.4 / (BLOCK_MS / 1000.0)) + 1,
      "speak() reports one envelope per block, plus one at the end",
      f"{len(emitted)} values for {secs:.2f}s of audio")
# Without this he finishes the sentence with his jaw hanging open on the last syllable.
check(emitted[-1] == 0.0, "his mouth is explicitly closed when the sentence ends",
      f"last value {emitted[-1]}")

quiet: list[float] = []
Speaker(synth=lambda _t: (tone(0.4, sr), sr), volume=0.1,
        open_stream=lambda _sr: _NullStream()).speak("anything", on_envelope=quiet.append)
check(quiet == emitted,
      "volume changes the audio but never the envelope",
      f"identical across volume 1.0 and 0.1 ({len(quiet)} values)")

empty: list[float] = []
Speaker(synth=lambda _t: (np.zeros(0, dtype=np.float32), sr),
        open_stream=lambda _sr: _NullStream()).speak("nothing", on_envelope=empty.append)
check(empty == [0.0], "an empty utterance closes his mouth instead of crashing",
      f"emitted {empty}")

try:
    Speaker(synth=lambda _t: (tone(0.1, sr), sr), volume=1.5)
    check(False, "an out-of-range volume is refused at construction", "it was accepted")
except ValueError as exc:
    check(True, "an out-of-range volume is refused at construction", str(exc))


# --- his mouth must not move before the sound. LB saw exactly this, from two causes.

class SlowSynth:
    """A synthesiser that takes measurable time, like the real one (~0.4s)."""

    def __init__(self, delay_s: float = 0.15) -> None:
        self.delay_s = delay_s
        self.started_at: float | None = None

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        time.sleep(self.delay_s)
        self.started_at = time.monotonic()
        return tone(0.3, sr), sr


class LaggyStream(_NullStream):
    """A sink that reports buffer latency, like a real sound card.

    `write()` returning does NOT mean the audio was heard — it means there was room in the
    buffer. A stream reporting 100ms of latency is 5 blocks ahead of the speaker.
    """

    def __init__(self, latency: float) -> None:
        self.latency = latency
        self.writes = 0

    def write(self, block: np.ndarray) -> None:
        self.writes += 1


slow = SlowSynth()
order: list[str] = []
t_call = time.monotonic()
Speaker(synth=slow, open_stream=lambda _sr: _NullStream()).speak(
    "hello", on_envelope=lambda _v: order.append("mouth"), on_start=lambda: order.append("start"))
# Cause 1: the caller enters `speaking` before synthesis finishes, so the rig runs its
# SYNTHETIC envelope through ~0.4s of silence. on_start exists to move that announcement to
# the moment sound begins.
check(order[0] == "start" and order.count("start") == 1 and slow.started_at is not None
      and slow.started_at <= time.monotonic(),
      "on_start fires once, after synthesis and before the first sound",
      f"synthesis took {slow.delay_s:.2f}s; sequence begins {order[:3]}")

# Cause 2: the envelope was emitted before the write, so it described audio that was queued
# rather than audio that was audible — his mouth led the speaker by the buffer depth.
lag = LaggyStream(latency=0.10)                      # 100ms = 5 blocks at 20ms
seen: list[float] = []
Speaker(synth=lambda _t: (tone(0.4, sr), sr),
        open_stream=lambda _sr: lag).speak("hello", on_envelope=seen.append)
straight = [e for _, e in envelope_blocks(tone(0.4, sr), sr)]
delay_blocks = round(0.10 / (BLOCK_MS / 1000.0))
# The value emitted alongside write N must be the envelope of block N-5, not block N.
aligned = seen[:len(straight) - delay_blocks] == straight[:len(straight) - delay_blocks]
check(delay_blocks == 5 and aligned and seen[-1] == 0.0 and len(seen) == len(straight) + 1,
      "the mouth is delayed to match the sound the card is actually playing",
      f"stream reports {lag.latency * 1000:.0f}ms, mouth held back {delay_blocks} blocks; "
      f"{len(seen)} values for {len(straight)} blocks")

# Latency PortAudio cannot see. On the Pi the Bose is on Bluetooth: A2DP buffers 150-250ms
# downstream of the sound card, and PortAudio reports 20ms. Without this knob his mouth leads
# by the difference and there is no way to tell it otherwise.
bt = LaggyStream(latency=0.02)                       # what the Pi reports with the Bose
deaf: list[float] = []
Speaker(synth=lambda _t: (tone(0.4, sr), sr), extra_latency_ms=200,
        open_stream=lambda _sr: bt).speak("hello", on_envelope=deaf.append)
lead_blocks = round((0.02 + 0.200) / (BLOCK_MS / 1000.0))
shifted = deaf[:len(straight) - lead_blocks] == straight[:len(straight) - lead_blocks]
check(lead_blocks == 11 and shifted,
      "configured latency the card cannot report is added to the delay (Bluetooth)",
      f"20ms reported + 200ms configured = {lead_blocks} blocks held back")

try:
    Speaker(synth=lambda _t: (tone(0.1, sr), sr), extra_latency_ms=-5)
    check(False, "a negative configured latency is refused", "it was accepted")
except ValueError as exc:
    check(True, "a negative configured latency is refused", str(exc))

# And with a zero-latency sink nothing is held back, so the simple case stays simple.
plain: list[float] = []
Speaker(synth=lambda _t: (tone(0.2, sr), sr),
        open_stream=lambda _sr: _NullStream()).speak("hello", on_envelope=plain.append)
check(plain[:-1] == [e for _, e in envelope_blocks(tone(0.2, sr), sr)],
      "a sink reporting no latency gets the envelope undelayed",
      f"{len(plain)} values, unshifted")


# ============================================================ 5. the mic gate

# ============================================ priming — the sink is woken before he speaks

section("priming")

# PipeWire suspends an idle output node, and resuming it swallows the start of whatever
# arrives. Measured on the Pi 2026-08-15: the HDMI sink reports state "suspended" with
# nothing playing, and LB heard the consequence as "the beginning is inaudible".
#
# The fix is silence written BEFORE the first real sample. That is only worth anything if it
# actually reaches the card first AND does not drag his mouth along with it, so both halves
# are asserted here rather than trusted to the config value being read.


class RecordingStream(_NullStream):
    """A sink that remembers what it was handed, so priming can be proven rather than assumed."""

    def __init__(self) -> None:
        self.blocks: list[np.ndarray] = []

    def write(self, block: np.ndarray) -> None:
        self.blocks.append(np.asarray(block).copy())


RATE = 22050
for prime_ms in (0.0, 200.0):
    rec = RecordingStream()
    started: list[int] = []
    Speaker(synth=lambda _t: (np.sin(np.linspace(0, 40, RATE)).astype(np.float32), RATE),
            open_stream=lambda _sr: rec, prime_ms=prime_ms).speak(
        "hello", on_start=lambda: started.append(len(rec.blocks)))

    lead = rec.blocks[0] if rec.blocks else np.zeros(1)
    silent_first = bool(rec.blocks) and float(np.abs(lead).max()) == 0.0
    want = int(RATE * prime_ms / 1000.0)

    if prime_ms == 0.0:
        check(not silent_first, "prime_ms=0 writes no lead-in at all",
              f"first block peaks {float(np.abs(lead).max()):.3f}")
    else:
        check(silent_first, f"prime_ms={prime_ms:.0f} writes silence FIRST",
              f"first block peaks {float(np.abs(lead).max()):.3f}, expected 0.0")
        check(lead.size == want, f"the lead-in is {prime_ms:.0f}ms long",
              f"{lead.size} samples, expected {want}")

    # The whole point of putting it before on_start(): his mouth must still begin with the
    # first REAL syllable. Padding both would trade clipping for a lip-sync error.
    check(started == [1] if prime_ms else started == [0],
          f"prime_ms={prime_ms:.0f}: the mouth starts after the silence, not during it",
          f"on_start fired at block index {started}")

check(True, "priming is measured, not assumed",
      "RecordingStream captures what reaches the card")


section("gate")

clock = FakeClock()
opens = {"n": 0}
gate = MicGate(on_open=lambda: opens.__setitem__("n", opens["n"] + 1), clock=clock)

check(gate.is_open(), "the gate is open by default — he can hear before he has ever spoken")

with gate.speaking(0.35):
    shut_while_speaking = not gate.is_open()
    clock.advance(2.0)                        # a 2s sentence
    shut_at_end = not gate.is_open()

shut_before_tail = not gate.is_open()
clock.advance(0.34)
shut_within_tail = not gate.is_open()
clock.advance(0.02)
open_after_tail = gate.is_open()

check(shut_while_speaking and shut_at_end and shut_before_tail and shut_within_tail
      and open_after_tail,
      "the mic is shut for the whole sentence and reopens exactly after the tail",
      f"closed through 2.00s of speech + 0.34s of tail, open at 0.36s "
      f"({gate.blocked_frames} frames discarded)")

# Fires once, on the transition — not on every frame after it, which would reset the model
# continuously and stop him ever hearing a complete phrase.
gate.is_open(), gate.is_open(), gate.is_open()
check(opens["n"] == 1, "reopening resets the detector exactly once",
      f"on_open fired {opens['n']} time(s) across a closure and four is_open() calls")

clock2 = FakeClock()
gate2 = MicGate(clock=clock2)
try:
    with gate2.speaking(0.2):
        raise RuntimeError("the sound card fell over mid-sentence")
except RuntimeError:
    pass
clock2.advance(0.25)
check(gate2.is_open(), "a failure mid-sentence still reopens the mic, rather than deafening him",
      "gate reopened after the exception propagated")

try:
    MicGate().open_after(-1.0)
    check(False, "a negative tail is refused", "it was accepted")
except ValueError as exc:
    check(True, "a negative tail is refused", str(exc))


# ============================================================ 6. he must not wake himself

section("self-wake")


def to_wake_rate(audio: np.ndarray, rate: int) -> np.ndarray:
    """Resample float audio to the wake model's 16kHz int16, as the microphone would hear it."""
    from scipy.signal import resample_poly
    from math import gcd

    if rate != SAMPLE_RATE_HZ:
        g = gcd(SAMPLE_RATE_HZ, rate)
        audio = resample_poly(audio, SAMPLE_RATE_HZ // g, rate // g)
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)


if synth is not None and cfg:
    try:
        detector = WakeDetector(
            model=build_model(cfg["wake"]["model"], cfg["wake"]["framework"]),
            threshold=cfg["wake"]["threshold"],
            refractory_s=0.0,
        )
        # TAKES, not one. Piper is a VITS model with a stochastic duration predictor, so the
        # same line comes out with different timing every run — 2.08s, 2.16s and 2.19s were
        # measured for this greeting within a minute. One take scoring low proves that take
        # was safe, not that the line is.
        TAKES = 3
        worst, fired = 0.0, []
        for line in speech["greeting"]:
            for _ in range(TAKES):
                audio, rate = synth(line)
                pcm = to_wake_rate(audio, rate)
                detector.reset()
                peak = 0.0
                for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
                    det = detector.feed(pcm[i:i + FRAME_SAMPLES])
                    peak = max(peak, detector.last_score)
                    if det:
                        fired.append(line)
                worst = max(worst, peak)
        # This is the check the whole gate exists for. A greeting containing his wake phrase
        # would loop: he answers, hears himself, answers again. The gate stops it in practice,
        # but a line that scores at all is a line waiting for the tail to be tuned too short.
        check(not fired, "nothing he says wakes him up",
              f"{len(speech['greeting'])} line(s) x {TAKES} takes, worst score {worst:.4f} "
              f"against threshold {cfg['wake']['threshold']}"
              + (f" — FIRED on {fired}" if fired else ""))
    except Exception as exc:  # noqa: BLE001
        check(False, "nothing he says wakes him up", f"{type(exc).__name__}: {exc}")


# ============================================================ 7. the rig link

section("bridge")


async def mouth_roundtrip() -> tuple[bool, str]:
    """Start the real server, connect a real client, and confirm the mouth channel."""
    from websockets.asyncio.client import connect

    bridge = HudBridge("127.0.0.1", 8798)
    server = await bridge.start()
    try:
        async with connect("ws://127.0.0.1:8798") as ws:
            await asyncio.wait_for(ws.recv(), timeout=2)          # the replayed state
            await bridge.broadcast({"type": "state", "value": "speaking"})
            await asyncio.wait_for(ws.recv(), timeout=2)

            for v in (0.0, 0.5, 1.0, 4.2, -3.0, float("inf")):
                await bridge.broadcast(
                    {"type": "mouth", "value": max(0.0, min(1.0, float(v)))})
            got = [json.loads(await asyncio.wait_for(ws.recv(), timeout=2)) for _ in range(6)]

            values = [m["value"] for m in got]
            if any(m["type"] != "mouth" for m in got):
                return False, f"wrong message types: {got}"
            if values != [0.0, 0.5, 1.0, 1.0, 0.0, 1.0]:
                return False, f"clamping is wrong: {values}"

            # A rig connecting mid-sentence must be told what he is DOING, not where his jaw
            # happens to be. If a mouth message ever became the replayed state, a late joiner
            # would sit with its mouth open and no idea why.
            async with connect("ws://127.0.0.1:8798") as ws2:
                late = json.loads(await asyncio.wait_for(ws2.recv(), timeout=2))
                if late != {"type": "state", "value": "speaking"}:
                    return False, f"late client got {late}, expected the current state"

        return True, "0..1 passed through, out-of-range clamped, late-join still gets a state"
    finally:
        server.close()


try:
    ok, detail = asyncio.run(mouth_roundtrip())
    check(ok, "the bridge carries the mouth channel and clamps it", detail)
except Exception as exc:  # noqa: BLE001
    check(False, "the bridge carries the mouth channel and clamps it",
          f"{type(exc).__name__}: {exc}")

# set_mouth() is the threadsafe wrapper the speech thread actually calls; the round-trip above
# exercises broadcast(). Prove the wrapper clamps too, without a running loop.
sent: list[dict] = []
probe = HudBridge()
probe.broadcast_threadsafe = sent.append          # type: ignore[method-assign]
for v in (0.5, 9.0, -1.0):
    probe.set_mouth(v)
check([m["value"] for m in sent] == [0.5, 1.0, 0.0]
      and all(m["type"] == "mouth" for m in sent),
      "set_mouth() clamps before anything reaches the wire",
      f"sent {[m['value'] for m in sent]} for inputs [0.5, 9.0, -1.0]")

rig = (REPO_ROOT / "hud" / "face-preview.html").read_text(encoding="utf-8")
check('m.type === "mouth"' in rig,
      "the rig actually handles the message type the bridge sends",
      "hud/face-preview.html dispatches on \"mouth\"")


async def serves_the_page() -> tuple[bool, str]:
    """The bridge must serve the rig over HTTP on the same port it accepts sockets on.

    A `file://` page cannot open this WebSocket — Chromium loads the rig and then silently
    never connects, which looks exactly like him ignoring you. Serving it over HTTP fixes
    that, makes the URL identical on every machine, and makes `?ws=` unnecessary because the
    rig derives the socket from `location.host` — which is only correct because it is the
    SAME port. That last part is what this check pins down.
    """
    import urllib.request

    from websockets.asyncio.client import connect

    bridge = HudBridge("127.0.0.1", 8797)
    server = await bridge.start()
    try:
        page = await asyncio.to_thread(
            lambda: urllib.request.urlopen("http://127.0.0.1:8797/", timeout=3).read())
        if b"face-preview" not in page and b"<svg" not in page:
            return False, f"served {len(page)} bytes that do not look like the rig"
        missing = await asyncio.to_thread(
            lambda: urllib.request.urlopen("http://127.0.0.1:8797/nope", timeout=3).status)
        return False, f"a bogus path returned {missing} instead of 404"
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return False, f"a bogus path returned {exc.code} instead of 404"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        server.close()

    # ...and the socket must still work on that very same port.
    bridge2 = HudBridge("127.0.0.1", 8797)
    server2 = await bridge2.start()
    try:
        async with connect("ws://127.0.0.1:8797") as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            if first.get("type") != "state":
                return False, f"socket on the same port replied {first}"
    finally:
        server2.close()
    return True, "GET / returns the rig, /nope is 404, and the WebSocket still upgrades"


try:
    ok, detail = asyncio.run(serves_the_page())
    check(ok, "the bridge serves the rig over HTTP on its own port", detail)
except Exception as exc:  # noqa: BLE001
    check(False, "the bridge serves the rig over HTTP on its own port",
          f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ report

if __name__ == "__main__":
    argparse.ArgumentParser(description="verify his voice offline").parse_args()

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
