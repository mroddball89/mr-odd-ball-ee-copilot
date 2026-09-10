#!/usr/bin/env python3
"""
Module:  router.py
Purpose: Decide which agent answers. The one dispatcher in the merged system.
Author:  LB
Date:    2026-08-17 (extended 2026-08-19 for the assistant merge)

The standalone assistant chose between brains with `orchestrator/classify.py` — a pure
function, keyword rules, effectively free. **That tier system is gone.** This module is the
single thing that decides who answers, and it decides with a Gemini structured-output call.

That is LB's explicit choice and it has a price worth naming out loud: classify cost ~0ms and
this costs a network round trip on every turn. Stage 8 measures it. What it buys is one
decision point instead of two, and routing that understands the question rather than matching
words in it — "what's the trace width for five amps" and "how wide does this power line need
to be" both land on HARDWARE without anybody maintaining a phrase list.

## UTILITY is the exception, and it is deliberate

Eleven routes, and one of them costs nothing: `UTILITY` is answered by `orchestrator/instant.py`
from lookup tables — the time, the date, a unit conversion, a physical constant, what a word
means. D2's claim was that most of what you ask a desk assistant needs no intelligence at all,
and that claim survived the merge. It is now an argument for a route rather than for a tier.

## The decision does not have to be Gemini's (2026-09-10)

Everything above is about WHICH decision gets made. It says nothing about who makes it, and the
paragraph at the top — *"a network round trip on every turn"* — turns out to be a cost that was
never actually required. This is an eleven-way classification against a fixed schema, which is
the cheapest thing an LLM does, and a 1.5B model on this desk can do it.

    setx ODDBALL_LOCAL_ROUTER qwen2.5:1.5b

Set that and `router_agent` classifies locally through `orchestrator/local_router.py`, which
sends the schema below to Ollama's native `/api/chat` as a **decoding grammar** rather than as a
polite request for JSON. Leave it unset and nothing changes. It is opt-in by model name for the
reason `engine/models.py` records about `ODDBALL_PERSONA_MODEL`: a provider that switches itself
because a key or a URL happens to be present switches by accident.

**Do not set it on LB's evidence-free say-so, and that includes mine.** The router picks which
of eleven agents answers, and a router that is wrong one time in three is worse than one that
costs money — a confident wrong answer looks exactly like a right one, which is the asymmetry
`tools/verify_router.py` was written around. `tools/evaluate_local_router.py` replays real past
utterances through a candidate and scores it against the route the rig actually took. That is
what a candidate has to pass first.
"""

import logging
import os
from enum import Enum

from pydantic import BaseModel, Field

from engine.models import ROUTER_MODEL, CLOUD_TIMEOUT_S, LLM_MAX_RETRIES

LOG = logging.getLogger("oddball.router")


# 1. Define the possible destinations
class AgentRoute(str, Enum):
    FIRMWARE = "firmware"
    HARDWARE = "hardware"
    MATH = "math"
    OS = "os"
    QUIZ = "quiz"
    WEB = "web"
    PERSONA = "persona"      # added for the merge — Mr Odd Ball himself
    UTILITY = "utility"      # added for the merge — the free, instant answers
    ACADEMIC = "academic"    # coursework and syllabi, grounded in LB's own uploaded PDFs
    SCREEN = "screen"        # look at the desktop and say what is on it
    GENERAL = "general"


# 2. Define the strict JSON structure
class RouteDecision(BaseModel):
    destination: AgentRoute = Field(description="The specific agent to route the user's query to.")
    reasoning: str = Field(description="A brief 1-sentence explanation of why this route was chosen.")


