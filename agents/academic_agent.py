#!/usr/bin/env python3
"""
Module:  academic_agent.py
Purpose: Answer coursework questions from LB's own syllabi, and from nothing else.
Author:  LB
Date:    2026-08-21

The ACADEMIC route. It reads two local sources and is forbidden to answer without them:

    the vector store    `data/academic/` chunked and embedded — prose. Policies, topics,
                        "what does the syllabus say about late work".
    the calendar        `academic_calendar.json` — structured dates. "What's due Friday".

## Why this one is stricter than the firmware agent

`agents/firmware_agent.py` is allowed to fall back on its own knowledge when the datasheets do
not cover a question, as long as it says so. That is right for firmware: the ESP32's GPIO
registers are a matter of public record, and a model's general answer is usually correct and
always checkable against a datasheet later.

**A syllabus is not public record.** There is no general knowledge about when LB's midterm is,
what his professor's late policy is, or which chapters his section covers — so a fluent answer
is a *fabricated* one, with nothing to check it against. Worse, it is the most costly kind of
fabrication in this repo: D8 and D9 both document confident numbers that nobody would question,
and "your project is due the 24th" is exactly that shape. Getting it wrong means missing it.

So this agent gets LB's exact directive, unhedged: answer from the provided context ONLY, and
say you do not know when it is not there. No "but generally...". There is no generally.

## What it does not do

**It does not append the deadline warning.** That check is global — `engine/core.py` runs it on
every turn, so an assignment due tomorrow surfaces whether LB asked about firmware, the time, or
nothing at all. Adding a second copy here would show it twice on exactly the turns where he is
already talking about coursework.
"""

from __future__ import annotations

import logging

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from engine.response import Card, CardKind, Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools.academic_calendar import format_calendar_for_llm
from tools.canvas_sync import sync_canvas_calendar
from tools.vector_db import ACADEMIC_COLLECTION, format_chunks, get_retriever

LOG = logging.getLogger("oddball.academic")

# The one tool this agent gets, and it is deliberately the only one.
#
# Every other capability here is a READ under strict grounding — retrieve, then answer from what
# was retrieved and nothing else. `sync_canvas_calendar` is the opposite shape: it is an action,
# it touches the network, and it REPLACES the calendar that the grounding rules above depend on.
# Giving this agent a second tool would start eroding the property that makes it trustworthy,
# so the bar for the next one is high.
#
# It is not behind a permission gate, and that is a considered call rather than an oversight.
# The WEB route gates because a model composes the query and it leaves the machine; this fetches
# one fixed URL out of LB's own `.env`, sends nothing, and overwrites a file that is rebuilt by
# running it again. The blast radius is "his calendar is refreshed", which is what he asked for.
ACADEMIC_TOOLS = [sync_canvas_calendar]
_BY_NAME = {t.name: t for t in ACADEMIC_TOOLS}

NO_SYLLABI = ("No syllabus excerpts were retrieved. No syllabi have been ingested, or none of "
              "them cover this question.")

