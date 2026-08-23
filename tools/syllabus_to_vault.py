#!/usr/bin/env python3
"""
Module:  syllabus_to_vault.py
Purpose: Read a syllabus PDF once, and leave a course note in the Markdown vault.
Author:  LB
Date:    2026-08-23

    python tools/syllabus_to_vault.py                      # every syllabus not yet converted
    python tools/syllabus_to_vault.py --file ece350.pdf    # just this one
    python tools/syllabus_to_vault.py --all --force        # redo them, overwriting
    python tools/syllabus_to_vault.py --file x.pdf --dry-run   # extract and print, write nothing

## The trade this makes, and why it is a better one than the RAG it replaces

D23 removed the academic vector store, and with it every answer about a course policy. This
puts those answers back **without** the machinery: one API call per syllabus, once, producing a
plain Markdown note in `vault/courses/`. After that it is greppable by `read_from_vault`, which
the HARDWARE, FIRMWARE and GENERAL/persona agents already carry, at zero further cost.

LB's own framing: *"dropping it into the Markdown Vault takes 5 seconds, avoids RAG overhead,
and keeps the system fast and lightweight."*

    the RAG              a Chroma collection, torch on the answer path, chunk retrieval,
                         re-embedding on every upload, and a syllabus that could ground a
                         firmware answer
    this                 one call, one file, `grep`

## The failure this module exists to prevent, and it is not a small one

**An extraction is a model writing facts into long-term memory.** Everything else in the vault
was dictated by LB; this is the first writer that is a language model, and once a note is in
`vault/courses/` the other agents read it back as if he had written it himself.

So two guards, and neither is optional:

**1. A textless PDF is refused before the model is called.** LB's Pi camera PDFs loaded as
perfectly good page objects with **zero extractable characters** (D12), and that is the normal
state of a scanned syllabus. Handed an empty document, a model does not say "this is empty" —
it writes a complete, plausible, entirely invented course policy, which then lives in the vault
as fact. `MIN_USABLE_CHARS` is the check, and it runs before any network call.

**2. Every field may be absent, and absence is recorded as absence.** The schema uses `""` for
"the syllabus does not say", the prompt insists on it, and `_render` prints *not stated in the
syllabus* rather than dropping the heading. A missing late policy that quietly becomes a
confident one is the exact fabrication `agents/academic_agent.py` was rewritten to refuse — and
laundering it through the vault would defeat that guard rather than respect it.

The note itself is stamped with the source file and the date, and says it was extracted
automatically, so a reader can always tell a machine wrote it.

## Where it runs

Never on the turn path. `tools/file_manager.py` hands it to the same background thread the
vector rebuild uses, because it costs an API call and several seconds. The CLI is synchronous.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ is None and str(REPO_ROOT) not in sys.path:
    # `python tools/syllabus_to_vault.py` puts tools/ on the path and not the repo root. Same
    # fix, same reason, as tools/file_manager.py — caught there by running the CLI on the Pi.
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel, Field                                # noqa: E402

from tools.knowledge_vault import VAULT_DIR, write_note             # noqa: E402

LOG = logging.getLogger("oddball.syllabus")

__all__ = ["convert", "convert_all", "read_pdf_text", "SYLLABUS_DIR", "VAULT_FOLDER",
           "MIN_USABLE_CHARS"]

SYLLABUS_DIR = REPO_ROOT / "data" / "academic"
VAULT_FOLDER = "courses"

# Below this many extractable characters the PDF is treated as unreadable and the model is not
# called. A real syllabus is thousands of characters; an image-only scan is zero, and the two
# pi_cam PDFs that taught this repo the lesson were exactly zero (D12). 400 is well clear of
# both — high enough that a cover page alone will not pass, low enough that a genuinely terse
# one-page outline still does.
MIN_USABLE_CHARS = 400

# How much of a long syllabus is sent. A 30-page course pack is mostly reading lists; the fields
# wanted here are on the first pages. Truncation is announced in the prompt so the model knows
# it is not seeing the end, rather than concluding the document simply lacks a late policy.
MAX_PROMPT_CHARS = 40_000


class SyllabusFacts(BaseModel):
    """What is worth keeping out of a syllabus. Every field may be empty.

    Empty means "the syllabus does not say", and that is a real answer that must survive to the
    note. A model that is not given a way to say "absent" will invent something plausible
    instead — which is the whole hazard of writing model output into long-term memory.
    """

    course_code: str = Field(
        description="The course code exactly as written, e.g. 'ECE 350' or 'POSC201'. Empty "
                    "string if the document does not state one.")
    course_name: str = Field(
        description="The full course title, e.g. 'Signals and Systems'. Empty if not stated.")
    instructor: str = Field(
        description="Instructor name, and their email and office if given. One short block of "
                    "text. Empty if not stated.")
    office_hours: str = Field(
        description="When and where the instructor holds office hours, verbatim where possible. "
                    "Empty if not stated.")
    grading: str = Field(
        description="The grading breakdown — each component and its percentage, one per line, "
                    "as markdown bullets. Empty if not stated.")
    late_policy: str = Field(
        description="The policy for late or missed work, quoted or closely paraphrased. Empty "
                    "if not stated.")
    other: str = Field(
        default="",
        description="Any other rule worth remembering: attendance, academic integrity, required "
                    "materials, exam format. Markdown bullets. Empty if there is nothing.")


EXTRACTION_PROMPT = """
You are reading a university course syllabus for an electrical engineering student, and writing
down the parts he will want to look up later.