# Every route the enum can produce is documented here. That is not tidiness — the model can
# only choose what it has been told about, and OS and QUIZ were reachable only by luck for as
# long as they were missing from this list.
ROUTER_PROMPT = """
You are the Master Orchestrator for Mr Odd Ball, an Electrical Engineering AI Copilot that
runs on the user's Windows 11 PC and talks out loud.
Your only job is to analyze the user's query and route it to the correct specialized agent.

Available Agents:
- FIRMWARE: C/C++, RTOS, microcontroller registers, bitmasks, and reading datasheets.
- HARDWARE: physical PCB layout, trace widths, current carrying capacity, IPC-2221. ALSO the
  user's own KiCad files — a schematic's parts or bill of materials, a board's layers or nets.
  He can read them; a question about what is on one of his designs is HARDWARE, not OS.
- MATH: physics equations, filter design, and calculations that need real computation.
- OS: controlling THIS Windows 11 PC — running PowerShell commands, checking disk space or
  RAM, managing files and folders on disk, launching applications. Anything that acts on the
  machine itself. **His notebook is not the filesystem**: "what notes have you got", "read me
  back my notes" and "add to my note about X" are GENERAL, not OS, however much they sound
  like files. OS is for the disk; GENERAL is for the vault.
- QUIZ: the user wants to be TESTED on what he knows — "quiz me", "test me on calculus", "ask
  me some questions about Kant". This covers EVERY subject he takes, not just engineering:
  calculus, philosophy, physics, chemistry, history, whatever is in his question bank. He may
  name a subject and he may not; either way it is QUIZ.
- WEB: current events, component pricing, or up-to-date information from the internet.
- UTILITY: the time, the date, a unit conversion, a physical constant, or the definition of
  an engineering term. Cheap lookups with one right answer and no reasoning required.
- PERSONA: chit-chat, jokes, greetings, how he is feeling, who he is, opinions. The user is
  talking TO Mr Odd Ball rather than asking him to do a job.
- ACADEMIC: coursework and class material — what a syllabus says, when something is due,
  grading or late-work policy, what a course covers. Grounded in the user's own uploaded
  syllabi and his live Canvas calendar, not general study help. ALSO refreshing that calendar:
  "sync Canvas", "update my schedule", "refresh my deadlines".
- SCREEN: what is ON the display right now. "What am I looking at", "what does that error
  say", "read that dialog to me", "why is this window complaining". He takes a screenshot and
  describes it. Choose this only when the answer is something VISIBLE on the desktop at this
  moment — a question about how a program works is GENERAL or FIRMWARE, and a question about a
  file's contents is HARDWARE or ACADEMIC. "What's on my screen" is SCREEN; "what's on my amp
  schematic" is HARDWARE, because that is a file he can read without looking at the display.
- GENERAL: anything that fits nowhere above — and the route that FILES a document the user has
  just uploaded through the chat panel, whether it is a syllabus, a datasheet or a schematic.
  ALSO his notebook: writing a note into his vault, adding to one, or reading one back. GENERAL
  is the only route with `save_to_vault` and `read_from_vault` bound.

Routing notes:
- **A NEW UPLOAD IS ALWAYS GENERAL.** If the user says he has just uploaded, attached, added or
  sent a file — "I just uploaded ECE350_syllabus.pdf", "here's the amp board schematic" — route
  to GENERAL, whatever kind of file it appears to be. GENERAL is the only route that can FILE a
  document, and it can file all three kinds. A question about what is INSIDE a file he uploaded
  earlier is not an upload: route that normally, so "what's on the amp board" is still HARDWARE
  and "when's the midterm" is still ACADEMIC.
- **A note is GENERAL, never OS.** "Write this down", "save that to my ECE350 notes", "read me
  my regulator note" — the vault is a folder of Markdown files and GENERAL is the route that can
  write it. OS is for the machine, and "managing files" in its description means the user's
  disk, not his notebook. Most note requests never reach this router at all
  (`orchestrator/note_intent.py` answers them for free); the ones that do are the phrasings that
  matcher deliberately refused, and they still belong here.
- Prefer UTILITY over MATH for a plain unit conversion or a looked-up constant. MATH is for
  problems that need working out, not for facts.
- Prefer PERSONA over GENERAL when the user is being social.
- Choose OS only when the user wants something DONE to this PC. A question *about* Windows
  or PowerShell in general is GENERAL; a request to check this machine's disk space is OS. Naming a FILE does
  not make it OS: "what's on my amp schematic" is HARDWARE, because reading design files is
  something the hardware agent does itself.
- **"Update my schedule" and "sync my calendar" are ACADEMIC, not OS.** They read like commands
  to the machine and they are not: the only thing being updated is his coursework calendar, and
  ACADEMIC is the route that can do it. OS is for the PC itself — its drives, its files, its
  applications.
- **SCREEN is for the display, OS is for the machine.** "What's on the screen" is SCREEN;
  "how much disk space is left" is OS. Both are about this PC and they are not the same
  question — one is answered by looking at pixels, the other by asking the operating system.
- ACADEMIC is about what a COURSE requires, not what the user knows — "when is the midterm due"
  or "what does the syllabus say about late homework" is ACADEMIC. "Test me on this", "quiz me
  on filters" or "quiz me on philosophy" is QUIZ even in an academic context, because the user
  wants to be evaluated, not told a policy. A datasheet or component question stays FIRMWARE
  even if it came up because of a class; ACADEMIC is for the course paperwork itself.
- **A subject he is not an engineer in does not make it GENERAL.** "Quiz me on Kant" and "test
  me on the French Revolution" are QUIZ, not GENERAL — the question bank holds every subject he
  uploads a practice paper for, and being tested is the request whatever the subject is.

User Query: {question}
"""

# ==========================================
# 🚀 OPTIMIZATION: build the engine once — but only if it is going to be used
# ==========================================
#
# **Built at import, on the main thread, exactly as it was before 2026-09-10.**
#
# Making it lazy was tried on 2026-09-10 and reverted the same day, and the reason is worth
# keeping because the lazy version looks strictly better than it is. It saved a local-only rig
# the cost of constructing a chain it would never invoke — a genuine but tiny startup win — and
# paid for it on the turn path twice over:
#
#   1. `engine/core.py:_route_within_deadline` calls `router_agent` on a daemon thread that it
#      ABANDONS after `ROUTER_DEADLINE_S`. So the first Gemini fallback on a local rig would
#      have imported `langchain_google_genai` *inside* the 20-second turn budget.
#   2. Two abandoned threads can be inside a lazy initialiser at once. Measured under a
#      one-second construction window: three concurrent callers built three separate
#      `ChatGoogleGenerativeAI` clients, each with its own connection pool, each alive until
#      its own blocking call returned.
#
# A `threading.Lock` fixes (2) and not (1). Building it here fixes both and deletes the
# question — the same argument `engine/run_voice.py` makes when it warms the embedding model at
# startup so that "the first corpus question will not wait".
#
# The cost is one `ChatGoogleGenerativeAI` on a rig that routes locally and may never use it.
# That rig already imports `engine/models.py`, which already demands a plausible
# `GOOGLE_API_KEY`, because every AGENT is still on Gemini — so nothing here is reachable
# without that key anyway, and the object is the cheap half of what it was already paying.
_router_chain = None