# The strict-grounding contract, verbatim where it matters. Two rules do the work:
#
#   1. ONLY the provided context. Not "prefer" it — only it. See the module docstring: there is
#      no general knowledge about LB's course to fall back on, so a fallback is a fabrication.
#   2. Say you do not know. A model asked to answer from context it does not have will
#      otherwise produce something plausible, and a plausible due date is worse than silence
#      because it stops LB from going to look it up.
ACADEMIC_PROMPT_TEMPLATE = """
You are an academic assistant for an electrical engineering student. You answer questions about
his courses using his own uploaded syllabi and the deadline calendar extracted from them.

You must answer this question using ONLY the provided local document context. If the answer is
not contained in the provided context, state that you do not know.

Do NOT use your general knowledge about how university courses usually work. Do not estimate a
date, infer a policy from convention, or describe what a syllabus "typically" says. If the
context does not contain the answer, the correct response is that you do not know and that he
should check the syllabus himself.

SYLLABUS EXCERPTS (retrieved from the student's own uploaded documents):
{syllabus_context}

DEADLINE CALENDAR (extracted from those same syllabi):
{calendar_context}

Rules about those sources:
- Cite the excerpts inline as [1], [2] and so on, matching the numbers above.
- For any question about WHEN something is due, use the deadline calendar. It is structured and
  exact; the excerpts are prose and may describe a schedule without stating a date.
- Never name a date, an assignment or a policy that does not appear above.
- The calendar comes from his LIVE CANVAS FEED. The syllabus excerpts are PDFs he uploaded and
  their dates may be out of date — where a date in the excerpts disagrees with the calendar, the
  calendar is right and the excerpt is a stale snapshot. Use the excerpts for policies and rules,
  not for dates.

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
    """Answer a coursework question from the local syllabi. Returns the raw reply text.

    `engine/core.py` calls `run_academic_agent_response` instead when it wants the Sources
    card; this signature exists so `main.py --text` and the harnesses can call it plainly,
    matching `agents/firmware_agent.py`.
    """
    return _answer(query)[0]


def run_academic_agent_response(query: str) -> Response:
    """The same answer, as a Response, with a Sources card when retrieval grounded it."""
    reply, sources = _answer(query)
    out = split(reply, route="academic")

    if sources:
        lines = "\n".join(
            f"[{i}] {s['source']}" + (f", page {s['page']}" if s["page"] else "")
            for i, s in enumerate(sources, 1))
        return Response(speech=out.speech,
                        cards=list(out.cards) + [Card(CardKind.MARKDOWN, "Sources", lines)],
                        route="academic", raw=out.raw)
    return out


def _answer(query: str) -> tuple[str, list[dict]]:
    """Retrieve locally, then generate. **In that order** — the whole point of the route.

    Returns:
        (reply_text, sources) — sources is one dict per retrieved chunk, for the card.
    """
    from tools.memory_manager import format_memory_for_llm

    # 1. LOCAL RETRIEVAL FIRST. Nothing is sent to Gemini until this has run.
    context, sources = "", []
    retriever = get_retriever(k=4, collection=ACADEMIC_COLLECTION)
    if retriever is not None:
        try:
            context, sources = format_chunks(retriever.invoke(query))
            LOG.info("retrieved %d syllabus chunk(s) for %r", len(sources), query)
        except Exception:                              # noqa: BLE001
            # Retrieval failing must not fail the answer. Unlike the firmware agent, what is
            # left is not a weaker answer but an honest refusal — the prompt has no context to
            # work from and is required to say so.
            LOG.exception("syllabus retrieval failed; answering with the calendar alone")

    # 2. The structured half. Cheap enough to always include: a JSON read, no API call, and it
    #    is the only source in the system that can answer "what's due Friday" exactly.
    calendar = format_calendar_for_llm()

    # 3. STRICT GENERATION. The context is injected; the prompt forbids going outside it.
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1, max_retries=LLM_MAX_RETRIES)
    prompt = ChatPromptTemplate.from_template(ACADEMIC_PROMPT_TEMPLATE).format(
        syllabus_context=context or NO_SYLLABI,
        calendar_context=calendar,
        chat_history=format_memory_for_llm(),
        question=query,
    )

    # `bind_tools` where available, and a plain invoke where it is not. The fallback is not
    # defensive programming for its own sake: `tools/verify_academic.py` substitutes a stub LLM
    # to prove retrieval happens BEFORE generation, and a stub is exactly the thing that has no
    # `bind_tools`. Requiring it here would make this agent untestable without a network.
    bind = getattr(llm, "bind_tools", None)
    response = (bind(ACADEMIC_TOOLS).invoke(prompt) if callable(bind) else llm.invoke(prompt))

    # 4. If it reached for the sync, run it and answer again with the result. Same bounded
    #    two-step as the other agents, and the second invoke is on the UNBOUND `llm` — a model
    #    that can still see the tool can call it again, and "refresh my calendar" has no natural
    #    stopping point.
    for call in getattr(response, "tool_calls", None) or []:
        chosen = _BY_NAME.get(call.get("name", ""))
        if chosen is None:
            continue
        try:
            result = str(chosen.invoke(call.get("args", {})))
        except Exception as exc:                                      # noqa: BLE001
            # The tool never raises; binding its arguments can, when a model invents a field.
            LOG.exception("%s failed with args=%r", chosen.name, call.get("args", {}))
            result = f"The sync could not be run: {type(exc).__name__}: {exc}"

        LOG.info("canvas sync ran from the academic agent")
        # The calendar on disk has just changed, so the one in the prompt above is stale. It is
        # re-read rather than reused — telling him "synced" while answering from the calendar
        # that was loaded before the sync is the exact failure this route exists to avoid.
        refreshed = ChatPromptTemplate.from_template(ACADEMIC_PROMPT_TEMPLATE).format(
            syllabus_context=context or NO_SYLLABI,
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
        return extract_text_content(second.content), sources

    return extract_text_content(response.content), sources
