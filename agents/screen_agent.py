#!/usr/bin/env python3
"""
Module:  screen_agent.py
Purpose: Look at LB's screen and say what is on it.
Author:  LB
Date:    2026-08-25

The SCREEN route. "What am I looking at?", "what does that error say?", "why is this window
complaining?" — questions whose answer is on the display and nowhere else.

## It is shaped exactly like the OS agent, and that is the point

    propose_screen_look(query) -> Response with .pending set, and NOTHING has been captured
    resume_screen_look(pending) -> Response, after approval was given

`agents/os_agent.py` documents why the gate is a suspended state rather than a blocking
`input()`, and every word of it applies here: a voice turn has no stdin, and a chat panel cannot
answer a console prompt. Reusing the shape means `engine/core.py` needed one dispatch line and
no new machinery — the `Pending` it already holds for shell commands holds this one too.

## Why a screenshot is gated at all

Because it leaves the machine. The frame goes to Gemini, and LB's desktop may have a terminal on
it with a key in the scrollback. That is not hypothetical — the first capture taken while
building this had his browser and a chat window in it, both legible at half scale.

The counter-argument is real too: **he asked.** "What's on my screen" is an explicit instruction,
and answering it with a yes/no question is ceremony rather than safety. So the gate is the
default and `ODDBALL_SCREEN_CONFIRM=0` removes it — the one gate in this repo it is reasonable
for LB to switch off, because he is the thing being protected and he is the one asking.
`ODDBALL_SCREEN=0` disables the whole route.

## What is NOT claimed

He is describing **one frame, captured at the moment of approval.** He cannot watch the screen,
cannot see what changed, and cannot act on what he sees — there is no click, no keystroke, no
scroll. `tools/gesture_pointer.py` deliberately has no keyboard capability for related reasons.
The prompt below says so, because a model shown a screenshot will otherwise happily offer to
press the button in it.
"""

from __future__ import annotations

import base64
import logging

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from engine.llm_text import extract_text_content
from engine.models import LLM_MAX_RETRIES, VISION_MODEL
from engine.response import Card, CardKind, Pending, Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools import screen_capture
from tools.screen_capture import KINDS, Capture

LOG = logging.getLogger("oddball.screen")

__all__ = ["propose_screen_look", "resume_screen_look"]

SCREEN_PROMPT = """
You are Mr Odd Ball, an Electrical Engineering copilot on a Windows 11 PC. You have just taken a
screenshot of LB's display and you are looking at it.

Answer his question from what is actually in the image.

Rules:
- Describe what you can SEE. If the answer is not visible, say so plainly rather than guessing
  from what such a screen usually contains. "I can't see the whole dialog" is a real answer.
- Read text back accurately. An error message, a file name and a line number are the reason he
  asked, so quote them exactly rather than paraphrasing.
- The image is downscaled, so small text may be unreadable. Say when you cannot read something
  instead of guessing at the characters.
- You cannot click, type, scroll or change anything on this screen. You are looking at one still
  frame. Do not offer to press a button, and do not describe what you are about to do to the
  window — describe what is in it, and tell LB what HE should do.

{chat_history}

LB asks: {question}
""" + SPOKEN_INSTRUCTION

# What he SAYS for each way a look can end. Lives here, not in `tools/`, so the tool stays free
# of persona — the same split `agents/os_agent.py` makes and for the same reason.
#
# Total over `screen_capture.KINDS`, asserted below. The failure this prevents is the one that
# table exists for: a new kind falling through to a generic line that happens to sound like
# success, so he says "here's what's on your screen" having captured nothing.
_SPEECH: dict[str, str] = {
    "captured":   "",     # never spoken — the model's description is the answer
    "disabled":   "Looking at your screen is switched off, so I can't see it.",
    "no-tool":    "I've got no way to take a screenshot on here. The details are on the screen — "
                  "which is the bit I can't see.",
    "no-display": "I couldn't find a screen to photograph. The reason's on the display.",
    "failed":     "I tried to take a picture of the screen and it didn't work. The error's up.",
    "too-big":    "The screenshot came out far too big to send, so I've left it alone.",
    "crash":      "Something went wrong trying to look at the screen. It's on the display.",
}

assert set(_SPEECH) == set(KINDS), f"_SPEECH is not total over KINDS: {set(KINDS) ^ set(_SPEECH)}"

_QUESTION = "I want to take a picture of your screen and look at it. Should I?"


