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

from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from engine.response import Card, CardKind, Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools.file_manager import (FILE_INSTRUCTION, FILE_TOOLS, file_followup_prompt,
                                run_file_calls)
from tools.kicad_parser import analyze_kicad_pcb, extract_kicad_bom
from tools.knowledge_vault import (VAULT_INSTRUCTION, VAULT_TOOLS, followup_prompt,
                                   run_vault_calls)
from tools.memory_manager import format_memory_for_llm
from tools.vector_db import format_chunks, get_retriever

LOG = logging.getLogger("oddball.firmware")

NO_DATASHEETS = "No datasheet excerpts were retrieved for this question."

# The KiCad readers are bound here as well as to the hardware agent, and that is not a
# duplicate — it is the pinout question. "Which pin is the HX711 clock wired to on my board" is
# a FIRMWARE question that can only be answered by reading a schematic, and before this the
# firmware agent had no way to look: the router would send it here and the answer would come out
# of the model's memory of a reference design. Same two objects, imported from one place, so the
# two agents cannot drift into reading different files.
_DESIGN_TOOLS = [extract_kicad_bom, analyze_kicad_pcb]
_ALL_TOOLS = VAULT_TOOLS + FILE_TOOLS + _DESIGN_TOOLS
_DESIGN_BY_NAME = {t.name: t for t in _DESIGN_TOOLS}

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

THE USER'S OWN BOARDS:
`extract_kicad_bom` reads one of his schematics and returns its bill of materials;
`analyze_kicad_pcb` reads a board and returns its layers, nets and footprints. They look in
`data/projects/` — where a schematic he uploads with the paperclip is filed — and then in his
KiCad folder, and both take a full path OR just the project's name.
- Use them for any question about how HIS hardware is wired: which pin a part is on, what the
  part actually is, whether a net exists. Do not answer that from a reference design you
  remember. The pin he asks about is the pin on his board, and only the file knows it.
- `list_project_files` tells you which boards he has uploaded, if you need the name first.

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
""" + VAULT_INSTRUCTION + FILE_INSTRUCTION + SPOKEN_INSTRUCTION


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
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1, max_retries=LLM_MAX_RETRIES)

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

    # 5. Execute the agent. Three families of tool are bound: the vault, the file manager, and
    #    the two KiCad readers.
    response = llm.bind_tools(_ALL_TOOLS).invoke(prompt)
    calls = getattr(response, "tool_calls", None)

    # 6. If it reached for a tool, run what it asked for and answer again with the result. The
    #    second invoke uses the UNBOUND `llm` in every branch, and that is what bounds the loop
    #    at one round: a model that can still see the tools can call them again, and "remember
    #    this" has no natural stopping point. Retrieval sources are unaffected — step 2 set them.
    #
    #    The three are checked in order and only the first that matched is answered from, so a
    #    model that asks for a schematic AND a vault write in one turn gets the schematic and
    #    the vault note is dropped. That is deliberate for now: interleaving three result sets
    #    into one prompt is a second thing to get wrong, and the case has not been seen.
    vault_results = run_vault_calls(calls)
    if vault_results:
        LOG.info("vault: %s", ", ".join(name for name, _text in vault_results))
        response = llm.invoke(followup_prompt(prompt, vault_results))
        return extract_text_content(response.content), sources

    file_results = run_file_calls(calls)
    if file_results:
        LOG.info("files: %s", ", ".join(name for name, _text in file_results))
        response = llm.invoke(file_followup_prompt(prompt, file_results))
        return extract_text_content(response.content), sources

    design_results = _run_design_calls(calls)
    if design_results:
        LOG.info("design: %s", ", ".join(name for name, _text in design_results))
        response = llm.invoke(_design_followup_prompt(prompt, design_results))
        # The tool output is appended verbatim so `engine/split.py` can lift it onto a card,
        # exactly as `agents/hardware_agent.py` does with the same tools: a pin number heard
        # once at 160 words per minute is gone, and a bill of materials cannot be heard at all.
        joined = "\n\n".join(text for _name, text in design_results)
        return (f"{extract_text_content(response.content)}\n\n"
                f"Tool Execution Result: {joined}"), sources

    return extract_text_content(response.content), sources


def _run_design_calls(tool_calls: list[dict] | None) -> list[tuple[str, str]]:
    """Execute whichever KiCad readers the model asked for. Same contract as `run_vault_calls`.

    The tools themselves never raise — `tools/kicad_parser.py` is built around that and
    `tools/verify_kicad.py` fuzzes 600 calls to prove it. Binding the ARGUMENTS still can, when
    the model invents a field, and that surfaces from LangChain rather than from the tool.
    """
    out: list[tuple[str, str]] = []
    for call in tool_calls or []:
        chosen = _DESIGN_BY_NAME.get(call.get("name", ""))
        if chosen is None:
            continue
        try:
            out.append((chosen.name, str(chosen.invoke(call.get("args", {})))))
        except Exception as exc:                                       # noqa: BLE001
            LOG.exception("%s failed to run with args=%r", chosen.name, call.get("args", {}))
            out.append((chosen.name,
                        f"I could not read that file: {type(exc).__name__}: {exc}"))
    return out


def _design_followup_prompt(base_prompt: str, results: list[tuple[str, str]]) -> str:
    """The second pass, after a KiCad file was read.

    The rule is the one `agents/hardware_agent.SUMMARY_PROMPT_TEMPLATE` makes for the same two
    tools, and it is not theoretical: asked which pin something is on, a model will supply the
    pin from the reference design it remembers if the file does not obviously say. A pin number
    that is not on the schematic is worse than no answer, because LB will go and probe it.
    """
    block = "\n\n".join(f"`{name}` returned:\n{text}" for name, text in results)
    return (
        f"{base_prompt}\n\n"
        f"THE USER'S OWN DESIGN FILE — read directly off disk, and correct. This has ALREADY "
        f"RUN; do not call any tool again.\n{block}\n\n"
        "Answer the question using only what is above. Do NOT name a component, reference, "
        "footprint, net or pin that does not appear in it, and do not describe what the design "
        "'should' also have. If what is above does not answer the question, say plainly that "
        "the file does not show it."
    )
