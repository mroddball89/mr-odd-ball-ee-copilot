#!/usr/bin/env python3
"""
Module:  academic_calendar.py
Purpose: Hold LB's coursework deadlines, and read them back for free on every turn.
Author:  LB
Date:    2026-08-21 (Canvas became the source of dates 2026-08-23)

    python tools/canvas_sync.py              # THE source of dates now — his live Canvas feed
    python tools/academic_calendar.py        # the old PDF extractor, kept as a fallback

## Dates come from Canvas now, not from the PDFs

**`tools/canvas_sync.py` is the live source and this module's extractor is a fallback.** A
syllabus is a snapshot; a date moved in week four is right in Canvas and wrong in the PDF, and
the PDF's version is the one that got extracted. LB: *"the static syllabus PDFs contain outdated
dates."* The feed also costs **no API call**, where extraction costs one per document against a
20-a-day quota (D3).

Everything below `load_calendar()` is unchanged and is what both writers feed: the file format,
the day-granularity comparison, the banner, and the prompt rendering. Only *where the rows come
from* moved.

The two writers coexist through the `source` field, and neither can erase the other's work:
`canvas_sync` owns rows marked `canvas`, this module owns rows marked with a PDF's filename, and
`extract_deadlines_from_syllabi` explicitly preserves Canvas rows on every run including a full
rebuild. The syllabus **RAG is untouched** — `tools/vector_db.py` still embeds `data/academic/`
and the agent still retrieves policy prose from it. Only date extraction was retired.

## Why extraction is a build step and not part of the answer

`agents/academic_agent.py` retrieves syllabus text semantically, which is the right tool for
"what does the syllabus say about late work" and the wrong one for "what's due this week".
A due date is a *structured* fact, and semantic search over prose returns the chunk that reads
most like the question — not the one with the nearest date in it. Asked what is due soonest, a
retriever will happily hand back the paragraph containing the word "soon".

So dates are extracted **once**, into `academic_calendar.json`, and every question after that
reads a local JSON file. That split matters for two reasons beyond correctness:

1. **Quota.** D3 measured the free tier at 20 requests per model per day. Extraction costs one
   API call per syllabus document, paid on the day LB adds a syllabus. Reading the file costs
   nothing, which is what lets `engine/core.py` check deadlines on **every single turn** —
   a check that cost an API call could not go there at any price.
2. **Time.** The deadline banner is on the turn path. A JSON read is microseconds; an
   extraction is seconds.

This is the same shape `tools/vector_db.py` already uses — a `python tools/...` build step, run
after adding PDFs, producing an artifact the answer path only reads.

## What happens before it is built

`load_calendar()` returns `[]` when the file does not exist. That is **not an error** — it is
the normal state of a fresh clone, exactly as `get_retriever()` returning None is. Nothing
warns, nothing crashes, and no deadline banner appears because there are no deadlines known.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

LOG = logging.getLogger("oddball.academic_calendar")

REPO_ROOT = Path(__file__).resolve().parents[1]
ACADEMIC_PATH = REPO_ROOT / "data" / "academic"
CALENDAR_FILE = ACADEMIC_PATH / "academic_calendar.json"

# How far ahead counts as "coming up". LB's number.
DEFAULT_WARNING_DAYS = 3


class Deadline(BaseModel):
    """One dated obligation from a syllabus."""

    course: str = Field(description="The course this belongs to, e.g. 'ECE 350' or "
                                    "'Signals and Systems'.")
    title: str = Field(description="What is due, e.g. 'Homework 4' or 'Midterm Exam 1'.")
    type: Literal["exam", "assignment", "quiz", "project", "other"] = Field(
        description="What kind of obligation this is.")
    due_date: str = Field(description="The due date as an ISO 8601 date, YYYY-MM-DD. If the "
                                      "syllabus gives a time as well, still return only the "
                                      "date.")


class SyllabusExtraction(BaseModel):
    """Everything dated that one syllabus document contains."""

    deadlines: list[Deadline] = Field(
        default_factory=list,
        description="Every dated assignment, exam, quiz or project in this document. Empty if "
                    "the document contains no dates.")


EXTRACTION_PROMPT = """
You are reading a university course syllabus for an electrical engineering student.

Extract EVERY dated obligation: assignments, homework, labs, quizzes, exams, midterms, finals
and project milestones.

