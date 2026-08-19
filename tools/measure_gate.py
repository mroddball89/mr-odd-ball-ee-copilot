#!/usr/bin/env python3
"""
Module:  measure_gate.py
Purpose: Measure what the half-duplex mic gate is actually worth, on real hardware.
Author:  LB
Date:    2026-08-11

    venv\\Scripts\\python.exe tools/measure_gate.py

Needs a speaker AND a microphone on the same box — so unlike tools/verify_speech.py this is
not part of the offline harness. It is the step-4 equivalent of the threshold measurement in
step 3: the number that turns `gate_tail_s = 0.35` from a guess into a decision.

Three trials, and the first exists to stop the other two being a vacuous pass:

  A  his own WAKE PHRASE, played at his own microphone, gate OFF
     If this does not fire, the echo path is not live — the speaker is too quiet, the mic is
     deaf, or they are on different devices — and trials B and C would score zero for the
     wrong reason and look like a triumph.
  B  the same phrase, gate ON. This is the fix, under the worst input there is.
  C  the real greeting, gate OFF. What actually happens in daily use if the gate is removed.

Every trial also reports the microphone's own RMS level during playback, because "he heard
nothing" and "there was nothing to hear" are different results with the same score.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from audio.gate import MicGate                                  # noqa: E402
from audio.say import PiperSynth, Speaker                       # noqa: E402
from audio.wake import WakeDetector, build_model, mic_frames    # noqa: E402
from orchestrator.settings import load_config                   # noqa: E402

LOG = logging.getLogger("oddball.measure")


@dataclass
class Trial:
    """What one playback told us."""

    label: str
    gated: bool
    peak_during: float = 0.0        # highest wake score while he was speaking
    peak_after: float = 0.0         # highest after the gate reopened — what the tail missed
    fires: int = 0
    mic_rms: float = 0.0            # loudest mic block during playback, 0..1
    scored_frames: int = 0          # frames that actually reached the detector
    blocked_frames: int = 0
    detail: list[str] = field(default_factory=list)


def run_trial(label: str, text: str, gated: bool, detector: WakeDetector, speaker: Speaker,
              device_in, tail_s: float, after_s: float = 2.0) -> Trial:
    """Speak `text` aloud with the microphone live, and report what the microphone made of it."""
    t = Trial(label=label, gated=gated)
    detector.reset()
    gate = MicGate(on_open=detector.reset)
    stop = threading.Event()
    # (timestamp, mic rms, wake score, fired) per frame. Timestamped and classified
    # afterwards rather than tested against a window as it goes — the first version of this
    # compared each frame against an end time that was only assigned once playback had
    # already finished, so every frame counted as "after" and nothing was ever measured
    # during the speech at all. Trial A is what exposed it, which is why trial A exists.
    samples: list[tuple[float, float, float, bool]] = []

    def listen() -> None:
        # The same three lines as orchestrator/run_wake.py's mic loop: keep pulling, only
        # feed when the gate is open. Instrumented here, identical in behaviour.
        try:
            for frame in mic_frames(device_in):
                if stop.is_set():
                    return
                rms = float(np.sqrt(np.mean((frame.astype(np.float64) / 32768.0) ** 2)))
                if gated and not gate.is_open():
                    samples.append((time.monotonic(), rms, float("nan"), False))
                    continue
                t.scored_frames += 1
                det = detector.feed(frame)
                samples.append((time.monotonic(), rms, detector.last_score, det is not None))
                if det is not None:
                    t.detail.append(f"fired at {det.score:.4f}")
        except Exception as exc:  # noqa: BLE001
            t.detail.append(f"microphone stopped: {exc}")
            stop.set()

    thread = threading.Thread(target=listen, name="mic", daemon=True)
    thread.start()
    time.sleep(1.0)                     # let the input stream settle before it matters

    started = time.monotonic()
    if gated:
        with gate.speaking(tail_s):
            secs = speaker.speak(text)
    else:
        secs = speaker.speak(text)
    ended = time.monotonic()

    # Keep listening past the end: this is where a too-short tail shows up, as his own
    # reverberation arriving after the microphone has already been let back in.
    time.sleep(after_s)
    stop.set()
    thread.join(timeout=2.0)
    t.blocked_frames = gate.blocked_frames

    for at, rms, score, fired in samples:
        during = started <= at <= ended
        if during:
            t.mic_rms = max(t.mic_rms, rms)
        if not np.isnan(score):
            if during:
                t.peak_during = max(t.peak_during, score)
            elif at > ended:
                t.peak_after = max(t.peak_after, score)
        t.fires += bool(fired)
    LOG.info("%s: %.2fs of speech, %d frames scored, %d blocked",
             label, secs, t.scored_frames, t.blocked_frames)
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="measure the mic gate on real hardware")
    ap.add_argument("--wake-phrase", default="Hey Mister Odd Ball",
                    help="the phrase used for trials A and B — his own wake word, spoken by "
                         "him, which is the worst thing his microphone can hear")
    ap.add_argument("--wake-wav", metavar="FILE", default=None,
                    help="play this recording for trials A and B instead of synthesising the "
                         "phrase. The stricter control: a fixture from tests/fixtures/wake/"
                         "positive is KNOWN to score 0.62-0.94 fed straight to the model, so "
                         "if playing it through the speaker does not fire, the failure is the "
                         "acoustic path and not the wake model failing to recognise a "
                         "synthetic voice.")
    ap.add_argument("--tail", type=float, default=None, help="override speech.gate_tail_s")
    ap.add_argument("--device-in", default=None, help="input device index or name")
    ap.add_argument("--device-out", default=None, help="output device index or name")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config()
    wake_cfg, speech_cfg = cfg["wake"], cfg["speech"]
    tail_s = args.tail if args.tail is not None else speech_cfg["gate_tail_s"]
    device_in = args.device_in if args.device_in is not None else wake_cfg["device"]
    if device_in not in ("", None):
        try:
            device_in = int(device_in)
        except ValueError:
            pass

    detector = WakeDetector(
        model=build_model(wake_cfg["model"], wake_cfg["framework"]),
        threshold=wake_cfg["threshold"],
        refractory_s=wake_cfg["refractory_s"],
    )
    speaker = Speaker(
        synth=PiperSynth(speech_cfg["voice"]),
        device=args.device_out if args.device_out is not None else speech_cfg["device"],
        volume=speech_cfg["volume"],
    )

    # For trials A and B the Speaker's synthesiser is swapped for one that returns a
    # recording. `Speaker` takes its synth as an argument precisely so this works — the
    # playback path, the envelope and the gate stay exactly the ones production uses.
    if args.wake_wav:
        import wave

        with wave.open(args.wake_wav, "rb") as wf:
            wav_rate = wf.getframerate()
            raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if wf.getnchannels() > 1:
                raw = raw.reshape(-1, wf.getnchannels())[:, 0]
        wav_audio = (raw.astype(np.float32) / 32768.0)
        control = Speaker(synth=lambda _t: (wav_audio, wav_rate),
                          device=speaker._device, volume=speech_cfg["volume"])
        control_label = Path(args.wake_wav).name
    else:
        control, control_label = speaker, f"{args.wake_phrase!r} (TTS)"

    greeting = speech_cfg["greeting"][0]
    trials = [
        run_trial("A  wake phrase, gate OFF", args.wake_phrase, False,
                  detector, control, device_in, tail_s),
        run_trial("B  wake phrase, gate ON ", args.wake_phrase, True,
                  detector, control, device_in, tail_s),
        run_trial("C  real greeting, gate OFF", greeting, False,
                  detector, speaker, device_in, tail_s),
    ]
    print(f"\n  control audio for A and B: {control_label}")

    print(f"\n  voice {speech_cfg['voice']}, volume {speech_cfg['volume']}, "
          f"threshold {wake_cfg['threshold']}, tail {tail_s}s")
    print(f"  {'trial':<26} {'mic RMS':>8} {'peak during':>12} {'peak after':>11} {'fires':>6}")
    for t in trials:
        print(f"  {t.label:<26} {t.mic_rms:>8.4f} {t.peak_during:>12.4f} "
              f"{t.peak_after:>11.4f} {t.fires:>6}")
        for d in t.detail:
            print(f"      {d}")

    a, b, c = trials
    print()
    # Trial A is the control. Without it, B scoring zero proves only that something was
    # silent somewhere.
    if a.mic_rms < 0.001:
        print("  INCONCLUSIVE: the microphone heard essentially nothing during playback "
              f"(RMS {a.mic_rms:.5f}).\n  The speaker and the microphone are not in the same "
              "room, or the output device is not the audible one.")
        return 2
    if a.fires == 0:
        print(f"  INCONCLUSIVE: his own wake phrase, played at his own microphone at "
              f"RMS {a.mic_rms:.4f}, peaked at only {a.peak_during:.4f} and never fired.\n"
              f"  There is no self-wake to prevent on this hardware, so trials B and C say "
              f"nothing about the gate.")
        return 2

    print(f"  The echo path is real: his own voice woke him {a.fires} time(s) at "
          f"{a.peak_during:.4f} with the gate off.")
    if b.fires == 0:
        print(f"  The gate closes it: 0 detections, {b.blocked_frames} frames discarded, "
              f"worst residual after the {tail_s}s tail {b.peak_after:.4f}.")
    else:
        print(f"  THE GATE LEAKS: {b.fires} detection(s) with it on. Raise gate_tail_s.")
    print(f"  In daily use the greeting itself peaks at {c.peak_during:.4f} ungated "
          f"— {'below' if c.peak_during < wake_cfg['threshold'] else 'ABOVE'} the "
          f"{wake_cfg['threshold']} threshold.\n")
    return 0 if b.fires == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
