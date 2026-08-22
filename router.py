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

Ten routes, and one of them costs nothing: `UTILITY` is answered by `orchestrator/instant.py`
from lookup tables — the time, the date, a unit conversion, a physical constant, what a word
means. D2's claim was that most of what you ask a desk assistant needs no intelligence at all,
and that claim survived the merge. It is now an argument for a route rather than for a tier.
"""

import os
from enum import Enum

from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from engine.models import ROUTER_MODEL


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
lives on a Raspberry Pi and talks out loud.
Your only job is to analyze the user's query and route it to the correct specialized agent.

Available Agents:
- FIRMWARE: C/C++, RTOS, microcontroller registers, bitmasks, and reading datasheets.
- HARDWARE: physical PCB layout, trace widths, current carrying capacity, IPC-2221. ALSO the
  user's own KiCad files — a schematic's parts or bill of materials, a board's layers or nets.
  He can read them; a question about what is on one of his designs is HARDWARE, not OS.
- MATH: physics equations, filter design, and calculations that need real computation.
- OS: controlling THIS Raspberry Pi — running commands, checking CPU temperature or RAM,
  managing files, launching applications. Anything that acts on the machine itself.
- QUIZ: the user wants to be tested, quizzed, or tutored on engineering material.
- WEB: current events, component pricing, or up-to-date information from the internet.
- UTILITY: the time, the date, a unit conversion, a physical constant, or the definition of
  an engineering term. Cheap lookups with one right answer and no reasoning required.
- PERSONA: chit-chat, jokes, greetings, how he is feeling, who he is, opinions. The user is
  talking TO Mr Odd Ball rather than asking him to do a job.
- ACADEMIC: coursework and class material — what a syllabus says, when something is due,
  grading or late-work policy, what a course covers. Grounded in the user's own uploaded
  syllabi, not general study help.
- GENERAL: anything that fits nowhere above.

Routing notes:
- Prefer UTILITY over MATH for a plain unit conversion or a looked-up constant. MATH is for
  problems that need working out, not for facts.
- Prefer PERSONA over GENERAL when the user is being social.
- Choose OS only when the user wants something DONE to the Pi. A question *about* Linux is
  FIRMWARE or GENERAL; a request to check this machine's temperature is OS. Naming a FILE does
  not make it OS: "what's on my amp schematic" is HARDWARE, because reading design files is
  something the hardware agent does itself.
- ACADEMIC is about what a COURSE requires, not what the user knows — "when is the midterm due"
  or "what does the syllabus say about late homework" is ACADEMIC. "Test me on this" or "quiz me
  on filters" is QUIZ even in an academic context, because the user wants to be evaluated, not
  told a policy. A datasheet or component question stays FIRMWARE even if it came up because of
  a class; ACADEMIC is for the course paperwork itself.

User Query: {question}
"""

# ==========================================
# 🚀 OPTIMIZATION: Pre-build the engine once!
# ==========================================
_llm = ChatGoogleGenerativeAI(model=ROUTER_MODEL, temperature=0.0)
_structured_llm = _llm.with_structured_output(RouteDecision)
_prompt = ChatPromptTemplate.from_template(ROUTER_PROMPT)
_router_chain = _prompt | _structured_llm


def router_agent(query: str) -> RouteDecision:
    # Now, when you ask a question, it just executes instantly
    # without having to rebuild the API connection and schema!
    decision = _router_chain.invoke({"question": query})
    return decision
