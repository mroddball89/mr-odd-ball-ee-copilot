#!/usr/bin/env python3
"""
Module:  verify_ocr.py
Purpose: Prove the image-only PDF path — that it reads, that it caches, and that it declines.
Author:  LB
Date:    2026-08-29

    python tools/verify_ocr.py
    python tools/verify_ocr.py --probe     # reintroduce the bug, expect RED

No network. The fixtures are BUILT here rather than checked in: a PIL image saved as a PDF is
a genuine image-only PDF with a known ground truth, which is exactly the file this whole path
exists for, and it means the harness does not depend on a 2 MB schematic staying in `data/`.

## Section 4 is the one that matters

Sections 1-3 prove OCR reads a page, caches it and fills a blank document in. All three would
stay green if `fill_blanks_with_ocr` also overwrote pages that already HAD text — and that
would be the worse bug, because OCR of a rendered page is strictly worse than the text layer
the PDF already carries. It transposes digits in resistor values. So section 4 is negatives:
what must NOT be re-read, and what must happen when the engine is unavailable.

`--probe` reintroduces exactly that — an OCR pass that reads every page — and shows section 4
going red, because a claim that a harness bites is worth what the last check of it was worth.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from tools import pdf_ocr                                            # noqa: E402
from tools.vector_db import fill_blanks_with_ocr                     # noqa: E402

PASSED = 0
FAILED = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"   PASS  {what}")
    else:
        FAILED += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def section(name: str) -> None:
    print(f"\n  {name}")


class _Doc:
    """The shape `fill_blanks_with_ocr` consumes: LangChain's page document, minus LangChain."""

    def __init__(self, content: str, source: Path, page: int):
        self.page_content = content
        self.metadata = {"source": str(source), "page": page}


# A real font, because the PIL bitmap default renders at a size OCR reads unreliably and a
# flaky fixture is worse than no fixture. First one that opens wins; the default is the
# last resort rather than the plan.
WINDOWS_ARIAL = r"C:\Windows\Fonts\arial.ttf"
WINDOWS_CALIBRI = r"C:\Windows\Fonts\calibri.ttf"
LINUX_DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# The words drawn into the fixture. Chosen to look like what is actually on a schematic —
# a net name, a pin, a resistor value — rather than lorem ipsum, because OCR failure modes
# are character-shaped and "GPIO" is a harder read than "hello".
FIXTURE_WORDS = ["ESP32", "GPIO13", "220", "OHM", "LED", "ANODE", "CATHODE", "GND"]


def make_image_pdf(path: Path, *pages: list[str], size: tuple[int, int] = (1240, 1754)) -> Path:
    """Write an image-only PDF, one page per word-list. No text layer at all.

    This is the fixture the whole module is about. `pypdf` extracts "" from it, exactly as it
    does from LB's ESP32 DevKit export.

    **Multi-page on purpose.** The first version made one page and section 4 pointed a
    two-document list at it, so its "page that already has text" named a page index the PDF did
    not have. `ocr_pdf` filtered it out for being out of range, the greedy `--probe` could not
    reach it either, and the probe did not bite: the check was green because the page was
    unreachable, not because it was protected. Caught by running the probe, which is the entire
    reason to have one.
    """
    from PIL import Image, ImageDraw, ImageFont                      # noqa: PLC0415

    font = None
    for candidate in (WINDOWS_ARIAL, WINDOWS_CALIBRI, LINUX_DEJAVU):
        try:
            font = ImageFont.truetype(candidate, 64)
            break
        except OSError:
            continue
    if font is None:                       # the bitmap default: small, but still readable
        font = ImageFont.load_default()

    rendered = []
    for words in pages:
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        for i, word in enumerate(words):
            draw.text((120, 120 + i * 110), word, fill="black", font=font)
        rendered.append(image)

    rendered[0].save(str(path), "PDF", resolution=200.0, save_all=True,
                     append_images=rendered[1:])
    return path


def text_layer_chars(path: Path) -> int:
    from pypdf import PdfReader                                      # noqa: PLC0415

    reader = PdfReader(str(path))
    return sum(len((page.extract_text() or "").strip()) for page in reader.pages)


