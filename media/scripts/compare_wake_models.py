#!/usr/bin/env python3
"""
Module:  compare_wake_models.py
Purpose: Score two wake models over the same fixtures and write the before/after CSV.
Author:  LB
Date:    2026-09-09

    python media/scripts/compare_wake_models.py
    python media/scripts/compare_wake_models.py --new training/output/hey_mr_odd_ball.onnx

Writes `media/data/<today>-wake-retrain-compare.csv`, one row per clip per model, and prints
the threshold sweep that decides whether the new model ships.

## Why a comparison script rather than two runs of measure_wake_fixtures.py

Because the question is not "what does this model score" but "does the band separate". Two
separate CSVs make that a manual join, and the number that matters — **the loudest negative,
and how many positives sit above it** — is a property of the pair, not of either file.

That number is the entire reason for the retrain. `config/oddball.toml` records a threshold
hand-tuned 0.76 -> 0.53 -> 0.76 in one week and reverted twice, because no value of it could
separate LB's voice from his room. A model where every negative sits below every useful
positive needs no such tuning.

## The held-out split is the honest half

`training/splits.json` was frozen on 2026-09-08 BEFORE any training data was generated. The
32 training positives and 15 training negatives are in the model's own diet — their scores
prove nothing. The 15 + 8 test clips are the measurement.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import sys
import wave
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

FIXTURES = REPO / "tests" / "fixtures" / "wake"
SPLITS = REPO / "training" / "splits.json"
FRAME = 1280


def peak_score(model, path: Path) -> float:
    """Highest score the model reaches anywhere in the clip.

    The RAW peak, not what the live detector reports: `WakeDetector` calls `reset()` the moment
    it fires, so its own reading is capped at the firing frame and understates every positive.
    That bug cost a threshold decision on 2026-08-15 — see config/oddball.toml.
    """
    with contextlib.closing(wave.open(str(path))) as handle:
        audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    model.reset()
    best = 0.0
    for start in range(0, len(audio) - FRAME, FRAME):
        best = max(best, max(model.predict(audio[start:start + FRAME]).values()))
    return float(best)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="score two wake models over the same fixtures")
    ap.add_argument("--old", default="models/hey_mr_odd_ball.onnx")
    ap.add_argument("--new", default="training/output/hey_mr_odd_ball.onnx")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    from training.oww_compat import patch                             # noqa: PLC0415

    patch()
    from audio.wake import build_model                                # noqa: PLC0415

    models = {"old": build_model(args.old, "onnx"), "new": build_model(args.new, "onnx")}

    split = json.loads(SPLITS.read_text(encoding="utf-8"))
    held_out = {r["clip"] for kind in ("positive", "negative") for r in split["test"][kind]}

    rows = []
    for kind in ("positive", "negative", "known-limits"):
        for clip in sorted((FIXTURES / kind).glob("*.wav")):
            row = {"kind": kind, "clip": clip.name,
                   "split": "test" if clip.name in held_out else "train"}
            for name, model in models.items():
                row[f"{name}_peak"] = round(peak_score(model, clip), 4)
            rows.append(row)

    out = Path(args.out) if args.out else (
        REPO / "media" / "data" / f"{date.today():%Y-%m-%d}-wake-retrain-compare.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {out.relative_to(REPO).as_posix()}  ({len(rows)} rows)\n")

    def report(scope: str, keep) -> None:
        pos = {n: np.array([r[f"{n}_peak"] for r in rows
                            if r["kind"] == "positive" and keep(r)]) for n in models}
        neg = {n: np.array([r[f"{n}_peak"] for r in rows
                            if r["kind"] == "negative" and keep(r)]) for n in models}
        print(f"  === {scope} ===")
        for name in models:
            worst = neg[name].max()
            above = int((pos[name] > worst).sum())
            print(f"    {name.upper():4} loudest negative {worst:.4f} — "
                  f"{above}/{len(pos[name])} positives clear it")
        print(f"    {'thr':>6}  {'OLD fires':>10} {'OLD false':>10}  "
              f"{'NEW fires':>10} {'NEW false':>10}")
        for threshold in (0.10, 0.15, 0.20, 0.30, 0.50, 0.76):
            cells = [f"    {threshold:6.2f} "]
            for name in models:
                cells.append(f" {int((pos[name] >= threshold).sum()):>4}/{len(pos[name]):<5}")
                cells.append(f"{int((neg[name] >= threshold).sum()):>10}")
            print("".join(cells))
        print()

    report("ALL FIXTURES", lambda r: True)
    report("HELD-OUT TEST ONLY — the honest number", lambda r: r["split"] == "test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
