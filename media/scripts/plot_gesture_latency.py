#!/usr/bin/env python3
"""
Module: plot_gesture_latency.py
Purpose: Chart where the 2.2 seconds of a gesture approval actually goes.
Author: LB
Date:   2026-08-22

    python media/scripts/plot_gesture_latency.py

Reads   media/data/2026-08-22-gesture-approval-breakdown.csv
        media/data/2026-08-22-gesture-approval-latency.csv
Writes  media/charts/gesture-approval-latency.svg

## What this shows

A thumbs-up approval on the Pi costs **2.2 seconds**, and it is not the camera work — it is
paying for a whole Python interpreter and a mediapipe import, once per approval, because
mediapipe 1.x cannot run in the assistant's own 3.13 interpreter (D15). The camera is opened in
a Python 3.12 child process instead, and that child is built and thrown away every time.

Two bars dominate and both are fixable in principle:

- **import mediapipe, 1.0 s** — pure per-process startup. A persistent worker pays it once.
- **4 warmup frames, 0.6 s** — 150 ms per frame, so the webcam is delivering about 6.6 fps
  rather than the 15 it was asked for. The warmup exists because the first frame off a freshly
  opened camera is auto-exposure garbage.

Actual detection — building the graph and running it — is **102 ms of the 2226**. The thing
that looks expensive is not the expensive thing, which is the reason this chart exists.
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BREAKDOWN = REPO / "media" / "data" / "2026-08-22-gesture-approval-breakdown.csv"
TRIALS = REPO / "media" / "data" / "2026-08-22-gesture-approval-latency.csv"
OUT = REPO / "media" / "charts" / "gesture-approval-latency.svg"

# Stages that are real detection work, coloured apart from the overhead they are buried in.
WORK = {"build HandLandmarker", "inference"}

W, H = 900, 470
LEFT, TOP = 330, 70
BAR_H, GAP = 30, 12
PLOT_W = W - LEFT - 130


def read_rows(path: Path) -> list[tuple[str, float]]:
    """(stage, ms) pairs, comments and blanks skipped."""
    rows: list[tuple[str, float]] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or row[0].lstrip('"').startswith("#") or row[0] == "stage":
                continue
            rows.append((row[0], float(row[1])))
    return rows


def read_trials(path: Path) -> list[float]:
    out: list[float] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or row[0].lstrip('"').startswith("#") or row[0] == "trial":
                continue
            out.append(float(row[1]))
    return out


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> int:
    stages = [(n, v) for n, v in read_rows(BREAKDOWN) if v > 0]
    trials = read_trials(TRIALS)
    total = sum(v for _, v in stages)
    scale = PLOT_W / max(v for _, v in stages)

    median = sorted(trials)[len(trials) // 2]
    work_ms = sum(v for n, v in stages if n in WORK)

    p: list[str] = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Segoe UI, sans-serif">')
    p.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    p.append(f'<text x="24" y="30" font-size="17" font-weight="600" fill="#111">'
             f'Gesture approval on the Pi: 2.2 s, and only {work_ms:.0f} ms of it is detection'
             f'</text>')
    p.append(f'<text x="24" y="50" font-size="12" fill="#555">'
             f'Raspberry Pi 5, Debian 13 aarch64 — main venv Python 3.13.5 spawns a Python '
             f'3.12.14 child (mediapipe 0.10.18) per approval. Measured 2026-08-22.</text>')

    y = TOP
    for name, ms in stages:
        is_work = name in WORK
        colour = "#1a7f37" if is_work else "#4facfe"
        width = max(1.0, ms * scale)
        p.append(f'<text x="{LEFT - 12}" y="{y + BAR_H * 0.68}" font-size="12.5" '
                 f'text-anchor="end" fill="#222">{esc(name)}</text>')
        p.append(f'<rect x="{LEFT}" y="{y}" width="{width:.1f}" height="{BAR_H}" '
                 f'fill="{colour}" rx="3"/>')
        p.append(f'<text x="{LEFT + width + 8:.1f}" y="{y + BAR_H * 0.68}" font-size="12.5" '
                 f'fill="#333">{ms:.0f} ms</text>')
        y += BAR_H + GAP

    y += 8
    p.append(f'<line x1="{LEFT}" y1="{y}" x2="{W - 130}" y2="{y}" stroke="#ccc"/>')
    y += 26
    p.append(f'<text x="{LEFT - 12}" y="{y}" font-size="13" font-weight="600" '
             f'text-anchor="end" fill="#111">TOTAL, measured end to end</text>')
    p.append(f'<text x="{LEFT}" y="{y}" font-size="13" font-weight="600" fill="#111">'
             f'{median:.0f} ms median over {len(trials)} trials '
             f'(min {min(trials):.0f}, max {max(trials):.0f})</text>')

    y += 34
    p.append(f'<rect x="{LEFT}" y="{y - 10}" width="13" height="13" fill="#1a7f37" rx="2"/>')
    p.append(f'<text x="{LEFT + 20}" y="{y + 1}" font-size="12" fill="#333">'
             f'actual hand detection</text>')
    p.append(f'<rect x="{LEFT + 165}" y="{y - 10}" width="13" height="13" fill="#4facfe" '
             f'rx="2"/>')
    p.append(f'<text x="{LEFT + 185}" y="{y + 1}" font-size="12" fill="#333">'
             f'process and device overhead, paid every single approval</text>')

    y += 30
    p.append(f'<text x="24" y="{y}" font-size="11.5" fill="#666">'
             f'The two big bars are why: a whole interpreter is started per approval because '
             f'mediapipe 1.x is SIGKILLed in the assistant’s own 3.13 venv (D15), and the '
             f'webcam delivers ~6.6 fps, not the 15 requested.</text>')
    p.append(f'<text x="24" y="{y + 17}" font-size="11.5" fill="#666">'
             f'A persistent worker would pay the 1.0 s import once instead of every time. '
             f'Sum of stages {total:.0f} ms vs {median:.0f} ms wall — the gap is '
             f'subprocess spawn and teardown.</text>')
    p.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(p), encoding="utf-8")
    print(f"  wrote {OUT.relative_to(REPO).as_posix()}")
    print(f"  {len(stages)} stages, {total:.0f} ms summed, {median:.0f} ms median wall")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
