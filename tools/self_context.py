#!/usr/bin/env python3
"""
Module:  self_context.py
Purpose: The block every agent gets before it answers — LB's rules, his own mistakes, his state.
Author:  LB
Date:    2026-08-25

    python tools/self_context.py
    python tools/self_context.py "open firefox"

## The one seam

There is no single "system prompt" in this repo. Seven agents each build their own — the
firmware agent has retrieval in its prompt, the persona agent has none, the OS agent has a
worked example. Adding three new blocks to seven templates would be seven places for them to
drift, and the eighth agent written next month would silently not have them.

But **every one of those agents already calls `tools.memory_manager.format_memory_for_llm()`**
and interpolates the result as `{chat_history}`. That is the seam, it already exists, and
`tools/verify_engine.py` already monkeypatches it — which is the proof that the repo already
treats it as the place where shared context enters a prompt.

So this module composes the block and `format_memory_for_llm()` prepends it. One function
changed, seven agents affected, no prompt template touched, and an agent written next month
gets it by doing what every other agent already does.

## The order is the argument

    1. STANDING CORRECTIONS   what LB told him. An instruction. Overrides everything.
    2. PAST MISTAKES          what went wrong on its own. A datum. To be considered.
    3. WHAT YOU ARE RIGHT NOW temperature, memory, ports, what he can actually do.

Corrections lead because they are the only one of the three with authority, and because a model
reading a long prompt weights the top of it. Putting the temperature first would put a number he
rarely needs above a rule he must never break.

The three are separate files and stay separate — see `tools/corrections.py` for why merging a
rule LB gave with a timeout he happened to hit would either soften the rule or harden the
timeout, and both are wrong.

## Cost

Read from disk on every agent call, and that is affordable because every piece is small and
already bounded by its own module: `corrections.MAX_PROMPT_CHARS` (3k), `reflections`
(2k, six entries), and a `system_state` snapshot cached for fifteen seconds. `MAX_CHARS` here is
a backstop over the total, so a bug in any one of them cannot eat the context window on its own.

Set `ODDBALL_SELF_CONTEXT=0` to turn the whole thing off — for a clean A/B, and because a
feature that changes every prompt in the system needs an off switch that does not require an
edit. `ODDBALL_STATE=0` drops only the machine-state block, which is the noisiest of the three
and the one LB is most likely to want gone.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Run directly as `python tools/self_context.py`, this file is not inside a package and the repo
# root is not on the path, so the `from tools import ...` calls inside `preamble` fail with
# ModuleNotFoundError. The guard fires ONLY in that case — imported normally as
# `tools.self_context`, `__package__` is "tools" and nothing here runs.
#
# Every documented CLI in this repo has to work when it is typed, and the ones in the docstring
# above are the first thing to reach for when he starts behaving oddly.
if __package__ in (None, ""):                                          # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOG = logging.getLogger("oddball.self")

__all__ = ["preamble", "set_question", "current_question", "MAX_CHARS"]

# A backstop over the three blocks together, in characters. Roughly 1,500 tokens at the
# 4-chars-per-token rule this repo uses elsewhere. Each block bounds itself; this bounds the sum,
# because "each of three things is individually reasonable" is how a context window gets eaten.
MAX_CHARS = 6_000

# The question being answered right now, so `reflections.similar()` can surface the failures
# that actually bear on it.
#
# **Module state rather than an argument, deliberately.** The alternative is a `question`
# parameter threaded through `format_memory_for_llm()` and into all seven agents' prompt
# construction — seven edits, and the eighth agent forgets. `Engine.ask()` sets this once at the
# top of a turn and the engine answers exactly one turn at a time (`run_voice._typed_thread`
# guards that with `in_turn`), so there is no interleaving to get wrong.
#
# It is a hint that improves relevance, never a correctness requirement: stale or empty, the
# preamble is still correct, it just falls back to the most recent mistakes instead of the most
# relevant ones. That property is what makes the shortcut acceptable.
_question: str = ""


def set_question(text: str) -> None:
    """Tell the preamble what is being asked, so past mistakes can be matched against it."""
    global _question
    _question = str(text or "")[:500]


def current_question() -> str:
    return _question


def _enabled(name: str) -> bool:
    """An env kill switch, off only for an explicit "0"/"false"/"no".

    Defaulting to ON matters: an unset variable is the normal state on the Pi, and a feature
    that silently does nothing until an environment variable is set is a feature LB would have
    to debug before he could use it.
    """
    return str(os.environ.get(name, "1")).strip().lower() not in ("0", "false", "no", "off")


def preamble(question: str | None = None) -> str:
    """The self-context block. **Never raises; returns "" when there is nothing to say.**

    Args:
        question: the current question, for matching past mistakes. Defaults to whatever
                  `set_question` was last given.

    Returns:
        A block ending in a newline, or "". Every sub-block is independently optional — a fresh
        install with no corrections and no mistakes yet still gets the machine state, and a
        machine that cannot read its own sensors still gets the rules.
    """
    if not _enabled("ODDBALL_SELF_CONTEXT"):
        return ""

    asked = _question if question is None else question
    parts: list[str] = []

    # Each block is imported and called inside its own try. One of the three failing must not
    # cost the other two — a corrupt reflection ledger should not take LB's standing rules out
    # of every prompt in the system.
    try:
        from tools import corrections
        parts.append(corrections.for_prompt())
    except Exception:                                                     # noqa: BLE001
        LOG.exception("standing corrections unavailable for this prompt")

    try:
        from tools import reflections
        parts.append(reflections.for_prompt(asked))
    except Exception:                                                     # noqa: BLE001
        LOG.exception("past mistakes unavailable for this prompt")

    if _enabled("ODDBALL_STATE"):
        try:
            from tools import system_state
            parts.append(system_state.for_prompt())
        except Exception:                                                 # noqa: BLE001
            LOG.exception("machine state unavailable for this prompt")

    block = "".join(p for p in parts if p)
    if not block.strip():
        return ""

    if len(block) > MAX_CHARS:
        # Cut from the END, so corrections survive and the machine state is what is lost. That
        # ordering is the whole point of the file and it has to hold under truncation too.
        block = block[:MAX_CHARS].rstrip() + "\n…(self-context truncated)\n"
        LOG.warning("self-context exceeded %d chars and was cut", MAX_CHARS)

    return "=== BEFORE YOU ANSWER ===" + block + "=== END ===\n\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="the block prepended to every agent prompt")
    ap.add_argument("question", nargs="?", default="",
                    help="a question, to see which past mistakes it surfaces")
    args = ap.parse_args(argv)

    block = preamble(args.question)
    if not block:
        print("  (nothing to inject — no corrections, no mistakes, no readable state)")
        return 0
    print(block)
    print(f"  --- {len(block)} characters, ceiling is {MAX_CHARS} ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
