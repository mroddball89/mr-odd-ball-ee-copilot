#!/usr/bin/env python3
"""
Module:  measure_wake_fixtures.py
Purpose: Score every wake fixture with the real model AND transcribe it, so a non-detection
         can be told apart from a bad recording.
Author:  LB
Date:    2026-08-30

    python media/scripts/measure_wake_fixtures.py

Writes `media/data/2026-08-30-wake-fixtures.csv`.

## Why the transcript column exists, and why it is the whole point

`tools/verify_wake.py` answers "did it fire". It cannot answer the question that actually
matters when a positive fails, which is **"was the phrase even in the recording?"** A clip
scoring 0.0014 has two completely different explanations:

    the take is bad      — he never said it, or the window cut it off. Re-record.
    the MODEL is deaf    — he said it clearly and the model did not hear it. Retrain.

Those lead to opposite actions, and no amount of threshold arithmetic separates them. Whisper
is an independent witness: it was trained on ordinary speech and has no idea what a wake word
is, so if `base.en` transcribes "Hey, Mr. Oddball" out of a clip the wake model scored 0.0277,
the phrase is unambiguously present and the wake model is the thing that failed.

That is exactly what happened on 2026-08-30, and it is why this file exists rather than
another threshold sweep. See the meta.json beside the CSV.

## `true_peak`, not `fired_at`

`score_clip` in the harness reports both and the distinction was a real bug once: the detector
calls `reset()` the instant it fires, so the highest score the harness can observe through the
detector is the score AT THE FIRING FRAME, not the peak the clip reaches. Choosing a threshold
from that number understates every positive. `score_clip` below reproduces the harness's two
passes deliberately — see its docstring for why it cannot simply be imported.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

FIXTURES = REPO / "tests" / "fixtures" / "wake"
OUT_CSV = REPO / "media" / "data" / "2026-08-30-wake-fixtures.csv"

# Recorded before this date = the original close-mic set; on/after = the marginal set added
# 2026-08-30 by `record_fixture.py --marginal`. Used only to label rows.
MARGINAL_LABELS = ("quiet-marginal", "midsentence", "offaxis", "room")


def score_clip(model, path: Path, threshold: float) -> tuple[int, float, float]:
    """`(hits, fired_at, true_peak)` — the two-pass scorer, matching verify_wake.score_clip.

    Reproduced rather than imported: `tools/verify_wake.py` runs its whole suite at module
    scope, so importing it would execute sixty checks as a side effect of asking for one
    function. The two passes are the load-bearing part and must not drift —

        pass 1, THROUGH the detector, gives pass/fail. The detector calls reset() the instant
                it fires, so the highest score observable here is the score at the FIRING
                frame, not the clip's peak.
        pass 2, RAW model, never interrupted, gives `true_peak` — a property of the audio
                alone and the only honest input to a threshold decision.

    Reporting pass 1's number as a peak is a bug this repo has already had once; see the
    superseded block in config/oddball.toml.
    """
    from audio.wake import WakeDetector, wav_frames                   # noqa: PLC0415

    model.reset()
    detector = WakeDetector(model, threshold=threshold)
    fired_at, hits = 0.0, 0
    for frame in wav_frames(path):
        if detector.feed(frame):
            hits += 1
        fired_at = max(fired_at, detector.last_score)

    model.reset()
    true_peak = 0.0
    for frame in wav_frames(path):
        scores = model.predict(frame)
        if scores:
            true_peak = max(true_peak, float(max(scores.values())))
    model.reset()
    return hits, fired_at, true_peak


def main() -> int:
    from audio.stt import Transcriber, build_model as build_stt, wav_audio   # noqa: PLC0415
    from audio.wake import build_model as build_wake                         # noqa: PLC0415
    from orchestrator.settings import load_config                            # noqa: PLC0415

    cfg = load_config()["wake"]
    threshold = float(cfg["threshold"])
    wake_model = build_wake(cfg["model"], framework=cfg.get("framework", "onnx"))
    print(f"threshold {threshold}, model {cfg['model']}")
    print("loading base.en for the transcript column…")
    transcriber = Transcriber(build_stt("base.en"))

    rows = []
    for kind in ("positive", "negative", "known-limits"):
        for path in sorted((FIXTURES / kind).glob("*.wav")):
            hits, fired_at, true_peak = score_clip(wake_model, path, threshold)
            label = path.stem.rsplit("-", 1)[0]
            rows.append({
                "kind": kind,
                "clip": path.name,
                "label": label,
                "set": "marginal" if label in MARGINAL_LABELS else "original",
                "fires": int(hits > 0),
                "fired_at": f"{fired_at:.4f}",
                "true_peak": f"{true_peak:.4f}",
                "threshold": threshold,
                "transcript": transcriber.transcribe(wav_audio(path)).text.strip(),
            })
            print(f"  {kind:<12} {path.name:<26} peak {true_peak:.4f} "
                  f"{'FIRES' if hits else '     '}  {rows[-1]['transcript'][:44]!r}")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    pos = [r for r in rows if r["kind"] == "positive"]
    neg = [r for r in rows if r["kind"] == "negative"]
    fired = [r for r in pos if r["fires"]]
    print(f"\n  positives {len(fired)}/{len(pos)} fire at {threshold}")
    print(f"  quietest positive {min(float(r['true_peak']) for r in pos):.4f}   "
          f"loudest negative {max(float(r['true_peak']) for r in neg):.4f}")
    print(f"  wrote {OUT_CSV.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
