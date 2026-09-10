#!/usr/bin/env python3
"""
Module:  llm_text.py
Purpose: Get the actual text out of an LLM response, and nothing else.
Author:  LB
Date:    2026-08-19

`AIMessage.content` is **not reliably a string**. Gemini returns a list of content blocks:

    [{'type': 'text', 'text': 'To route a 5A power line...',
      'extras': {'signature': 'EtofCtcfARFNMg9wFs0jMC74Xl2Jbgfa...'}}]

...where `signature` is several kilobytes of base64. `str()` on that gives you a stringified
Python dict with the whole blob inside it, and every downstream consumer then sees a "reply"
that is 95% cryptographic padding.

This was already solved once, in `agents/web_agent.py`, as `extract_text_content()` — LB wrote
it precisely because the signature was reaching the final answer. It is lifted here because
the hardware and math agents needed the same thing and did not have it: their second-pass
summaries were returning the raw block list, which meant the `SPOKEN:` line was buried inside
a stringified dict where the splitter could not find it, and every answer fell back to "the
numbers are on the screen" while the signature blob went up on a card.

One copy, used by every agent, so the next agent added cannot forget.
"""

from __future__ import annotations

__all__ = ["extract_text_content"]


def extract_text_content(content) -> str:
    """The human-readable text of an LLM response, with API metadata stripped.

    Args:
        content: an `AIMessage.content` — a string, or a list of blocks, or something else.

    Returns:
        The concatenated text. Never None; falls back to `str()` so a shape nobody anticipated
        degrades to something readable rather than to an exception on the answer path.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # "text" is the only key worth reading. "extras"/"signature"/"thought" are
                # transport detail and are exactly what must not reach the answer.
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)

    return str(content) if content is not None else ""
