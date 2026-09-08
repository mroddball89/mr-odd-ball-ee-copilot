#!/usr/bin/env python3
"""
Module:  train_wake.py
Purpose: Run openWakeWord's trainer on this machine. The three phases, in order.
Author:  LB
Date:    2026-09-08

    python training/train_wake.py --freeze-background     # once, before anything else
    python training/train_wake.py --generate_clips        # ~2.5 GB of synthetic WAV, hours
    python training/train_wake.py --augment_clips         # WAV -> features
    python training/train_wake.py --train_model           # the model itself

## Why this wrapper exists rather than `python -m openwakeword.train`

Two reasons, and both are things that break the run rather than preferences.

**1. `openwakeword.data` cannot be imported on this box until scipy is patched.** `acoustics`
0.2.6 dies on `from scipy.special import sph_harm`, which scipy 1.17 renamed. See
`training/oww_compat.py` for why that is an alias and not a stub. The patch has to be applied
BEFORE the trainer imports anything, which a wrapper can do and a command line cannot.

**2. The trainer's whole body is under `if __name__ == '__main__'`.** There is no function to
call — so this runs it with `runpy` under that name, forwarding the flags untouched. Nothing
about the training logic is reimplemented here; if that file changes, this keeps working.

## --freeze-background, and the race it removes

`background_paths` points at LB's own `captures/` — ~20 minutes of his real room, standing in
for the multi-GB AudioSet download (see the config for that trade). But **the rig writes to
`captures/` while it runs.** `--save-captures` is on in `config/start_oddball.bat`, so every
wake saves a new WAV there, and the directory grew by two files during the session that wrote
this file.

Augmentation reads that directory with `os.scandir` and hands each path to a WAV loader. A file
caught mid-write is a truncated RIFF header, and the failure would arrive hours into a run, as
a crash inside a worker, on a file that is perfectly valid by the time anybody looks at it.

So the background set is COPIED once into `training/corpora/background/` and the config points
there. The snapshot is dated in its manifest, because "which room recordings trained this
model" is part of the model's provenance and `captures/` will not answer it later.
"""

from __future__ import annotations

import argparse
import json
import runpy
import shutil
import sys
import wave
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CONFIG = REPO / "training" / "hey_mr_odd_ball.yaml"
BACKGROUND = REPO / "training" / "corpora" / "background"
SOURCE = REPO / "captures"

# Shorter than this and the clip is a fragment — a false wake that caught one syllable. They
# are useless as background noise and there are a lot of them: `data/oddball.log` measured 55%
# of wake captures holding 0.40s or less of voiced audio.
MIN_SECONDS = 1.0


def freeze_background() -> int:
    """Copy `captures/` into a frozen snapshot the trainer can read safely.

    Skips anything unreadable or too short, and writes a manifest saying what was taken and
    when — the model's provenance, in the one place that will still be true in six months.
    """
    BACKGROUND.mkdir(parents=True, exist_ok=True)
    for stale in BACKGROUND.glob("*.wav"):
        stale.unlink()

    taken, skipped, seconds = [], [], 0.0
    for clip in sorted(SOURCE.glob("*.wav")):
        try:
            with wave.open(str(clip)) as handle:
                duration = handle.getnframes() / handle.getframerate()
        except Exception as exc:                                      # noqa: BLE001
            # Precisely the case this function exists for: a file being written right now.
            skipped.append((clip.name, f"unreadable: {type(exc).__name__}"))
            continue
        if duration < MIN_SECONDS:
            skipped.append((clip.name, f"{duration:.2f}s — a fragment, not background"))
            continue
        shutil.copy2(clip, BACKGROUND / clip.name)
        taken.append(clip.name)
        seconds += duration

    manifest = {
        "what": "The background-noise set used to augment the wake-model training data.",
        "why": "captures/ is written by the live rig; training must read a frozen copy or it "
               "can hit a half-written WAV hours into a run.",
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "source": "captures/",
        "clips": len(taken),
        "minutes": round(seconds / 60, 1),
        "min_seconds_kept": MIN_SECONDS,
        "skipped": [{"clip": c, "why": w} for c, w in skipped],
        "note": "These are recordings of LB's room and are gitignored. The model trained on "
                "them is a binary; no audio is recoverable from it.",
    }
    (BACKGROUND.parent / "background_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"  froze {len(taken)} clips ({seconds / 60:.1f} min) into "
          f"{BACKGROUND.relative_to(REPO).as_posix()}")
    if skipped:
        print(f"  skipped {len(skipped)}:")
        for name, why in skipped[:5]:
            print(f"    {name}  — {why}")
        if len(skipped) > 5:
            print(f"    ... and {len(skipped) - 5} more")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="run the openWakeWord trainer with this machine's compatibility patch")
    ap.add_argument("--freeze-background", action="store_true",
                    help="snapshot captures/ into training/corpora/background/ and stop")
    ap.add_argument("--training_config", default=str(CONFIG))
    ap.add_argument("--generate_clips", action="store_true")
    ap.add_argument("--augment_clips", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--train_model", action="store_true")
    args = ap.parse_args(argv)

    if args.freeze_background:
        return freeze_background()

    if not (args.generate_clips or args.augment_clips or args.train_model):
        ap.error("pick a phase: --generate_clips, --augment_clips or --train_model")

    if not BACKGROUND.exists() or not any(BACKGROUND.glob("*.wav")):
        ap.error("no frozen background set — run --freeze-background first. Training against "
                 "a live captures/ can hit a half-written WAV hours into the run.")

    from training.oww_compat import patch, patch_torch_load            # noqa: PLC0415

    patch()
    # Needed by --generate_clips: the trainer imports piper-sample-generator, which loads a
    # pickled VITS model that PyTorch 2.6's safe loader refuses. Applied for every phase rather
    # than only generation, because it is scoped to calls that state no opinion and costs
    # nothing when no checkpoint is loaded.
    patch_torch_load()

    # Rebuilt rather than forwarded wholesale: the trainer parses argv itself, and it must not
    # see `--freeze-background`, which is ours.
    forwarded = ["openwakeword.train", "--training_config", args.training_config]
    for flag in ("generate_clips", "augment_clips", "overwrite", "train_model"):
        if getattr(args, flag):
            forwarded.append(f"--{flag}")

    print(f"  {' '.join(forwarded[1:])}\n")
    sys.argv = forwarded
    runpy.run_module("openwakeword.train", run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
