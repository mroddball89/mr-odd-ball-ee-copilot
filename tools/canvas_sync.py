#!/usr/bin/env python3
"""
Module:  canvas_sync.py
Purpose: Pull LB's real coursework dates from his Canvas `.ics` feed, live, into the calendar.
Author:  LB
Date:    2026-08-23

    python tools/canvas_sync.py                 # sync, and print what changed
    python tools/canvas_sync.py --dry-run       # fetch and parse, write nothing
    python tools/canvas_sync.py --keep-syllabus # do not drop PDF-extracted dates

## What this replaces, and why

`tools/academic_calendar.py` extracted dates by sending each syllabus PDF to Gemini. That works
once and then rots: **a syllabus is a snapshot and Canvas is the truth.** A date moved in week
four is right in Canvas and wrong in the PDF, and the PDF's version is the one that got
extracted. LB's words: *"the static syllabus PDFs contain outdated dates."*

So deadlines now come from the feed, and the feed costs **no API call at all** — it is one HTTP
GET and a parse. That is a strict improvement on a path D3 says is rationed to 20 model requests
a day.

**The syllabus RAG is untouched.** `tools/vector_db.py` still embeds `data/academic/` into the
`academic` collection and `agents/academic_agent.py` still retrieves from it. Prose — the late
policy, the grading breakdown, what the course covers — is exactly what a syllabus is good for
and exactly what a calendar feed does not carry. Only *date extraction* is retired.

## The URL is a credential, and it is not in this file

The feed URL ends in `user_<40 characters>.ics`. That token **is** the authentication: anyone
holding the URL can read the whole calendar, with no login, until LB resets it in Canvas. This
repository has a GitHub remote, so a hardcoded URL here would be a published secret.

It therefore lives in `.env` as `ODDBALL_CANVAS_ICS`, which is gitignored and excluded from the
deploy tarball — the same treatment `GOOGLE_API_KEY` gets, for the same reason, and the same
convention `ODDBALL_KICAD_ROOT` follows (D7).

    ODDBALL_CANVAS_ICS=https://<school>.instructure.com/feeds/calendars/user_....ics

Get it from Canvas: **Calendar -> Calendar Feed**, bottom right.

## Four things the real feed taught, none of which the obvious implementation gets right

Measured against LB's live feed on 2026-08-23 — 139 events, one course, 68 KB:

**1. `DTSTART` is a `date`, not a `datetime`.** All 139 of them. But Canvas emits a *datetime*
for anything with a time of day, and an assignment due "11:59 PM" is stored as `04:59Z` the
NEXT day. Taking `.date()` off that UTC value reports the wrong day — the single most costly
mistake this module can make, and the reason `_due_date` converts to local before it truncates.
The Pi is `America/New_York`, so the error would have been silent and one day long.

**2. Summaries carry HTML entities.** `Sample AI Policy&#8212;Responsible Use` — 2 of 139.
That text is read aloud by Piper and printed on a card, so `&#8212;` has to become an em dash
before it reaches either. `html.unescape`, once, at parse time.

**3. The bracketed course is not a course code.** It is `POSC201.W02_Fall 2026` — code, section
and term. "POSC201.W02_Fall 2026 — Knowledge Check VM.1" is not a sentence anybody wants read
out, so the code is cleaned to `POSC201` and the full string kept as `section` for reference.

**4. One course produced 139 deadlines.** A syllabus extraction produced ten or twenty. Since
`format_calendar_for_llm()` puts the calendar in the prompt of every ACADEMIC turn, five courses
would be ~700 lines of context per question. That is why this module bounds what it imports and
why `academic_calendar.format_calendar_for_llm` now bounds what it renders.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ is None and str(REPO_ROOT) not in sys.path:
    # `python tools/canvas_sync.py` puts tools/ on the path and not the repo root, so the
    # `tools.academic_calendar` import below would fail. Same fix, same reason, as
    # tools/file_manager.py — caught there by running the CLI on the Pi after a deploy.
    sys.path.insert(0, str(REPO_ROOT))

from langchain_core.tools import tool                                # noqa: E402

from tools.academic_calendar import (CALENDAR_FILE, format_deadlines,  # noqa: E402
                                     get_upcoming_deadlines, load_calendar)

LOG = logging.getLogger("oddball.canvas")

__all__ = ["sync_canvas_calendar", "sync", "parse_ics", "canvas_url", "CANVAS_SOURCE"]

# The environment variable holding the feed URL. See the module docstring: this is a credential.
CANVAS_ICS_ENV = "ODDBALL_CANVAS_ICS"

# Stamped on every entry this module writes, and the reason a Canvas sync and a syllabus
# extraction can share one file without erasing each other. `tools/academic_calendar.py` writes
# a PDF's filename into the same field; each writer only ever replaces its own rows.
CANVAS_SOURCE = "canvas"

# How far back to keep events. A fortnight of recent past is worth having — "did I miss
# something last week" is a real question — and a semester of finished work is prompt weight
# with no reader.
DEFAULT_SINCE_DAYS = 14

# A hard ceiling on imported events, so a feed that turns out to hold five years of history
# cannot silently produce a calendar nothing can put in a prompt. Generous: LB's one course
# produced 139, so this is roughly ten courses' worth.
MAX_EVENTS = 1500

# `Assignment Name [COURSE.SECTION_Term]`. Matched 139/139 against the live feed, and it is
# Canvas's own format rather than a guess — but `_split_summary` still degrades to "no course"
# instead of raising, because a feed is not a contract.
_SUMMARY_RE = re.compile(r"^(?P<title>.*?)\s*\[(?P<course>[^\]]+)\]\s*$", re.DOTALL)

# Where a course code stops and the section/term begins: `POSC201.W02_Fall 2026` -> `POSC201`.
_COURSE_TAIL = re.compile(r"[._]")

# Type inference, in priority order. First match wins, so "Final Exam Review Quiz" is an exam
# rather than a quiz — the more consequential reading, which is the right default when the only
# evidence is a title. The keys are the values `academic_calendar.Deadline` already allows, so
# an entry written here validates against the same schema the PDF path produces.
_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("exam", ("final exam", "midterm", "exam", " test", "test ", "proctor")),
    ("quiz", ("quiz", "knowledge check", "self-assessment")),
    ("project", ("project", "capstone", "presentation", "milestone")),
    ("assignment", ()),          # the default, and deliberately last
]


def canvas_url(explicit: str | None = None) -> str:
    """The feed URL, or a `ValueError` that says exactly how to supply one.

    Order: an explicit argument, then `ODDBALL_CANVAS_ICS`. There is deliberately **no**
    fallback constant — see the module docstring. A default that is a live token is a published
    token the moment anybody commits.
    """
    url = (explicit or os.environ.get(CANVAS_ICS_ENV, "")).strip().strip("'\"")
    if not url:
        raise ValueError(
            f"No Canvas feed URL. Put it in .env as:\n"
            f"    {CANVAS_ICS_ENV}=https://<school>.instructure.com/feeds/calendars/user_....ics\n"
            f"Canvas gives you the link under Calendar -> Calendar Feed (bottom right).\n"
            f"It is a credential — .env is gitignored and is not deployed, which is where it "
            f"belongs.")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"{CANVAS_ICS_ENV} does not look like a URL: {url[:40]!r}")
    return url


def fetch_ics(url: str, timeout: float = 30.0) -> bytes:
    """GET the feed. Returns the raw bytes.

    Raises:
        RuntimeError: anything that means we did not get a calendar — a network failure, a
                      non-200, or an HTML login page served with a 200, which is what Canvas
                      returns for a revoked token and which would otherwise parse to zero
                      events and look like an empty semester.
    """
    import requests                                                  # noqa: PLC0415

    try:
        response = requests.get(url, timeout=timeout,
                                headers={"User-Agent": "mr-odd-ball/1.0 (+calendar sync)"})
    except requests.RequestException as exc:
        raise RuntimeError(f"could not reach Canvas: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(f"Canvas answered {response.status_code} for that feed URL. "
                           f"If it is 401 or 404 the token has been reset — get a fresh link "
                           f"from Calendar -> Calendar Feed.")

    body = response.content
    if not body.lstrip().startswith(b"BEGIN:VCALENDAR"):
        # A 200 that is not a calendar. Named explicitly because the alternative is "0 events
        # imported", which reads as "you have no coursework" rather than "your link is dead".
        kind = response.headers.get("content-type", "unknown")
        raise RuntimeError(f"that URL returned {kind}, not a calendar. A revoked feed token "
                           f"gives a login page with a 200 — check the link in Canvas.")
    return body


def _split_summary(summary: str) -> tuple[str, str, str]:
    """`(title, course, section)` from a Canvas summary. Never raises.

    `Knowledge Check VM.1 [POSC201.W02_Fall 2026]` -> `("Knowledge Check VM.1", "POSC201",
    "POSC201.W02_Fall 2026")`. A summary with no bracket keeps its whole text as the title and
    reports an empty course, which renders as an untagged deadline rather than a crash.
    """
    text = html.unescape(summary or "").strip()
    match = _SUMMARY_RE.match(text)
    if not match:
        return text, "", ""

    title = match.group("title").strip()
    section = match.group("course").strip()
    course = _COURSE_TAIL.split(section, 1)[0].strip() or section
    return (title or text), course, section


def _classify(title: str) -> str:
    """The `type` field, inferred from the title. See `_TYPE_RULES` for why order matters."""
    lowered = f" {title.lower()} "
    for kind, needles in _TYPE_RULES:
        if not needles:
            return kind
        if any(needle in lowered for needle in needles):
            return kind
    return "assignment"


def _due_date(value) -> date | None:
    """One event's due date, as a LOCAL calendar date. None if it has none.

    This is the function that decides which day an assignment is on, and the conversion in it
    is not decoration. Canvas stores a due time of 11:59 PM as `04:59Z` the following day, so
    `.date()` on the raw UTC value is off by one — every time, silently, in the direction that
    makes LB think he has an extra day. `astimezone()` with no argument converts to the
    machine's local zone, which on the Pi is `America/New_York`.

    An all-day `DTSTART` is already a `date` and has no zone to convert; it is used as-is.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # A floating time, which iCalendar defines as local to the reader. Nothing to
            # convert, and guessing UTC here would reintroduce the off-by-one above.
            return value.date()
        return value.astimezone().date()
    if isinstance(value, date):
        return value
    return None


