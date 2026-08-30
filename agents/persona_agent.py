#!/usr/bin/env python3
"""
Module:  persona_agent.py
Purpose: Mr Odd Ball himself — chit-chat, jokes, and being someone rather than something.
Author:  LB
Date:    2026-08-19

The PERSONA route. Everything else in `agents/` answers a question; this one *is* the
character, and the character is the interface — a blue cartoon ball with two big eyes and a
wide toothy grin, living on the PC on LB's desk.

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

from langchain_core.prompts import ChatPromptTemplate

from engine.models import build_persona_llm
from engine.llm_text import extract_text_content
from tools.file_manager import (FILE_INSTRUCTION, FILE_TOOLS, file_followup_prompt,
                                run_file_calls)
from tools.knowledge_vault import (VAULT_INSTRUCTION, VAULT_TOOLS, followup_prompt,
                                   run_vault_calls)
from tools.memory_manager import format_memory_for_llm

# Verbatim from brains/local.py. Do not "improve" the wording — it is the character.
#
# **One word has been changed, and only because it is a FACT rather than a character trait.**
# The Pi was retired on 2026-08-26; he runs on LB's Windows PC. A persona that introduces
# itself as living on hardware that is in a drawer will say so out loud the first time anybody
# asks it where it lives, and it did. The grin, the oddness and the brevity are untouched.
PERSONA = (
    "You are Mr Odd Ball: a blue cartoon ball with two big eyes and a wide toothy grin, "
    "living on the Windows PC on LB's desk. LB is an electrical engineering student. "
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
    + VAULT_INSTRUCTION
    + """
Saying something out loud from the vault is still SPEECH: one to three short sentences, no
markdown, no file paths. "I've written that down" is the whole confirmation — do not read the
folder and filename aloud.
"""
    + FILE_INSTRUCTION
    + """
Filing is still SPEECH too. "That's your syllabus — I've filed it and I'm reading the dates out
of it now" is the whole answer. Do not read a folder path aloud, and do not list what else is in
the inbox unless he asked.

When you genuinely cannot tell what a file is, one short question is the right answer:
"Is that a syllabus or a datasheet?" Ask it and stop. Do not guess, and do not file it anyway
and mention that you were unsure.
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
    # Built by `engine/models.build_persona_llm`, not constructed here, because WHICH provider
    # answers as Mr Odd Ball is a budget decision and belongs where the other model constants
    # live. It may be Gemini or OpenRouter; both return something with `bind_tools`, and this
    # file does not care which — which is the point of the factory.
    llm = build_persona_llm(temperature=0.8)

    history = format_memory_for_llm()
    prompt_template = ChatPromptTemplate.from_template(PERSONA_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)

    # This is also the GENERAL route — `engine/core.py` sends both here — which is why the
    # vault is bound to the character rather than to a separate catch-all agent. "Remember
    # that I'm using the 2N3904" is a thing LB says in passing, not a hardware query, and it
    # would otherwise route to PERSONA and be forgotten in forty turns.
    #
    # The file tools are here for the same reason, and this agent is their PRIMARY home:
    # `router.py` sends every "I just uploaded X" here whatever the file turns out to be,
    # because one filer that can ask "which is it?" beats four agents each guessing about the
    # kinds of document they happen to know. Hardware and firmware also carry them, so an
    # upload announced in the middle of a board question is still filed rather than lost.
    #
    # Same bounded two-step as the firmware agent: tools on the first pass, off on the second.
    response = llm.bind_tools(VAULT_TOOLS + FILE_TOOLS).invoke(prompt)
    calls = getattr(response, "tool_calls", None)

    vault_results = run_vault_calls(calls)
    if vault_results:
        return extract_text_content(llm.invoke(followup_prompt(prompt, vault_results)).content)

    file_results = run_file_calls(calls)
    if file_results:
        return extract_text_content(
            llm.invoke(file_followup_prompt(prompt, file_results)).content)

    return extract_text_content(response.content)
