#!/usr/bin/env python3
"""
Module:  speakable.py
Purpose: Turn a retrieved passage into something he can say out loud — and prove it is safe
         to say before it is ever stored.
Author:  LB
Date:    2026-08-14

    python -m memory.speakable "some long passage of course text..."

## Where this came from

Read across from PiSugar's `whisplay-ai-chatbot` (GPL-3.0 — **read, not copied**), which has
an `ENABLE_KNOWLEDGE_SUMMARY` flag: at index time it asks an LLM to summarise each chunk to
30 words and stores the result. The problem it solves is ours exactly — D32 measured that
Piper runs ~160 wpm, so **40 words is about 15 seconds of audio**, and a retrieved paragraph
is unusable as a spoken answer.

**Their implementation would be unsafe here, and this repo already has the measurement that
says so.** D30 caught every candidate local model stating first-year electronics relationships
fluently and wrongly — asked for the RC time constant it answered *"add the product of
resistance and capacitance, then divide by the frequency"*. Phase 4's whole architecture
follows from that: the answer path is **extractive**, and the model never writes the sentence.
Letting a model paraphrase a formula at index time reintroduces exactly that failure, one step
earlier and harder to see, because a wrong summary is then stored and trusted forever.

## So this is extraction first, generation last, and verification always

1. **Extract.** Pick the best sentence (or two) the source already contains. Nothing is
   invented, so nothing can be invented wrongly. This handles definitions, statements of law,
   and most `chunk_type = definition | formula` rows — which is the majority of what a
   coursework corpus is made of.

2. **Refuse.** If no span of the source is speakable within the budget, return **None**.
   `speakable = ''` is already legal in the Phase 4 schema and means "indexed, not quotable
   as-is" — the chunk is still retrievable, it just cannot be read aloud verbatim.

3. **Generate — off the Pi, opt-in, and never for a formula.** Only when extraction fails,
   only on the build box where Claude or Gemini are available, and never for
   `chunk_type in ('formula', 'table')`, which are precisely what D30 measured models
   mangling. The generator is injected, so this module never imports one.

4. **Verify, whatever produced it.** Every candidate goes through `verify()` before it is
   stored, extracted or generated alike.

## The check that makes this worth having

**Every number in the summary must appear in the source.**

That is the `formulas.Worked` contract applied to prose. A hand-typed answer in a table teaches
LB the wrong number without anything going red; a summary that invents a figure does the same
thing at scale and with a citation attached. Numbers are what a wrong answer costs him a burnt
component or a bad lab result over, so numbers are what get checked.

An extracted sentence passes this trivially — which is the point. It is the generated path the
check exists to police, and it is enforced identically on both so the safe path cannot drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from orchestrator.formulas import UNSPEAKABLE, unspeakable

__all__ = ["Speakable", "extract", "verify", "make_speakable", "MAX_WORDS"]

# D32's ceiling, measured rather than chosen: Piper runs ~160 words per minute, so 40 words is
# about 15 seconds of audio. `formulas.py`'s longest entry is 33 words.
MAX_WORDS = 40

# A sentence starting with one of these is a fragment of an argument, not an answer. Phase 4's
# plan names the failure directly: a `theory` paragraph pulled mid-derivation opens
# "Substituting this into the previous result..." and is nonsense read aloud.
_CONNECTIVES = (
    "substituting", "therefore", "thus", "hence", "consequently", "conversely",
    "similarly", "likewise", "however", "moreover", "furthermore", "nevertheless",
    "this", "these", "those", "that", "it follows", "in this case", "as before",
    "again", "next", "then", "so", "but", "and", "or", "also", "here", "there",
    "note that", "recall that", "from the above", "in the previous", "as shown",
    "as we saw", "combining", "rearranging", "solving", "equating",
)

# Cross-references cannot survive being read aloud: "see figure 3" and "in Table 2.1" name
# things a listener does not have.
_REFERENCES = re.compile(
    r"(?i)\b(fig(ure)?|table|eq(uation)?|section|chapter|appendix|ref)\b\.?\s*[\d(]")

# Numbers, including decimals, signs and exponents. Used for the grounding check, so it has to
# match what a person would call a number rather than every digit in a word.
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Speakable:
    """One passage, reduced to something he can say.

    Args:
        text:     exactly what he would say. Verified.
        source:   the passage it came from, for the audit trail.
        method:   "extract" or "generate" — which path produced it.
        words:    length, so a caller can budget without recounting.
    """

    text: str
    source: str
    method: str
    words: int


def sentences(text: str) -> list[str]:
    """Split prose into sentences. Deliberately simple — no NLP dependency."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if not flat:
        return []
    return [s.strip() for s in _SENTENCE_END.split(flat) if s.strip()]


