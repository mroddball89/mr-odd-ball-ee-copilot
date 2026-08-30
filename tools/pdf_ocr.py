#!/usr/bin/env python3
"""
Module:  pdf_ocr.py
Purpose: Read the PDFs that carry no text, so an image-only schematic is searchable.
Author:  LB
Date:    2026-08-29

    python tools/pdf_ocr.py data/projects/ESP32/esp32devkitv1_schematics.pdf
    python tools/pdf_ocr.py --scan            # every PDF under data/, reporting which need OCR
    python tools/pdf_ocr.py --check           # is the engine installed and does it load?

## The problem this exists for, measured

`tools/vector_db.py` has always REPORTED image-only PDFs rather than hiding them:

    datasheets: 3 page(s) carried NO extractable text - esp32devkitv1_schematics.pdf,
                pi_cam3.pdf, pi_cam3_noir_wide.pdf

That was the honest thing to print and it was as far as it went. On 2026-08-29 LB uploaded
`esp32devkitv1_schematics.pdf`, was told "its text is being indexed in the background", and
the file contributed **zero chunks** — the sentence was true about the intent and false about
the outcome. Ask him about that board's pinout and the store has nothing to retrieve.

## Rendering the PAGE, not extracting its images

The obvious approach is `pypdf`'s `page.images`, and it is wrong here. That ESP32 schematic
holds **368 embedded JPEG2000 tiles**: the drawing was exported as fragments, and a net label
is routinely split across two of them. OCR'ing tiles one at a time reads half a word at a
time.

So the page is RASTERISED whole and read once. `pypdfium2` does the rendering — permissively
licensed, a self-contained wheel, no system binary to install (which is what rules out
`pytesseract`, whose engine is a separate download, and `pdf2image`, which needs poppler).

`rapidocr-onnxruntime` does the reading, and it is the natural fit rather than a new
dependency class: this repo already runs `onnxruntime` for the wake word and Piper, so the
OCR models sit on a runtime that is present and warm.

## The DPI is measured, and the measurement is a correction

Received wisdom is that OCR of small schematic text wants high DPI. Swept on the two real
files, `media/data/2026-08-29-ocr-dpi-sweep.csv`:

    esp32devkitv1_schematics   150dpi  404 chars    200dpi  432    300dpi  431    400dpi  428
    pi_cam3                    150dpi 2023 chars    200dpi 2062    300dpi 2069    400dpi 2078

**Above 150 it is flat.** RapidOCR resizes internally to its detector's input size, so paying
for a 400 dpi raster buys a bigger intermediate and the same characters. 200 is the peak on
the schematic and within 1% of the peak on the datasheet, so 200 it is — chosen off the sweep,
not off a blog post.

## Why the cache is not optional

`tools/file_manager.py` rebuilds the whole vector store after **every** upload. The 2026-08-29
log shows three rebuilds in four minutes. At 2-6 seconds a page, re-reading every image-only
PDF on every rebuild would put OCR on the interactive path and make each upload slower than
the last as the corpus grows.

So a page is read once and the text is kept in `data/.ocr_cache/`, keyed by the file's path,
size and mtime. Touch the PDF and it is read again; leave it alone and it is free forever.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger("oddball.ocr")

__all__ = ["ocr_pdf", "ocr_available", "enabled", "CACHE_DIR", "RENDER_DPI"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / ".ocr_cache"

# From the sweep in the header. Not a round number picked for looking sensible — the number
# where the curve goes flat.
RENDER_DPI = 200

# A page that OCRs to less than this is noise: a border, a logo, a page number. Treating it as
# content puts a chunk in the store whose only effect is to be retrieved instead of something
# useful. The ESP32 schematic yields 432 characters and the pi_cam3 datasheet 2062, so this is
# an order of magnitude below the real cases.
MIN_USEFUL_CHARS = 24

# Bounded, because OCR is the one step here that scales with page count and LB uploads
# datasheets. A 300-page PDF that arrived as scans would otherwise hold up the rebuild for
# twenty minutes with no way to interrupt it. Beyond this the pages are left blank and SAID to
# be left blank, which is the same contract vector_db.py already has for text it cannot read.
MAX_PAGES_PER_FILE = 40

_ENGINE = None                 # the RapidOCR instance, built once and reused
_ENGINE_FAILED = False         # so a missing dependency is reported once, not per page


def enabled() -> bool:
    """Is OCR switched on? `ODDBALL_OCR=0` turns it off.

    Same convention as `ODDBALL_SCREEN` in `tools/screen_capture.py`: anything that costs real
    time on the turn path gets an off switch that is not a code edit.
    """
    return str(os.environ.get("ODDBALL_OCR", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _engine():
    """The OCR engine, built once. Returns None when it cannot be had.

    Returning None rather than raising is deliberate and matches the rest of the build path:
    a missing optional dependency must degrade the store to what it was yesterday — image-only
    PDFs reported and skipped — and must never take down a rebuild that would otherwise index
    sixteen perfectly good pages.
    """
    global _ENGINE, _ENGINE_FAILED
    if _ENGINE is not None or _ENGINE_FAILED:
        return _ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR                    # noqa: PLC0415
        _ENGINE = RapidOCR()
    except Exception as exc:                                         # noqa: BLE001
        _ENGINE_FAILED = True
        LOG.warning("OCR is unavailable (%s: %s) — image-only PDFs stay unsearchable. "
                    "pip install rapidocr-onnxruntime pypdfium2", type(exc).__name__, exc)
    return _ENGINE


def ocr_available() -> bool:
    """True when both halves — the renderer and the reader — import and load."""
    if not enabled():
        return False
    try:
        import pypdfium2                                             # noqa: PLC0415,F401
    except Exception:                                                # noqa: BLE001
        return False
    return _engine() is not None


def _cache_key(pdf: Path) -> str:
    """Path, size and mtime. Change any of the three and the page is read again."""
    try:
        stat = pdf.stat()
        stamp = f"{pdf.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|dpi{RENDER_DPI}"
    except OSError:
        stamp = str(pdf)
    return hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:32]


def _read_cache(pdf: Path) -> dict[int, str] | None:
    path = CACHE_DIR / f"{_cache_key(pdf)}.json"
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): v for k, v in blob["pages"].items()}
    except (OSError, ValueError, KeyError):
        return None


def _write_cache(pdf: Path, pages: dict[int, str]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{_cache_key(pdf)}.json").write_text(
            json.dumps({"source": str(pdf), "dpi": RENDER_DPI,
                        "pages": {str(k): v for k, v in pages.items()}}),
            encoding="utf-8")
    except OSError as exc:
        # A cache that cannot be written is slow, not broken. Say so and carry on.
        LOG.warning("could not write the OCR cache (%s) — pages will be re-read next time", exc)


def ocr_pdf(pdf: Path, pages: list[int] | None = None) -> dict[int, str]:
    """Read `pdf` with OCR. Returns {0-based page index: text}, empty when nothing was read.

    Args:
        pdf:   the file.
        pages: which page indices to read, or None for every page. `vector_db` passes only the
               pages that came back blank, so a mostly-text PDF with one scanned page costs one
               page of OCR rather than all of it.

    Returns:
        A mapping of page index to text. Pages that yielded less than `MIN_USEFUL_CHARS` are
        left out entirely rather than mapped to a short string — an absent key means "still
        nothing here", which is what the caller's blank-page report already knows how to say.

    Never raises. Every failure path returns what was read so far.
    """
    if not enabled():
        return {}

    cached = _read_cache(pdf)
    if cached is not None:
        return {i: t for i, t in cached.items() if pages is None or i in pages}

    engine = _engine()
    if engine is None:
        return {}

    try:
        import numpy as np                                           # noqa: PLC0415
        import pypdfium2 as pdfium                                   # noqa: PLC0415
    except Exception as exc:                                         # noqa: BLE001
        LOG.warning("OCR needs pypdfium2 to rasterise (%s) — skipping %s",
                    type(exc).__name__, pdf.name)
        return {}

    out: dict[int, str] = {}
    try:
        document = pdfium.PdfDocument(str(pdf))
    except Exception as exc:                                         # noqa: BLE001
        LOG.warning("could not open %s for OCR (%s)", pdf.name, exc)
        return {}

    try:
        wanted = list(range(len(document))) if pages is None else sorted(set(pages))
        wanted = [i for i in wanted if 0 <= i < len(document)]
        if len(wanted) > MAX_PAGES_PER_FILE:
            LOG.info("%s: OCR'ing the first %d of %d pages (MAX_PAGES_PER_FILE)",
                     pdf.name, MAX_PAGES_PER_FILE, len(wanted))
            wanted = wanted[:MAX_PAGES_PER_FILE]

        for index in wanted:
            try:
                bitmap = document[index].render(scale=RENDER_DPI / 72)
                image = np.asarray(bitmap.to_pil().convert("RGB"))
                result, _ = engine(image)
            except Exception as exc:                                 # noqa: BLE001
                # One unreadable page must not cost the other thirty-nine.
                LOG.warning("OCR failed on %s page %d (%s)", pdf.name, index + 1, exc)
                continue

            # RapidOCR returns [[box, text, confidence], ...] in reading order, or None.
            spans = [str(line[1]).strip() for line in (result or []) if len(line) > 1]
            text = " ".join(s for s in spans if s)
            if len(text) >= MIN_USEFUL_CHARS:
                out[index] = text
    finally:
        try:
            document.close()
        except Exception:                                            # noqa: BLE001
            pass

    _write_cache(pdf, out)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    import time

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="OCR a PDF that carries no extractable text")
    ap.add_argument("pdf", nargs="?", help="the file to read")
    ap.add_argument("--check", action="store_true", help="is the engine installed?")
    ap.add_argument("--scan", action="store_true",
                    help="report every PDF under data/ that would need OCR")
    args = ap.parse_args(argv)

    if args.check:
        print(f"  ODDBALL_OCR      {'on' if enabled() else 'off'}")
        print(f"  render           {RENDER_DPI} dpi via pypdfium2")
        print(f"  engine           {'ready' if ocr_available() else 'UNAVAILABLE'}")
        print(f"  cache            {CACHE_DIR}")
        return 0 if ocr_available() else 1

    if args.scan:
        from pypdf import PdfReader                                  # noqa: PLC0415
        needy = 0
        for path in sorted(DATA_DIR.rglob("*.pdf")):
            try:
                reader = PdfReader(str(path))
                blank = [i for i, page in enumerate(reader.pages)
                         if not (page.extract_text() or "").strip()]
            except Exception as exc:                                 # noqa: BLE001
                print(f"  {path.name:52s} unreadable ({type(exc).__name__})")
                continue
            if blank:
                needy += 1
                cached = "cached" if _read_cache(path) is not None else "not yet OCR'd"
                print(f"  {path.name:52s} {len(blank)}/{len(reader.pages)} blank page(s), {cached}")
        print(f"\n  {needy} file(s) need OCR to be searchable.")
        return 0

    if not args.pdf:
        ap.error("give a PDF, or --scan, or --check")

    path = Path(args.pdf)
    started = time.time()
    got = ocr_pdf(path)
    took = time.time() - started
    print(f"\n{path.name}: {len(got)} page(s) read in {took:.1f}s")
    for index, text in sorted(got.items()):
        print(f"\n  --- page {index + 1}, {len(text)} chars ---")
        print(f"  {text[:600]}{'...' if len(text) > 600 else ''}")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
