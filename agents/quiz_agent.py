#!/usr/bin/env python3
"""
Module:  quiz_agent.py
Purpose: The one part of the quiz that may call a model — and only when LB asks it to.
Author:  LB
Date:    2026-08-19 (rewritten 2026-09-02)

## What changed, and why it is the whole point of this file

This agent used to mark **every single answer**. `Engine._quiz_turn` called
`evaluate_quiz_answer` once per question, so a ten-question revision session was ten Gemini
calls against a tier counted in REQUESTS at 20 per model name per day (D3). Revising for an
hour took the router, the persona agent and the firmware agent down with it for the rest of the
day, and the thing being bought with that quota was a decision about whether "V = I R" matches
"V = I * R".

`tools/quiz_grade.py` now does the marking, on this machine, for nothing. LB's instruction:
*the question and answer knowledge is internal, so it does not have to use an outside AI bot
unless I ask for a further explanation.*

So this agent is now reached from **one place**: `Engine._explain_quiz`, when LB has asked to
be told more and `quiz_grade.explain_locally` has already reported that the deck holds nothing
further. A practice paper that shipped its worked solutions therefore costs zero API calls to
revise from, which is the common case and the one worth being free.

## Why it still takes {chat_history}

Not for the conversation log — a grader that can see the last forty turns can see the answer it
is about to mark, and that was true when this graded and is still true when it explains. It
wants what `format_memory_for_llm` carries in FRONT of that log: LB's standing corrections and
the machine's state.

`tools/verify_awareness.py` caught this agent as the one of the eight that did not call that
function, which meant "always spell out the units" applied to every route except the one where
LB is being taught about units. A rule with a hole in it is not a rule.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("oddball.quiz")

__all__ = ["explain_quiz_answer", "evaluate_quiz_answer"]

EXPLANATION_PROMPT = """
You are an expert tutor helping a student revise.
{chat_history}
The question was: {question}
The correct answer is: {correct_answer}
The student answered: {user_answer}
They have already been told whether they were right. Do NOT mark them again.

They have asked you to explain the answer further. Explain WHY the answer is what it is:
1. Give the reasoning or the working, step by step, briefly.
2. If they got it wrong, name the specific misconception their answer suggests.
3. Stay strictly accurate. Do not accept or invent wrong maths, and say so plainly if the
   stored answer itself looks wrong to you.
Keep it under 150 words. The student is listening to this out loud.
"""

# Kept for the case where a deck carries no answer at all, and for anything still importing the
# old name. NOT on the marking path any more — see the module docstring.
EVALUATION_PROMPT = EXPLANATION_PROMPT


def explain_quiz_answer(question: str, correct_answer: str, user_answer: str = "") -> str:
    """Explain one answer in more depth than the deck itself can. **Costs one API call.**

    Args:
        question:       what was asked.
        correct_answer: the official answer from the bank.
        user_answer:    what LB said, so the explanation can name his specific mistake. May be
                        empty — he can ask for an explanation without having answered.

    Returns:
        The explanation, or a plain sentence saying why there is none. **Never raises.** This
        is reached from inside quiz mode, and an exception here would drop LB out of a mode he
        is midway through — over an optional extra, on a quota that runs dry by design.
    """
    try:
        from langchain_core.prompts import ChatPromptTemplate        # noqa: PLC0415
        from langchain_google_genai import ChatGoogleGenerativeAI    # noqa: PLC0415

        from engine.llm_text import extract_text_content             # noqa: PLC0415
        from engine.models import AGENT_MODEL, CLOUD_TIMEOUT_S, LLM_MAX_RETRIES       # noqa: PLC0415
        from tools.memory_manager import format_memory_for_llm       # noqa: PLC0415

        llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1,
                                     max_retries=LLM_MAX_RETRIES,
                                 timeout=CLOUD_TIMEOUT_S)
        prompt = ChatPromptTemplate.from_template(EXPLANATION_PROMPT).format(
            chat_history=format_memory_for_llm(),
            question=question,
            correct_answer=correct_answer,
            user_answer=user_answer or "(they did not answer)")
        return extract_text_content(llm.invoke(prompt).content)
    except Exception as exc:                                         # noqa: BLE001
        LOG.exception("the quiz explanation call failed")
        from engine.core import _failure_line                        # noqa: PLC0415

        # The failure line rather than a generic apology: it distinguishes a dry free tier —
        # which is not a fault — from an actual break, and LB needs to know which before he
        # decides whether to ask again.
        return (f"I could not get you a deeper explanation. {_failure_line(exc)} "
                f"The answer is still: {correct_answer}")


def evaluate_quiz_answer(question: str, correct_answer: str, user_answer: str) -> str:
    """**Deprecated.** Marking is `tools.quiz_grade.grade`, which is local and free.

    Kept as a name so nothing that imports it breaks, and delegating to the explainer rather
    than to a second copy of the old prompt — the one thing this must not do is quietly become
    a marking path again and put a network call back on every answer.
    """
    LOG.warning("evaluate_quiz_answer is deprecated — marking is local, see tools/quiz_grade.py")
    return explain_quiz_answer(question, correct_answer, user_answer)