def _starts_with_connective(sentence: str) -> bool:
    """Does this open mid-argument?

    The comma matters and was missed on the first pass: textbooks write *"Rearranging, we
    find..."* at least as often as *"Rearranging the expression..."*, and a plain
    `startswith(word + " ")` sees the first as a sentence beginning with the word
    "rearranging," — which is not in the list. Caught by `tools/verify_speakable.py`.
    """
    lowered = sentence.lower().lstrip("\"'([ ")
    return any(lowered == word or lowered.startswith(word + " ")
               or lowered.startswith(word + ",") for word in _CONNECTIVES)


def verify(text: str, source: str) -> list[str]:
    """Every reason `text` is not safe to say. Empty list means it is.

    Args:
        text:   the candidate spoken line.
        source: the passage it claims to summarise.

    Returns:
        Reasons, as sentences a person can act on. Applied identically to extracted and
        generated candidates, so the safe path cannot quietly drift from the checked one.
    """
    problems: list[str] = []
    stripped = (text or "").strip()

    if not stripped:
        return ["it is empty"]

    words = len(stripped.split())
    if words > MAX_WORDS:
        problems.append(f"{words} words, over the {MAX_WORDS}-word ceiling "
                        f"({words * 60 // 160}s of Piper)")
    if not stripped.endswith((".", "!", "?")):
        problems.append("it does not end on a sentence boundary, which Piper streams on")
    if not stripped.isascii():
        bad = "".join(sorted({c for c in stripped if not c.isascii()}))
        problems.append(f"non-ASCII characters: {bad!r}")

    unsayable = unspeakable(stripped)
    if unsayable:
        problems.append(f"characters Piper cannot say: {unsayable!r}")

    for symbol in ("*", "/", "^", "=", "<", ">", "_", "|"):
        if symbol in stripped:
            problems.append(f"a bare {symbol!r} operator, which reads as silence")

    if _REFERENCES.search(stripped):
        problems.append("a cross-reference to something the listener cannot see")

    if _starts_with_connective(stripped):
        problems.append("it opens mid-argument, so it is a fragment rather than an answer")

    # THE GROUNDING CHECK. Numbers are where a wrong answer costs LB a burnt component, so
    # numbers are what must have come from the source rather than from a model.
    if source:
        in_source = set(_NUMBER.findall(source))
        invented = [n for n in _NUMBER.findall(stripped) if n not in in_source]
        if invented:
            problems.append(f"numbers that are not in the source: {sorted(set(invented))}")

    return problems


def _score(sentence: str, source: str) -> float:
    """How good a spoken answer this sentence would make. Higher is better."""
    words = len(sentence.split())
    if words == 0:
        return -1e9
    score = 0.0
    # Prefer sentences that use most of the budget without exceeding it: a four-word fragment
    # is technically speakable and answers nothing.
    score += min(words, MAX_WORDS) / MAX_WORDS * 10.0
    if words > MAX_WORDS:
        score -= 100.0
    # The first sentence of a chunk is usually its topic sentence, which is what a definition
    # actually lives in.
    first = sentences(source)
    if first and sentence == first[0]:
        score += 4.0
    if _starts_with_connective(sentence):
        score -= 50.0
    if _REFERENCES.search(sentence):
        score -= 50.0
    # A definitional shape — "X is ...", "X means ..." — is what a listener asked a question.
    if re.search(r"(?i)\b(is|are|means|refers to|equals|is called|is defined)\b", sentence):
        score += 3.0
    return score


