#!/usr/bin/env python3
"""
Module:  academic_calendar.py
Purpose: Read LB's coursework deadlines back, for free, on every turn.
Author:  LB
Date:    2026-08-21 (a writer until 2026-08-23; a reader only since — D22, D23)

    python tools/canvas_sync.py              # WRITES the calendar, from his live Canvas feed
    python tools/academic_calendar.py        # prints what is in it, and what the agent is shown

## This module writes nothing

It used to. `extract_deadlines_from_syllabi()` read every PDF under `data/academic/`, sent each
to Gemini with a structured-output schema, and wrote the dates it got back. **It is deleted.**

Dates come from `tools/canvas_sync.py` — LB's live Canvas `.ics` feed — because a syllabus is a
snapshot and a schedule is not. A date moved in week four is right in Canvas and wrong in the
PDF, and the PDF's version was the one that got extracted. The feed also costs no API call at
all, against a tier D3 measured at 20 requests per model per day.

So there is exactly **one writer** and this file is not it. What lives here is everything that
reads `academic_calendar.json`:

    load_calendar()            the file, or [] — never raises
    get_upcoming_deadlines()   what is due soon, for the global banner
    format_deadlines()         rendered for a card, never spoken
    format_calendar_for_llm()  rendered for the ACADEMIC agent's prompt, and bounded

## Why reading is separate from writing at all

The deadline check runs on **every single turn** — `engine/core.py` appends it to any answer,
on any subject, which is D11's whole argument: a reminder you see only when you were already
thinking about coursework is a reminder that fires at the wrong time.

That is only affordable because reading is a JSON parse and costs microseconds and no quota.
Anything that put a network call or a model call on this path would take the banner off it.

## What happens before anything is synced

`load_calendar()` returns `[]` when the file does not exist. That is **not an error** — it is
the normal state of a fresh clone, exactly as `get_retriever()` returning None is. Nothing
warns, nothing crashes, and no banner appears, because no deadlines are known.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

LOG = logging.getLogger("oddball.academic_calendar")

REPO_ROOT = Path(__file__).resolve().parents[1]
ACADEMIC_PATH = REPO_ROOT / "data" / "academic"
CALENDAR_FILE = ACADEMIC_PATH / "academic_calendar.json"

# How far ahead counts as "coming up". LB's number.
DEFAULT_WARNING_DAYS = 3


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
# There is no build step here any more.
#
# `extract_deadlines_from_syllabi()` lived below this line: it read every PDF under
# `data/academic/`, sent each one to Gemini with a structured-output schema, and wrote the
# dates it got back. It is **deleted** (D23). Dates come from `tools/canvas_sync.py`, which
# reads LB's live Canvas feed — a snapshot of a schedule cannot compete with the schedule.
#
# What went with it: `Deadline` and `SyllabusExtraction` (the pydantic schemas), the extraction
# prompt, `_documents_by_source()`, and this module's only use of `pypdf` via
# `tools/vector_db.load_pdfs`. Nothing here imports a model or touches the network now.
#
# `git log -- tools/academic_calendar.py` has it if the argument is ever revisited.
# ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Print the calendar. A reader's debug CLI, where a builder's used to be."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    entries = load_calendar()
    if not entries:
        print(f"No deadlines on file. {CALENDAR_FILE} does not exist or is empty.")
        print("Sync them with:  python tools/canvas_sync.py")
        return 0

    print(f"{len(entries)} deadline(s) in {CALENDAR_FILE}")
    print()
    soon = get_upcoming_deadlines()
    print(f"Within {DEFAULT_WARNING_DAYS} days:")
    print(format_deadlines(soon) if soon else "  (nothing)")
    print()
    print("--- what the ACADEMIC agent is shown ---")
    print(format_calendar_for_llm())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