Extract only these things: the course code and title, the instructor and how to reach them,
office hours, the grading breakdown, the late/missed work policy, and any other standing rule
worth remembering.

RULES, and the first one matters more than the rest:

- **If the syllabus does not state something, return an EMPTY STRING for that field.** Do not
  fill it with what a course usually does. This text is being written into his permanent notes
  and will be read back later as fact — an invented late penalty is worse than a blank one,
  because a blank one sends him to look at the real document and an invented one does not.
- Quote or closely paraphrase. Do not summarise a percentage into "most of the grade".
- Do not extract individual assignment DUE DATES. His deadlines come from a live Canvas feed
  and are already handled; dates copied out of a PDF go stale and would contradict it.
- If the document is not a syllabus at all, leave every field empty.

SYLLABUS TEXT{truncation_note}:
{document}
"""


def read_pdf_text(path: Path) -> tuple[str, int]:
    """All extractable text from a PDF. Returns `(text, page_count)`.

    Uses `pypdf` directly rather than `tools/vector_db.load_pdfs`: that walks a directory and
    returns per-page LangChain documents for chunking, and this wants one file as one string.

    Never raises — a corrupt or encrypted PDF returns `("", 0)` and the caller reports it as
    unreadable, which is the same outcome as a scan and needs the same sentence.
    """
    try:
        from pypdf import PdfReader                                  # noqa: PLC0415

        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(pages).strip(), len(reader.pages)
    except Exception as exc:                                          # noqa: BLE001
        LOG.warning("could not read %s: %s: %s", path.name, type(exc).__name__, exc)
        return "", 0


def _slug(text: str) -> str:
    """A course code as a filename stem: 'ECE 350' -> 'ECE350'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "", (text or "").strip())


def _note_name(facts: SyllabusFacts, source: Path) -> str:
    """What to call the note.

    The model's course code when it gave one, the PDF's own stem otherwise. Never the model's
    course *name*, which is prose and makes a poor filename, and never empty.
    """
    return _slug(facts.course_code) or _slug(source.stem) or "course"


def _field(heading: str, value: str) -> str:
    """One section of the note. An absent field is PRINTED as absent, not omitted.

    Dropping the heading would make "no late policy in the syllabus" and "nobody has extracted
    this yet" look identical in a grep, and they call for opposite actions.
    """
    body = (value or "").strip()
    return f"## {heading}\n\n{body if body else '*not stated in the syllabus*'}\n"