Rules:
- Return an ISO 8601 date (YYYY-MM-DD) for each one. The current academic year is {year}; if
  the syllabus gives a date with no year, use the year that makes the date fall inside the
  academic term the document describes.
- If a date is genuinely absent or unreadable, LEAVE THAT ITEM OUT. Do not guess a date, and do
  not invent an item to fill a gap in a schedule. A missing deadline is recoverable; a wrong
  one sends the student to an exam on the wrong day.
- Office hours, lecture topics and reading assignments with no due date are NOT deadlines.

SYLLABUS TEXT:
{document}
"""


# ---------------------------------------------------------------------------------------
# Reading — the answer path. No API calls, no heavy imports, microseconds.
# ---------------------------------------------------------------------------------------

def load_calendar() -> list[dict]:
    """Every known deadline, or `[]` if none have been extracted yet.

    Never raises. A malformed file is logged and treated as empty — the deadline banner is a
    convenience, and taking down every turn in the copilot because a JSON file got truncated
    would be a poor trade.
    """
    if not CALENDAR_FILE.exists():
        return []
    try:
        with CALENDAR_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        LOG.exception("could not read %s — treating it as empty", CALENDAR_FILE)
        return []

    entries = data.get("deadlines", []) if isinstance(data, dict) else data
    return entries if isinstance(entries, list) else []


def _parse_due(entry: dict) -> datetime | None:
    """`entry`'s due date as a datetime, or None if it cannot be read.

    Dates are compared at **day** granularity, deliberately. A deadline recorded as 2026-08-24
    parses to midnight, so a strict `datetime.now()` comparison would stop showing it at
    00:00 on the day it is due — which is the single day it matters most.
    """
    raw = str(entry.get("due_date", "")).strip()
    if not raw:
        return None
    try:
        # fromisoformat handles both "2026-08-24" and "2026-08-24T17:00:00".
        return datetime.fromisoformat(raw)
    except ValueError:
        LOG.debug("unparseable due_date %r in %r", raw, entry.get("title"))
        return None


def get_upcoming_deadlines(days: int = DEFAULT_WARNING_DAYS) -> list[dict]:
    """Everything due between today and `days` days from now, soonest first.

    Args:
        days: how far ahead to look. 3 is LB's number and the default everywhere.

    Returns:
        A list of calendar entries, each with an added `days_away` integer. Empty when nothing
        is due, when the calendar has never been built, or when every entry is in the past.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=days)

    upcoming = []
    for entry in load_calendar():
        due = _parse_due(entry)
        if due is None:
            continue
        due_day = due.replace(hour=0, minute=0, second=0, microsecond=0)
        if today <= due_day <= horizon:
            upcoming.append({**entry, "days_away": (due_day - today).days})

    upcoming.sort(key=lambda e: e["days_away"])
    return upcoming


def format_deadlines(entries: list[dict]) -> str:
    """Render deadlines for a card. One per line, soonest first.

    Written for the eye, not the ear — `engine/core.py` puts this on a card and never speaks
    it, the same way the backup reminder is shown and not said.
    """
    if not entries:
        return ""

    lines = []
    for e in entries:
        away = e.get("days_away")
        when = ("due TODAY" if away == 0 else
                "due tomorrow" if away == 1 else
                f"due in {away} days" if isinstance(away, int) else
                f"due {e.get('due_date', '?')}")
        course = f"{e.get('course', '').strip()} — " if e.get("course") else ""
        lines.append(f"• {course}{e.get('title', 'Untitled')} ({e.get('due_date', '?')}) — {when}")
    return "\n".join(lines)


# How far ahead `format_calendar_for_llm` lists everything, and the kinds it lists no matter how
# far away they are. Both exist because the Canvas feed changed the arithmetic completely.
#
# A syllabus extraction produced ten or twenty dates per course. LB's ONE Canvas course produced
# **139**, measured 2026-08-23 — 9,580 characters, about 2,400 tokens, injected into the prompt
# of every single ACADEMIC turn. Five courses would be ~12,000 tokens per question, most of it
# weekly knowledge checks three months out that nobody is asking about.
#
# So: everything inside the horizon, plus every exam and project regardless of date. That second
# clause is the important half — "when is the final?" is exactly the question a horizon would
# break, and an exam in December is precisely the thing worth carrying all term.
CALENDAR_HORIZON_DAYS = 60
CALENDAR_ALWAYS_TYPES = ("exam", "project")

