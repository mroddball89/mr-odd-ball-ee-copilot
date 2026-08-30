#!/usr/bin/env python3
"""
Module:  response.py
Purpose: What an agent gives back — split into what he says and what he shows.
Author:  LB
Date:    2026-08-19

## Why a reply is two things

The terminal copilot returned a string and printed it. That worked because a terminal is one
channel: you read everything, at your own pace, and a register table is as readable as a
sentence.

Speech is not that channel. D32 measured Piper at ~160 words per minute, so **40 words is
about 15 seconds of audio** — and a firmware answer with a C snippet in it is several hundred
words of unreadable-aloud punctuation. Reading `REG_WRITE(GPIO_ENABLE_REG, BIT13);` out loud
produces "reg underscore write open paren gpio underscore enable underscore reg comma bit
thirteen close paren semicolon", which is worse than saying nothing.

So every answer carries **both halves**, and the caller decides what to do with each:

    speech   goes to Piper. Short by construction. Safe to say out loud.
    cards    go to the chat panel. Never spoken, however long or symbol-dense they are.

A voice-only turn plays `speech` and drops the cards on the HUD as it goes. A typed turn
renders both. Nothing has to guess.

## The rule that keeps it honest

`speech` must be **true on its own**. It is not a teaser for the cards — LB will often be
across the room and will never look at the screen for that turn. "I've put the code up" is a
failure; "Set bit 13 of the GPIO enable register" is the answer.

Equally, `speech` must never be the ONLY place a number appears if that number is worth
writing down. Heard once at 160 wpm, a trace width is gone. It goes in both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Card", "Pending", "Response", "CardKind"]


class CardKind:
    """What a card is, which is what the HUD renders it as.

    A plain class of constants rather than an Enum: these cross the WebSocket as strings, and
    an Enum would only be unwrapped again at the boundary.
    """

    CODE = "code"            # a fenced block — monospace, syntax-highlighted, copy button
    TABLE = "table"          # a markdown table — rendered as a real <table>
    LOG = "log"              # terminal output, search results — monospace, scrollable
    MARKDOWN = "markdown"    # prose that was too long or too symbolic to say
    ERROR = "error"          # something went wrong; styled to be obvious


@dataclass(frozen=True)
class Card:
    """One block of the answer that belongs on screen rather than in the air.

    Args:
        kind:  a CardKind. Decides how the HUD draws it.
        title: a short heading, e.g. "GPIO configuration" or "IPC-2221". May be "".
        body:  the content, verbatim. NOT escaped here — the HUD escapes at render time,
               because escaping at construction means double-escaping when the same card is
               also written to a file.
        lang:  for CODE only — "cpp", "python", "bash". "" when unknown.
    """

    kind: str
    title: str
    body: str
    lang: str = ""

    def to_dict(self) -> dict:
        """The wire form. `hud_bridge` broadcasts this under {"type": "card"}."""
        return {"kind": self.kind, "title": self.title, "body": self.body, "lang": self.lang}


@dataclass(frozen=True)
class Pending:
    """An action waiting on LB's approval. See engine/gates.py.

    **`spoken` and `shown` are different strings on purpose.** Reading
    `cat /sys/class/thermal/thermal_zone0/temp` out loud is unusable, so the model supplies a
    plain description for the ear while the exact command goes on a card for the eye. The card
    is rendered BEFORE the question is asked — approving something you only heard paraphrased
    is the risk this whole shape exists to manage.

    Args:
        kind:      "os" or "web" — which agent is waiting, and so which resume() to call.
        tool_args: the arguments to hand the tool if approved. Opaque here.
        spoken:    the yes/no question, safe to say aloud.
        shown:     the exact command or query, verbatim, for the card.
        tool:      which of that agent's tools resumes this. `kind` selects the AGENT and this
                   selects the TOOL — two levels, one decision each, so `engine/core.py` did
                   not need a line changed when the app launcher was added.

                   Appended last WITH A DEFAULT on purpose: every construction in this repo is
                   positional with four arguments (verify_engine.py, verify_chat.py,
                   demo_chat.py, web_agent.py), and a field in the middle would have broken
                   all of them silently by shifting what `spoken` and `shown` mean.
    """

    kind: str
    tool_args: dict
    spoken: str
    shown: str
    tool: str = "execute_terminal_command"


@dataclass(frozen=True)
class Response:
    """One answer, in both channels.

    Args:
        speech:  what he says. At most speakable.MAX_WORDS words, no code, no tables, no URLs.
                 May be "" when there is genuinely nothing to say — but a turn that shows a
                 card and says nothing is almost always a bug, so the splitter works hard to
                 avoid it.
        cards:   what he shows. Ordered; the HUD renders them top to bottom.
        route:   the AgentRoute that produced this, for the log and the HUD's route chip.
        pending: set when the turn is waiting on approval, and nothing has run yet.
        raw:     the agent's unsplit reply. Kept for the memory log and for debugging the
                 splitter — what got dropped is otherwise unrecoverable.
    """

    speech: str
    cards: list[Card] = field(default_factory=list)
    route: str = ""
    pending: Pending | None = None
    raw: str = ""

    @property
    def is_gated(self) -> bool:
        return self.pending is not None

    def to_dict(self) -> dict:
        return {
            "speech": self.speech,
            "cards": [c.to_dict() for c in self.cards],
            "route": self.route,
            "pending": (
                {"kind": self.pending.kind, "tool": self.pending.tool,
                 "spoken": self.pending.spoken, "shown": self.pending.shown}
                if self.pending else None
            ),
        }
