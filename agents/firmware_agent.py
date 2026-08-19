#!/usr/bin/env python3
"""
Module:  firmware_agent.py
Purpose: C/C++, registers, RTOS — grounded in LB's own datasheets where they cover it.
Author:  LB
Date:    2026-08-17 (retrieval wired in 2026-08-19)

## What changed, and why it was the most important gap in the project

`tools/vector_db.py` chunked datasheet PDFs, embedded them locally and persisted them to
ChromaDB — and **nothing ever read from it.** This agent prompted Gemini with chat history
alone, so every question about a register was answered from the model's memory of the
internet, with a datasheet sitting unqueried on disk three directories away.

That is the D30 failure with a citation-shaped hole where the evidence should be. A model will
state a register address fluently and wrongly, and there is no tell in the wording. The whole
point of ingesting the PDFs was to stop asking it to.

Retrieval is now on the answer path, and three rules make it worth having:

1. **Prefer the retrieved text.** It is LB's actual part, not a similar one.
2. **Say when the datasheets do not cover it.** An ungrounded answer that sounds identical to
   a grounded one is the thing this exists to prevent, so the prompt requires him to name
   which he is giving.
3. **Cite the page.** The sources go on a card. A register value that cannot be traced back to
   a page is just another confident sentence.

When no store has been built — a fresh clone, no PDFs — `get_retriever()` returns None and
this degrades to exactly what it did before, minus the pretence.
"""

from __future__ import annotations

import logging

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from engine.models import AGENT_MODEL
from engine.llm_text import extract_text_content
from engine.response import Card, CardKind, Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools.memory_manager import format_memory_for_llm
from tools.vector_db import format_chunks, get_retriever

LOG = logging.getLogger("oddball.firmware")

NO_DATASHEETS = "No datasheet excerpts were retrieved for this question."

FIRMWARE_PROMPT_TEMPLATE = """
You are an expert Embedded Systems & Firmware Engineer.
Always provide exact register names, bitmasks, or C/C++ code snippets when applicable.
If referencing a datasheet, be precise.

DATASHEET EXCERPTS (retrieved from the user's own document library):
{datasheet_context}

Rules about those excerpts:
- If they answer the question, USE THEM. They are the user's actual parts, and they outrank
  anything you remember about similar parts.
- Cite them inline as [1], [2] and so on, matching the numbers above.
- If they do NOT cover the question, say so plainly in one short clause — for example "the
  datasheets I have don't cover this, but generally..." — and then answer from your own
  knowledge. Never present an ungrounded answer as if it came from the excerpts.

{chat_history}

EXAMPLE 1:
User: How do I configure GPIO 13 as an output on ESP32?
AI: To configure GPIO 13 as an output on the ESP32, you must set the corresponding bit in the
GPIO_ENABLE_REG register.
Here is the C code snippet:
```c
// Set GPIO 13 as output
REG_WRITE(GPIO_ENABLE_REG, BIT13);
```

User Question: {question}
""" + SPOKEN_INSTRUCTION


def run_firmware_agent(query: str) -> str:
    """Answer a firmware question, grounded in the local datasheet store where possible.

    Returns the raw reply text. `engine/core.py` calls `run_firmware_agent_response` instead
    when it wants the sources card too; this signature is kept so `main.py --text` and the
    existing tests are unaffected.
    """
    return _answer(query)[0]


def run_firmware_agent_response(query: str) -> Response:
    """The same answer, as a Response, with a Sources card when retrieval grounded it."""
    reply, sources = _answer(query)
    out = split(reply, route="firmware")

    if sources:
        lines = "\n".join(
            f"[{i}] {s['source']}" + (f", page {s['page']}" if s["page"] else "")
            for i, s in enumerate(sources, 1))
        return Response(speech=out.speech,
                        cards=list(out.cards) + [Card(CardKind.MARKDOWN, "Sources", lines)],
                        route="firmware", raw=out.raw)
    return out


def _answer(query: str) -> tuple[str, list[dict]]:
    # 1. Initialize the LLM
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1)

    # 2. Retrieve from the local datasheet store. None means it was never built.
    context, sources = "", []
    retriever = get_retriever(k=4)
    if retriever is not None:
        try:
            context, sources = format_chunks(retriever.invoke(query))
            LOG.info("retrieved %d chunk(s) for %r", len(sources), query)
        except Exception:                              # noqa: BLE001
            # Retrieval failing must not fail the answer — it just makes it ungrounded, and
            # the prompt already requires him to say so.
            LOG.exception("retrieval failed; answering without the datasheets")

    # 3. Retrieve saved history from SD card (or local storage)
    history = format_memory_for_llm()

    # 4. Build the prompt with datasheet context, memory context and the user's new question
    prompt_template = ChatPromptTemplate.from_template(FIRMWARE_PROMPT_TEMPLATE)
    prompt = prompt_template.format(
        datasheet_context=context or NO_DATASHEETS,
        chat_history=history,
        question=query,
    )

    # 5. Execute the agent and return the text response
    response = llm.invoke(prompt)

    return extract_text_content(response.content), sources