def _gemini_chain():
    """The Gemini routing chain. Built at import; this is the accessor and the rebuild path.

    Rebuilds if `_router_chain` has been cleared — which `tools/verify_local_router.py` does, and
    which keeps this a function rather than a bare global for the same reason
    `engine/models.build_persona_llm` is a function: a thing a harness can stand in for.
    """
    global _router_chain
    if _router_chain is None:
        from langchain_google_genai import ChatGoogleGenerativeAI          # noqa: PLC0415
        from langchain_core.prompts import ChatPromptTemplate              # noqa: PLC0415

        llm = ChatGoogleGenerativeAI(model=ROUTER_MODEL, temperature=0.0,
                                     max_retries=LLM_MAX_RETRIES, timeout=CLOUD_TIMEOUT_S)
        _router_chain = ChatPromptTemplate.from_template(ROUTER_PROMPT) | \
            llm.with_structured_output(RouteDecision)
    return _router_chain


# The name of the toggle, kept HERE rather than in `orchestrator/local_router.py` so that
# `router_provider()` below can answer without importing that module.
#
# That is not tidiness. `router_provider()` runs on every turn of every rig, and the first
# version imported `local_router` before it looked at the variable — so a machine that had
# opted out still executed that module's import-time code on every question, outside the
# `try/except LocalRouterError` that exists to keep LB on the air. A typo in
# `ODDBALL_LOCAL_ROUTER_TIMEOUT_S` on a rig with the feature switched OFF raised ValueError and
# failed every turn, with a perfectly good Gemini chain sitting unused. "Leave it unset and
# nothing changes" has to be literally true, and one string constant is what makes it so.
LOCAL_ROUTER_VAR = "ODDBALL_LOCAL_ROUTER"


def router_provider() -> str:
    """"local" or "google" — which model decides the route.

    Named to match `engine.models.persona_provider()`, and decided the same way: by whether an
    environment variable names a model, never by whether a URL or a key happens to be present.
    That rule is written out at length in `engine/models.py` because breaking it cost a day —
    the persona defaulted to OpenRouter whenever a key existed, and the resulting silent tool
    failure was invisible until it was looked for.

    Reads the environment directly and imports nothing. See `LOCAL_ROUTER_VAR` above.
    """
    return "local" if os.environ.get(LOCAL_ROUTER_VAR, "").strip() else "google"


# Whether a dead local router is allowed to spend a Gemini request.
#
# **Default yes, and `tools/evaluate_local_router.py` is why the switch exists anyway.** A
# fallback is right on the turn path for the same reason `engine.models.build_persona_llm`
# falls back rather than failing: an Ollama that is not running must not take the assistant off
# the air, and LB should hear an answer, not a diagnostic.
#
# It is exactly wrong during evaluation. A gym that silently answered with Gemini every time
# Qwen crashed would report a match rate measuring nothing at all — the worst kind of green.
# The gym calls `orchestrator.local_router.route_locally` directly and never reaches this code,
# so the flag is belt and braces; set `ODDBALL_LOCAL_ROUTER_STRICT=1` for a rig that should fail
# loudly rather than quietly cost money.
def _strict() -> bool:
    return os.environ.get("ODDBALL_LOCAL_ROUTER_STRICT", "").strip().lower() in ("1", "true", "yes")


def router_agent(query: str) -> RouteDecision:
    """Decide which agent answers `query`.

    Returns a `RouteDecision` whichever model produced it, so `engine/core.py` cannot tell and
    does not need to. Exceptions propagate unchanged — `_route_within_deadline` re-raises them
    and the quota latch reads them.
    """
    if router_provider() == "local":
        from orchestrator.local_router import LocalRouterError, route_locally   # noqa: PLC0415

        try:
            return route_locally(query)
        except LocalRouterError as exc:
            if _strict():
                raise
            # WARNING, not INFO. This is the line that explains a bill: routing has quietly
            # gone back to costing a free-tier request per turn, and the only notice of it is
            # here. `data/oddball.log` is where nine fixes came from on 2026-08-29 precisely
            # because it said things like this.
            LOG.warning("local router unavailable, falling back to Gemini for this turn — %s", exc)

    # Now, when you ask a question, it just executes instantly
    # without having to rebuild the API connection and schema!
    decision = _gemini_chain().invoke({"question": query})
    return decision


# Built now, on the main thread, before any turn thread exists. See the block above.
_gemini_chain()