# The phrases LB will actually type, written into every note so the vault's SUBSTRING search
# can find it. This is not padding — it was measured.
#
# `read_from_vault` matches the whole needle against the filename or the text, with no tokenising
# and no stemming, which is the right trade for a folder of dozens of notes and no index. The
# consequence is that a note is findable only by words it literally contains. The first real
# conversion produced a heading called "Late and missed work", so:
#
#     read_from_vault("office hours")  -> found
#     read_from_vault("late policy")   -> NOT FOUND        <- the likeliest question of all
#
# The fix belongs here rather than in the searcher. Making the scan tokenise would make every
# two-word query match far more than it should, in a tool three agents already depend on; making
# the note carry the vocabulary costs one line and changes nothing else.
_SEARCH_TERMS = ("late policy", "late work", "grading breakdown", "grade breakdown",
                 "attendance policy", "office hours", "instructor", "exam format",
                 "course policy", "syllabus")


def _render(facts: SyllabusFacts, source: Path, pages: int) -> str:
    """The Markdown note. Stamped, so a reader can always tell a machine wrote it."""
    title = " — ".join(p for p in (facts.course_code.strip(), facts.course_name.strip()) if p)
    terms = ", ".join(_SEARCH_TERMS)
    return "\n".join([
        f"# {title or source.stem}",
        "",
        f"> Extracted automatically from `{source.name}` ({pages} pages) on {date.today()} by "
        f"`tools/syllabus_to_vault.py`. Anything marked *not stated* was absent from the "
        f"document — it was not guessed at. Check the PDF for anything that matters.",
        "",
        f"*Search terms: {terms}.*",
        "",
        _field("Instructor", facts.instructor),
        _field("Office hours", facts.office_hours),
        _field("Grading", facts.grading),
        _field("Late and missed work", facts.late_policy),
        _field("Other rules", facts.other),
        "",
        "*Deadlines are not here on purpose — they come from the live Canvas feed "
        "(`tools/canvas_sync.py`), which stays right when a date moves.*",
    ])


def convert(path: Path, force: bool = False, dry_run: bool = False) -> tuple[bool, str]:
    """Convert one syllabus PDF into a vault note. Returns `(ok, message)`.

    Never raises: this is called from a background thread and from an agent's filing path, and
    an exception in either is a failure nobody sees or a spoken traceback.

    **Costs one API call**, and only when the PDF has readable text. The order matters — the
    guard is upstream of the spend, so a folder of scans costs nothing at all.
    """
    if not path.is_file():
        return False, f"{path.name}: no such file."

    text, pages = read_pdf_text(path)
    if len(text) < MIN_USABLE_CHARS:
        # Refused BEFORE the model is called. See the module docstring: an empty document does
        # not produce an empty answer, it produces an invented one.
        return False, (
            f"{path.name}: only {len(text)} characters of text across {pages} page(s) — this is "
            f"an image-only scan, so there is nothing to read. Nothing was written and no API "
            f"call was made. OCR it, or replace it with a text-bearing PDF.")

    note_stem = _slug(path.stem) or "course"
    existing = VAULT_DIR / VAULT_FOLDER / f"{note_stem}.md"

    truncation_note = ""
    document = text
    if len(document) > MAX_PROMPT_CHARS:
        document = document[:MAX_PROMPT_CHARS]
        truncation_note = (f" (TRUNCATED — this is the first {MAX_PROMPT_CHARS} characters of a "
                           f"longer document, so a field you cannot find may simply be past the "
                           f"end. Leave it empty rather than guessing)")

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI    # noqa: PLC0415

        from engine.models import AGENT_MODEL, LLM_MAX_RETRIES       # noqa: PLC0415

        llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.0,
                                     max_retries=LLM_MAX_RETRIES)
        facts = llm.with_structured_output(SyllabusFacts).invoke(
            EXTRACTION_PROMPT.format(truncation_note=truncation_note, document=document))
    except Exception as exc:                                          # noqa: BLE001
        LOG.exception("extraction failed for %s", path.name)
        return False, f"{path.name}: the extraction failed — {type(exc).__name__}: {exc}"

    if not any(v.strip() for v in (facts.instructor, facts.office_hours, facts.grading,
                                   facts.late_policy, facts.other)):
        # Every field empty. Either it is not a syllabus, or the model obeyed the "leave it
        # blank" rule for all of it — and a note of six *not stated* headings is worse than no
        # note, because it looks like the question has been answered.
        return False, (f"{path.name}: nothing usable was found in it — no instructor, grading, "
                       f"late policy or office hours. Nothing was written.")

    name = _note_name(facts, path)
    note = _render(facts, path, pages)

    if dry_run:
        return True, f"{path.name} -> vault/{VAULT_FOLDER}/{name}.md (dry run)\n\n{note}"

    # `replace=True`, and `write_note` rather than the `save_to_vault` tool. A regenerated note
    # must REPLACE its predecessor: appending would stack a stale late policy above the current
    # one in the same file, which is precisely the conflicting-data failure D22 and D23 removed
    # from the calendar, rebuilt inside the vault.
    result = write_note(f"{name}.md", note, folder=VAULT_FOLDER, replace=True)
    if result.startswith("Failed"):
        return False, f"{path.name}: {result}"

    verb = "Rewrote" if existing.exists() or force else "Wrote"
    return True, (f"{verb} vault/{VAULT_FOLDER}/{name}.md from {path.name} — "
                  f"{'grading, ' if facts.grading.strip() else ''}"
                  f"{'late policy, ' if facts.late_policy.strip() else ''}"
                  f"{'office hours' if facts.office_hours.strip() else 'no office hours'}.")


