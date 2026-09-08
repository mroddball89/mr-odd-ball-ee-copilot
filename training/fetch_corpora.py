#!/usr/bin/env python3
"""
Module:  fetch_corpora.py
Purpose: Download the training corpora under a hard storage budget. Nothing else touches disk.
Author:  LB
Date:    2026-09-08

    python training/fetch_corpora.py --plan            # print the budget, download nothing
    python training/fetch_corpora.py                   # fetch, default budget 1.6 GB
    python training/fetch_corpora.py --budget-gb 1.0   # smaller slice

## Why this file exists at all

openWakeWord's published training recipe downloads ~16 GB of precomputed negative features plus
several GB of background audio corpora (AudioSet, FMA). LB capped the whole thing at **2 GB**.

The interesting part is that the cap is affordable, and the reason is worth writing down:
the 16 GB file is not a corpus, it is a **feature matrix** — 5,625,000 rows of (16, 96) float16,
one row per ~1.4s window of negative audio. Rows are independent training examples of fixed
size, laid out contiguously. So a prefix of the file is a valid smaller training set, and an
HTTP range request maps exactly onto a row range.

    header      128 bytes
    row          16 x 96 x 2 = 3,072 bytes
    5,625,000 rows                   16.09 GB   ~2000 hours
    454,382 rows (1.3 GB)             8.1%      ~162 hours

**This is a sample, not a summary.** Every row we take is a real, full-fidelity negative
example; we simply take fewer of them. The one assumption is that the corpus is not ordered in
a way that makes a prefix unrepresentative — ACAV100M is a web-scraped set and the file is
shipped as a single shuffled matrix, but this is stated as an assumption because this script
cannot verify it. If the trained model turns out to have a blind spot for one KIND of
background, that assumption is the first thing to doubt.

## What the 2 GB buys, and what it does NOT

Under budget, from the network:

    ACAV100M feature slice   ~1.3 GB    negative TRAINING examples (features, no audio)
    validation set features   176 MB    held-out negatives, for false-positives-per-hour
    MIT room impulse responses  8 MB    270 RIRs, for reverberation augmentation
                              -------
                              ~1.5 GB

Already on disk, costing nothing further:

    piper generator            195 MB   synthetic positive speech
    captures/                   36 MB   20 min of LB's real room, as background noise
    tests/fixtures/wake         17 MB   his real calls and hard negatives

**NOT downloaded: AudioSet and FMA.** Those are the multi-GB background-audio corpora the
standard recipe mixes into the positives. Their job is replaced by `captures/` — see
`background_paths` in `hey_mr_odd_ball.yaml`, which also states the risk that swap carries.

## The budget this file does NOT control

Generation is transient and much larger: 40,000 synthetic positives at ~2s of 16 kHz 16-bit
mono is ~2.5 GB of WAV on disk before it is turned into ~123 MB of features. That is working
space during Step 5, not corpus storage, and it is freed afterwards. `--budget-gb` here covers
the permanent download only; the generation step has its own batching.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPORA = REPO / "training" / "corpora"

HF = "https://huggingface.co/datasets"
FEATURES_URL = (f"{HF}/davidscripka/openwakeword_features/resolve/main/"
                "openwakeword_features_ACAV100M_2000_hrs_16bit.npy")
VALIDATION_URL = (f"{HF}/davidscripka/openwakeword_features/resolve/main/"
                  "validation_set_features.npy")
RIR_TREE = ("https://huggingface.co/api/datasets/"
            "davidscripka/MIT_environmental_impulse_responses/tree/main/16khz?limit=1000")
RIR_FILE = (f"{HF}/davidscripka/MIT_environmental_impulse_responses/resolve/main/16khz/")

# The default slice. 1.3 GB leaves room for the 176 MB validation set and the 8 MB of impulse
# responses inside a 1.6 GB budget, which sits under LB's 2 GB cap with the piper generator
# (195 MB, already downloaded) counted in.
DEFAULT_BUDGET_GB = 1.6

CHUNK = 1 << 20


def _get(url: str, headers: dict | None = None) -> urllib.request.addinfourl:
    request = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(request, timeout=60)


def read_npy_header(url: str) -> tuple[tuple, str, int, int]:
    """Read a remote .npy's header with one range request.

    Returns (shape, dtype string, bytes per row, offset where the data starts).
    """
    with _get(url, {"Range": "bytes=0-255"}) as response:
        raw = response.read(256)
    if raw[:6] != b"\x93NUMPY":
        raise ValueError(f"not a .npy file: {url}")
    major = raw[6]
    if major == 1:
        header_len = int.from_bytes(raw[8:10], "little")
        offset = 10
    else:
        header_len = int.from_bytes(raw[8:12], "little")
        offset = 12
    header = ast.literal_eval(raw[offset:offset + header_len].decode("latin1").strip())
    if header.get("fortran_order"):
        # A column-major file cannot be sliced by rows with a byte range, which is the entire
        # trick this script depends on. Refused loudly rather than silently mis-sliced.
        raise ValueError("the feature file is fortran_order; a row slice is not a byte slice")

    import numpy as np                                                # noqa: PLC0415

    shape = header["shape"]
    row_bytes = int(np.dtype(header["descr"]).itemsize)
    for dim in shape[1:]:
        row_bytes *= int(dim)
    return shape, header["descr"], row_bytes, offset + header_len


def fetch_slice(url: str, out: Path, rows: int, shape: tuple, descr: str,
                row_bytes: int, data_start: int) -> None:
    """Download the first `rows` rows and write them as a standalone .npy."""
    import numpy as np                                                # noqa: PLC0415

    out.parent.mkdir(parents=True, exist_ok=True)
    want = rows * row_bytes
    last = data_start + want - 1
    print(f"    {rows:,} rows = {want / 2**30:.2f} GB")

    # Written through numpy so the header is correct for the NEW shape. Copying the original
    # header would claim 5,625,000 rows over a file holding a fraction of them, and every
    # reader would run off the end.
    array = np.lib.format.open_memmap(
        out, mode="w+", dtype=np.dtype(descr), shape=(rows,) + tuple(shape[1:]))
    view = array.reshape(-1).view(np.uint8)

    written = 0
    with _get(url, {"Range": f"bytes={data_start}-{last}"}) as response:
        while written < want:
            block = response.read(min(CHUNK, want - written))
            if not block:
                break
            view[written:written + len(block)] = np.frombuffer(block, dtype=np.uint8)
            written += len(block)
            if written % (64 << 20) < CHUNK:
                print(f"      {written / 2**30:5.2f} / {want / 2**30:.2f} GB", flush=True)
    array.flush()
    del array

    if written != want:
        out.unlink(missing_ok=True)
        raise IOError(f"expected {want} bytes, got {written} — the slice was not written")
    print(f"    wrote {out.relative_to(REPO).as_posix()}")


def fetch_whole(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with _get(url) as response, out.open("wb") as handle:
        shutil.copyfileobj(response, handle, CHUNK)
    print(f"    wrote {out.relative_to(REPO).as_posix()} "
          f"({out.stat().st_size / 2**20:.0f} MB)")


def fetch_rirs(out_dir: Path) -> None:
    """Fetch the impulse responses, retrying each, and CHECK the total against the listing.

    The count check is the point. Reading this directory mid-run showed 61 of 270, which is
    indistinguishable from a silent partial download — and the original loop had no retry and
    no final tally, so a quarter of the reverberation augmentation could have gone missing
    with nothing anywhere saying so. Reverb is the cheapest way to buy across-the-room
    performance, and across-the-room is a measured failure in this model.
    """
    import json                                                       # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    with _get(RIR_TREE) as response:
        entries = json.load(response)
    wavs = [e["path"].rsplit("/", 1)[-1] for e in entries if e["path"].endswith(".wav")]
    print(f"    {len(wavs)} impulse responses listed")

    failed = []
    for index, name in enumerate(wavs, 1):
        target = out_dir / name
        if target.exists() and target.stat().st_size > 0:
            continue
        for attempt in range(3):
            try:
                with _get(RIR_FILE + name) as response, target.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
                break
            except Exception as exc:                                  # noqa: BLE001
                if attempt == 2:
                    failed.append((name, f"{type(exc).__name__}: {exc}"))
                    target.unlink(missing_ok=True)
        if index % 60 == 0:
            print(f"      {index}/{len(wavs)}", flush=True)

    have = len(list(out_dir.glob("*.wav")))
    if have != len(wavs):
        detail = f"; first failure: {failed[0][0]} - {failed[0][1]}" if failed else ""
        raise IOError(f"got {have} of {len(wavs)} impulse responses" + detail)
    print(f"    {have}/{len(wavs)} present")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fetch training corpora under a storage budget")
    ap.add_argument("--budget-gb", type=float, default=DEFAULT_BUDGET_GB,
                    help=f"total download budget in GB (default {DEFAULT_BUDGET_GB})")
    ap.add_argument("--plan", action="store_true",
                    help="print what would be downloaded and stop")
    args = ap.parse_args(argv)

    print(f"\n  budget {args.budget_gb:.2f} GB -> {CORPORA.relative_to(REPO).as_posix()}\n")

    print("  reading the remote feature-file header...")
    shape, descr, row_bytes, data_start = read_npy_header(FEATURES_URL)
    total_rows = shape[0]
    total_gb = total_rows * row_bytes / 2**30
    print(f"    full file: {total_rows:,} rows x {row_bytes:,} B = {total_gb:.2f} GB "
          f"(~2000 hours)")

    # The validation set and the impulse responses are small and fixed; the slice takes
    # whatever is left. Reserved rather than subtracted afterwards, so a tight budget shrinks
    # the slice instead of silently skipping the validation set.
    reserved_gb = (176 + 8) / 1024
    slice_gb = max(0.0, args.budget_gb - reserved_gb)
    rows = int(slice_gb * 2**30 // row_bytes)
    if rows <= 0:
        print(f"\n  budget too small: {reserved_gb:.2f} GB is needed for the validation set "
              f"and impulse responses alone.\n")
        return 1

    print(f"\n  plan:")
    print(f"    ACAV100M slice        {rows:,} rows  {rows * row_bytes / 2**30:5.2f} GB  "
          f"({rows / total_rows * 100:.1f}% of the corpus, ~{2000 * rows / total_rows:.0f} hrs)")
    print(f"    validation features                   0.17 GB")
    print(f"    MIT impulse responses                 0.01 GB")
    print(f"    ------------------------------------------")
    print(f"    total                                {(rows * row_bytes / 2**30) + reserved_gb:5.2f} GB")
    print(f"\n    NOT downloaded: AudioSet, FMA. `captures/` stands in as background audio.")

    if args.plan:
        print("\n  --plan: nothing downloaded.\n")
        return 0

    # Every step below SKIPS work already done. The reason to re-run this script is that
    # something failed partway, and a script that answers a failed 8 MB download by re-fetching
    # 1.42 GB is one nobody will re-run. Measured, twice, because this guard was not here.
    print("\n  1/3 feature slice")
    slice_path = CORPORA / "acav100m_slice.npy"
    want_bytes = 128 + rows * row_bytes
    if slice_path.exists() and abs(slice_path.stat().st_size - want_bytes) <= 128:
        print(f"    already have {slice_path.stat().st_size / 2**30:.2f} GB - skipping")
    else:
        fetch_slice(FEATURES_URL, slice_path, rows, shape, descr, row_bytes, data_start)

    print("  2/3 validation features")
    val_path = CORPORA / "validation_set_features.npy"
    if val_path.exists() and val_path.stat().st_size > (100 << 20):
        print(f"    already have {val_path.stat().st_size / 2**20:.0f} MB - skipping")
    else:
        fetch_whole(VALIDATION_URL, val_path)
    print("  3/3 impulse responses")
    fetch_rirs(CORPORA / "mit_rirs")

    used = sum(f.stat().st_size for f in CORPORA.rglob("*") if f.is_file())
    print(f"\n  done — {used / 2**30:.2f} GB in {CORPORA.relative_to(REPO).as_posix()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
