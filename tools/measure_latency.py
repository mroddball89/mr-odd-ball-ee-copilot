#!/usr/bin/env python3
"""
Module:  measure_latency.py
Purpose: Measure the output latency PortAudio cannot see, so speech.output_latency_ms is a
         measurement rather than a guess.
Author:  LB
Date:    2026-08-12

    venv/bin/python tools/measure_latency.py

Needs a speaker AND a microphone on the same box, like tools/measure_gate.py.

Why this exists: his mouth is driven from `stream.write()`, which returns when there is room
in the buffer, not when sound leaves the speaker. audio/say.py compensates using the latency
PortAudio reports — but **PortAudio only knows about its own layer.** With the Bose on
Bluetooth the Pi reports 20ms while A2DP is buffering 150-250ms downstream, so his lips run
ahead of his voice and nothing in the software knows it.

Method. A click is written into an already-running output stream, so stream start-up is not
in the number. The stream is in steady state, so write() is running exactly one output
latency ahead of the speaker; the click is then heard by the microphone one flight time and
one input latency later:

    t_detected - t_written = output_latency + flight + input_latency

Everything but output_latency is either reported by PortAudio or computed from the distance,
so the remainder is what to put in the config. Several clicks are timed and the median taken,
because Bluetooth jitters.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from audio.say import as_device                                  # noqa: E402
from audio.wake import FRAME_SAMPLES, SAMPLE_RATE_HZ             # noqa: E402
from orchestrator.settings import load_config                    # noqa: E402

SPEED_OF_SOUND_M_S = 343.0
CLICK_MS = 60.0            # spans several 10ms input frames, so a peak is unambiguous
CLICK_HZ = 3000.0          # well above room rumble, well inside every codec's passband


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="measure output latency end to end")
    ap.add_argument("--clicks", type=int, default=5, help="how many to time (median wins)")
    ap.add_argument("--distance-m", type=float, default=1.0,
                    help="speaker-to-microphone distance, for the flight-time correction")
    ap.add_argument("--device-in", default=None)
    ap.add_argument("--device-out", default=None)
    args = ap.parse_args(argv)

    import sounddevice as sd

    cfg = load_config()
    dev_in = as_device(args.device_in if args.device_in is not None else cfg["wake"]["device"])
    dev_out = as_device(args.device_out if args.device_out is not None
                        else cfg["speech"]["device"])

    out_rate = 48000
    click_n = int(out_rate * CLICK_MS / 1000.0)
    t = np.arange(click_n) / out_rate
    # Hann-windowed so it is a clean impulse rather than two edge transients.
    click = (np.sin(2 * np.pi * CLICK_HZ * t) * np.hanning(click_n)).astype(np.float32) * 0.9
    # NOT digital silence. A Bluetooth sink fed pure zeros idles, and the link takes a moment
    # to come back — which swallowed six clicks out of seven and left one lonely number
    # looking like a result. Inaudible dither keeps the stream alive between clicks.
    rng = np.random.default_rng(0)
    quiet = (rng.standard_normal(int(out_rate * 0.02)) * 1e-4).astype(np.float32)

    heard: list[tuple[float, float]] = []          # (timestamp, rms) per input frame
    stop = threading.Event()

    def listen() -> None:
        with sd.InputStream(samplerate=SAMPLE_RATE_HZ, channels=1, dtype="int16",
                            blocksize=160, device=dev_in or None) as s:   # 10ms resolution
            print(f"  input latency reported: {s.latency * 1000:.1f}ms")
            heard.append((-1.0, s.latency))        # smuggle it out for the correction
            while not stop.is_set():
                block, _ = s.read(160)
                rms = float(np.sqrt(np.mean((block[:, 0].astype(np.float64) / 32768.0) ** 2)))
                heard.append((time.monotonic(), rms))

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    time.sleep(1.5)                                 # settle, and gather a noise floor
    if len(heard) < 2:
        print("  the microphone produced nothing — cannot measure.")
        return 2
    in_latency = heard[0][1]
    floor = statistics.median(r for _, r in heard[1:] if r > 0) if len(heard) > 2 else 0.0
    print(f"  noise floor {floor:.5f}")

    written: list[float] = []
    with sd.OutputStream(samplerate=out_rate, channels=1, dtype="float32",
                         blocksize=int(out_rate * 0.02), device=dev_out or None,
                         latency="low") as out:
        print(f"  output latency reported: {out.latency * 1000:.1f}ms")
        for _ in range(20):                         # reach steady state before timing anything
            out.write(quiet)
        for _ in range(args.clicks):
            out.write(click)
            written.append(time.monotonic())
            for _ in range(20):                     # ~0.4s between clicks
                out.write(quiet)
    time.sleep(0.5)
    stop.set()
    thread.join(timeout=2.0)

    # Take the LOUDEST frame inside a bounded window after each write, not the first frame
    # over a threshold. First-crossing pairs a click with whatever happened to be loud next —
    # if one click is missed it locks onto the following one, and the deltas come out as
    # multiples of the click spacing. The first version of this did exactly that and reported
    # a 43-3516ms spread, which is a detector bug wearing the costume of a measurement.
    # Calibrate the trigger against what actually ARRIVED rather than an assumed level. A
    # fixed 0.02 threshold was above what the Bose delivers at its normal volume, so six of
    # seven clicks went unheard and the one that got through looked like the whole answer.
    samples = heard[1:]
    peak_all = max((rms for _, rms in samples), default=0.0)
    if peak_all < floor * 3:
        print(f"\n  Nothing rose above the noise floor (peak {peak_all:.5f} vs floor "
              f"{floor:.5f}). The speaker is not reaching this microphone.")
        return 2
    trigger = floor + (peak_all - floor) * 0.35
    print(f"  loudest arrival {peak_all:.5f}, trigger {trigger:.5f}")

    WINDOW_S = 0.70                                  # generous: A2DP is 150-250ms
    deltas, rejected = [], 0
    for t_write in written:
        window = [(at, rms) for at, rms in samples if t_write < at <= t_write + WINDOW_S]
        if not window:
            rejected += 1
            continue
        at, rms = max(window, key=lambda p: p[1])
        if rms < trigger:
            rejected += 1
            continue
        deltas.append(at - t_write)

    print(f"  per-click delay: {', '.join(f'{d * 1000:.0f}ms' for d in deltas) or 'none'}"
          + (f"   ({rejected} not heard)" if rejected else ""))

    if len(deltas) < 2:
        print("\n  Too few clicks heard to trust a median. The speaker and microphone are not\n"
              "  on the same box, the volume is too low, or the room is too noisy.")
        return 2
    if max(deltas) - min(deltas) > 0.25:
        print("\n  The delays disagree by more than 250ms, so the median means little.\n"
              "  Re-run somewhere quieter, or tune output_latency_ms by eye instead.")
        return 2

    flight = args.distance_m / SPEED_OF_SOUND_M_S
    round_trip = statistics.median(deltas)
    output_latency = round_trip - in_latency - flight
    reported = out.latency
    extra = max(0.0, output_latency - reported)

    print(f"\n  {len(deltas)}/{args.clicks} clicks detected, "
          f"spread {min(deltas) * 1000:.0f}-{max(deltas) * 1000:.0f}ms")
    print(f"  median round trip        {round_trip * 1000:7.1f} ms")
    print(f"  - input latency          {in_latency * 1000:7.1f} ms")
    print(f"  - flight ({args.distance_m:.1f}m)          {flight * 1000:7.1f} ms")
    print(f"  = true output latency    {output_latency * 1000:7.1f} ms")
    print(f"  PortAudio reported       {reported * 1000:7.1f} ms")
    print(f"\n  Put this in config/oddball.toml:\n      output_latency_ms = {extra * 1000:.0f}")
    if extra * 1000 < 25:
        print("  (i.e. PortAudio is telling the truth here — leave it at 0.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
