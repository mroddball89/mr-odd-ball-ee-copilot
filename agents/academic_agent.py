#!/usr/bin/env python3
"""
Module:  academic_agent.py
Purpose: Answer coursework questions from LB's live Canvas calendar, and from nothing else.
Author:  LB
Date:    2026-08-21 (Canvas-only from 2026-08-23 — the syllabus RAG was removed, D23)

The ACADEMIC route. It reads **one** source and is forbidden to answer without it:

    the calendar    `data/academic/academic_calendar.json`, synced from LB's Canvas `.ics`
                    feed by `tools/canvas_sync.py`. Structured dates. "What's due Friday."

## What this used to do, and why it stopped

Until 2026-08-23 this agent also retrieved syllabus prose out of a Chroma collection and cited
it — the late policy, the grading split, what a course covers. That is gone. LB: *"I want to
completely excise the Syllabus PDF / Academic RAG feature, transitioning the Academic Agent to
rely 100% on the Canvas calendar."*

**What that costs is worth naming, because nothing else in the system covers it.** A calendar
feed carries titles and dates. It does not carry "late work loses 10% per day", and there is now
no path in this repo that can answer that question. The right answer to a policy question is
that he does not know and LB should look at the syllabus — which is what the prompt below tells
him to say, rather than letting him improvise one.

D23 has the full argument, including the part where the stated reason for the removal —
conflicting dates — had already been solved by D22 and no longer applied.

## Why this one is stricter than the firmware agent

`agents/firmware_agent.py` may fall back on its own knowledge when the datasheets do not cover a
question, as long as it says so. That is right for firmware: an ESP32's GPIO registers are a
matter of public record, and a general answer is usually correct and always checkable.

**A course schedule is not public record.** There is no general knowledge about when LB's midterm
is, so a fluent answer is a *fabricated* one with nothing to check it against — and it is the
most costly kind of fabrication in this repo, because "your project is due the 24th" is exactly
the shape of a sentence nobody questions. Getting it wrong means missing it.

So the agent gets LB's directive unhedged: answer from the calendar ONLY, and say you do not
know when it is not there. There is no "but generally". There is no generally.

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

LOG = logging.getLogger("oddball.academic")

# The one tool this agent gets, and it is deliberately the only one.
#
# Everything else here is a READ of a local JSON file under strict grounding.
# `sync_canvas_calendar` is the opposite shape: it is an action, it touches the network, and it
# REPLACES the calendar the grounding rules depend on. The bar for a second tool is high.
#
# It is not behind a permission gate, and that is considered rather than overlooked. The WEB
# route gates because a model composes the query and it leaves the machine; this fetches one
# fixed URL out of LB's own `.env`, sends nothing, and overwrites a file that is rebuilt by
# running it again. The blast radius is "his calendar is refreshed", which is what he asked for.
ACADEMIC_TOOLS = [sync_canvas_calendar]
_BY_NAME = {t.name: t for t in ACADEMIC_TOOLS}

# The strict-grounding contract. Two rules do the work:
#
#   1. ONLY the calendar. Not "prefer" it — only it. See the module docstring: there is no
#      general knowledge about LB's course to fall back on, so a fallback is a fabrication.
#   2. Say you do not know. A model asked about a policy it cannot see will otherwise produce
#      something plausible, and a plausible late policy is worse than silence because it stops
#      him from going to look it up.
#
# The policy paragraph is the new half, and it is load-bearing. This agent used to be able to
# answer those from retrieved syllabus text; it cannot any more, and a model that is not told
# so will answer them from what universities usually do.
ACADEMIC_PROMPT_TEMPLATE = """
You are the schedule manager for an electrical engineering student. You answer questions about
his coursework using his deadline calendar, which is synced live from his Canvas account.

You must answer using ONLY the calendar below. If the answer is not in it, say you do not know.

Do NOT use your general knowledge about how university courses usually work. Do not estimate a
date, infer a schedule from convention, or describe what a course "typically" involves. If the
calendar does not contain the answer, the correct response is that you do not know.

DEADLINE CALENDAR (synced from his Canvas feed):
{calendar_context}

Rules about that calendar:
- It is the ONLY source you have. It is structured and exact — use it verbatim.
- Never name a date, an assignment or a course that does not appear in it.
- If it says items exist beyond the listed range, and he asks about a date in that range, say
  you would need to check rather than saying nothing is due.

WHAT YOU CANNOT ANSWER:
You do not have his syllabi. You cannot answer questions about course POLICIES — late penalties,
grading breakdowns, attendance rules, exam formats, office hours, what a course covers. You have
titles and dates and nothing else.
When he asks one of those, say plainly that you only have his schedule and that he will need to
check the syllabus himself. Do NOT describe what such a policy usually says. A plausible late
penalty is worse than admitting you do not have it, because it stops him checking the real one.

REFRESHING THE CALENDAR:
You have one tool, `sync_canvas_calendar`. Call it when he asks you to sync, refresh or update
his schedule, calendar, assignments or deadlines — "sync Canvas", "update my schedule", "refresh
my deadlines" — and also when he tells you a date you just gave him is wrong or out of date,
because that is what a stale calendar sounds like.
- Do NOT call it to answer an ordinary question about what is due. The calendar above is already
  loaded; syncing on every question would put a network round trip on every turn.
- Never claim you have refreshed anything unless the tool actually ran and said so.

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

    # If it reached for the sync, run it and answer again with the result. Bounded at one round
    # by using the UNBOUND `llm` on the second pass — a model that can still see the tool can
    # call it again, and "refresh my calendar" has no natural stopping point.
    for call in getattr(response, "tool_calls", None) or []:
        chosen = _BY_NAME.get(call.get("name", ""))
        if chosen is None:
            continue
        try:
            result = str(chosen.invoke(call.get("args", {})))
        except Exception as exc:                                          # noqa: BLE001
            # The tool never raises; binding its arguments can, when a model invents a field.
            LOG.exception("%s failed with args=%r", chosen.name, call.get("args", {}))
            result = f"The sync could not be run: {type(exc).__name__}: {exc}"

        LOG.info("canvas sync ran from the academic agent")
        # The calendar on disk has just changed, so the one in the prompt above is stale. It is
        # re-read rather than reused — saying "synced" while answering from the calendar loaded
        # before the sync is the exact failure this route exists to avoid.
        refreshed = ChatPromptTemplate.from_template(ACADEMIC_PROMPT_TEMPLATE).format(
            calendar_context=format_calendar_for_llm(),
            chat_history=format_memory_for_llm(),
            question=query,
        )
        second = llm.invoke(
            f"{refreshed}\n\nTHE CALENDAR HAS JUST BEEN REFRESHED FROM CANVAS. The tool has "
            f"ALREADY RUN — do not call it again. It reported:\n{result}\n\n"
            f"Tell him what happened in one or two short sentences, and answer his question "
            f"from the refreshed deadlines above. If the tool reported a failure, say plainly "
            f"that the calendar was not updated and why.")
        return extract_text_content(second.content)

    return extract_text_content(response.content)
