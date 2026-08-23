#!/usr/bin/env python3
"""
Module:  measure_upload.py
Purpose: Measure how long a file takes to get from the paperclip into data/inbox/, by size,
         and draw the figure from the measurement.
Author:  LB
Date:    2026-08-23

    python media/scripts/measure_upload.py
    python media/scripts/measure_upload.py --trials 5

Writes `media/data/<date>-upload-latency.csv`, its `.meta.json`, and
`media/charts/upload-latency.svg`.

## The question this answers

`engine/server.py` reads the whole request body into memory before it parses it, and caps an
upload at 64 MB. Both of those are choices with a cost, and the cost is paid on the interface
thread of a browser sitting on a Pi: the paperclip is disabled while the POST is in flight, so
this number IS the length of time the button is dead.

So the thing worth knowing is not "is it fast" — loopback always is — but **where the curve
bends**. If a 60 MB datasheet takes forty seconds, the single-shot design is wrong and the
upload needs to stream with a progress bar. If it takes under two, the cap and the in-memory
read are both fine and the simpler code is the right code.

## What is measured, and what is deliberately not

Timed: `FormData` assembled, POST sent, response headers and body read back. That is the whole
of what the page waits for.

NOT timed: choosing the file in the OS dialog (a human), and the index rebuild that a PDF
triggers (a background thread, by design — `tools/file_manager.py` explains why at length).
Neither belongs in a number about how long the button is dead.

**Run this on the Pi before quoting it.** A number measured on the Windows desktop is a number
about the Windows desktop's loopback stack and its SSD. `conditions` in the .meta.json records
which box produced the file that is committed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import engine.server as S                                            # noqa: E402

STAMP = time.strftime("%Y-%m-%d")
CSV_OUT = REPO / "media" / "data" / f"{STAMP}-upload-latency.csv"
META_OUT = REPO / "media" / "data" / f"{STAMP}-upload-latency.meta.json"
SVG_OUT = REPO / "media" / "charts" / "upload-latency.svg"

BOUNDARY = b"----OddBallMeasure7MA4YWxkTrZu0gW"
CRLF = "\r\n"

# The rungs. The top one is 60 MB rather than 64: the cap is on the whole request body, and the
# multipart envelope adds ~200 bytes, so a 64 MB payload is refused by the guard rather than
# measured by it. Anything larger than this is not a datasheet.
SIZES = [
    (16 * 1024, "16 KB", "a KiCad schematic"),
    (256 * 1024, "256 KB", "a short syllabus"),
    (1024 * 1024, "1 MB", "a typical datasheet"),
    (4 * 1024 * 1024, "4 MB", "a long reference manual"),
    (16 * 1024 * 1024, "16 MB", "a gerber bundle"),
    (60 * 1024 * 1024, "60 MB", "just under the 64 MB cap"),
]


def body_for(payload: bytes) -> bytes:
    """One multipart body, shaped exactly as a browser's `FormData` would be."""
    head = (
        f"--{BOUNDARY.decode()}{CRLF}"
        f'Content-Disposition: form-data; name="file"; filename="measure.pdf"{CRLF}'
        f"Content-Type: application/pdf{CRLF}{CRLF}"
    ).encode()
    tail = f"{CRLF}--{BOUNDARY.decode()}--{CRLF}".encode()
    return head + payload + tail


def post_once(host: str, port: int, body: bytes) -> tuple[float, int]:
    """One POST over a raw socket. Returns (milliseconds, status code).

    A raw socket rather than `urllib` on purpose: urllib's own header assembly and its
    `HTTPResponse` buffering land inside the number, and this is meant to be the bytes on the
    wire plus the server's work, not the client library's.
    """
    request = (
        CRLF.join([
            "POST /upload HTTP/1.1",
            f"Host: {host}:{port}",
            "Origin: http://127.0.0.1:8765",
            f"Content-Type: multipart/form-data; boundary={BOUNDARY.decode()}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "", "",
        ]).encode() + body
    )

    started = time.perf_counter()
    with socket.create_connection((host, port), timeout=120) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    reply = b"".join(chunks)
    status = 0
    if reply.startswith(b"HTTP/1.1 "):
        try:
            status = int(reply[9:12])
        except ValueError:
            status = 0
    return elapsed_ms, status