def _already_converted(path: Path) -> bool:
    """True if a note derived from THIS pdf already exists.

    Matched on the stamp line rather than on the filename, because the note is named after the
    course code the model read out of the document and the PDF is named whatever LB called it.
    """
    folder = VAULT_DIR / VAULT_FOLDER
    if not folder.is_dir():
        return False
    needle = f"from `{path.name}`"
    for note in folder.glob("*.md"):
        try:
            if needle in note.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def convert_all(force: bool = False, dry_run: bool = False) -> list[tuple[bool, str]]:
    """Convert every syllabus under `data/academic/` that has no note yet.

    **Skips already-converted files unless `force`.** One API call per document against a
    20-a-day tier (D3) means a `--all` that re-read everything would be a quarter of the day's
    quota for no new information.
    """
    if not SYLLABUS_DIR.is_dir():
        return [(False, f"{SYLLABUS_DIR} does not exist.")]

    out: list[tuple[bool, str]] = []
    for pdf in sorted(SYLLABUS_DIR.glob("*.pdf")):
        if not force and _already_converted(pdf):
            out.append((True, f"{pdf.name}: already in the vault — pass --force to redo it."))
            continue
        out.append(convert(pdf, force=force, dry_run=dry_run))
    if not out:
        out.append((False, f"No PDFs under {SYLLABUS_DIR}."))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=None,
                    help="one syllabus in data/academic/, by name or path")
    ap.add_argument("--all", action="store_true",
                    help="every syllabus without a note yet (the default when --file is absent)")
    ap.add_argument("--force", action="store_true", help="redo files that already have a note")
    ap.add_argument("--dry-run", action="store_true",
                    help="extract and PRINT the note, write nothing. Still costs the API call.")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.file:
        candidate = Path(args.file)
        if not candidate.is_file():
            candidate = SYLLABUS_DIR / Path(args.file).name
        results = [convert(candidate, force=args.force, dry_run=args.dry_run)]
    else:
        results = convert_all(force=args.force, dry_run=args.dry_run)

    failed = 0
    for ok, message in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
        failed += 0 if ok else 1
    return 1 if failed and len(results) == failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