def extract(source: str, max_words: int = MAX_WORDS) -> Speakable | None:
    """The best speakable span the source already contains, or None.

    Args:
        source: the passage.
        max_words: budget. Defaults to D32's 40-word ceiling.

    Returns:
        A verified `Speakable`, or **None** if nothing in the source can be said as-is. None
        is a normal outcome and is not a failure — the Phase 4 schema already allows
        `speakable = ''`, meaning "indexed, retrievable, not quotable aloud".

    Nothing is invented, so nothing can be invented wrongly. That is the whole argument for
    trying this before any generator.
    """
    candidates = [s for s in sentences(source) if len(s.split()) <= max_words]
    if not candidates:
        return None

    # Best single sentence first.
    best = max(candidates, key=lambda s: _score(s, source))
    if not verify(best, source):
        return Speakable(text=best, source=source, method="extract",
                         words=len(best.split()))

    # Then the best pair that still fits — a definition plus its consequence, which is the
    # house style in formulas.py and define.py.
    for i in range(len(candidates) - 1):
        pair = f"{candidates[i]} {candidates[i + 1]}"
        if len(pair.split()) <= max_words and not verify(pair, source):
            return Speakable(text=pair, source=source, method="extract",
                             words=len(pair.split()))

    # Any sentence that verifies at all.
    for sentence in sorted(candidates, key=lambda s: -_score(s, source)):
        if not verify(sentence, source):
            return Speakable(text=sentence, source=source, method="extract",
                             words=len(sentence.split()))
    return None


def make_speakable(
    source: str,
    chunk_type: str = "theory",
    generator: Callable[[str], str] | None = None,
    max_words: int = MAX_WORDS,
) -> Speakable | None:
    """Extraction first, generation last, verification always.

    Args:
        source:     the passage.
        chunk_type: Phase 4's type. `formula` and `table` are NEVER generated.
        generator:  optional `text -> summary`. Injected, so this module imports no model and
                    stays testable with a stub. Runs on the BUILD box, never on the Pi.
        max_words:  budget.

    Returns:
        A verified `Speakable`, or None.

    **`formula` and `table` never reach the generator**, and that is not configuration — it is
    the D30 measurement written as a branch. Those two types are exactly what every model was
    caught mangling, so for them extraction is the only path and refusing is the fallback.
    """
    found = extract(source, max_words)
    if found is not None:
        return found

    if generator is None:
        return None
    if chunk_type in ("formula", "table"):
        return None

    try:
        written = generator(source)
    except Exception:                                              # noqa: BLE001
        return None

    problems = verify(written or "", source)
    if problems:
        return None
    return Speakable(text=written.strip(), source=source, method="generate",
                     words=len(written.split()))


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    passages = args or [
        "Kirchhoff's current law states that the algebraic sum of currents entering a node "
        "is zero. Substituting this into the loop equation above and rearranging gives the "
        "result shown in Figure 4.2, which we will use throughout the remainder of the "
        "chapter to analyse ladder networks of arbitrary depth.",
    ]
    for passage in passages:
        got = extract(passage)
        print(f"\nsource ({len(passage.split())} words): {passage[:88]}...")
        if got:
            print(f"  -> [{got.method}, {got.words} words] {got.text}")
        else:
            print("  -> (nothing in it is speakable as-is; speakable = '')")
    check = verify("The time constant is 47 milliseconds.", "tau equals R times C.")
    print(f"\ngrounding check demo: {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
