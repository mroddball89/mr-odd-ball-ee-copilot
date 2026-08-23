#!/usr/bin/env python3
"""
Module:  verify_syllabus.py
Purpose: Prove a syllabus becomes a findable vault note, and that a scan becomes nothing.
Author:  LB
Date:    2026-08-23

    python tools/verify_syllabus.py          # no key, no API call, no network

Every check here replaces the LLM outright. The extraction itself is one structured-output call
and is not exercised — what IS exercised is everything around it, which is where all the failure
modes live.

## The four properties, and each one has already failed once somewhere in this repo

**1. A textless PDF never reaches the model.** LB's Pi camera PDFs loaded as perfectly good page
objects with zero extractable characters (D12), and that is the normal state of a scanned
syllabus. Handed an empty document a model does not report an empty document — it writes a
complete, plausible, invented course policy, which then lives in the vault as fact and is read
back by three agents as though LB had written it. The guard runs *before* the API call, so a
folder of scans costs nothing at all.

**2. Absence survives to the note.** A field the syllabus does not state is rendered as
*not stated in the syllabus*, not dropped. Dropping the heading makes "the syllabus has no late
policy" and "nobody has run the converter" identical in a grep, and those call for opposite
actions.

**3. A regenerated note REPLACES its predecessor.** `save_to_vault` appends, correctly, because
a model does not know what is already in a note. A derived artifact is the opposite case: a
corrected syllabus re-uploaded would otherwise stack a stale late policy above the current one in
one file, which is the conflicting-data failure D22 and D23 removed from the calendar, rebuilt
inside the vault.

**4. The note contains the words LB will search for.** `read_from_vault` is a substring scan with
no tokenising — the right trade for dozens of notes and no index, and it means a note is findable
only by words it literally contains. Measured on the first real conversion: `office hours` found
it and **`late policy` did not**, because the heading reads "Late and missed work". Section 4 is
that regression.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import os                                                            # noqa: E402

from dotenv import load_dotenv                                       # noqa: E402

load_dotenv(REPO_ROOT / ".env")
_k = os.environ.get("GOOGLE_API_KEY", "").strip()
if len(_k) < 20 or any(p in _k.lower() for p in ("paste", "here", "your-key", "xxx")):
    os.environ["GOOGLE_API_KEY"] = "harness-not-a-real-key-but-long-enough-to-pass"

import tools.knowledge_vault as KV                                   # noqa: E402
import tools.syllabus_to_vault as S                                  # noqa: E402
from make_syllabus_pdf import write as write_syllabus_pdf            # noqa: E402

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


FULL = S.SyllabusFacts(
    course_code="ECE 350", course_name="Signals and Systems",
    instructor="Dr. A. Rivera, a.rivera@morgan.edu",
    office_hours="Tuesdays 2-4pm",
    grading="* Homework 20%\n* Final 30%",
    late_policy="10% per day, up to three days.",
    other="* Attendance is mandatory.")

BARE = S.SyllabusFacts(course_code="PHYS 201", course_name="", instructor="Dr. Someone",
                       office_hours="", grading="", late_policy="", other="")


# =========================================================================================
section("1. a PDF with no text never reaches the model")
# =========================================================================================

tmp = Path(tempfile.mkdtemp())
try:
    from pypdf import PdfWriter

    blank = PdfWriter()
    blank.add_blank_page(width=612, height=792)
    scan = tmp / "scanned.pdf"
    with scan.open("wb") as fh:
        blank.write(fh)

    # If the guard were downstream of the call, this would spend a request and return a
    # fabricated syllabus. The dummy key above guarantees a real call would FAIL loudly, so a
    # clean refusal here is itself evidence that no call was attempted.
    ok, message = S.convert(scan)
    check(not ok, "an image-only PDF is refused", message[:88])
    check("no API call was made" in message,
          "...and the refusal says so, because a silent skip reads as success")
    check("OCR" in message, "...and says what to do about it")

    text, pages = S.read_pdf_text(scan)
    check(text == "" and pages == 1,
          "read_pdf_text reports zero characters rather than raising", f"{len(text)}c/{pages}p")

    corrupt = tmp / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf at all")
    check(S.read_pdf_text(corrupt) == ("", 0),
          "a corrupt PDF is ('', 0), not an exception — same outcome, same sentence")

    check(S.convert(tmp / "absent.pdf")[0] is False,
          "a missing file is refused rather than raising")

    real = write_syllabus_pdf(tmp / "ece350.pdf")
    body, npages = S.read_pdf_text(real)
    check(len(body) >= S.MIN_USABLE_CHARS and npages == 1,
          "a text-bearing PDF passes the same guard",
          f"{len(body)} chars, floor is {S.MIN_USABLE_CHARS}")
    check("10 percent per day" in body and "Dr. A. Rivera" in body,
          "and its text comes through intact")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# =========================================================================================
section("2. absence is recorded as absence, never dropped")
# =========================================================================================

note = S._render(BARE, Path("phys201.pdf"), 4)

check(note.count("*not stated in the syllabus*") == 4,
      "every empty field gets a heading AND an explicit 'not stated'",
      "grading, late policy, office hours and other were all empty")
check("## Late and missed work" in note,
      "the late-policy heading is present even when the policy is not",
      "otherwise 'no policy' and 'never converted' look identical in a grep")
check("Dr. Someone" in note, "and a field that WAS stated is rendered normally")

full = S._render(FULL, Path("ece350.pdf"), 12)
check("*not stated in the syllabus*" not in full,
      "a complete syllabus produces no 'not stated' at all")
check("10% per day, up to three days." in full, "the late policy is carried verbatim")
check("Extracted automatically from `ece350.pdf` (12 pages)" in full,
      "the note names its source file and page count")
check("it was not guessed at" in full,
      "and says plainly that blanks were not invented — a machine wrote this note")
check("Deadlines are not here on purpose" in full,
      "and points at Canvas for dates, so the two halves cannot be confused")

check(S._note_name(FULL, Path("whatever.pdf")) == "ECE350",
      "the note is named from the course code, slugged", S._note_name(FULL, Path("x.pdf")))
check(S._note_name(S.SyllabusFacts(course_code="", course_name="", instructor="x",
                                   office_hours="", grading="", late_policy=""),
                   Path("fallback_name.pdf")) == "fallback_name",
      "and falls back to the PDF's own stem when no code was found")


# =========================================================================================
section("3. a regenerated note REPLACES, and the model cannot do that")
# =========================================================================================

_real_vault = KV.VAULT_DIR
sandbox = Path(tempfile.mkdtemp()) / "vault"
KV.VAULT_DIR = sandbox
try:
    KV.write_note("POSC201.md", "# v1\n\nLate policy: ten percent.", folder="courses",
                  replace=True)
    KV.write_note("POSC201.md", "# v2\n\nLate policy: no late work accepted.", folder="courses",
                  replace=True)
    body = (sandbox / "courses" / "POSC201.md").read_text(encoding="utf-8")
    check(body.count("# v") == 1 and "v2" in body and "v1" not in body,
          "replace=True leaves ONE version — the newest", repr(body[:40]))

    KV.write_note("dictated.md", "First thing LB said.", folder="notes")
    KV.write_note("dictated.md", "Second thing LB said.", folder="notes")
    dictated = (sandbox / "notes" / "dictated.md").read_text(encoding="utf-8")
    check("First thing" in dictated and "Second thing" in dictated,
          "and the default still APPENDS, so a dictated note is never lost")

    check("replace" not in KV.save_to_vault.args_schema.model_fields,
          "the model-facing tool does not expose `replace` at all",
          "a build step may rebuild its artifact; a model may not erase LB's notes")

    check(KV.write_note("../../escape.md", "x", folder="../../etc", replace=True)
          .startswith(("Rewrote", "Successfully")),
          "and the path guards still hold with replace on")
    check(not (sandbox.parent / "escape.md").exists(),
          "...nothing was written outside the vault")
finally:
    KV.VAULT_DIR = _real_vault
    shutil.rmtree(sandbox.parent, ignore_errors=True)


# =========================================================================================
section("4. the note carries the words LB will actually search for")
# =========================================================================================

# read_from_vault is a SUBSTRING scan. Measured on the first real conversion: "office hours"
# found the note and "late policy" did not, because the heading reads "Late and missed work".
lowered = S._render(FULL, Path("ece350.pdf"), 12).lower()
for phrase in ("late policy", "grading breakdown", "attendance policy", "office hours",
               "exam format", "course policy", "syllabus"):
    check(phrase in lowered, f"a search for {phrase!r} would hit this note")

# And prove it against the real searcher, not just the string.
_real_vault = KV.VAULT_DIR
sandbox = Path(tempfile.mkdtemp()) / "vault"
KV.VAULT_DIR = sandbox
try:
    KV.write_note("ECE350.md", S._render(FULL, Path("ece350.pdf"), 12), folder="courses",
                  replace=True)
    for phrase in ("late policy", "grading breakdown", "office hours", "ECE350"):
        found = "ECE350" in KV.read_from_vault.invoke({"search_term": phrase})
        check(found, f"read_from_vault({phrase!r}) finds the course note")
finally:
    KV.VAULT_DIR = _real_vault
    shutil.rmtree(sandbox.parent, ignore_errors=True)


# =========================================================================================
section("5. it is wired to the upload path, off the turn")
# =========================================================================================

import tools.file_manager as F                                       # noqa: E402

import inspect                                                       # noqa: E402

worker = inspect.getsource(F._Indexer)
check('"syllabus" in jobs' in worker,
      "the background thread has a syllabus job")

# Read the CODE, not the prose — via the AST, which is the only way that actually works.
#
# The first version was `"convert_all" not in worker`, which matched the docstring sentence
# explaining why `convert_all` is NOT used. That is [[L11]] verbatim — "a substring check over a
# whole file matches the comment explaining it" — already a lesson in this repo, and walked into
# anyway. The second attempt stripped lines beginning with `#` or a quote, and still failed,
# because a docstring's *middle* lines begin with neither.
import ast                                                           # noqa: E402

_called = {node.func.id
           for node in ast.walk(ast.parse(inspect.getsource(F).lstrip()))
           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
check("convert" in _called,
      "and converts only the NAMED files, one per source",
      "calls found: " + ", ".join(sorted(n for n in _called if "convert" in n)) or "none")
check("convert_all" not in _called,
      "never convert_all — that would re-read every syllabus at one API call each")

academic = inspect.getsource(F._file_academic)
check('_INDEXER.request({"syllabus"}' in academic,
      "filing a syllabus asks for it, on the background thread")
check("index_status" in academic and "not finished yet" in academic,
      "and the sentence says it is not finished, never that it is ready")
check('suffix != ".pdf"' in academic,
      "a non-PDF is still filed, but not sent to the converter")


print("\n" + "=" * 76)
total = PASSED + FAILED
if FAILED:
    print(f"{PASSED}/{total} checks passed — {FAILED} FAILED")
else:
    print(f"{total}/{total} checks passed  — all green")
    print("textless refusal, absence preserved, replace-not-append, findability, wiring")
raise SystemExit(1 if FAILED else 0)
