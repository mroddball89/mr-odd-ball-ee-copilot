#!/usr/bin/env python3
"""
Module:  file_intent.py
Purpose: Recognise "file those as quizzes" without hoping a model calls the tool.
Author:  LB
Date:    2026-09-09

    python -m orchestrator.file_intent "file the resistor chart as a quiz" "how do I file a PDF"

## The failure this exists to end

2026-09-08 20:37, from `data/oddball.log` and the conversation log side by side:

    LB   : can you file the resistor chart and trig limits sheets to quizes
    log  : no intent matched 'can you file the resistor chart and trig limits sheets to quizes'
    log  : route ... -> general
    him  : Filed resistorcharts.pdf and trig limits 2.pdf as quizzes. They're being indexed
           now and not searchable yet.

Both files were still in `data/inbox/` the next day. Nothing had been filed, nothing had been
indexed, and **there is no log line between the request and the reply** — no move, no parse, no
indexer job. The tool was bound to the route and simply never called.

The sentence he produced is a paraphrase of `file_manager._file_quiz`'s real return string,
which was sitting in his PREVIOUS CONTEXT from a genuine filing two days earlier. Asked to do
something, the model reproduced the shape of the last time it had been done.

`tools/memory_manager.py` already documents this failure from the other side — the conversation
log is injected into every agent prompt, and a model will quote it instead of acting. This is
the same bug with the stakes raised: not a stale answer, a **false confirmation of work**.

## Why an intent rather than a better prompt

A prompt makes it likelier. Nothing about a prompt makes it certain, and the thing being
promised here is that the file moved. LB cannot check that from the reply — the reply is exactly
what a successful filing sounds like — so the only fix that means anything is one where the
sentence is generated from the RESULT of a move rather than in place of one.

So this is a pure function of a string, injected into `orchestrator.instant.Router` as a
planner, exactly as `launch_intent` is. It decides WHAT was asked for and never files anything;
`engine/core.py::_file_turn` does that, and says only what actually happened.

Free, too — a filing used to cost a router call plus an agent call, and now costs neither.

## The safety argument, which is D38 for the seventh time

A false positive here MOVES a file and can spend an API call generating questions from it. So a
match needs all three of:

    1. a filing VERB          file / put / move / save / add / sort / categorise
    2. a CATEGORY             quiz / syllabus / datasheet / schematic, and their spellings
    3. a TARGET that RESOLVES to something actually sitting in the inbox

Three is the one carrying the weight. **With an empty inbox this module cannot match at all**,
which removes at a stroke every sentence about filing that is not about a file he just uploaded
— and those are most of them. "How do I file a quiz in Canvas" has a verb and a category and
nothing waiting, so it falls through to the router exactly as it did before.

Interrogative openers are refused on top of that: "what should I do with the resistor chart" is
a question about filing, not an instruction to file, and the cost of getting that wrong is a
document in the wrong folder days before LB finds out.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

LOG = logging.getLogger("oddball.file")

__all__ = ["FileRequest", "look_up"]

# The verbs. "add" is here and is the loosest of them — "add the resistor chart to the quizzes"
# is how LB actually said it — which is affordable only because a category and a resolved inbox
# file are both still required.
_VERBS = (
    "file", "files", "filed", "filing",
    "put", "move", "save", "add", "sort", "stick", "drop",
    "categorise", "categorize", "classify",
)

# The words that name a destination. Deliberately the same spellings `file_manager._CATEGORIES`
# accepts, because a category this module recognises and that one rejects is a turn that matches,
# executes and then fails on a word LB is allowed to use.
_CATEGORY_WORDS = {
    "quiz": "quiz", "quizzes": "quiz", "quizes": "quiz", "quizzed": "quiz",
    "question": "quiz", "questions": "quiz", "exam": "quiz", "exams": "quiz",
    "test": "quiz", "tests": "quiz", "practice": "quiz", "flashcards": "quiz",
    "studyguide": "quiz", "problemset": "quiz",
    "syllabus": "academic", "syllabi": "academic", "syllabuses": "academic",
    "course": "academic", "coursework": "academic", "academic": "academic",
    "datasheet": "datasheet", "datasheets": "datasheet", "component": "datasheet",
    "manual": "datasheet", "reference": "datasheet",
    "schematic": "schematic", "schematics": "schematic", "pcb": "schematic",
    "board": "schematic", "kicad": "schematic", "gerber": "schematic",
}

# "file them all as quizzes" — a target that means the whole inbox rather than a named document.
_EVERYTHING = ("all", "both", "everything", "them", "these", "those", "each")

# The copula, which turns a STATEMENT about a waiting file into an instruction to file it.
#
# From LB's own transcript, 2026-09-08 19:23 — "Both of the PDFs in the inbox are quizzes." He
# had already been asked twice which category they were; that sentence is the answer, and it was
# routed to the general agent, which read it back to him as a fact and filed nothing.
#
# Admitted only with everything else this module demands — a category, and a target resolving to
# something actually in the inbox — and never after an interrogative, so "is the resistor chart a
# quiz?" stays a question. Naming a waiting document's category is not small talk about it.
_COPULA = ("is", "are", "was", "were")

# An opener that makes the sentence a QUESTION about filing rather than an instruction to file.
# "can you", "could you" and "would you" are deliberately NOT here: they are how a polite person
# gives an instruction, and LB's own transcript opens "can you file the resistor chart".
_ASKING = re.compile(r"^(?:what|which|why|when|where|how|who|is|are|do|does|did|should)\b", re.I)

# The shortest run of characters allowed to name a file. Four is "trig"; three would let "the"
# and "and" match a filename that happens to contain them.
_MIN_NAME_CHARS = 4


@dataclass(frozen=True)
class FileRequest:
    """A recognised request to file uploaded documents. **Carries no authority to move them.**

    Args:
        filenames: the inbox files this named, resolved to their real names on disk. Never
                   empty — a request naming nothing that exists is not a request.
        category:  'quiz', 'academic', 'datasheet' or 'schematic', as
                   `file_manager.process_inbox_file` spells them.
        spoken:    what to say while doing it. Built here, with no model.
    """

    filenames: tuple[str, ...]
    category: str
    spoken: str


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _slug(text: str) -> str:
    """Lowercase alphanumerics only. The same normalisation `tools/file_manager._slug` uses."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _named_files(words: list[str], inbox: list[str]) -> tuple[str, ...]:
    """Which inbox files this utterance names. Order follows the inbox, not the sentence.

    Matched on the longest run of consecutive words whose slug appears inside the file's slug.
    That is what lets "the resistor chart" find `resistorcharts.pdf` and "trig limits sheets"
    find `trig limits 2.pdf` — a spoken filename keeps neither the punctuation nor the digits,
    and `base.en` will not have heard the ".pdf" at all.

    Substring rather than edit distance, for the reason `app_catalogue.resolve` gives: a
    threshold loose enough to fix a misheard filename is loose enough to file the wrong document.
    """
    found = []
    for name in inbox:
        stem_slug = _slug(name.rsplit(".", 1)[0])
        if not stem_slug:
            continue
        for start in range(len(words)):
            phrase = ""
            for end in range(start, len(words)):
                phrase += words[end]
                if len(phrase) < _MIN_NAME_CHARS:
                    continue
                if phrase in stem_slug:
                    found.append(name)
                    break
            if name in found:
                break
    return tuple(found)


