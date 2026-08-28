#!/usr/bin/env python3
"""
Module:  plot_stt_models.py
Purpose: The figure for the STT model choice — accuracy against latency, on LB's own voice.
Author:  LB
Date:    2026-08-28

    python media/scripts/plot_stt_models.py

Reads  media/data/2026-08-28-stt-tiny-vs-base.csv  (media/scripts/measure_stt_models.py)
Writes media/charts/2026-08-28-stt-models.svg

SVG by hand, like every other chart here, so it regenerates on the machine that produced it.

## What the figure has to show

**The headline is one number: `tiny.en` transcribes "sync my schedule" correctly 0 times out
of 6.** Everything else is context for that. LB said it six times across two days and was
misheard every time — "sink my schedule", "sick mass schedule", "i think my schedule" — and
none of those spellings is in `orchestrator/route_hint._SYNC`, so none of them reached ACADEMIC.

So the left panel is that one comparison, big. The right panel is the trade it costs, and it
has to be honest in both directions: `base.en` and `small.en` each buy accuracy and each
introduce a false route — a mumbled "So." transcribed as "That's all.", which
`instant._is_dismissal` matches and which ends the conversation.

The latency panel carries the Pi's old figures as a dashed reference, because the whole reason
this measurement exists is that the model was chosen against them and the hardware changed.
"""

from __future__ import annotations

import collections
import csv
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "media" / "data" / "2026-08-28-stt-tiny-vs-base.csv"
OUT = REPO / "media" / "charts" / "2026-08-28-stt-models.svg"

W, H = 1040, 620
INK, MUTED, GRID = "#1B1B1B", "#666666", "#E3E3E3"
BAD, WARN, GOOD = "#C0504D", "#E8A33D", "#4F8A5B"

ORDER = ("tiny.en", "base.en", "small.en")

# What the Pi measured, from config/oddball.toml [stt]. Cited, not re-measured — the Pi is gone.
PI_SECONDS = {"tiny.en": 1.225, "base.en": 2.26}      # midpoints of 1.05-1.40 and 2.03-2.49

MUST_NOT_ROUTE = {"165632_so-", "165705_cool-", "162259_available-", "084910_elbow-",
                  "165715_to-meet-them-", "170750_i-ll-see-you-in-the-next-video-",
                  "181033_we-ll-see-you-in-the-next-one-"}


def esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def load():
    if not DATA.exists():
        sys.exit(f"  no data at {DATA}\n  run: python media/scripts/measure_stt_models.py")
    rows = list(csv.DictReader(open(DATA, encoding="utf-8")))
    by = collections.defaultdict(list)
    for r in rows:
        by[r["model"]].append(r)
    return by


def stats(rows: list[dict]) -> dict:
    judged = [r for r in rows if r["expected"]]
    sched = [r for r in rows if r["expected"] == "academic"]
    noise = [r for r in rows if r["clip"] in MUST_NOT_ROUTE]
    return {
        "right": sum(r["correct"] == "yes" for r in judged),
        "judged": len(judged),
        "sync": sum(r["correct"] == "yes" for r in sched),
        "sync_of": len(sched),
        "false": sum(1 for r in noise if r["free_destination"]),
        "noise": len(noise),
        "median": statistics.median(float(r["seconds"]) for r in rows),
        "worst": max(float(r["seconds"]) for r in rows),
    }


