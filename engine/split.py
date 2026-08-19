#!/usr/bin/env python3
"""
Module:  split.py
Purpose: Turn one agent's reply into a spoken half and a shown half.
Author:  LB
Date:    2026-08-19

    python -m engine.split "some agent reply with a ```c block``` in it"

## Two ways to get the spoken half, and the good one comes first

**Primary: the agent says what to say.** Every agent prompt ends with an instruction to close
its reply with a `SPOKEN:` line — one sentence, at most 40 words, no code, no symbols. That is
by far the most reliable source, because the model writing the sentence is the one that knows
which of three numbers was the answer and which two were working. A summariser reading the
finished reply has to guess that, and it guesses wrong on exactly the replies that matter.

**Fallback: extract one.** When the line is missing — the model ignored it, or the reply came
from a tool path that never saw the prompt — `memory.speakable.extract()` picks the best
sentence the reply already contains. Extraction, never generation: D30 measured local models
stating first-year electronics relationships fluently and wrongly, and a generated summary of
a correct answer can be wrong in the same way, one step further from anywhere it would be
noticed.

**Last resort: a per-route line that admits what happened.** Better than silence, and better
than reading a code block aloud.

## The filter is the load-bearing part

Whatever produced the spoken half, it goes through `is_speakable()` before it is allowed out.
That is deliberate: it means the safe path and the risky path are policed identically, so the
safe one cannot quietly drift. A `SPOKEN:` line containing a code fence is rejected exactly as
hard as an extracted one would be.

What can never be spoken:

    fenced code            "backtick backtick backtick c" is not a sentence
    markdown tables        a table read aloud is a stream of pipes
    URLs and file paths    "h t t p s colon slash slash" — and he cannot click it for you
    hex and bitmasks       "0x3F" is worse aloud than "the low six bits"
    tracebacks             the useful line is the last one; the rest is noise
    UNSPEAKABLE glyphs     τ Ω μ π √ ² ³ × ÷ ≈ ° Δ ω ∞ ± ≤ ≥ — formulas.py already lists these
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine.response import Card, CardKind, Response
from memory import speakable
from orchestrator.formulas import unspeakable

__all__ = ["split", "is_speakable", "SPOKEN_INSTRUCTION", "MAX_WORDS"]

MAX_WORDS = speakable.MAX_WORDS          # 40. D32's ceiling, ~15s of Piper audio.

# Appended to every agent prompt template. Kept here rather than in each agent so the
# instruction and the parser that reads it cannot disagree — they change together or not
# at all.
SPOKEN_INSTRUCTION = """
Finally, on its own last line, write:
SPOKEN: <one sentence a person could understand hearing it once, at most 40 words>