def parse_ics(data: bytes, since_days: int = DEFAULT_SINCE_DAYS,
              today: date | None = None) -> tuple[list[dict], dict]:
    """Turn feed bytes into calendar entries. Returns `(entries, stats)`.

    Entries match the schema `tools/academic_calendar.py` already reads:
    `{course, title, due_date, type, source}`, plus `section` for the full Canvas string.

    Args:
        data:       the raw `.ics`.
        since_days: drop events older than this many days. See DEFAULT_SINCE_DAYS.
        today:      overrideable for testing; defaults to the local date.

    `stats` reports what was dropped and why, because a sync that silently halves the calendar
    is indistinguishable from a semester ending.
    """
    from icalendar import Calendar                                   # noqa: PLC0415

    today = today or date.today()
    cutoff = today - timedelta(days=max(0, since_days))

    calendar = Calendar.from_ical(data)
    stats = {"events": 0, "no_date": 0, "too_old": 0, "duplicates": 0, "kept": 0,
             "capped": 0, "courses": 0}

    seen: set[tuple[str, str, str]] = set()
    entries: list[dict] = []

    for component in calendar.walk():
        if component.name != "VEVENT":
            continue
        stats["events"] += 1

        # DUE is VTODO's field; Canvas uses VEVENT/DTSTART today, but a feed that switches
        # would otherwise import as "no date" for every row.
        raw = component.get("DTSTART") or component.get("DUE")
        due = _due_date(getattr(raw, "dt", None))
        if due is None:
            stats["no_date"] += 1
            continue
        if due < cutoff:
            stats["too_old"] += 1
            continue

        title, course, section = _split_summary(str(component.get("SUMMARY", "")))
        if not title:
            stats["no_date"] += 1
            continue

        key = (course, title, due.isoformat())
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)

        entry = {
            "course": course,
            "title": title,
            "due_date": due.isoformat(),
            "type": _classify(title),
            "source": CANVAS_SOURCE,
        }
        if section and section != course:
            entry["section"] = section
        entries.append(entry)

        if len(entries) >= MAX_EVENTS:
            stats["capped"] = 1
            LOG.warning("stopped at %d events — the feed has more", MAX_EVENTS)
            break

    entries.sort(key=lambda e: (e["due_date"], e["course"], e["title"]))
    stats["kept"] = len(entries)
    stats["courses"] = len({e["course"] for e in entries if e["course"]})
    return entries, stats


