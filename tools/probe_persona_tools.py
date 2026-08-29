#!/usr/bin/env python3
"""
Module:  probe_persona_tools.py
Purpose: Does this model still call tools under the REAL persona prompt? The only test that counts.
Author:  LB
Date:    2026-08-29

    python tools/probe_persona_tools.py                          # the current PERSONA_MODEL
    python tools/probe_persona_tools.py --models nvidia/nemotron-nano-9b-v2:free
    python tools/probe_persona_tools.py --models a/b:free c/d:free --control

**Spends real requests.** One per model per prompt, on whichever provider the slug names. The
OpenRouter `:free` tier is not drawn from the Google quota; `--control` adds one Gemini call.

## Why this file exists

`agents/persona_agent.py` is also the GENERAL route, and GENERAL is **the only route that can
file an uploaded document**. If its model stops emitting tool calls, `save_to_vault` and
`process_inbox_file` stop working — and the model does not say so. It says "I've written that
down", because that is what the conversation looks like it should end with.

On 2026-08-29 `minimax/minimax-m2.7:free` was switched in on the strength of its OpenRouter
page, which advertises `tools` and `tool_choice`. It does support them. Measured:

    bare 10-word prompt, tools bound                tool_calls  3/3
    the real 6,608-char persona prompt              tool_calls  0/3
    the same prompt, gemini-3.5-flash-lite          tool_calls  1/1

**A short probe passes and the real thing fails.** That is the whole reason this file tests
against `PERSONA_PROMPT_TEMPLATE` itself rather than a toy prompt — and the reason a model's
own capability list is not evidence.

## What "passing" means

Emitting a `tool_call` for an utterance that plainly asks to be remembered. Not the text of
the reply — the reply is the thing that lies. Two of minimax's three misses claimed the note
was written.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Utterances that must produce a tool call. Ordinary things LB says, not instructions to use a
# tool — "call save_to_vault" proves only that the wire format works, which was never in doubt.
PROMPTS: tuple[str, ...] = (
    "remember that I'm using the 2N3904 for the preamp",
    "make a note that the regulator needs a heatsink",
    "save this to my vault: the scope probe is set to 10x",
)


def build(slug: str, temperature: float):
    """A chat model for `slug`, OpenRouter when it has a "/" in it, Gemini otherwise."""
    from engine.models import (LLM_MAX_RETRIES, OPENROUTER_API_KEY,   # noqa: PLC0415
                               OPENROUTER_BASE_URL)

    if "/" in slug:
        from langchain_openai import ChatOpenAI                       # noqa: PLC0415
        if not OPENROUTER_API_KEY:
            sys.exit("  OPENROUTER_API_KEY is not set — nothing to probe against.")
        return ChatOpenAI(model=slug, temperature=temperature, max_retries=LLM_MAX_RETRIES,
                          base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY,
                          default_headers={"X-Title": "Mr Odd Ball EE Copilot"})

    from langchain_google_genai import ChatGoogleGenerativeAI          # noqa: PLC0415
    return ChatGoogleGenerativeAI(model=slug, temperature=temperature,
                                  max_retries=LLM_MAX_RETRIES)


def probe(slug: str, temperature: float) -> tuple[int, int, list[str]]:
    """Run every prompt through `slug`. Returns (calls, total, notes about lies)."""
    import agents.persona_agent as PA                                  # noqa: PLC0415
    from tools.file_manager import FILE_TOOLS                          # noqa: PLC0415
    from tools.knowledge_vault import VAULT_TOOLS                      # noqa: PLC0415
    from tools.memory_manager import format_memory_for_llm             # noqa: PLC0415

    llm = build(slug, temperature).bind_tools(VAULT_TOOLS + FILE_TOOLS)
    called = 0
    lies: list[str] = []

    for question in PROMPTS:
        prompt = PA.PERSONA_PROMPT_TEMPLATE.format(
            chat_history=format_memory_for_llm(), question=question)
        try:
            reply = llm.invoke(prompt)
        except Exception as exc:                                       # noqa: BLE001
            print(f"    {question[:44]!r:48} ERROR {type(exc).__name__}: {exc}"[:150])
            continue

        calls = getattr(reply, "tool_calls", None) or []
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        called += bool(calls)

        # The dangerous case: no tool call AND a claim that one happened.
        claimed = any(p in text.lower() for p in
                      ("written that down", "wrote that down", "written it down",
                       "saved that", "saved it", "noted that down", "added that"))
        if not calls and claimed:
            lies.append(text.strip()[:70])

        mark = "OK  " if calls else ("LIE " if claimed else "--  ")
        print(f"    {mark}{question[:42]!r:46} "
              f"tools={len(calls)}  {text.strip()[:52]!r}")

    return called, len(PROMPTS), lies


def main(argv: "list[str] | None" = None) -> int:
    from engine.models import PERSONA_MODEL                            # noqa: PLC0415

    ap = argparse.ArgumentParser(description="does this model call tools under the real prompt?")
    ap.add_argument("--models", nargs="+", default=[PERSONA_MODEL])
    ap.add_argument("--temperature", type=float, default=0.8,
                    help="the persona runs at 0.8; lower it to see if that is the variable")
    ap.add_argument("--control", action="store_true",
                    help="also run gemini-3.5-flash-lite, which is known to pass")
    args = ap.parse_args(argv)

    import agents.persona_agent as PA                                  # noqa: PLC0415
    from tools.memory_manager import format_memory_for_llm             # noqa: PLC0415
    size = len(PA.PERSONA_PROMPT_TEMPLATE.format(
        chat_history=format_memory_for_llm(), question=PROMPTS[0]))
    print(f"\n  the real persona prompt is {size:,} characters. That is the point of this file.\n")

    slugs = list(args.models) + (["gemini-3.5-flash-lite"] if args.control else [])
    verdicts: list[tuple[str, int, int, int]] = []

    for slug in slugs:
        print(f"  {slug}  (temperature {args.temperature})")
        called, total, lies = probe(slug, args.temperature)
        verdicts.append((slug, called, total, len(lies)))
        print()

    print("=" * 78)
    for slug, called, total, lies in verdicts:
        verdict = "USABLE" if called == total else ("PARTIAL" if called else "UNUSABLE")
        extra = f", and claimed success {lies}x without calling anything" if lies else ""
        print(f"  {slug:44} {called}/{total} {verdict}{extra}")
    print("=" * 78)
    print("  A model is only usable for PERSONA if it calls tools on ALL of these. That route")
    print("  is also GENERAL, and GENERAL is the only route that can file an upload.\n")

    return 0 if all(c == t for _, c, t, _ in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
