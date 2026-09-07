#!/usr/bin/env python3
"""
Module:  quiz_manager.py
Purpose: The quiz's front door. One question out, one answer marked, nothing left the machine.
Author:  LB
Date:    2026-08-19 (rewritten 2026-09-02)

## What this used to be

Twenty-five lines that read a three-question JSON file and called `random.choice` on it. That
was honest about its scope and the scope was the problem: there was no way to add a fourth
question, no notion of a subject, and `random.choice` over three items hands back the question
just answered about a third of the time.

It is now a thin front door onto three modules that do the work:

    tools/quiz_bank.py     the questions, filed by subject, on disk
    tools/quiz_import.py   PDFs in — a practice exam becomes a deck
    tools/quiz_grade.py    marking, LOCALLY

`get_random_question()` is kept, with its old name and its old return shape, because
`tools/verify_engine.py` stubs it by that name and `README.md` documents it. It is now a
one-line wrapper over `quiz_bank.pick`.

## The design constraint, in LB's words

*"Make sure the question and answer knowledge is internal so it does not have to use an outside
AI bot unless I ask for a further explanation of an answer."*

So: **nothing in this module, or in the three it calls, touches the network.** Not the picking,
not the importing, not the marking. `agents/quiz_agent.py` is the only part of the quiz that
can reach a model, and it is now called from exactly one place — an explicit request for more
explanation than the deck itself holds.
"""

from __future__ import annotations

import logging

from tools.quiz_bank import (QuizItem, deck_sizes, load_all, load_deck, pick,  # noqa: F401
                             resolve_subject, subjects)
from tools.quiz_grade import Grade, explain_locally, grade  # noqa: F401

LOG = logging.getLogger("oddball.quiz")

__all__ = ["get_random_question", "grade", "explain_locally", "Grade",
           "QuizItem", "subjects", "deck_sizes", "resolve_subject", "bank_summary",
           "load_deck", "load_all"]


def get_random_question() -> dict:
    """One question as a plain dict. The shape `engine/core.py` and the harnesses expect.

    The old signature, returning the old shape: `{"question": ..., "answer": ...}`. Callers
    that only know about those two keys keep working; the `choices`, `kind` and `explanation`
    a modern caller needs are also in the dict, so nothing is lost by going through here.

    Returns a placeholder rather than raising when the bank is empty. This is reached from
    inside a turn, and an empty bank is a thing to be TOLD about — "upload a practice quiz" —
    not an exception that drops LB out of the mode he just asked to enter.
    """
    item = pick()
    if item is None:
        return {"question": "I have no questions in the bank yet. Upload a practice quiz or a "
                            "question-and-answer PDF and ask me to file it as a quiz.",
                "answer": "", "kind": "short", "choices": {}, "explanation": "",
                "subject": "", "id": ""}
    return item.to_dict() | {"kind": item.kind, "choices": item.choices,
                             "explanation": item.explanation}


def bank_summary() -> str:
    """What is in the bank, as a sentence. Answers "what can you quiz me on?"."""
    sizes = deck_sizes()
    if not sizes:
        return ("There is nothing in the question bank yet. Upload a practice quiz or a "
                "question-and-answer PDF through the paperclip and tell me to file it as a "
                "quiz, and I will read the questions out of it.")
    total = sum(sizes.values())
    listed = ", ".join(f"{name} ({count})" for name, count in sizes.items())
    return f"I have {total} question(s) across {len(sizes)} subject(s): {listed}."