def build(by) -> str:
    models = [m for m in ORDER if m in by]
    s = {m: stats(by[m]) for m in models}

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>',
         f'<text x="40" y="46" font-size="23" font-weight="600" fill="{INK}">'
         f'Which Whisper model, on LB&#8217;s own voice, on the workstation</text>',
         f'<text x="40" y="72" font-size="14" fill="{MUTED}">'
         f'27 real recordings from captures/, int8, 4 threads, Ryzen 7 5700X, 2026-08-28. '
         f'Scored on where the transcript ROUTES, which needs no ground truth.</text>']

    # ---- left: the headline -----------------------------------------------------------
    x0, y0, ph, gap, bw = 60, 152, 250, 108, 40
    o.append(f'<text x="{x0}" y="{y0 - 20}" font-size="15" font-weight="600" fill="{INK}">'
             f'&#8220;sync my schedule&#8221;, said 6 times &#8212; how many reached ACADEMIC'
             f'</text>')
    per = ph / 6.0
    for tick in range(7):
        y = y0 + ph - tick * per
        o.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + len(models)*gap}" y2="{y:.1f}" '
                 f'stroke="{GRID}"/>')
        o.append(f'<text x="{x0-10}" y="{y+4:.1f}" font-size="12" fill="{MUTED}" '
                 f'text-anchor="end">{tick}</text>')

    for i, m in enumerate(models):
        cx = x0 + i * gap + gap / 2
        n = s[m]["sync"]
        h = max(n * per, 3)
        col = BAD if n == 0 else (WARN if n < 4 else GOOD)
        o.append(f'<rect x="{cx-bw/2:.1f}" y="{y0+ph-h:.1f}" width="{bw}" height="{h:.1f}" '
                 f'fill="{col}"/>')
        o.append(f'<text x="{cx:.1f}" y="{y0+ph-h-9:.1f}" font-size="17" font-weight="600" '
                 f'fill="{col}" text-anchor="middle">{n}/6</text>')
        o.append(f'<text x="{cx:.1f}" y="{y0+ph+22:.1f}" font-size="13" fill="{INK}" '
                 f'text-anchor="middle">{esc(m)}</text>')
    o.append(f'<line x1="{x0}" y1="{y0+ph}" x2="{x0+len(models)*gap}" y2="{y0+ph}" '
             f'stroke="{INK}"/>')
    o.append(f'<text x="{x0}" y="{y0+ph+50}" font-size="12.5" fill="{BAD}">'
             f'tiny.en &#8212; the shipping default &#8212; got it right none of the six times.'
             f'</text>')

    # ---- right: the trade --------------------------------------------------------------
    rx = x0 + len(models) * gap + 96
    o.append(f'<text x="{rx}" y="{y0 - 20}" font-size="15" font-weight="600" fill="{INK}">'
             f'What each one costs</text>')
    scale = 190 / max(max(s[m]["worst"] for m in models), 2.5)
    row_h = ph / len(models)
    for i, m in enumerate(models):
        y = y0 + i * row_h + 26
        med, worst = s[m]["median"], s[m]["worst"]
        o.append(f'<text x="{rx}" y="{y-12:.1f}" font-size="13" font-weight="600" '
                 f'fill="{INK}">{esc(m)}</text>')
        o.append(f'<rect x="{rx}" y="{y-4:.1f}" width="{max(med*scale,2):.1f}" height="13" '
                 f'fill="{GOOD if med < 1.0 else WARN}"/>')
        o.append(f'<rect x="{rx+med*scale:.1f}" y="{y-4:.1f}" '
                 f'width="{max((worst-med)*scale,1):.1f}" height="13" fill="{GRID}"/>')
        o.append(f'<text x="{rx+worst*scale+8:.1f}" y="{y+7:.1f}" font-size="12" '
                 f'fill="{INK}">{med:.2f}s median, {worst:.2f}s worst</text>')
        note = (f'{s[m]["right"]}/{s[m]["judged"]} routed right, '
                f'{s[m]["false"]} false route' + ('' if s[m]["false"] == 1 else 's'))
        o.append(f'<text x="{rx}" y="{y+26:.1f}" font-size="12" '
                 f'fill="{BAD if s[m]["false"] else MUTED}">{note}</text>')
        if m in PI_SECONDS:
            px = rx + PI_SECONDS[m] * scale
            o.append(f'<line x1="{px:.1f}" y1="{y-10:.1f}" x2="{px:.1f}" y2="{y+15:.1f}" '
                     f'stroke="{MUTED}" stroke-dasharray="3 3"/>')
    o.append(f'<text x="{rx}" y="{y0+ph+34}" font-size="11.5" fill="{MUTED}">'
             f'Dashed marks = what the SAME model measured on the Pi, from config/oddball.toml.'
             f'</text>')
    o.append(f'<text x="{rx}" y="{y0+ph+52}" font-size="11.5" fill="{MUTED}">'
             f'base.en here is faster than tiny.en was there.</text>')

    # ---- the sentence to leave with -----------------------------------------------------
    o.append(f'<text x="40" y="{H-56}" font-size="14" fill="{INK}">'
             f'The model was chosen on the Pi, where tiny.en was the only one that fit the '
             f'2-second turn. On this box all three fit, and</text>')
    o.append(f'<text x="40" y="{H-36}" font-size="14" fill="{INK}">'
             f'tiny.en is the only one that never hears &#8220;sync my schedule&#8221;. '
             f'The accuracy is bought with a false dismissal: a mumbled &#8220;So.&#8221; '
             f'becomes &#8220;That&#8217;s all.&#8221;</text>')
    o.append("</svg>")
    return "\n".join(o)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(load()), encoding="utf-8")
    print(f"  wrote {OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
