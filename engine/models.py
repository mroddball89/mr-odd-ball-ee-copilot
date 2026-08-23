#!/usr/bin/env python3
"""
Module:  models.py
Purpose: Which Gemini model does which job, and why. One place, not seven.
Author:  LB
Date:    2026-08-19

## The measurement that made this file necessary

**The free tier is 20 requests per day, per model, per project — not ~1,500.**

Measured 2026-08-19 against this project's own key, from the 429 body:

    quotaId:     GenerateRequestsPerDayPerProjectPerModel-FreeTier
    quotaMetric: generativelanguage.googleapis.com/generate_content_free_tier_requests
    quotaValue:  20
    model:       gemini-3.5-flash

`~/oddball/CLAUDE.md` records the assumption "a *free* Gemini API key from AI Studio is
available separately (~1,500 req/day)". For **gemini-3.5-flash that is wrong by a factor of
75**, and the whole architecture rests on it: a merged turn costs a router call plus one or
two agent calls, so 20/day is roughly **seven to ten questions before he goes quiet for the
rest of the day**. The first end-to-end smoke test of `Engine.ask()` exhausted it in five.

## What the quota being PER MODEL buys

Each model name has its own daily bucket. So splitting jobs across models is not a trick —
it multiplies the usable budget, and it happens to be the right engineering call anyway:

    ROUTER   a 9-way classification with a fixed schema. No reasoning, no long output.
             `flash-lite` does it correctly in ~890ms and leaves `flash`'s bucket alone.
    AGENT    register values, IPC-2221 numbers, physics. Where accuracy is worth paying for.
    PERSONA  jokes and chit-chat. Wrong is cheap here, so it runs on lite as well.

Measured 2026-08-19, same routing prompt, same question:

    gemini-3.5-flash-lite      890ms   -> hardware   (correct)
    gemini-3.1-flash-lite    25469ms   -> hardware   (correct, but unusable on the turn path)

## If he goes quiet

A 429 is not a bug and must not be reported as one. `engine/core.py` catches it and says so.
The real fixes, in the order LB should consider them:

1. Ask fewer questions per turn — the UTILITY route already costs nothing, and widening it is
   free capacity.
2. Enable billing on the API project. LB's standing decision is no card, so this is his call
   and nobody else's.
3. Put Tier 1 back for PERSONA — `brains/local.py` in the standalone assistant ran a local
   LFM2.5 on the Pi with no quota at all. It was deliberately not carried over in the merge,
   and jokes are exactly the traffic it was good at.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["ROUTER_MODEL", "AGENT_MODEL", "PERSONA_MODEL", "FREE_TIER_DAILY_LIMIT",
           "LLM_MAX_RETRIES"]

# Measured, not documented — from the 429 body on 2026-08-19. Defined above the key
# guard because the guard quotes it.
FREE_TIER_DAILY_LIMIT = 20


# Loaded HERE, and this is the only place it should be.
#
# `router.py` builds its LangChain chain at import time, and ChatGoogleGenerativeAI validates
# the key in its constructor — so the key must be in the environment before `import router`
# happens, not before `main()` runs. `main.py` called `load_dotenv()` at the top and that was
# enough for `python main.py`, and enough for nothing else: `python -m engine.run_voice` and
# every harness that imports the engine went straight past it and died in Pydantic validation.
#
# models.py is imported by router.py before the chain is built, so putting it here fixes every
# entry point at once, including ones not written yet.
#
# The path is explicit rather than `load_dotenv()` bare: the no-argument form finds the file by
# walking up from the CALLER's frame, which fails outright under `python -` and finds the wrong
# thing under a systemd unit whose working directory is not the repo.
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"
load_dotenv(ENV_FILE)

# GEMINI_API_KEY is accepted as an alias, and that is not politeness — it is what the
# standalone assistant's `brains/gemini.py` read, so a Pi that has been running Tier 3 already
# has a `.env` using that name. Without this, deploying the merged copilot onto that Pi fails
# at import with a Pydantic validation error, which reads as a broken install rather than as
# one renamed variable.
if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

# Checked HERE, for the same reason load_dotenv is here: this module is imported before
# `router.py` builds its chain, and ChatGoogleGenerativeAI validates the key in its
# constructor. Without this guard a missing key surfaces as a twenty-line Pydantic traceback
# from inside LangChain — which, under a systemd unit on a headless Pi, is a journal entry
# nobody can act on. The message names the file to create and the page to get a key from.
def _key_problem(key: str) -> str | None:
    """Why this key cannot be real, or None if it looks plausible.

    Checked at import so a bad key fails at STARTUP, next to the file that holds it, instead
    of at question time as a spoken "my API key isn't working" with the reason buried in a log.

    That is not hypothetical. On 2026-08-19 the Pi ran for twenty minutes answering every
    question with that line, because `.env` on both boxes contained the literal string
    `PASTE_NEW_KEY_HERE` — the placeholder out of the setup instructions, pasted verbatim. Every
    layer behaved correctly and none of them could say the useful thing.

    Deliberately NOT a format regex beyond a length floor. Google has changed its key shape at
    least once (this project has seen both `AIza…` and `AQ.Ab8…`), and a validator that rejects
    the next valid format is worse than no validator: it fails closed on a working key, and the
    error tells you to fix the one thing that is not wrong.
    """
    if not key:
        return "is not set"
    lowered = key.lower()
    for placeholder in ("paste", "your-key", "your_key", "here", "xxx", "<", ">", "example"):
        if placeholder in lowered:
            return f"is still the placeholder text ({key[:24]}…)" if len(key) > 24 else \
                   f"is still the placeholder text ({key})"
    if len(key) < 20:
        return f"is only {len(key)} characters — real keys are around 39"
    if any(c.isspace() for c in key):
        return "contains whitespace, so it was probably wrapped or partly pasted"
    return None


_problem = _key_problem(os.environ.get("GOOGLE_API_KEY", "").strip())
if _problem:
    raise SystemExit(
        f"\nThe Gemini API key {_problem}.\n\n"
        f"  File:    {ENV_FILE}\n"
        f"  Content: GOOGLE_API_KEY=AIza...        <- the actual key, not this text\n\n"
        f"Get one from https://aistudio.google.com/apikey — the free tier is "
        f"{FREE_TIER_DAILY_LIMIT} requests per model per day.\n\n"
        f"On the Pi, avoid retyping it — pipe it in so nothing can be truncated:\n"
        f"  read -rs KEY && printf 'GOOGLE_API_KEY=%s\\n' \"$KEY\" > {ENV_FILE} "
        f"&& chmod 600 {ENV_FILE}\n\n"
        f"`.env` is gitignored and excluded from the deploy, so it is created once per machine "
        f"and never syncs.\n"
    )

# The lite models use fixed sampling and warn, on EVERY call, that `temperature` was ignored.
# It is true and it is not actionable — routing wants temperature 0 and gets deterministic
# behaviour regardless — so it is noise printed once per question, in the middle of the answer.
#
# Filtered narrowly by message and category rather than with a blanket
# `warnings.filterwarnings("ignore")`, which is what the original main.py did: a global ignore
# also hides the deprecation warnings that are the only notice LangChain gives before an
# import moves, and this project is pinned across two LangChain 1.x minors.
warnings.filterwarnings("ignore", message=r".*uses fixed sampling defaults.*",
                        category=UserWarning)

# Overridable from .env so LB can move a job to another model without editing code — which is
# the whole reason this file exists, since the name used to be hardcoded in seven places.
# How many times a failed request is retried. **Zero, and that is measured.**
#
# The google-genai SDK retries a 429 with exponential backoff - 1.78s, 2.54s, 4.44s, 8.79s,
# 16.5s - and on 2026-08-22 that turned one turn into **217 seconds** while the free tier was
# gone. A daily quota does not come back in 8 seconds, so every one of those retries was
# certain to fail before it was sent. Worse, the turn thread is the thread that drains the
# microphone: 3,568 audio frames were dropped while he sat there retrying.
#
# Measured with the quota actually exhausted: default (6) took 217s to give up, 0 took **0.2s**.
#
# The cost of this is real and worth naming: a genuinely transient blip - a dropped wifi packet,
# a 503 - now fails the turn instead of being retried invisibly. That is the right trade at
# 40 words per turn. He says "something went wrong", LB asks again, and it costs three seconds
# rather than three minutes of being deaf. Override with ODDBALL_LLM_MAX_RETRIES if a flaky
# link ever makes that the wrong call.
LLM_MAX_RETRIES = int(os.environ.get("ODDBALL_LLM_MAX_RETRIES", "0"))

ROUTER_MODEL = os.environ.get("ODDBALL_ROUTER_MODEL", "gemini-3.5-flash-lite")
AGENT_MODEL = os.environ.get("ODDBALL_AGENT_MODEL", "gemini-3.5-flash")
PERSONA_MODEL = os.environ.get("ODDBALL_PERSONA_MODEL", "gemini-3.5-flash-lite")