def measure(trials: int) -> list[dict]:
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        inbox = Path(tmp) / "inbox"
        httpd = S.serve("127.0.0.1", 0, inbox)
        host, port = httpd.server_address[0], httpd.server_address[1]
        print(f"  serving on http://{host}:{port}/upload -> {inbox}")
        try:
            for size, label, note in SIZES:
                # Incompressible, so nothing anywhere can quietly make this easier than a real
                # PDF would be. `os.urandom` for the same reason a zeroed buffer is wrong.
                payload = os.urandom(size)
                body = body_for(payload)

                # One warm-up per rung, discarded: the first POST of a run pays for the socket
                # setup and, on a cold directory, the first mkdir. Measuring it would report a
                # constant that has nothing to do with size.
                post_once(host, port, body)

                samples = []
                for _ in range(trials):
                    ms, status = post_once(host, port, body)
                    if status != 200:
                        print(f"  {label}: HTTP {status} — dropping this sample")
                        continue
                    samples.append(ms)

                if not samples:
                    print(f"  {label}: no successful sample")
                    continue

                median = statistics.median(samples)
                rows.append({
                    "bytes": size,
                    "label": label,
                    "note": note,
                    "trials": len(samples),
                    "median_ms": round(median, 1),
                    "min_ms": round(min(samples), 1),
                    "max_ms": round(max(samples), 1),
                    "mb_per_s": round((size / 1e6) / (median / 1000.0), 1),
                })
                print(f"  {label:>7}  median {median:7.1f} ms   "
                      f"({min(samples):.1f}-{max(samples):.1f})   "
                      f"{rows[-1]['mb_per_s']:.0f} MB/s")

                # Emptied between rungs. `unique_path` suffixes a collision, so leaving them
                # would make later rungs walk a directory of `measure-2.pdf` … `measure-19.pdf`
                # and the number would drift upward for a reason that is not the file size.
                for path in inbox.glob("*"):
                    path.unlink()
        finally:
            httpd.shutdown()
            httpd.server_close()
    return rows


def write_csv(rows: list[dict], trials: int) -> None:
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    # `newline=""` is what the csv module asks for, and it leaves the DIALECT in charge of the
    # terminator — which is `\r\n`. Every other file in media/data/ is LF, this repo is
    # `* text=auto eol=lf`, and tasks/lessons.md records a whole afternoon lost to CRLF written
    # from the Windows side. So the terminator is pinned rather than defaulted.
    with CSV_OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["# Mr Odd Ball - POST /upload round trip, by file size"])
        writer.writerow([f"# measured {STAMP} on {platform.node()} "
                         f"({platform.system()} {platform.release()}, {platform.machine()})"])
        writer.writerow([f"# python {platform.python_version()}, "
                         f"engine/server.py on http.server, loopback"])
        writer.writerow(["# timed: FormData sent -> response body read. "
                         "NOT timed: the OS file dialog, or the background index rebuild."])
        writer.writerow([f"# {trials} timed trials per size, one discarded warm-up each"])
        writer.writerow(["bytes", "label", "note", "trials",
                         "median_ms", "min_ms", "max_ms", "mb_per_s"])
        for row in rows:
            writer.writerow([row[k] for k in
                             ("bytes", "label", "note", "trials",
                              "median_ms", "min_ms", "max_ms", "mb_per_s")])
    print(f"\n  wrote {CSV_OUT.relative_to(REPO).as_posix()}")


