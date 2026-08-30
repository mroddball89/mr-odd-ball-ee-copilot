#!/usr/bin/env python3
"""
Module:  academic_agent.py
Purpose: Answer coursework questions from LB's Canvas calendar and his own course notes.
Author:  LB
Date:    2026-08-21 (Canvas-only 2026-08-23 per D23; notes added back the same day, D25)

The ACADEMIC route. It reads **two** local sources and is forbidden to answer without them:

    the calendar    `data/academic/academic_calendar.json`, synced from LB's Canvas `.ics`
                    feed by `tools/canvas_sync.py`. Structured dates. "What's due Friday."
    the notes       `vault/courses/*.md`, written once from an uploaded syllabus by
                    `tools/syllabus_to_vault.py`. Policies. "What's the late penalty."

**The division is absolute: Canvas owns every date, the notes own everything else.** A date in
a note is a snapshot the feed may already have moved past, so the prompt forbids taking one from
there; a late policy is not in a calendar feed at all.

## How it got here, in three steps on one day

**D23 removed a Chroma collection of syllabus chunks**, at LB's request, and with it every answer
about a course policy. That was the right call for the wrong stated reason, and the cost was
named at the time: nothing left in the repo could say what the late penalty was.

**D24 put the syllabi back as plain Markdown** — one extraction into `vault/courses/`, no
embeddings, no retrieval machinery. Cheaper than the RAG by an order of magnitude and greppable
forever after.

**D25 gave this route the key to it.** For a few hours the notes existed and the one route that
gets every coursework question could not reach them: asked plainly, GENERAL or HARDWARE answered
from the vault; asked as coursework, ACADEMIC said it did not know. LB: *"the Markdown Vault
isn't a bloated vector DB — it's just clean text files."*

## Why this is stricter than the firmware agent

`agents/firmware_agent.py` may fall back on its own knowledge when the datasheets do not cover a
question, as long as it says so. That is right for firmware: an ESP32's GPIO registers are a
matter of public record, and a general answer is usually correct and always checkable.

**A course is not public record.** There is no general knowledge about when LB's midterm is or
what his professor's late penalty is, so a fluent answer is a *fabricated* one with nothing to
check it against — and it is the most costly kind of fabrication here, because "your project is
due the 24th" is exactly the shape of a sentence nobody questions.

So: answer from the calendar and the notes ONLY. A search that comes back empty means he has no
notes on that course, and saying so sends him to the syllabus. Inventing a plausible policy stops
him going.

## What it does not do

**It does not append the deadline warning.** That check is global — `engine/core.py` runs it on
every turn, so an assignment due tomorrow surfaces whether LB asked about firmware, the time, or
nothing at all. A second copy here would show it twice on exactly the turns where he is already
talking about coursework.
"""

from __future__ import annotations

import logging

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from engine.response import Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools.academic_calendar import format_calendar_for_llm
from tools.canvas_sync import sync_canvas_calendar
from tools.knowledge_vault import read_from_vault

LOG = logging.getLogger("oddball.academic")

# Two tools, and the split between them is the whole architecture of this route:
#
#   sync_canvas_calendar   WRITES. Refreshes the deadline calendar from the live feed.
#   read_from_vault        READS. Course policies, out of `vault/courses/*.md`.
#
# **Canvas owns dates; the vault owns everything else.** Neither can answer the other's
# question, and that is deliberate rather than a limitation — a date in a note goes stale the
# moment it moves in Canvas, and a late policy is not in a calendar feed at all.
#
# `read_from_vault` was added 2026-08-23 (D25). D23 had left this route calendar-only, which was
# right while there was nothing else to read; D24 put the syllabi into the vault as plain
# Markdown, and at that point the route that gets every coursework question was the one route
# that could not reach them. LB: *"the Markdown Vault isn't a bloated vector DB — it's just
# clean text files."* It is a substring scan over a folder of notes: no embeddings, no torch, no
# retrieval machinery, and nothing on the answer path until the model actually asks for it.
#
# The same two objects the other agents bind, imported from one place, so a note written by
# HARDWARE is read identically here.
#
# Neither is behind a permission gate. The WEB route gates because a model composes the query
# and it leaves the machine; `sync_canvas_calendar` fetches one fixed URL out of LB's own `.env`
# and sends nothing, and `read_from_vault` only reads local files it is already allowed to read.
ACADEMIC_TOOLS = [sync_canvas_calendar, read_from_vault]
_BY_NAME = {t.name: t for t in ACADEMIC_TOOLS}

