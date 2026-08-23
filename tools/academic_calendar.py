#!/usr/bin/env python3
"""
Module:  academic_calendar.py
Purpose: Pull dated coursework out of LB's syllabi once, and read it back for free forever.
Author:  LB
Date:    2026-08-21

    python tools/academic_calendar.py        # build (or rebuild) the calendar from data/academic/

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


def format_calendar_for_llm(entries: list[dict] | None = None) -> str:
    """The whole calendar as prompt context, so the agent can answer date questions from it.

    The counterpart to `tools/vector_db.format_chunks()`: retrieved prose grounds "what does
    the policy say", and this grounds "when is it due".
    """
    entries = load_calendar() if entries is None else entries
    if not entries:
        return "No syllabus deadlines have been extracted yet."

    lines = [f"Today's date is {datetime.now():%Y-%m-%d}.", "", "KNOWN DEADLINES:"]
    for e in sorted(entries, key=lambda x: str(x.get("due_date", ""))):
        lines.append(f"- {e.get('due_date', '?')}  {e.get('course', '?')}  "
                     f"{e.get('title', '?')} ({e.get('type', 'other')})")
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


def extract_deadlines_from_syllabi() -> int:
    """Read every syllabus, extract its dates, write `academic_calendar.json`.

    **Costs one API call per syllabus file.** Run it when syllabi change, not on a schedule.

    Returns:
        How many deadlines were written.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI        # noqa: PLC0415

    from engine.models import AGENT_MODEL, LLM_MAX_RETRIES          # noqa: PLC0415

    documents = _documents_by_source()
    if not documents:
        print(f"No PDFs found under {ACADEMIC_PATH}. Put your syllabi in there and run again.")
        return 0

    print(f"1. Read {len(documents)} syllabus file(s) from {ACADEMIC_PATH}")

    # AGENT_MODEL, not ROUTER_MODEL. Reading a date out of a schedule table is exactly the kind
    # of accuracy D3 says is worth paying `flash` for — and this runs once, not per turn.
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.0, max_retries=LLM_MAX_RETRIES)
    structured = llm.with_structured_output(SyllabusExtraction)

    print("2. Extracting deadlines...")
    deadlines: list[dict] = []
    for source, text in documents.items():
        name = Path(source).name
        try:
            result = structured.invoke(
                EXTRACTION_PROMPT.format(year=datetime.now().year, document=text))
        except Exception as exc:                                     # noqa: BLE001
            # One unreadable syllabus must not cost the other four. Named, so LB knows which.
            print(f"   FAILED  {name}: {type(exc).__name__}: {exc}")
            continue

        found = [{**d.model_dump(), "source": name} for d in result.deadlines]
        deadlines.extend(found)
        print(f"   {len(found):3} from {name}")

    deadlines.sort(key=lambda d: d["due_date"])

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
