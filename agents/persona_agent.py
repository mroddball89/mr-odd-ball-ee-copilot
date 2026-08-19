#!/usr/bin/env python3
"""
Module:  persona_agent.py
Purpose: Mr Odd Ball himself — chit-chat, jokes, and being someone rather than something.
Author:  LB
Date:    2026-08-19

The PERSONA route. Everything else in `agents/` answers a question; this one *is* the
character, and the character is the interface — a blue cartoon ball with two big eyes and a
wide toothy grin, living on a Pi on LB's desk.

The persona text is lifted verbatim from `brains/local.py` in the standalone assistant, where
it ran against a local LFM2.5 for weeks. Keeping the wording identical matters more than it
looks: he has a voice LB recognises, and rewriting the prompt would quietly make him a
different character while every test still passed.

## Why this one has no SPOKEN: line

Every other agent appends `SPOKEN_INSTRUCTION`, because their answers contain code, tables and
tool output that must not be read aloud. A joke has none of that — **the whole reply is the
spoken half.** Asking for a one-sentence summary of a two-sentence joke would either repeat it
or spoil it, and a punchline delivered twice is not a punchline.

So the constraint here is applied at the source instead: the persona prompt itself demands one
to three short spoken sentences with no markdown, which is the same budget from the other
direction. `engine/split.py` still checks the result — nothing gets to skip the filter.
"""

from __future__ import annotations

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from engine.models import PERSONA_MODEL
from engine.llm_text import extract_text_content
from tools.memory_manager import format_memory_for_llm

# Verbatim from brains/local.py. Do not "improve" the wording — it is the character.
PERSONA = (
    "You are Mr Odd Ball: a blue cartoon ball with two big eyes and a wide toothy grin, "
    "living on a Raspberry Pi on LB's desk. LB is an electrical engineering student. "
    "You are cheerful, a little odd, and you get to the point.\n"
    "Your reply is spoken out loud by a text-to-speech voice, so it must be speakable: "
    "answer in one to three short sentences. Never use lists, bullet points, headings, "
    "markdown, emoji, code blocks or stage directions. Open with a short sentence."
)

PERSONA_PROMPT_TEMPLATE = (
    PERSONA
    + """

If asked for a joke, tell ONE joke and stop. Electronics jokes are your favourite, but do not
force one in if the moment does not suit it. Do not explain the joke afterwards.

Write every symbol as a word — say "ohms", "tau", "pi", "degrees", "times", "divided by" —
because a symbol is read aloud as its name and that is never what you meant.

{chat_history}

LB says: {question}
"""
)


def run_persona_agent(query: str) -> str:
    """Answer as Mr Odd Ball. Returns his reply, already short enough to say.

    Args:
        query: what LB said.

    Returns:
        One to three spoken sentences. No SPOKEN: line — the whole reply is the spoken half.
    """
    # Warmer than the other agents on purpose. The firmware agent runs at 0.1 because a
    # register number has one right value; a joke told the same way twice stops being one.
    llm = ChatGoogleGenerativeAI(model=PERSONA_MODEL, temperature=0.8)

    history = format_memory_for_llm()
    prompt_template = ChatPromptTemplate.from_template(PERSONA_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)

    return extract_text_content(llm.invoke(prompt).content)