# The strict-grounding contract. Three rules do the work:
#
#   1. ONLY these two sources. Not "prefer" them — only them. See the module docstring: there is
#      no general knowledge about LB's course to fall back on, so a fallback is a fabrication.
#   2. Say you do not know. A model asked about a policy it cannot see will otherwise produce
#      something plausible, and a plausible late policy is worse than silence because it stops
#      him going to look it up.
#   3. LOOK before saying you do not know. The notes are not in this prompt — they are behind a
#      tool call — so "I do not have that" is only true after `read_from_vault` came back empty.
#      An agent that skips the search and refuses is indistinguishable, to LB, from one that has
#      no notes at all.
#
# Rule 3 is the one this route did not need until D25, and it is the one a model is most likely
# to get wrong: refusing is cheap, and it looks like obedience to rule 1.
ACADEMIC_PROMPT_TEMPLATE = """
You are the coursework assistant for an electrical engineering student. You have two sources: a
deadline calendar synced live from his Canvas account, and a folder of notes made from the
syllabi he has uploaded.

You must answer using ONLY the calendar below and what `read_from_vault` returns. If the answer
is in neither, say you do not know.

Do NOT use your general knowledge about how university courses usually work. Do not estimate a
date, infer a schedule from convention, or describe what a course "typically" involves. If
neither source contains the answer, the correct response is that you do not know.

DEADLINE CALENDAR (synced from his Canvas feed):
{calendar_context}

Rules about that calendar:
- It is the only source of DATES, and it is structured and exact — use it verbatim.
- Never name a date, an assignment or a course that does not appear in it.
- It is NOT a roster. It lists only the courses that have DATED work in Canvas, so a class he
  is enrolled in that has posted nothing yet is missing from it entirely. Never answer "how many
  classes am I taking" from it, never give a course count as though it were his enrolment, and
  never tell him a class does not exist because it is not listed. Say which courses you have
  dated work for, and say plainly that Canvas has given you nothing for any others — he is the
  one who knows what he is enrolled in.
- If it says items exist beyond the listed range, and he asks about a date in that range, say
  you would need to check rather than saying nothing is due.

COURSE POLICIES — YOU MUST LOOK THEM UP:
The calendar above holds titles and dates and nothing else. Everything ELSE about a course —
the late penalty, the grading breakdown, attendance rules, exam formats, office hours, who the
instructor is, required textbooks — lives in your saved notes, one file per course, written when
he uploaded the syllabus.

`read_from_vault` searches those notes. When he asks about any of the above, CALL IT FIRST with
a short search term: the course code ("POSC201"), or the thing he asked about ("late policy",
"office hours", "grading breakdown"). Do not answer a policy question without looking.

Then:
- If the notes answer it, answer from them and say which course the note was for.
- If the search comes back with nothing, say plainly that you have no notes on that course yet
  and that he can upload the syllabus with the paperclip. Do NOT describe what such a policy
  usually says. A plausible late penalty is worse than admitting you do not have it, because it
  stops him checking the real one.
- A note may say a field was *not stated in the syllabus*. That is a real answer — tell him the
  syllabus does not say, rather than filling the gap yourself.

REFRESHING THE CALENDAR:
`sync_canvas_calendar` pulls his deadlines fresh from Canvas. Call it when he asks you to sync,
refresh or update his schedule, calendar, assignments or deadlines — "sync Canvas", "update my
schedule", "refresh my deadlines" — and also when he tells you a date you just gave him is wrong
or out of date, because that is what a stale calendar sounds like.
- Do NOT call it to answer an ordinary question about what is due. The calendar above is already
  loaded; syncing on every question would put a network round trip on every turn.
- Never claim you have refreshed anything unless the tool actually ran and said so.

WHICH SOURCE ANSWERS WHICH QUESTION — this division is absolute:
- WHEN something is due, or what is due: the CALENDAR above. Canvas owns every date.
- Anything else about a course: the NOTES, via `read_from_vault`.
- Never take a date out of a note. Notes are made from syllabus PDFs, and a date in one is a
  snapshot that Canvas may already have moved. If a note and the calendar disagree about a date,
  the calendar is right.

{chat_history}

User Question: {question}
""" + SPOKEN_INSTRUCTION