def sync(url: str | None = None, since_days: int = DEFAULT_SINCE_DAYS,
         keep_syllabus: bool = False, dry_run: bool = False) -> dict:
    """Fetch, parse and write `academic_calendar.json`. Returns the stats dict.

    Args:
        url:           the feed, or None to read `ODDBALL_CANVAS_ICS`.
        since_days:    how much recent past to keep.
        keep_syllabus: keep deadlines that came from a PDF extraction. **Off by default**,
                       because the whole point of the change is that those dates are stale —
                       LB said so. On, they are preserved and only Canvas rows are replaced.
        dry_run:       parse and report, write nothing.

    Raises:
        ValueError / RuntimeError: no URL, or the feed could not be fetched or parsed. Both
                                   carry a sentence that says what to do about it.
    """
    resolved = canvas_url(url)
    entries, stats = parse_ics(fetch_ics(resolved), since_days=since_days)

    existing = load_calendar()
    # Only ever replace this module's own rows. A syllabus extraction writes the PDF's filename
    # into `source`, so the two writers coexist by construction rather than by whoever ran last.
    kept_other = [e for e in existing
                  if str(e.get("source", "")) != CANVAS_SOURCE] if keep_syllabus else []
    stats["dropped_syllabus"] = (
        0 if keep_syllabus else
        len([e for e in existing if str(e.get("source", "")) != CANVAS_SOURCE]))
    stats["replaced_canvas"] = len([e for e in existing
                                    if str(e.get("source", "")) == CANVAS_SOURCE])

    merged = sorted(kept_other + entries, key=lambda e: str(e.get("due_date", "")))
    stats["total"] = len(merged)

    if dry_run:
        stats["written"] = 0
        return stats

    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now().isoformat(),
        "source": CANVAS_SOURCE,
        "feed_host": resolved.split("/")[2] if "//" in resolved else "",
        "deadlines": merged,
    }
    # The host, never the URL. `feed_host` makes "where did this come from" answerable from the
    # file; writing the whole URL would put the token in a file that is not gitignored.
    with CALENDAR_FILE.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
    stats["written"] = len(merged)
    LOG.info("canvas sync: %d event(s) -> %s", len(merged), CALENDAR_FILE)
    return stats