That line is read aloud by a text-to-speech voice and is often the ONLY part heard, so it must
answer the question by itself. Do not write "see the code above" or "as shown" — say the actual
answer. No code, no symbols, no URLs, no file paths, no hex numbers: write "bit thirteen of the
GPIO enable register", not "BIT13 in GPIO_ENABLE_REG". Spell out units: "ohms", not the symbol.
"""

# --- what a reply is made of -----------------------------------------------------------

_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)(?:```|\Z)", re.DOTALL)
_SPOKEN_LINE = re.compile(r"^[ \t>*_-]*SPOKEN[:：][ \t]*(.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)

# A markdown table: two or more consecutive lines containing a pipe, at least one of which is
# a separator row. Requiring the separator is what stops a prose sentence with a pipe in it
# from being torn out of the narration.
_TABLE = re.compile(
    r"(?:^[ \t]*\|.*\|[ \t]*$\n)+?^[ \t]*\|[ \t:|-]+\|[ \t]*$\n(?:^[ \t]*\|.*\|[ \t]*$\n?)*",
    re.MULTILINE)

# The tool paths write these headers verbatim. os_controller.py returns "Terminal Output:\n..."
# and "Terminal Error:\n...", math_agent "Python Sandbox Execution Result:\n...", and
# hardware_agent "Tool Execution Result: ...". They are logs, not speech.
_LOG_HEADERS = (
    "Terminal Output:", "Terminal Error:", "Python Sandbox Execution Result:",
    "Tool Execution Result:", "Web Search Result:", "OS Execution Result:",
)

# --- the speech filter -----------------------------------------------------------------

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_PATH = re.compile(r"(?:^|\s)(?:/[\w.-]+){2,}/?|\b[A-Za-z]:\\[\\\w.-]+")
_HEX = re.compile(r"(?i)\b0x[0-9a-f]+\b")
_TRACEBACK = re.compile(r"(?i)\btraceback \(most recent call last\)|^\s*File \"", re.MULTILINE)
# Identifier_with_underscores, or CamelCase shouting like REG_WRITE — reads terribly aloud.
_CODE_IDENT = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b|\b[A-Z]{2,}[0-9]*\(")


@dataclass(frozen=True)
class Rejection:
    """Why a candidate spoken line was refused. Returned so the harness can assert on it."""

    reason: str
    detail: str = ""

    def __bool__(self) -> bool:      # a Rejection is always falsey; "no rejection" is None
        return False


def is_speakable(text: str, max_words: int = MAX_WORDS) -> Rejection | None:
    """None if `text` is safe to say out loud, otherwise why it is not.

    Applied to EVERY candidate whatever produced it — the agent's own SPOKEN line, an
    extracted sentence, or a hand-written fallback. Policing the safe path identically to the
    risky one is what stops the safe path drifting.
    """
    if not text or not text.strip():
        return Rejection("empty")
    if "```" in text:
        return Rejection("fenced code")
    if _TABLE.search(text + "\n"):
        return Rejection("markdown table")
    if "|" in text and text.count("|") >= 2:
        return Rejection("table pipes")
    if _URL.search(text):
        return Rejection("url", _URL.search(text).group(0)[:40])
    if _PATH.search(text):
        return Rejection("file path", _PATH.search(text).group(0).strip()[:40])
    if _HEX.search(text):
        return Rejection("hex literal", _HEX.search(text).group(0))
    if _TRACEBACK.search(text):
        return Rejection("traceback")
    if _CODE_IDENT.search(text):
        return Rejection("code identifier", _CODE_IDENT.search(text).group(0)[:40])
    bad = unspeakable(text)
    if bad:
        return Rejection("unspeakable characters", bad)
    words = len(text.split())
    if words > max_words:
        return Rejection("too long", f"{words} words, budget {max_words}")
    return None


# --- pulling the reply apart -----------------------------------------------------------

def _take_spoken_line(reply: str) -> tuple[str, str]:
    """Split off the SPOKEN: line. Returns (the line's content, the reply without it).

    Takes the LAST match, because the instruction says "on its own last line" and a model that
    also quotes the instruction back in an example would otherwise have the example taken as
    the answer.
    """
    matches = list(_SPOKEN_LINE.finditer(reply))
    if not matches:
        return "", reply
    last = matches[-1]
    return last.group(1).strip(), (reply[:last.start()] + reply[last.end():]).strip()


def _title_for(kind: str, lang: str, index: int) -> str:
    if kind == CardKind.CODE:
        return {"c": "C", "cpp": "C++", "python": "Python", "bash": "Shell",
                "sh": "Shell", "json": "JSON"}.get(lang.lower(), lang.upper() or "Code")
    return {CardKind.TABLE: "Table", CardKind.LOG: "Output",
            CardKind.MARKDOWN: "Detail"}.get(kind, "")


def _extract_cards(reply: str) -> tuple[list[Card], str]:
    """Pull the unspeakable blocks out. Returns (cards, the prose that was left).

    Order matters: fences first, because a table inside a fenced block is code, not a table.
    """
    cards: list[Card] = []
    rest = reply

    for i, m in enumerate(_FENCE.finditer(reply)):
        lang, body = m.group(1), m.group(2).rstrip()
        if body.strip():
            cards.append(Card(CardKind.CODE, _title_for(CardKind.CODE, lang, i), body, lang))
    rest = _FENCE.sub("\n", rest)

    for m in _TABLE.finditer(rest + "\n"):
        body = m.group(0).strip()
        if body:
            cards.append(Card(CardKind.TABLE, "Table", body))
    rest = _TABLE.sub("\n", rest + "\n")

    # Tool output: everything from the header to the end of that block.
    for header in _LOG_HEADERS:
        idx = rest.find(header)
        while idx != -1:
            body = rest[idx + len(header):].strip()
            if body:
                cards.append(Card(CardKind.LOG, header.rstrip(":"), body))
            rest = rest[:idx]
            idx = rest.find(header)

    return cards, re.sub(r"\n{3,}", "\n\n", rest).strip()


def split(reply: str, route: str = "", fallback: str = "") -> Response:
    """Turn one agent reply into a Response.

    Args:
        reply:    the agent's raw text.
        route:    the AgentRoute name, recorded on the Response and used to pick a fallback.
        fallback: what to say when nothing survives. Defaults per route.

    Never raises, and never returns a Response whose `speech` fails `is_speakable`. A turn that
    would otherwise say nothing gets the fallback instead — silence reads as a crash.
    """
    raw = reply or ""
    spoken, remainder = _take_spoken_line(raw)
    cards, prose = _extract_cards(remainder)

    # 1. The agent's own line, if it survives the filter.
    speech = spoken if spoken and is_speakable(spoken) is None else ""

    # 2. Extract one from the prose that is left.
    if not speech and prose:
        got = speakable.extract(prose, max_words=MAX_WORDS)
        if got and is_speakable(got.text) is None:
            speech = got.text

    # 3. A rejected SPOKEN line is still evidence of intent — try its first sentence alone,
    #    which is usually the answer with a code identifier trailing after it.
    if not speech and spoken:
        first = speakable.sentences(spoken)
        if first and is_speakable(first[0]) is None:
            speech = first[0]

    # 4. Say something.
    if not speech:
        speech = fallback or _FALLBACKS.get(route, _FALLBACKS[""])

    # Prose that did not become the spoken line still belongs on screen — it is the reasoning,
    # and dropping it would make the panel less useful than the terminal was.
    if prose and prose != speech:
        cards.insert(0, Card(CardKind.MARKDOWN, "", prose))

    return Response(speech=speech, cards=cards, route=route, raw=raw)


# Per-route, because "I've put it on the screen" is the honest thing to say for a firmware
# answer and the wrong thing to say for a joke.
_FALLBACKS = {
    "firmware": "I've put the code and the register details on the screen.",
    "hardware": "The numbers are on the screen.",
    "math": "I've worked it out — the result is on the screen.",
    "os": "That's done. The output is on the screen.",
    "web": "I've put what I found on the screen.",
    "": "I've put the answer on the screen.",
}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="split an agent reply (debug CLI)")
    ap.add_argument("reply", nargs="?", help="the reply text; omit to read stdin")
    ap.add_argument("--route", default="")
    args = ap.parse_args(argv)

    text = args.reply if args.reply is not None else sys.stdin.read()
    got = split(text, route=args.route)
    print(f"SPEECH ({len(got.speech.split())} words): {got.speech}")
    for c in got.cards:
        print(f"CARD [{c.kind}{'/' + c.lang if c.lang else ''}] {c.title!r}: "
              f"{c.body[:70]!r}{'...' if len(c.body) > 70 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