def run_academic_agent(query: str) -> str:
    """Answer a coursework question from the calendar. Returns the raw reply text.

    `engine/core.py` calls `run_academic_agent_response`; this signature exists so
    `main.py --text` and the harnesses can call it plainly, matching the other agents.
    """
    return _answer(query)


def run_academic_agent_response(query: str) -> Response:
    """The same answer, as a Response.

    There is no Sources card any more. It named the syllabus PDF and page a claim came from, and
    with retrieval gone there is nothing to cite — the calendar is a single local file, and a
    card saying "Sources: academic_calendar.json" on every turn is furniture, not evidence.
    """
    return split(_answer(query), route="academic")


def _answer(query: str) -> str:
    """Load the calendar, then generate under the strict contract above.

    No retrieval, no vector store, no API call before this one. The calendar is a JSON read —
    microseconds — which is what lets it be loaded fresh on every turn rather than cached.
    """
    from tools.memory_manager import format_memory_for_llm                # noqa: PLC0415

    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1, max_retries=LLM_MAX_RETRIES)
    prompt = ChatPromptTemplate.from_template(ACADEMIC_PROMPT_TEMPLATE).format(
        calendar_context=format_calendar_for_llm(),
        chat_history=format_memory_for_llm(),
        question=query,
    )

    # `bind_tools` where available, and a plain invoke where it is not. The fallback is not
    # defensive programming for its own sake: `tools/verify_academic.py` substitutes a stub LLM,
    # and a stub is exactly the thing that has no `bind_tools`. Requiring it would make this
    # agent untestable without a network.
    bind = getattr(llm, "bind_tools", None)
    response = (bind(ACADEMIC_TOOLS).invoke(prompt) if callable(bind) else llm.invoke(prompt))

    # Run whatever it asked for, then answer once more with the results. Bounded at one round by
    # using the UNBOUND `llm` on the second pass — a model that can still see the tools can call
    # them again, and neither "refresh my calendar" nor "search my notes" has a natural stopping
    # point.
    #
    # ALL calls are run before the second pass, not the first one only. A single turn can
    # legitimately want both — "sync Canvas and remind me of the late policy" — and answering
    # off whichever the model happened to list first would silently drop the other.
    results: list[tuple[str, str]] = []
    for call in getattr(response, "tool_calls", None) or []:
        chosen = _BY_NAME.get(call.get("name", ""))
        if chosen is None:
            continue
        try:
            results.append((chosen.name, str(chosen.invoke(call.get("args", {})))))
        except Exception as exc:                                          # noqa: BLE001
            # The tools never raise; binding their arguments can, when a model invents a field.
            LOG.exception("%s failed with args=%r", chosen.name, call.get("args", {}))
            results.append((chosen.name,
                            f"That tool could not be run: {type(exc).__name__}: {exc}"))

    if not results:
        return extract_text_content(response.content)

    LOG.info("academic tools ran: %s", ", ".join(name for name, _ in results))

    # The calendar is re-read rather than reused. If `sync_canvas_calendar` ran, the copy in the
    # prompt above is stale by definition — and saying "synced" while answering from the
    # calendar loaded *before* the sync is the exact failure this route exists to avoid. It is a
    # local JSON read, so doing it unconditionally costs nothing and removes a branch that would
    # otherwise have to know which tool changed what.
    refreshed = ChatPromptTemplate.from_template(ACADEMIC_PROMPT_TEMPLATE).format(
        calendar_context=format_calendar_for_llm(),
        chat_history=format_memory_for_llm(),
        question=query,
    )
    block = "\n\n".join(f"`{name}` returned:\n{text}" for name, text in results)
    second = llm.invoke(
        f"{refreshed}\n\nTOOL RESULTS — these have ALREADY RUN. Do not call any tool again.\n"
        f"The calendar above was re-read after they ran, so it is current.\n\n{block}\n\n"
        f"Answer him now, using only the calendar above and the results here. If a sync ran, "
        f"say so in one short clause. If a note search came back empty, say you have no notes "
        f"on that course rather than describing what a course usually does. If a note says a "
        f"field was not stated in the syllabus, tell him the syllabus does not say it.")
    return extract_text_content(second.content)