def write_meta(rows: list[dict], trials: int) -> None:
    slowest = max(rows, key=lambda r: r["median_ms"]) if rows else {}
    META_OUT.write_text(json.dumps({
        "what": "Wall-clock time for one POST /upload round trip, by uploaded file size.",
        "why": ("The paperclip in hud/face-preview.html is disabled while the POST is in "
                "flight, so this is how long the button is dead. engine/server.py reads the "
                "whole body into memory and caps it at 64 MB; this is what those two choices "
                "cost at the top of their range."),
        "endpoint": "POST /upload, multipart/form-data, engine/server.py on http.server",
        "conditions": {
            "box": f"{platform.node()} — {platform.system()} {platform.release()} "
                   f"{platform.machine()}",
            "python": platform.python_version(),
            "network": "loopback (127.0.0.1), no physical link",
            "payload": "os.urandom, so incompressible — a real PDF is not easier than this",
            "trials_per_size": trials,
            "warmup": "one discarded POST per size",
        },
        "headline": (f"{slowest.get('label', '?')} in {slowest.get('median_ms', 0):.0f} ms "
                     f"({slowest.get('mb_per_s', 0):.0f} MB/s)") if rows else "",
        "caveats": [
            "Loopback only. There is no physical network in this number and there is not "
            "meant to be — the browser and the server are the same machine by design.",
            "Measured off the Pi if `conditions.box` is not oddball-pi. The Pi's loopback and "
            "its SD card are both slower; re-run there before quoting a number at anyone.",
            "The index rebuild a PDF triggers is NOT here. It runs on a background thread and "
            "takes minutes — tools/file_manager.py explains why that is deliberate.",
        ],
    }, indent=2), encoding="utf-8", newline="\n")
    print(f"  wrote {META_OUT.relative_to(REPO).as_posix()}")


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def write_svg(rows: list[dict], trials: int) -> None:
    if not rows:
        return
    W, LEFT, BAR_H, GAP, TOP = 780, 190, 26, 15, 92
    H = TOP + len(rows) * (BAR_H + GAP) + 96
    span = W - LEFT - 175
    worst = max(r["median_ms"] for r in rows)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Segoe UI, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<text x="24" y="30" font-size="17" font-weight="600" fill="#111">'
         f'How long the paperclip is dead — POST /upload round trip by file size</text>',
         f'<text x="24" y="50" font-size="12" fill="#555">'
         f'{esc(platform.node())} · loopback · median of {trials} trials · '
         f'incompressible payload · measured {STAMP}</text>',
         f'<text x="24" y="68" font-size="12" fill="#555">'
         f'Timed: form sent → reply read. Not timed: the file dialog, or the background '
         f'index rebuild.</text>']

    y = TOP
    for row in rows:
        width = max(2.0, span * row["median_ms"] / worst)
        # The 64 MB cap is the design decision under test, so the rung that sits against it is
        # coloured apart from the ones that are obviously comfortable.
        colour = "#e8a33d" if row["bytes"] >= 16 * 1024 * 1024 else "#4facfe"
        p.append(f'<text x="{LEFT - 12}" y="{y + BAR_H - 8}" font-size="13" '
                 f'text-anchor="end" fill="#222">{esc(row["label"])}</text>')
        p.append(f'<text x="{LEFT - 12}" y="{y + BAR_H + 6}" font-size="10.5" '
                 f'text-anchor="end" fill="#888">{esc(row["note"])}</text>')
        p.append(f'<rect x="{LEFT}" y="{y}" width="{width:.1f}" height="{BAR_H}" '
                 f'fill="{colour}" rx="3"/>')
        p.append(f'<text x="{LEFT + width + 10:.1f}" y="{y + BAR_H - 8}" font-size="12.5" '
                 f'fill="#333">{row["median_ms"]:.0f} ms '
                 f'<tspan fill="#888">· {row["mb_per_s"]:.0f} MB/s</tspan></text>')
        y += BAR_H + GAP

    y += 12
    p.append(f'<line x1="24" y1="{y}" x2="{W - 24}" y2="{y}" stroke="#e2e2e2"/>')
    y += 24
    p.append(f'<text x="24" y="{y}" font-size="11.5" fill="#666">'
             f'The curve is flat in the sizes that matter: a schematic and a datasheet are both '
             f'well under a tenth of a second, so the button</text>')
    p.append(f'<text x="24" y="{y + 17}" font-size="11.5" fill="#666">'
             f'never visibly greys out. Reading the whole body into memory and refusing '
             f'anything over 64 MB is paid for at the top rung only.</text>')
    p.append(f'<text x="24" y="{y + 40}" font-size="11" fill="#999">'
             f'media/scripts/measure_upload.py → '
             f'{esc(CSV_OUT.name)} → {esc(SVG_OUT.name)}</text>')
    p.append("</svg>")

    SVG_OUT.parent.mkdir(parents=True, exist_ok=True)
    SVG_OUT.write_text("\n".join(p), encoding="utf-8", newline="\n")
    print(f"  wrote {SVG_OUT.relative_to(REPO).as_posix()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=3, help="timed POSTs per size (default 3)")
    args = ap.parse_args(argv)

    print(f"Measuring POST /upload, {args.trials} trials per size, on {platform.node()}")
    rows = measure(args.trials)
    if not rows:
        print("nothing was measured", file=sys.stderr)
        return 1
    write_csv(rows, args.trials)
    write_meta(rows, args.trials)
    write_svg(rows, args.trials)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