# A ceiling on lines even after the filter above, so a term with a genuinely enormous number of
# near-term items degrades instead of producing a prompt nothing can answer from.
CALENDAR_MAX_LINES = 120


def format_calendar_for_llm(entries: list[dict] | None = None,
                            horizon_days: int = CALENDAR_HORIZON_DAYS) -> str:
    """The calendar as prompt context, so the agent can answer date questions from it.

    The counterpart to `tools/vector_db.format_chunks()`: retrieved prose grounds "what does
    the policy say", and this grounds "when is it due".

    Args:
        entries:      the calendar, or None to load it.
        horizon_days: how far ahead to list everything. Exams and projects beyond it are still
                      listed; routine work beyond it is summarised as a count.

    **It says what it left out.** A model handed a silently truncated calendar will answer
    "nothing is due then" about a date it was never shown, which is the fabrication this whole
    route exists to prevent — so the omission is stated in the context itself, and the model is
    told to say it does not know rather than to infer from an absence.
    """
    entries = load_calendar() if entries is None else entries
    if not entries:
        return "No coursework deadlines are on file yet."

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = today + timedelta(days=horizon_days)

    shown, deferred = [], []
    for entry in sorted(entries, key=lambda x: str(x.get("due_date", ""))):
        due = _parse_due(entry)
        keep = (due is None                                   # undated: show it, it is unusual
                or due <= horizon
                or str(entry.get("type", "")).lower() in CALENDAR_ALWAYS_TYPES)
        (shown if keep else deferred).append(entry)

    overflow = []
    if len(shown) > CALENDAR_MAX_LINES:
        shown, overflow = shown[:CALENDAR_MAX_LINES], shown[CALENDAR_MAX_LINES:]

    lines = [f"Today's date is {datetime.now():%Y-%m-%d}.", "", "KNOWN DEADLINES:"]
    for e in shown:
        lines.append(f"- {e.get('due_date', '?')}  {e.get('course', '?')}  "
                     f"{e.get('title', '?')} ({e.get('type', 'other')})")

    hidden = deferred + overflow
    if hidden:
        courses = sorted({str(e.get("course", "?")) for e in hidden})
        last = max(str(e.get("due_date", "")) for e in hidden)
        lines += [
            "",
            f"({len(hidden)} further item(s) are on file beyond {horizon:%Y-%m-%d}, running to "
            f"{last}, in: {', '.join(courses)}. They are NOT listed above. If the user asks "
            f"about a date after {horizon:%Y-%m-%d} and it is not listed, say you would need to "
            f"check rather than saying nothing is due.)",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# Building — the once-per-syllabus step. Spends quota; never on the answer path.
# ---------------------------------------------------------------------------------------

def _documents_by_source() -> dict[str, str]:
    """Every syllabus under `data/academic/`, as one text blob per file.

    Per FILE rather than per page: a course schedule is a table that runs across a page break,
    and an extraction shown only page 3 will read a date off a row whose header was on page 2.
    """
    from tools.vector_db import load_pdfs                             # noqa: PLC0415

    by_source: dict[str, list[str]] = {}
    for doc in load_pdfs(ACADEMIC_PATH):
        meta = getattr(doc, "metadata", {}) or {}
        by_source.setdefault(str(meta.get("source", "unknown")), []).append(doc.page_content)
    return {src: "\n".join(pages) for src, pages in by_source.items()}


def extract_deadlines_from_syllabi(sources: set[str] | None = None) -> int:
    """Read the syllabi, extract their dates, write `academic_calendar.json`.

    Args:
        sources: filenames (not paths) to re-extract, or None for all of them. When given,
                 deadlines already recorded from OTHER files are carried over untouched.

    Returns:
        How many deadlines are in the calendar afterwards.

    **Costs one API call per syllabus file processed**, which is why `sources` exists.

    D3 measured the free tier at 20 requests per model per day. `tools/file_manager.py` calls
    this every time LB uploads a syllabus through the paperclip, and re-reading all five of his
    syllabi to learn the dates in the one that just arrived would spend a quarter of the day's
    quota on four files that have not changed. Passing the one filename makes an upload cost one
    request instead of five.

    The merge is by `source`, and that is why the extraction writes that field at all: every
    deadline in the file knows which document it came from, so replacing one document's dates is
    a filter rather than a diff. A file that is re-extracted and now yields nothing correctly
    ends up with no deadlines — its old ones go, because they came from the version that has
    been replaced.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI        # noqa: PLC0415

    from engine.models import AGENT_MODEL, LLM_MAX_RETRIES          # noqa: PLC0415

    documents = _documents_by_source()
    if not documents:
        print(f"No PDFs found under {ACADEMIC_PATH}. Put your syllabi in there and run again.")
        return 0

    # Canvas rows ALWAYS survive, whether this is a full run or an incremental one. The feed is
    # the live source of dates now (tools/canvas_sync.py); this path only ever owned the rows it
    # extracted from PDFs, and a full rebuild wiping the feed's work would be a calendar that
    # silently reverts to stale dates the next time somebody runs this script.
    from tools.canvas_sync import CANVAS_SOURCE                      # noqa: PLC0415

    kept: list[dict] = [d for d in load_calendar()
                        if str(d.get("source", "")) == CANVAS_SOURCE]
    if kept:
        print(f"   keeping {len(kept)} Canvas deadline(s) — this script only owns PDF rows")
    if sources:
        wanted = {Path(s).name for s in sources}
        documents = {src: text for src, text in documents.items() if Path(src).name in wanted}
        if not documents:
            print(f"None of {', '.join(sorted(wanted))} is under {ACADEMIC_PATH}. "
                  f"The calendar is unchanged.")
            return len(load_calendar())
        # Everything from a document we are NOT re-reading survives this run, on top of the
        # Canvas rows already held above.
        kept = kept + [d for d in load_calendar()
                       if str(d.get("source", "")) not in wanted
                       and str(d.get("source", "")) != CANVAS_SOURCE]
        print(f"1. Re-reading {len(documents)} syllabus file(s), and keeping {len(kept)} "
              f"deadline(s) already extracted from the others")
    else:
        print(f"1. Read {len(documents)} syllabus file(s) from {ACADEMIC_PATH}")

    # AGENT_MODEL, not ROUTER_MODEL. Reading a date out of a schedule table is exactly the kind
    # of accuracy D3 says is worth paying `flash` for — and this runs once, not per turn.
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.0, max_retries=LLM_MAX_RETRIES)
    structured = llm.with_structured_output(SyllabusExtraction)

    print("2. Extracting deadlines...")
    deadlines: list[dict] = list(kept)
    for source, text in documents.items():
        name = Path(source).name
        try:
            result = structured.invoke(
                EXTRACTION_PROMPT.format(year=datetime.now().year, document=text))
        except Exception as exc:                                     # noqa: BLE001
            # One unreadable syllabus must not cost the other four. Named, so LB knows which.
            print(f"   FAILED  {name}: {type(exc).__name__}: {exc}")
            # On an incremental run this file's OLD deadlines were dropped from `kept` on the
            # assumption it was about to be re-read. It was not, so they come back — otherwise
            # a rate-limited API call would silently delete a course's whole schedule, and the
            # only symptom would be a deadline banner that stopped appearing.
            if sources:
                previous = [d for d in load_calendar() if str(d.get("source", "")) == name]
                deadlines.extend(previous)
                print(f"        kept the {len(previous)} deadline(s) already on file for it")
            continue

        found = [{**d.model_dump(), "source": name} for d in result.deadlines]
        deadlines.extend(found)
        print(f"   {len(found):3} from {name}")

    # `.get`, not `[...]`: `kept` comes off disk, and a hand-edited entry missing its date must
    # not take the whole rebuild down with a KeyError.
    deadlines.sort(key=lambda d: str(d.get("due_date", "")))

    ACADEMIC_PATH.mkdir(parents=True, exist_ok=True)
    with CALENDAR_FILE.open("w", encoding="utf-8") as fh:
        json.dump({"generated": datetime.now().isoformat(), "deadlines": deadlines},
                  fh, indent=2)

    print(f"3. Wrote {len(deadlines)} deadline(s) to {CALENDAR_FILE}")

    soon = get_upcoming_deadlines()
    if soon:
        print(f"\n   Coming up within {DEFAULT_WARNING_DAYS} days:\n{format_deadlines(soon)}")
    return len(deadlines)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    extract_deadlines_from_syllabi()