def _summarise(stats: dict) -> str:
    """One or two sentences about a completed sync, for LB rather than for a log."""
    bits = [f"Synced {stats['kept']} deadline(s) from Canvas across "
            f"{stats['courses']} course(s)."]
    if stats.get("too_old"):
        bits.append(f"{stats['too_old']} already-past event(s) were left out.")
    if stats.get("dropped_syllabus"):
        bits.append(f"{stats['dropped_syllabus']} older date(s) extracted from syllabus PDFs "
                    f"were replaced.")
    if stats.get("capped"):
        bits.append(f"The feed had more than {MAX_EVENTS} events and was truncated.")

    upcoming = get_upcoming_deadlines()
    if upcoming:
        bits.append(f"\n\nComing up:\n{format_deadlines(upcoming)}")
    else:
        bits.append("Nothing is due in the next few days.")
    return " ".join(bits)


@tool
def sync_canvas_calendar() -> str:
    """
    Refreshes the coursework deadline calendar from the user's live Canvas feed.
    Call this when he asks you to sync, refresh or update his schedule, calendar or
    assignments — for example "sync Canvas", "update my schedule", "refresh my deadlines",
    or when he says a due date you gave him is out of date.
    It fetches his Canvas .ics feed and replaces the stored deadlines. Takes no arguments.
    """
    try:
        return _summarise(sync())
    except (ValueError, RuntimeError) as exc:
        # Never raises: this runs inside an agent turn, where an exception becomes a spoken
        # traceback. The messages from `canvas_url` and `fetch_ics` are already written to be
        # said out loud and to name the fix.
        return f"I could not sync Canvas. {exc}"
    except Exception as exc:                                          # noqa: BLE001
        LOG.exception("canvas sync failed")
        return (f"I could not sync Canvas: {type(exc).__name__}: {exc}. The calendar is "
                f"unchanged.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None,
                    help=f"the .ics feed. Defaults to ${CANVAS_ICS_ENV} from .env")
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS,
                    help=f"keep events this many days into the past (default "
                         f"{DEFAULT_SINCE_DAYS})")
    ap.add_argument("--keep-syllabus", action="store_true",
                    help="keep deadlines extracted from syllabus PDFs. Off by default — the "
                         "Canvas feed is the live source and the PDFs are a snapshot.")
    ap.add_argument("--dry-run", action="store_true", help="fetch and parse, write nothing")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    # .env is loaded the same way engine/models.py does it, so the CLI works with no exports.
    try:
        from dotenv import load_dotenv                               # noqa: PLC0415

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    try:
        stats = sync(args.url, since_days=args.since_days,
                     keep_syllabus=args.keep_syllabus, dry_run=args.dry_run)
    except (ValueError, RuntimeError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print(f"  events in feed      {stats['events']}")
    print(f"  imported            {stats['kept']}   across {stats['courses']} course(s)")
    print(f"  skipped, no date    {stats['no_date']}")
    print(f"  skipped, past       {stats['too_old']}   (older than {args.since_days} days)")
    print(f"  skipped, duplicate  {stats['duplicates']}")
    if stats.get("dropped_syllabus"):
        print(f"  syllabus rows gone  {stats['dropped_syllabus']}   "
              f"(pass --keep-syllabus to keep them)")
    print(f"  calendar now holds  {stats['total']}")
    if args.dry_run:
        print("\n  --dry-run: nothing was written.")
    else:
        print(f"\n  wrote {CALENDAR_FILE}")

    upcoming = get_upcoming_deadlines()
    if upcoming:
        print(f"\n  Coming up:\n{format_deadlines(upcoming)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
