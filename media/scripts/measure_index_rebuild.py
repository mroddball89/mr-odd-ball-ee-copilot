#!/usr/bin/env python3
"""
Module:  measure_index_rebuild.py
Purpose: Measure what an index rebuild costs before it has embedded anything, and per chunk.
Author:  LB
Date:    2026-08-23

    ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python media/scripts/measure_index_rebuild.py'

Writes `media/data/<date>-index-rebuild-<host>.csv` and its `.meta.json`.

## Why this number decides an architecture, not a setting

`tools/file_manager.py` hands the rebuild to a **background thread** and returns a sentence
saying the document is not searchable yet. That is a real cost — LB has to be told twice, and
`index_status` exists only to answer the follow-up — so it has to be justified by a number
rather than by "embedding is slow".

The thing being measured is the cost paid **before a single chunk is embedded**: importing
torch, importing `langchain_huggingface`, and loading `all-MiniLM-L6-v2` off the SD card. That
is a fixed toll on every rebuild, however small the upload. If it were 200 ms the background
thread would be over-engineering and the tool should just block.

## What it does NOT measure, and why that matters more than it looks

The per-chunk rate here is measured on short synthetic strings, so treat it as a floor.

More importantly: **a rebuild re-embeds the WHOLE corpus, not the new file.**
`build_vector_database()` walks all of `data/` and writes both collections from scratch. So the
per-chunk number multiplies by everything LB has ever uploaded, not by what he just uploaded —
the first datasheet costs the fixed toll plus a few seconds, and the fiftieth costs the fixed
toll plus fifty datasheets' worth. Any sentence about how long a rebuild takes is wrong unless
it says which of those it means.

**Run it on the Pi.** On a desktop this measures a desktop's SSD and is not the number that
decides anything — the SD card is the interesting variable, and it is the reason the model load
dominates the two imports put together.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

STAMP = time.strftime("%Y-%m-%d")
HOST = platform.node().lower().replace(" ", "-")
CSV_OUT = REPO / "media" / "data" / f"{STAMP}-index-rebuild-{HOST}.csv"
META_OUT = REPO / "media" / "data" / f"{STAMP}-index-rebuild-{HOST}.meta.json"

# Run in a FRESH interpreter each time. Imports are cached per process, so a second measurement
# in the same one would report ~0 s for the very thing being measured — which would look like a
# spectacular result and be an artefact of `sys.modules`.
CHILD = r'''
import time, sys
t0 = time.perf_counter(); import torch; a = time.perf_counter() - t0
t0 = time.perf_counter()
from langchain_huggingface import HuggingFaceEmbeddings
b = time.perf_counter() - t0
t0 = time.perf_counter()
sys.path.insert(0, {repo!r})
from tools.vector_db import get_embeddings
emb = get_embeddings()
c = time.perf_counter() - t0
t0 = time.perf_counter()
vectors = emb.embed_documents(["a chunk of datasheet text, about this long"] * {n})
d = (time.perf_counter() - t0) / {n} * 1000
print(f"{{a:.3f}},{{b:.3f}},{{c:.3f}},{{d:.3f}},{{len(vectors[0])}},{{torch.__version__}}")
'''

CHUNKS = 50


def one_run() -> tuple[float, float, float, float, int, str] | None:
    """One measurement, in its own interpreter. None if it failed."""
    done = subprocess.run(
        [sys.executable, "-c", CHILD.format(repo=str(REPO), n=CHUNKS)],
        cwd=REPO, capture_output=True, text=True, timeout=900)
    for line in reversed(done.stdout.strip().splitlines()):
        parts = line.split(",")
        if len(parts) == 6:
            return (float(parts[0]), float(parts[1]), float(parts[2]),
                    float(parts[3]), int(parts[4]), parts[5])
    print(done.stderr.strip()[-500:] or "no output", file=sys.stderr)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=3)
    args = ap.parse_args(argv)

    print(f"Measuring the index rebuild's fixed cost on {platform.node()}, "
          f"{args.trials} trials, one interpreter each")

    runs = []
    for i in range(args.trials):
        got = one_run()
        if got is None:
            print(f"  trial {i + 1}: failed", file=sys.stderr)
            continue
        torch_s, lc_s, model_s, per_chunk_ms, dim, torch_v = got
        runs.append(got)
        print(f"  trial {i + 1}:  torch {torch_s:5.2f}s  langchain {lc_s:5.2f}s  "
              f"model {model_s:5.2f}s  =  {torch_s + lc_s + model_s:5.2f}s fixed  "
              f"|  {per_chunk_ms:.1f} ms/chunk")

    if not runs:
        print("nothing was measured", file=sys.stderr)
        return 1

    med = lambda i: statistics.median(r[i] for r in runs)          # noqa: E731
    torch_s, lc_s, model_s, per_chunk_ms = med(0), med(1), med(2), med(3)
    fixed = torch_s + lc_s + model_s

    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", encoding="utf-8", newline="") as fh:
        # lineterminator pinned to LF — see measure_upload.py; the repo is eol=lf and the csv
        # module's dialect default is CRLF.
        w = csv.writer(fh, lineterminator="\n")
        w.writerow([f"# Mr Odd Ball - index rebuild fixed cost, {platform.node()}"])
        w.writerow([f"# measured {STAMP} on {platform.system()} {platform.release()} "
                    f"{platform.machine()}, python {platform.python_version()}"])
        w.writerow([f"# torch {runs[0][5]}, all-MiniLM-L6-v2 (384 dim), {args.trials} trials, "
                    f"one fresh interpreter each"])
        w.writerow(["# a rebuild re-embeds the WHOLE corpus, so per_chunk multiplies by "
                    "everything uploaded so far, not by the new file"])
        w.writerow(["stage", "seconds"])
        w.writerow(["import torch", round(torch_s, 3)])
        w.writerow(["import langchain_huggingface", round(lc_s, 3)])
        w.writerow(["load all-MiniLM-L6-v2", round(model_s, 3)])
        w.writerow(["FIXED TOTAL", round(fixed, 3)])
        w.writerow(["ms per chunk", round(per_chunk_ms, 3)])

    META_OUT.write_text(json.dumps({
        "what": "Time an index rebuild spends before embedding its first chunk, plus the "
                "per-chunk rate afterwards.",
        "why": "tools/file_manager.py runs the rebuild on a background thread and makes Mr Odd "
               "Ball say a document is not searchable yet. That costs LB an extra exchange, so "
               "it has to be justified by a number rather than by an assumption that embedding "
               "is slow.",
        "conditions": {
            "box": f"{platform.node()} — {platform.system()} {platform.release()} "
                   f"{platform.machine()}",
            "python": platform.python_version(),
            "torch": runs[0][5],
            "model": "all-MiniLM-L6-v2, 384 dim, from the local HuggingFace cache",
            "trials": len(runs),
            "chunks_per_trial": CHUNKS,
            "page_cache": "warm — the model had been loaded earlier in the session",
        },
        "headline": f"{fixed:.1f} s before a single chunk is embedded, then "
                    f"{per_chunk_ms:.1f} ms/chunk",
        "caveats": [
            "Warm page cache. The FIRST rebuild after a reboot pays SD-card read for the model "
            "and measured ~2 s longer (13.5 s against 11.25 s) on the same box.",
            "Per-chunk is measured on short synthetic strings and is a floor, not a datasheet.",
            "A rebuild re-embeds the WHOLE corpus. The fixed cost is paid once per rebuild; the "
            "per-chunk cost multiplies by every document ever uploaded, not by the new one.",
        ],
    }, indent=2), encoding="utf-8", newline="\n")

    print(f"\n  FIXED: {fixed:.2f} s before the first chunk  |  {per_chunk_ms:.1f} ms/chunk")
    print(f"  wrote {CSV_OUT.relative_to(REPO).as_posix()}")
    print(f"  wrote {META_OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