def propose_screen_look(query: str) -> Response:
    """Ask whether to look. **Nothing is captured here.** Costs no model call.

    `os_agent.propose_launch` makes the same trade and states it: "Want me to open Firefox?" is
    already a sentence, so paying Gemini to compose one is waste. So is this — there is exactly
    one thing being asked and it does not vary with the question.

    Args:
        query: what LB asked. Carried through the `Pending` so the resumed turn still knows
               whether he wanted the whole desktop described or one error message read.

    Returns:
        A gated `Response`, or an ungated one when `ODDBALL_SCREEN_CONFIRM=0` or the feature is
        switched off entirely.
    """
    if not screen_capture.enabled():
        return Response(speech=_SPEECH["disabled"], route="screen",
                        cards=[Card(CardKind.ERROR, "Screen capture is off",
                                    "ODDBALL_SCREEN=0 is set. Unset it to let him look at the "
                                    "screen.")],
                        raw="Screen capture is disabled by ODDBALL_SCREEN=0.")

    pending = Pending(kind="screen", tool_args={"question": query}, spoken=_QUESTION,
                      shown="capture the whole screen, downscale it, and send it to Gemini",
                      tool="capture_screen")

    if not screen_capture.confirm_wanted():
        LOG.info("ODDBALL_SCREEN_CONFIRM=0 — capturing without asking")
        return resume_screen_look(pending)

    backend, why_not = screen_capture.available_backend()
    return Response(
        speech=_QUESTION,
        # The card is the whole point of the gate over voice: what is about to happen, in front
        # of LB, at the moment he is asked. It names the destination, because "take a
        # screenshot" and "send a screenshot to Google" are different things to agree to.
        cards=[Card(CardKind.MARKDOWN, "Wants to look at the screen",
                    f"Capture the whole screen with `{backend or 'no tool available'}`, "
                    f"downscale it to {int(screen_capture.SCALE * 100)}%, and **send it to "
                    f"Gemini** to be described.\n\n"
                    f"The frame is kept at `{screen_capture.FRAME_DIR}` so you can see exactly "
                    f"what was sent.{('  \n\n**' + why_not + '**') if why_not else ''}")],
        route="screen", pending=pending,
        raw=f"Proposed screen capture via {backend or 'no available backend'}.")


def _ask_vision(question: str, shot: Capture) -> str:
    """Send the frame and the question to the vision model. Returns his raw reply."""
    from tools.memory_manager import format_memory_for_llm

    llm = ChatGoogleGenerativeAI(model=VISION_MODEL, temperature=0.2,
                                 max_retries=LLM_MAX_RETRIES)
    prompt = SCREEN_PROMPT.format(chat_history=format_memory_for_llm(), question=question)

    encoded = base64.b64encode(shot.data).decode("ascii")
    message = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": f"data:{shot.mime};base64,{encoded}"},
    ])
    return extract_text_content(llm.invoke([message]).content)


def resume_screen_look(pending: Pending) -> Response:
    """Capture the screen and describe it. Only called after an explicit yes.

    Args:
        pending: the approved `Pending`, carrying the original question in `tool_args`.
    """
    question = str(pending.tool_args.get("question") or "What is on the screen?")
    shot = screen_capture.capture()

    if not shot.ok:
        # Recorded, because "I tried to look and could not" is exactly the kind of thing that
        # should not have to be rediscovered next time. A missing `grim` is a permanent
        # condition and he should stop offering to look until it is installed.
        from tools import reflections
        reflections.note(
            kind="screen-capture", what=f"look at the screen to answer {question!r}",
            why=f"{shot.kind}: {shot.detail}",
            lesson=("say you cannot see the screen instead of offering to look, until this is "
                    "fixed" if shot.kind in ("no-tool", "no-display", "disabled") else ""))
        return Response(
            speech=_SPEECH.get(shot.kind, _SPEECH["crash"]),
            cards=[Card(CardKind.ERROR, "Could not look at the screen",
                        f"{shot.kind}: {shot.detail}")],
            route="screen", raw=f"Screen capture failed ({shot.kind}): {shot.detail}")

    try:
        answer = _ask_vision(question, shot)
    except Exception as exc:                                              # noqa: BLE001
        LOG.exception("the vision model failed")
        # Re-raised so `Engine.ask` handles it exactly like any other model failure — quota
        # latching included. Swallowing it here would report a 429 as "I couldn't see it",
        # which sends LB looking at his screenshot tool for a problem with his API key.
        raise

    response = split(answer, route="screen",
                     fallback="I've put what I can see on the screen.")
    return Response(
        speech=response.speech,
        cards=list(response.cards) + [
            # Named, the way FIRMWARE and ACADEMIC name their sources. An answer about the
            # screen and an answer about what a screen usually looks like read identically;
            # this is the card that tells them apart.
            Card(CardKind.LOG, "Looked at",
                 f"{shot.path}\n{len(shot.data) / 1024:.0f} kB, captured with {shot.backend}"),
        ],
        route="screen", raw=answer)