def run(probe: bool = False) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="oddball-ocr-"))
    saved_cache = pdf_ocr.CACHE_DIR
    pdf_ocr.CACHE_DIR = tmp / "cache"

    if probe:
        # THE BUG: OCR every page, including the ones that already carry text. Sections 1-3
        # cannot tell the difference; section 4 can, and that is the point of having it.
        print("\n  PROBE: fill_blanks_with_ocr reads EVERY page, not just the blank ones\n")
        import tools.vector_db as V                                  # noqa: PLC0415

        def greedy(documents):
            from tools import pdf_ocr as O                           # noqa: PLC0415
            pages, files = 0, 0
            by_file: dict[Path, list] = {}
            for doc in documents:
                by_file.setdefault(Path(doc.metadata["source"]), []).append(doc)
            for pdf, docs in by_file.items():
                got = O.ocr_pdf(pdf, pages=[d.metadata["page"] for d in docs])
                for doc in docs:
                    text = got.get(doc.metadata["page"], "")
                    if text:
                        doc.page_content = text
                        doc.metadata["ocr"] = True
                        pages += 1
                files += 1 if got else 0
            return pages, files

        V.fill_blanks_with_ocr = greedy
        globals()["fill_blanks_with_ocr"] = greedy

    try:
        # =================================================================================
        section("1. the engine is present, and says so honestly")
        # =================================================================================
        available = pdf_ocr.ocr_available()
        check(pdf_ocr.enabled(), "OCR is enabled by default (ODDBALL_OCR unset)")
        if not available:
            print("\n   OCR ENGINE UNAVAILABLE — install with:")
            print("      pip install rapidocr-onnxruntime pypdfium2")
            print("   The remaining sections need it. Reporting this rather than passing "
                  "vacuously.\n")
            return 1
        check(True, "the renderer and the reader both load")
        check(pdf_ocr.RENDER_DPI == 200,
              "the render DPI is the swept figure, not a round guess",
              f"{pdf_ocr.RENDER_DPI} dpi; see media/data/2026-08-29-ocr-dpi-sweep.csv")

        # =================================================================================
        section("2. an image-only PDF is read")
        # =================================================================================
        scanned = make_image_pdf(tmp / "scanned.pdf", FIXTURE_WORDS)
        check(text_layer_chars(scanned) == 0,
              "the fixture really has NO text layer — pypdf extracts nothing",
              "if this fails the fixture is wrong, not the code")

        started = time.time()
        got = pdf_ocr.ocr_pdf(scanned)
        took = time.time() - started
        check(0 in got, "OCR returns text for page 1", f"{len(got)} page(s) in {took:.1f}s")

        read = got.get(0, "").upper()
        found = [w for w in FIXTURE_WORDS if w in read]
        check(len(found) >= len(FIXTURE_WORDS) - 2,
              f"it reads back at least {len(FIXTURE_WORDS) - 2} of {len(FIXTURE_WORDS)} words",
              f"found {found}")
        check("ESP32" in read or "GND" in read,
              "including a net name, which is what makes it worth retrieving", read[:80])

        # =================================================================================
        section("3. it is cached, because the store rebuilds on every upload")
        # =================================================================================
        started = time.time()
        again = pdf_ocr.ocr_pdf(scanned)
        cached_s = time.time() - started
        check(again == got, "a second read returns exactly the same text")
        check(cached_s < max(0.25, took / 4),
              "...and comes from the cache rather than the engine",
              f"{cached_s * 1000:.0f}ms vs {took * 1000:.0f}ms cold")

        time.sleep(1.1)                    # mtime has one-second resolution on some filesystems
        scanned.touch()
        check(pdf_ocr._read_cache(scanned) is None,
              "touching the PDF invalidates its cache entry — the key carries size and mtime")

        # =================================================================================
        section("4. NEGATIVES — what must NOT be read. This is the section that bites.")
        # =================================================================================
        #
        # A page that already carries text must never be OCR'd over. OCR of a rendered page is
        # strictly worse than the embedded text layer: it is where "220Ω" becomes "2200" and a
        # resistor value LB solders to is off by a decade.
        # TWO real pages. Page 1 carries text in the document list AND readable pixels in the
        # PDF, so an OCR pass that ignored the "only blanks" rule would visibly overwrite it.
        # That reachability is what makes the check below a test rather than a coincidence.
        blank = make_image_pdf(tmp / "blank_page.pdf", FIXTURE_WORDS,
                               ["REGULATOR", "LDO", "3V3", "OUTPUT", "CAPACITOR"])
        documents = [
            _Doc("", blank, 0),                                       # blank: must be filled
            _Doc("The regulator is a 3.3 volt LDO.", blank, 1),       # has text: must not be
        ]
        pages, _files = fill_blanks_with_ocr(documents)
        check(documents[1].page_content == "The regulator is a 3.3 volt LDO.",
              "a page that ALREADY has text is left exactly as it was",
              documents[1].page_content[:60])
        check(documents[1].metadata.get("ocr") is not True,
              "...and is not marked as OCR'd, because it was not")
        check(documents[0].page_content.strip() != "",
              "while the blank page beside it IS filled in", f"{pages} page(s) rescued")
        check(documents[0].metadata.get("ocr") is True,
              "and the filled page is MARKED, so a citation can say where the text came from")

        # A page with almost nothing on it is noise, not content. Indexing it puts a chunk in
        # the store whose only effect is to be retrieved instead of something useful.
        sparse = make_image_pdf(tmp / "sparse.pdf", ["7"])
        thin = pdf_ocr.ocr_pdf(sparse)
        check(0 not in thin,
              f"a page yielding under {pdf_ocr.MIN_USEFUL_CHARS} characters is NOT indexed",
              f"got {thin.get(0, '')!r}")

        # The off switch, and the missing-dependency path, must both degrade to "no OCR" —
        # never to a crash that takes down a rebuild indexing sixteen good pages.
        os.environ["ODDBALL_OCR"] = "0"
        try:
            check(pdf_ocr.ocr_pdf(scanned) == {},
                  "ODDBALL_OCR=0 turns it off completely")
            check(pdf_ocr.ocr_available() is False, "...and reports itself unavailable")
        finally:
            os.environ.pop("ODDBALL_OCR", None)

        broken = tmp / "not-a-pdf.pdf"
        broken.write_bytes(b"this is not a PDF at all")
        check(pdf_ocr.ocr_pdf(broken) == {},
              "a file that will not open returns nothing rather than raising")

        missing = tmp / "does-not-exist.pdf"
        check(pdf_ocr.ocr_pdf(missing) == {}, "and so does a file that is not there")

        empty_docs: list = []
        check(fill_blanks_with_ocr(empty_docs) == (0, 0),
              "an empty document list is a no-op, not an exception")

    finally:
        pdf_ocr.CACHE_DIR = saved_cache
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if probe:
        if FAILED:
            print(f"\n  The harness BITES: {FAILED} check(s) in section 4 went red.\n")
            return 0
        print("\n  PROBE DID NOT BITE — section 4 is not testing what it claims.\n")
        return 1
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        return 1
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prove the image-only PDF path")
    ap.add_argument("--probe", action="store_true",
                    help="reintroduce the bug section 4 exists to catch")
    args = ap.parse_args(argv)
    return run(probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