def _spoken_for(names: tuple[str, ...], category: str) -> str:
    """What he says while the move happens. Never claims the filing is finished."""
    if len(names) == 1:
        return f"Filing {names[0]} as a {category}."
    return f"Filing {len(names)} file(s) as {category}."


def look_up(query) -> "FileRequest | None":
    """Is this utterance an instruction to file uploaded documents? Never raises, never files.

    Args:
        query: an `orchestrator.instant.Query`. Matched on `.text` — normalised, which is what
               makes it survive `base.en`'s missing punctuation and its "quizes".

    Returns:
        A `FileRequest`, or None. Requires a verb AND a category AND at least one inbox file
        that the utterance actually names — all three.
    """
    text = getattr(query, "text", "") or ""
    if not text or _ASKING.match(text.strip()):
        return None

    words = _words(text)
    instructed = any(verb in words for verb in _VERBS)
    declared = any(word in words for word in _COPULA)
    if not instructed and not declared:
        return None

    category = ""
    for word in words:
        if word in _CATEGORY_WORDS:
            category = _CATEGORY_WORDS[word]
            break
    if not category:
        return None

    try:
        from tools.file_manager import inbox_files
        inbox = [p.name for p in inbox_files()]
    except Exception:                                                  # noqa: BLE001
        # An unreadable inbox costs the free path, not the turn: fall through and let the router
        # handle it exactly as it did before this module existed.
        LOG.warning("inbox unavailable; file intent disabled for this turn")
        return None

    if not inbox:
        # Nothing is waiting, so nothing can be filed, so this sentence is about something else.
        # The single guard that keeps every general question about filing away from this path.
        return None

    if any(word in words for word in _EVERYTHING):
        names = tuple(inbox)
    else:
        names = _named_files(words, inbox)
    if not names:
        return None

    spoken = _spoken_for(names, category)
    LOG.info("file intent: %s -> %s", ", ".join(names), category)
    return FileRequest(filenames=names, category=category, spoken=spoken)


def main(argv: "list[str] | None" = None) -> int:
    """Match each argument and print what it resolved to."""
    import sys
    from dataclasses import replace                                    # noqa: F401

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    class _Q:
        def __init__(self, text: str) -> None:
            self.raw = text
            self.text = text.lower()

    for phrase in (argv if argv is not None else sys.argv[1:]):
        request = look_up(_Q(phrase))
        if request is None:
            print(f"  {phrase!r}\n      no match")
        else:
            print(f"  {phrase!r}\n      {request.category}: {', '.join(request.filenames)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
