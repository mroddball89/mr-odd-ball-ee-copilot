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
import gc
import json
import runpy
import shutil
import subprocess
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


FEATURE_FILES = ("positive_features_train.npy", "negative_features_train.npy",
                 "positive_features_test.npy", "negative_features_test.npy")


def check_features() -> "list[str]":
    """Return the reasons the feature set is not usable. Empty list means it is.

    ## Why exit code 0 from --augment_clips is not enough

    On 2026-09-09 augmentation died on its first clip — torchaudio 2.11 routing through a
    TorchCodec that will not load here — and left behind
    `positive_features_train.npy`, 471 MB, shape (80300, 16, 96), **entirely zeros**.

    `openwakeword/train.py:755` gates the WHOLE augmentation block on that one filename
    existing. So every later run skipped augmentation in 12 seconds, reported success, and left
    the negative features that training needs unwritten. Training then failed on a missing file
    — which was lucky. Had the negatives been present from an earlier attempt, it would have
    trained on 80,300 rows of zeros and reported a model.

    A file of zeros is the worst shape a failure can take: it is present, it is the right size,
    it is the right dtype, and it is meaningless. So presence is not the test. Content is.

    Checked cheaply: ~200 rows sampled across each file rather than reading 2 GB.
    """
    import numpy as np                                                # noqa: PLC0415

    out_dir = REPO / "training" / "output" / "hey_mr_odd_ball"
    problems = []
    for name in FEATURE_FILES:
        path = out_dir / name
        if not path.exists():
            problems.append(f"{name} was never written")
            continue
        try:
            array = np.load(path, mmap_mode="r")
        except Exception as exc:                                      # noqa: BLE001
            problems.append(f"{name} will not load: {type(exc).__name__}: {exc}")
            continue
        if len(array) == 0:
            problems.append(f"{name} is empty")
            continue
        rows = len(array)
        step = max(1, rows // 200)
        empty = not np.any(np.asarray(array[::step]))
        del array                       # release the mmap handle; see clear_stale_features
        gc.collect()
        if empty:
            problems.append(f"{name} is all zeros ({rows:,} rows) — augmentation "
                            f"pre-allocated it and never filled it")
    return problems


def clear_stale_features() -> int:
    """Delete the feature files if the set is not complete and usable. Returns how many went.

    **All or nothing, and that is forced by openWakeWord rather than chosen.**

    The first version of this kept files that were valid and removed only the corrupt ones,
    which is the obvious behaviour and is wrong here. `train.py:755` gates ALL FOUR
    `compute_features_from_generator` calls behind one condition:

        if not os.path.exists(.../"positive_features_train.npy") or args.overwrite:

    So a surviving `positive_features_train.npy` does not save the work it represents — it
    makes augmentation skip the three files that are still missing, "succeed" in twelve
    seconds, and leave training with nothing to read. Keeping it costs more than deleting it.

    Measured: 17 minutes of positive featurisation completed, then `trim_mmap` died on
    WinError 32. Keeping that output looked like saving 17 minutes and actually meant the
    negatives could never be built without also deleting it.
    """
    import numpy as np                                                # noqa: PLC0415

    out_dir = REPO / "training" / "output" / "hey_mr_odd_ball"

    # If every file is present AND usable, there is nothing to do and augmentation will
    # rightly skip itself. Anything less than that means the whole set is rebuilt.
    if not check_features():
        return 0

    removed = 0
    for name in FEATURE_FILES:
        path = out_dir / name
        if not path.exists():
            continue
        bad = True                       # incomplete set: this one goes too, whatever it holds
        # **Close any memmap before unlinking.** np.load(mmap_mode=...) holds an open handle,
        # and Windows refuses to delete an open file — "WinError 32: being used by another
        # process", where the other process is this one. Caught by running it, and it is the
        # same bug openWakeWord's own trim_mmap has.
        gc.collect()
        if bad:
            size = path.stat().st_size / 2**20
            path.unlink()
            print(f"    removed {name} ({size:.0f} MB) — the set is incomplete, and "
                  f"openWakeWord rebuilds all four or none")
            removed += 1
    return removed


def run_all() -> int:
    """generate -> augment -> train, back to back, so the machine can be left alone.

    **Each phase is a SUBPROCESS, not another `runpy` call in this one.** Three reasons, and
    the last is the one that would actually bite:

      * the trainer sets module-level state and rewrites `sys.argv`, so phase two would inherit
        whatever phase one left behind.
      * it calls `sys.exit()` on some paths, which `runpy` propagates as SystemExit — one phase
        deciding to exit would take the other two with it.
      * generation holds several GB of torch and audio buffers. A fresh process per phase hands
        that back to the OS instead of carrying it into training.

    Stops at the first failure. Training on features that were never written would produce a
    model out of nothing and report success, which is the worst available outcome.
    """
    phases = [
        ("generate_clips", "synthesising positives and adversarial negatives"),
        ("augment_clips", "mixing in room noise and reverberation, then featurising"),
        ("train_model", "training, and exporting the ONNX"),
    ]
    rule = "=" * 78
    started = datetime.now()

    for index, (flag, what) in enumerate(phases, 1):
        print("")
        print(rule)
        print(f"  [{index}/{len(phases)}] --{flag}  ({what})")
        print(rule, flush=True)

        # A feature file that is present but full of zeros makes openWakeWord skip the whole
        # augmentation block (train.py:755 gates it on one filename existing), so the phase
        # "succeeds" in twelve seconds and training gets nothing. Clear those first.
        if flag == "augment_clips":
            cleared = clear_stale_features()
            if cleared:
                print(f"    {cleared} unusable feature file(s) cleared before augmenting")

        phase_started = datetime.now()
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), f"--{flag}"],
                                cwd=str(REPO))
        took = datetime.now() - phase_started

        # **Exit code 0 is not proof the phase did its job.** See check_features.
        if flag == "augment_clips" and result.returncode == 0:
            problems = check_features()
            if problems:
                print("")
                print(f"  --augment_clips exited 0 but the features are not usable:")
                for problem in problems:
                    print(f"    - {problem}")
                print("  Refusing to train on them. Re-run --all; the bad files are now gone.")
                return 1

        if result.returncode != 0:
            print("")
            print(f"  --{flag} FAILED after {took} (exit {result.returncode}). Stopping.")
            print("  Nothing after this ran. Fix it and re-run --all: the finished phases skip")
            print("  themselves, so no work is repeated.")
            return result.returncode
        print("")
        print(f"  --{flag} done in {took}")

    print("")
    print(rule)
    print(f"  ALL PHASES DONE in {datetime.now() - started}")
    print(rule)

    produced = sorted((REPO / "training" / "output").rglob("*.onnx"))
    if not produced:
        print("")
        print("  no .onnx found under training/output — check the log above.")
        return 0

    print("")
    print("  models written:")
    for model in produced:
        print(f"    {model.relative_to(REPO).as_posix()}  ({model.stat().st_size / 1024:.0f} KB)")

    # NOT installed on purpose. Swapping the live model in automatically would mean the first
    # evidence it is worse arrives as LB being unable to wake his assistant — and the whole
    # point of training/splits.json is that the comparison happens against clips the model has
    # never seen, before anything ships.
    print("")
    print("  NOT installed. models/hey_mr_odd_ball.onnx is untouched, deliberately.")
    print("  Verify against the frozen split first:")
    print("      python media/scripts/measure_wake_fixtures.py")
    print("      python tools/verify_wake.py")
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
    ap.add_argument("--all", action="store_true",
                    help="run all three phases back to back, stopping at the first failure")
    args = ap.parse_args(argv)

    if args.freeze_background:
        return freeze_background()

    if args.all:
        if not BACKGROUND.exists() or not any(BACKGROUND.glob("*.wav")):
            ap.error("no frozen background set — run --freeze-background first.")
        return run_all()

    if not (args.generate_clips or args.augment_clips or args.train_model):
        ap.error("pick a phase: --generate_clips, --augment_clips, --train_model, or --all")

    if not BACKGROUND.exists() or not any(BACKGROUND.glob("*.wav")):
        ap.error("no frozen background set — run --freeze-background first. Training against "
                 "a live captures/ can hit a half-written WAV hours into the run.")

    from training.oww_compat import (patch, patch_torch_load,          # noqa: PLC0415
                                     patch_torchaudio_load, patch_trim_mmap)

    patch()
    # Needed by --generate_clips: the trainer imports piper-sample-generator, which loads a
    # pickled VITS model that PyTorch 2.6's safe loader refuses. Applied for every phase rather
    # than only generation, because it is scoped to calls that state no opinion and costs
    # nothing when no checkpoint is loaded.
    patch_torch_load()
    # Needed by --augment_clips: torchaudio 2.11 dropped its native backends and routes
    # every load through TorchCodec, whose Windows DLL will not load here. Every file this
    # pipeline reads is 16 kHz mono WAV, which soundfile handles.
    patch_torchaudio_load()
    # Needed by --augment_clips: openWakeWord's own trim_mmap deletes a memmap it still
    # holds open, which POSIX allows and Windows does not. It killed augmentation AFTER
    # all 80,300 positive clips had been featurised.
    patch_trim_mmap()

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
