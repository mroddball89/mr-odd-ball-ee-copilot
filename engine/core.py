#!/usr/bin/env python3
"""
Module:  core.py
Purpose: The switchboard. One question in, one Response out.
Author:  LB
Date:    2026-08-19

This is `main.py`'s loop with the terminal taken out of it. That is the whole refactor, and
the constraint that makes it worth doing is negative:

    **No print(). No input().**

`main.py` interleaved four jobs — routing, mode state, asking permission, and formatting for a
terminal — and the last of those made the other three unreachable from anywhere else. A voice
turn cannot answer `input("Allow execution? (y/n): ")`, and a chat panel cannot read a
`print()`. Pulling the terminal out is what lets the voice loop and the typed panel be two
callers of the same `ask()` rather than two copies of the same logic that drift.

## What Engine holds

State that outlives a single question, and nothing else:

    mode        "normal" or "quiz" — the quiz lock, which bypasses the router entirely
    quiz_item   the question currently being answered
    pending     an action waiting on approval; the next ask() is read as the answer

It deliberately does NOT hold the conversation history. That already lives in
`tools/memory_manager.py`, on the local disk, and having two of them is how they disagree.

## Ordering inside ask()

1. A pending gate short-circuits everything. If he asked "should I run this?", the next thing
   said is the answer to that and must never be routed as a fresh question.
2. Quiz mode short-circuits the router. Same reason `main.py` did it: while locked, everything
   is an answer to the question on the table.
3. Otherwise route, dispatch, split, log, and run the two reminders — the backup clock and
   the coursework deadline check. Both are appended to the SHOWN half of any routed or free
   turn, and neither reaches quiz mode or a permission answer, which are conversations already
   in progress rather than fresh questions.
"""

from __future__ import annotations

import logging
import random
import re
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from engine.response import Card, CardKind, Pending, Response
from engine.split import split
from router import AgentRoute, router_agent

# How long the router leg may take before the turn gives up on it and goes to GENERAL.
#
# 20 seconds is deliberately generous: the routes that WORK land in 0.77-0.88s, so this is
# more than twenty times the normal cost and cannot be hit by an ordinary slow day. It exists
# for the pathology measured on 2026-08-29, where four routes took 91s, 127s, 162s and 286s
# and every one of them returned 200 OK. See `Engine._route_within_deadline`.
#
# Overridable, because the right number on a worse connection is not this one.
ROUTER_DEADLINE_S = float(os.environ.get("ODDBALL_ROUTER_DEADLINE_S", "20"))

LOG = logging.getLogger("oddball.engine")

# Ways of saying "stop quizzing me". A family rather than one string, because `tiny.en` will
# not reliably produce "exit quiz" — it turned "What is the date?" into "What is today?" and
# "Set a timer" into "at a timer". A lock with one exact exit phrase is a lock LB gets stuck
# inside, and the way out must not depend on the transcript being perfect.
_QUIZ_EXITS = (
    "exit quiz", "quit quiz", "stop quiz", "end quiz", "leave quiz", "exit the quiz",
    "stop the quiz", "end the quiz", "quit the quiz", "stop quizzing", "no more questions",
    "im done", "i'm done", "i am done", "am done", "thats all", "that's all",
    "stop testing me", "no more quiz", "get me out",
)

# Single words that mean "out" on their own. Matched as WHOLE WORDS, unlike the phrases above:
# "exit" as a substring would fire inside "excited", and a quiz that drops out when you say
# you are excited about something is worse than one that needs a clearer word.
_QUIZ_EXIT_WORDS = ("exit", "quit", "enough", "stop", "escape")

QUIZ_CHIP = "QUIZ MODE — say 'exit quiz' to stop"

# Ways of asking to be told MORE about the answer just given. **This is the only door in the
# quiz that a network call is behind**, and it is deliberately narrow: LB asked for the marking
# to be internal and the model to be reached only when he asks for a further explanation, so
# anything that is not clearly such a request is treated as an answer to the next question.
#
# Matched as phrases, not single words, for exactly the opposite reason `_QUIZ_EXITS` accepts
# bare words. The exit is loose because being trapped in a mode is worse than dropping one
# answer. This is tight because a false positive spends a request off a 20-a-day tier and skips
# a question he was midway through answering.
_QUIZ_EXPLAIN = (
    "explain that", "explain it", "explain this", "explain why", "explain the answer",
    "explain further", "explain more", "more detail", "in more detail", "tell me more",
    "why is that", "why is it", "why that", "how come", "how did you get",
    "how do you get", "how does that work", "walk me through", "show me the working",
    "show your working", "i dont understand", "i do not understand", "i dont get it",
    "i do not get it", "go deeper", "elaborate", "expand on that", "what do you mean",
)

# "Next question", "skip this one", "pass". Distinct from not knowing the answer: this asks to
# MOVE ON without being marked, where "I don't know" is an answer and is marked as one.
_QUIZ_SKIP = ("skip", "skip this", "skip it", "skip this one", "next question", "next one",
              "move on", "another one", "another question", "come back to that",
              "come back to this")

# "How am I doing?" mid-quiz, answered off the session's own tally. No model, no route.
_QUIZ_SCORE = ("how am i doing", "whats my score", "what is my score", "how many have i got",
               "how many did i get", "score so far", "how am i getting on", "my score")


# ---------------------------------------------------------------------------------------
# Committing an answer. 2026-09-06.
#
# LB, asked how the quiz should decide he has finished answering, chose: *"you say when you're
# ready."* Nothing is marked until he says so. A pause is then never mistaken for an answer,
# and — the case that actually matters — muttering through a derivation out loud is not marked
# as one either. "Okay so one over 4.7k plus one over 4.7k" is a man thinking, and the machine
# used to mark it wrong and move on.
#
# Two shapes, because both are things people say and refusing either would be a trap:
#
#   "my answer is 2x"     the answer arrives with the phrase.   -> graded immediately
#   "ready" / "got it"    the phrase arrives first.             -> the NEXT utterance is it
#
# Only active while `[quiz] commit_required` is on, which is the default for VOICE and off for
# typing — see `Engine.__init__`.
# ---------------------------------------------------------------------------------------

# The answer carried in the same breath. A REGEX and not a phrase tuple, because the payload has
# to be sliced out of the ORIGINAL text: `_matches` normalises through
# `re.sub(r"[^a-z0-9' ]+", " ", ...)`, which would turn "V = I times R" into "v i times r" and
# hand the grader an answer with its equals sign removed. The prefix is matched loosely; what
# follows it is passed through untouched.
#
# Longest alternatives first — "my final answer is" has to win against "answer is", or the
# payload keeps the word "final" and the grader marks it wrong.
_COMMIT_CARRY = re.compile(
    r"^\s*(?:"
    r"my\s+final\s+answer\s+is|my\s+answer\s+is|the\s+answer\s+is|final\s+answer\s+is|"
    r"answer\s+is|"
    r"i(?:['’]|\s+wi)?ll\s+go\s+with|i\s+go\s+with|going\s+with|"
    r"i(?:['’]|\s+wi)?ll\s+say|"
    r"i(?:['’]|\s+wi)?ll\s+choose|i\s+choose|"
    r"i(?:['’]|\s+wi)?ll\s+pick|i\s+pick"
    r")\s*[:,]?\s+(?P<body>\S.*)$",
    re.I)

# The bare signal: he has it, but has not said it yet.
#
# **"i'm done" is deliberately NOT here.** It is already in `_QUIZ_EXITS`, which is checked
# first, so it ends the quiz — and that ordering is not changed. The exit family is loose on
# purpose ("being trapped in a mode is worse than dropping one answer"), and quietly stealing
# one of its phrases for a new meaning would break the way out. Bare "done" is fine: it is not
# in the exit family and cannot reach it.
_QUIZ_COMMIT_BARE = ("ready", "i'm ready", "im ready", "i am ready", "i've got it",
                     "ive got it", "i have it", "got it", "done", "finished",
                     "that's my answer", "thats my answer", "final answer", "thats it",
                     "that's it")

# What he says into a pause, when he has been quiet long enough that saying nothing would start
# to read as the machine having crashed.
#
# **Three words.** Speaking holds `MicGate.speaking` plus `gate_tail_s`, so every syllable here
# is a syllable he is not being heard through — a nudge that arrives just as he starts answering
# eats the start of his answer. A long reassurance is a long deafness.
_QUIZ_NUDGE = ("Take your time.", "No rush.", "Still with you.")


def _commit_payload(text: str) -> str:
    """The answer carried by a commit phrase, or "" when there is none.

    Sliced out of the original text rather than a normalised copy, so "my answer is V = I R"
    keeps its equals sign for `quiz_grade._sympy_verdict` to read.
    """
    found = _COMMIT_CARRY.match(text or "")
    return found.group("body").strip() if found else ""


@dataclass
class QuizSession:
    """One run of the quiz. What `mode == "quiz"` is holding while it is on.

    Replaces the single `quiz_item` attribute, which could hold the current question and
    nothing else — so nothing could stop the same question being asked twice in a row, nothing
    could tell LB how he had done, and "explain that" had no way to know what "that" was.

    Args:
        subject:  the deck he asked for, or "" for the whole bank. Fixed for the session:
                  "quiz me on calculus" means calculus until he leaves and asks for something
                  else.
        item:     the question on the table now.
        asked:    ids already put to him, so `quiz_bank.pick` does not repeat itself.
        answered: how many he has actually answered — skips do not count.
        score:    running total. A partial credit is worth half, and `Grade.scored` says so.
        last_item:  the question just marked, and
        last_grade: how it was marked. Both held so that "explain that", said AFTER the next
                    question has been asked, explains the one he means rather than the one on
                    the table. Getting this wrong would explain a question he has not yet had a
                    chance to answer, which is the quiz spoiling itself.
        awaiting:   "commit" while waiting for LB to say he is ready, "answer" once he has said
                    it and the next thing he says is the answer. Only meaningful while
                    `[quiz] commit_required` is on; otherwise it stays "answer" forever, which
                    is exactly the behaviour before 2026-09-06.
        silences:   consecutive TURNS that heard nothing at all. Reset by any utterance, even
                    one ignored as thinking-aloud, because a man muttering is a man still there.
                    Bounded by `[quiz] idle_turns` — this is what stops a forgotten session
                    holding the microphone open on an empty room.
        ignored:    consecutive utterances that carried no commit phrase. Bounded by
                    `[quiz] nudge_after_ignored`, after which he says the phrase out loud once.
                    Without this a mis-transcribed "my answer is" leaves LB answering into a
                    machine that will never mark him, with no feedback at all.
    """

    subject: str = ""
    item: object | None = None
    asked: set = field(default_factory=set)
    answered: int = 0
    score: float = 0.0
    last_item: object | None = None
    last_grade: object | None = None
    last_answer: str = ""
    awaiting: str = "commit"
    silences: int = 0
    ignored: int = 0

    def tally(self) -> str:
        """The score, as a sentence. "" before anything has been answered."""
        if not self.answered:
            return ""
        rounded = f"{self.score:.1f}".rstrip("0").rstrip(".")
        return f"{rounded} out of {self.answered}"


def _matches(text: str, phrases: tuple) -> bool:
    """True when `text` is one of `phrases`, or clearly starts or ends with one.

    Not a bare `in`: "explain that" appearing inside a long answer to a philosophy question is
    LB answering, not LB asking to be taught. Containment is allowed only for a short utterance,
    where there is nothing else it could be.
    """
    flat = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())
    flat = re.sub(r"\s+", " ", flat).strip()
    if not flat:
        return False
    if any(flat == p for p in phrases):
        return True
    if len(flat.split()) <= 8:
        return any(flat.startswith(p) or flat.endswith(p) or f" {p} " in f" {flat} "
                   for p in phrases)
    return False


# "quiz me on calculus", "test me on chapter 3 of philosophy", "ask me some circuits questions".
# The subject is whatever follows, cleaned of the words that are part of the request rather than
# part of the subject.
_QUIZ_SUBJECT = re.compile(
    r"\b(?:on|about|from|in|over|with|regarding)\s+(?P<subject>.+?)\s*$", re.I)

_SUBJECT_NOISE = re.compile(
    r"\b(?:please|stuff|things|material|questions?|topics?|chapter\s*\d*|unit\s*\d*|"
    r"section\s*\d*|my|the|some|of|a|an|for|class|course|homework|notes?|today|now)\b", re.I)


def quiz_subject_of(text: str) -> str:
    """The subject LB named, or "" when he named none.

    "Quiz me on calculus" -> "calculus". "Test me" -> "". "Quiz me on some of my philosophy
    material" -> "philosophy", because the words that are part of the ASKING are stripped and
    only the subject is left.

    Returning "" is a perfectly good answer and means the whole bank — `quiz_bank.resolve_subject`
    is what turns whatever comes out of here into a deck that exists, and it also returns "" when
    it cannot tell, so an unrecognised subject widens to everything rather than failing.
    """
    match = _QUIZ_SUBJECT.search((text or "").strip())
    if not match:
        return ""
    subject = _SUBJECT_NOISE.sub(" ", match.group("subject"))
    subject = re.sub(r"[^A-Za-z0-9 ]+", " ", subject)
    return re.sub(r"\s+", " ", subject).strip()


def _is_quiz_exit(text: str) -> bool:
    """True if `text` asks to leave quiz mode.

    Looser than the yes/no matcher, and deliberately so. The failure modes are not symmetric:
    a false positive drops one answer and LB asks to be quizzed again, while a false negative
    traps him in a loop that keeps asking questions — with `tiny.en` between him and the exit.
    `main.py` accepted exactly one phrase, which was safe to type and would not have survived
    being spoken.

    `Engine.leave_quiz()` is the escape that does not depend on being heard at all.
    """
    flat = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    flat = re.sub(r"\s+", " ", flat).strip()
    if any(phrase in flat for phrase in _QUIZ_EXITS):
        return True
    return any(re.search(rf"(?<![a-z0-9]){w}(?![a-z0-9])", flat) for w in _QUIZ_EXIT_WORDS)


def _note_handled(exc: Exception, where: str) -> None:
    """Record a failure that was CAUGHT and turned into a sentence. **Never raises.**

    See `_failure_line` for why this exists at all. Kept as a separate function so the recording
    cannot get tangled up in the branching above it, and so it is one obvious place to look when
    an entry appears in the ledger that nobody can account for.
    """
    try:
        from tools import reflections

        slug = re.sub(r"[^a-z0-9]+", "-", str(where or "").lower()).strip("-")
        reflections.note(
            kind=f"handled/{slug}" if slug else "handled",
            what=f"{where or 'a model call'} — degraded to a fallback sentence",
            why=f"{type(exc).__name__}: {exc}",
            lesson="")
    except Exception:                                  # noqa: BLE001
        LOG.debug("could not record a handled failure", exc_info=True)


def _failure_line(exc: Exception, *, where: str = "", record: bool = True) -> str:
    """What he says when a turn fails. Names the layer, because "something went wrong" helps
    nobody and costs LB the debugging time of finding out which layer it was.

    The quota case is called out on its own because it is **not a fault** — it is the free
    tier doing exactly what it says, and reporting it as a crash sends LB looking for a bug
    that is not there. `brains/gemini.py` in the standalone assistant made the same
    distinction for the same reason.

    Args:
        exc:    what went wrong.
        where:  which call degraded, in words — "quiz explanation". Becomes the entry's kind.
        record: False when the caller has ALREADY written a better entry. `Engine.ask` has the
                route and the utterance, so `_reflect_on_failure` says more than this can; two
                entries for one failure would read as two problems.

    ## Why the ledger is written from here

    On 2026-09-04 the quiz explanation call failed five times in one session with the same
    ImportError. Every one was logged at ERROR with a full traceback — and **the mistake ledger
    recorded none of them**, because `explain_quiz_answer` is documented as never raising: it
    catches, returns a sentence, and `Engine.ask` sees a turn that succeeded.

    That is not a bug in the quiz agent. It is the consequence of hanging the instrument on the
    exception boundary in a codebase whose whole style is to never let an exception reach one.
    Every well-written `except` was a hole in the memory.

    `_failure_line` is the seam that does not have that problem, and it is already the
    convention: `agents/quiz_agent.py` imports it across a package boundary precisely so a
    graceful degradation says the right thing. Anything that degrades calls this, so anything
    that degrades is now recorded — including the next one, written by someone who never read
    this docstring. That is the same argument `tools/self_context.py` makes about
    `format_memory_for_llm`, and it is the reason both work.

    **So: if you catch an exception and answer anyway, say it with `_failure_line`.**
    """
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        from engine import quota
        from engine.models import FREE_TIER_DAILY_LIMIT

        if quota.is_daily_exhaustion(text):
            # LB's words, first, because this is the sentence he asked to hear and the one
            # that stops him debugging a fault that is not there.
            #
            # Returned WITHOUT recording, and that is the same call `_reflect_on_failure` makes:
            # a dry free tier is a budget running out, it is true for every turn until midnight,
            # and writing it down would fill the ledger with identical entries and push out the
            # ones that mean something.
            return ("API quota exceeded for today. That's my "
                    f"{FREE_TIER_DAILY_LIMIT} free questions gone until it resets. "
                    "The utility stuff still works — ask me the time.")
        # A per-MINUTE 429 is a different animal: it clears in seconds, and telling him to
        # come back tomorrow over a burst would be wrong.
        said = "I'm being rate limited for a moment. Ask me again in a few seconds."
    elif "NOT_FOUND" in text or "404" in text:
        said = "That model name isn't valid any more. The details are on the screen."
    elif "PERMISSION_DENIED" in text or "API key" in text:
        said = "My API key isn't working. The details are on the screen."
    else:
        said = "Something went wrong on my end. It's on the screen."

    if record:
        _note_handled(exc, where)
    return said


@dataclass
class Turnlog:
    """Where one question's time went. Stage 8 plots these."""

    route: str = ""
    route_s: float = 0.0
    agent_s: float = 0.0
    mode: str = "normal"
    extras: list[str] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return self.route_s + self.agent_s

    def line(self) -> str:
        return (f"turn: route {self.route_s * 1000:.0f}ms -> {self.route or '-'} | "
                f"agent {self.agent_s:.2f}s | total {self.total_s:.2f}s"
                + (f" | {', '.join(self.extras)}" if self.extras else ""))


@dataclass
class NoteDraft:
    """A note being dictated, waiting on one more thing before it can be written.

    LB asked for two behaviours that both need a turn to be held open: a bare "take a note"
    should ask what to write down, and every new note should ask what to call it. So a note can
    take up to three turns, and this is what carries the first two into the third.

    **It is not a `Pending`.** A `Pending` is an approval — a yes/no about an action already
    fully described, resolved by `orchestrator/classify_yes.py`. This is the opposite shape: the
    action is not yet described, and the answer is content rather than consent. Reusing
    `Pending` would mean `is_yes("the TL072 has output on pin 1")` deciding whether to save it.

    Args:
        op:       "new" or "append" — which operation is waiting.
        awaiting: "content" or "name" — what the next utterance will be read as.
        content:  what he has been given so far, verbatim.
        folder:   the vault folder LB named, "" for the default.
        name:     what to call it, once he has said.
        path:     for "append", the note already resolved by `knowledge_vault.find_notes`.
                  Held as a path rather than as a name so the note that gets added to is the
                  one that was found, not one re-resolved a turn later against a vault that
                  may have changed underneath.
    """

    op: str = "new"
    awaiting: str = "content"
    content: str = ""
    folder: str = ""
    name: str = ""
    path: "Path | None" = None
    # Set when the recording that produced `content` hit the cap, so `content` is the front of
    # a sentence. The next utterance CONTINUES it rather than replacing it — without this, LB
    # finishing his own sentence would overwrite the half he had already dictated, which is a
    # worse outcome than the truncation it is trying to repair.
    truncated: bool = False


class Engine:
    """The copilot, with no terminal attached.

    Args:
        confirm_gates: when False, OS and WEB run without asking. **Only for harnesses.**
                       The default is the safe one, and it is not configurable from the UI —
                       a permission gate with an off switch on the surface is not a gate.
        quiz_commit:   require a commit phrase ("my answer is...", "ready") before anything is
                       marked. See `[quiz] commit_required`.
        quiz_idle_turns:     consecutive silent turns before a quiz session gives up. 0 = never.
        quiz_nudge_after:    thinking-aloud utterances before he reminds LB of the phrase.
                             0 = never remind.
    """

    def __init__(self, confirm_gates: bool = True, quiz_commit: bool = False,
                 quiz_idle_turns: int = 0, quiz_nudge_after: int = 0) -> None:
        self.mode = "normal"
        self.quiz: QuizSession | None = None
        self.pending: Pending | None = None
        self.note_draft: NoteDraft | None = None
        self._confirm_gates = confirm_gates
        # **Defaults are OFF, and `engine/run_voice.py` is the only caller that turns them on.**
        #
        # That is not timidity about a new feature, it is where the problem lives. The commit
        # phrase exists because a spoken answer has no end marker — the recorder has to guess
        # from silence, and it guessed at 1.5 seconds. **Typing has no such problem**: Enter is
        # the end marker, it is unambiguous, and it is already there. Requiring "my answer is"
        # from a man at a keyboard would be pure friction bought for nothing.
        #
        # So `main.py --text` and all three harnesses keep the behaviour they have always had,
        # and the voice rig gets the pacing LB asked for.
        self._quiz_commit = bool(quiz_commit)
        self._quiz_idle_turns = int(quiz_idle_turns)
        self._quiz_nudge_after = int(quiz_nudge_after)
        self.last: Turnlog = Turnlog()

    # `quiz_item` was the whole of the quiz's state before `QuizSession` existed, and it is
    # named in `tools/verify_engine.py` and in `README.md`. Kept as a view onto the session
    # rather than deleted: a harness that sets `eng.quiz_item` to put a question on the table
    # is doing a reasonable thing, and breaking it would mean editing the test that protects
    # this code in the same change that rewrites it — which is how a rewrite goes out untested.
    @property
    def quiz_item(self):
        """The question on the table, or None. A view onto `self.quiz`."""
        return self.quiz.item if self.quiz else None

    @quiz_item.setter
    def quiz_item(self, value) -> None:
        if self.quiz is None:
            self.quiz = QuizSession()
        self.quiz.item = value

    # --- the one entry point -----------------------------------------------------------

    def ask(self, text: str, truncated: bool = False) -> Response:
        """Answer one question. Never raises; a failure comes back as a spoken sentence.

        Args:
            text:      what LB said or typed. Already transcribed.
            truncated: the recording hit `max_s`, so this transcript is the FRONT of a sentence
                       and the rest was never captured. Only the spoken path can know this;
                       `answer_typed` never sets it, because typing has no cap.

        Keyword-defaulted rather than required, so the three harnesses and the chat panel that
        call `ask(text)` are untouched — and so that a caller which does not know about audio
        cannot accidentally claim a transcript is complete when it has no way to tell.
        """
        t = Turnlog(mode=self.mode)
        self.last = t
        text = (text or "").strip()

        # Tell the self-context block what is being asked, so `tools/reflections.py` can put the
        # failures that bear on THIS question in front of the agent rather than the six most
        # recent ones. Set here because this is the one entry point both channels come through;
        # see `tools/self_context.py` for why it is module state rather than an argument.
        try:
            from tools import self_context
            self_context.set_question(text)
        except Exception:                              # noqa: BLE001
            LOG.debug("could not set the self-context question", exc_info=True)

        # Nothing said WHILE a gate is open is an answer to the gate, and the answer is no.
        # This ordering is load-bearing and was got wrong first time: the empty check used to
        # come first and returned "I didn't catch that" while leaving `pending` set — so the
        # gate stayed open, and the next thing LB said, about anything at all, was consumed as
        # its answer. tools/verify_engine.py --probe is what surfaced it, by showing that the
        # silence checks were passing without ever reaching the gate.
        if not text:
            if self.pending is not None:
                self.pending = None
                t.extras.append("gate: no answer -> declined")
                return Response(speech="No problem, I'll leave it.", route="",
                                raw="Action aborted: no answer to the permission question.")
            # A held note question is closed by silence for the same reason, and it is the same
            # bug if it is not: a draft left open consumes whatever LB says next, about
            # anything at all, and files it as the note.
            if self.note_draft is not None:
                self.note_draft = None
                t.route = "note"
                t.extras.append("note: no answer -> cancelled")
                return Response(speech="No problem, nothing written down.", route="note",
                                raw="Note abandoned: no answer to the note question.")
            return Response(speech="I didn't catch that.", route="", raw="")

        try:
            if self.pending is not None:
                return self._resolve_pending(text, t)

            # A note question that is open owns the next utterance, exactly as the gate above
            # does — "What should I call it?" is answered by the next thing said, and routing
            # that answer would send "op amp pinouts" to the persona agent as a fresh question.
            #
            # BELOW the gate, because a gate and a draft are never open at once and the gate is
            # the one that guards an action. ABOVE quiz mode and the correction check, because
            # both of those read an ordinary utterance, and while a draft is open there is no
            # such thing: "that was wrong" is a perfectly good thing to write in a note.
            if self.note_draft is not None:
                return self._resolve_note(text, t, truncated=truncated)

            if self.mode == "quiz":
                return self._quiz_turn(text, t)

            # A correction outranks the router, and the ORDER here is load-bearing.
            #
            # Below the gate, because "no, don't" while a permission question is open is a
            # decline and must stay one — `_resolve_pending` already reads anything that is not
            # a clear yes as no, and stealing that line would leave the gate open.
            #
            # Below quiz mode, because "wrong" is a plausible thing to say to a quiz question
            # and `_quiz_turn` owns every utterance while the mode is on.
            #
            # Above `_routed_turn`, because a correction is not a question: routing it would
            # spend a Gemini call to send "that was wrong" to the persona agent, which would
            # apologise charmingly and forget it.
            correction = self._correction(text)
            if correction is not None:
                return self._record_correction(correction, text, t)

            return self._routed_turn(text, t)
        except Exception as exc:                       # noqa: BLE001 — the answer path never dies
            LOG.exception("turn failed")
            t.extras.append(f"error {type(exc).__name__}")

            # Remember a daily exhaustion so the NEXT turn does not pay to rediscover it.
            # Latched per model, because D3 split the jobs across three model names precisely
            # so that one running dry does not silence the others.
            from engine import quota
            from engine.models import AGENT_MODEL, PERSONA_MODEL, ROUTER_MODEL

            if quota.is_daily_exhaustion(exc):
                # Matched with `names_model`, NOT `in`: AGENT_MODEL is a strict prefix of
                # ROUTER_MODEL, so a substring test latches both off one exhaustion.
                named = [m for m in (ROUTER_MODEL, AGENT_MODEL, PERSONA_MODEL)
                         if m and quota.names_model(str(exc), m)]
                # Nothing named means we cannot tell which bucket ran dry. Latch NOTHING
                # rather than guess: a wrong latch costs a day of silence, and the next turn
                # now fails in 0.2s (LLM_MAX_RETRIES=0) rather than 217s, so rediscovering it
                # is cheap. Guessing was only ever worth it when the retry was expensive.
                for model in named:
                    quota.note(model, exc)
                t.extras.append(f"quota latched: {','.join(named) if named else 'none (unnamed model)'}")

            self._reflect_on_failure(text, t, exc)
            return Response(
                # `record=False`: the line above has already written a richer entry, with the
                # route and the utterance in it. Recorded twice, one failure reads as two.
                speech=_failure_line(exc, record=False),
                cards=[Card(CardKind.ERROR, type(exc).__name__, str(exc))],
                route=t.route,
            )
        finally:
            self._reflect_on_slowness(text, t)
            LOG.info("%s", t.line())

    # --- learning from what went wrong -------------------------------------------------

    def _reflect_on_failure(self, text: str, t: Turnlog, exc: Exception) -> None:
        """Record an exception in `vault/reflections.md`. **Never raises.**

        This is the broadest of the reflection hooks and catches what the narrow ones miss: any
        agent, any tool, any model call that got as far as raising. It runs INSIDE the existing
        `except` rather than around it, so the sentence LB hears is unchanged and the ledger is
        a side effect of an answer that already worked.

        A quota exhaustion is deliberately NOT recorded. It is not a mistake — it is a budget
        running out, it will be true for every turn until midnight, and writing it down would
        fill the ledger with two hundred identical entries and push out the ones that mean
        something. `engine/quota.py` already latches it where it belongs.
        """
        try:
            from engine import quota
            if quota.is_daily_exhaustion(exc):
                return

            from tools import reflections
            reflections.note(
                kind=f"turn-failed/{t.route or 'unrouted'}",
                what=f"answer {text[:120]!r}" + (f" via the {t.route} agent" if t.route else ""),
                why=f"{type(exc).__name__}: {exc}",
                lesson="")
        except Exception:                              # noqa: BLE001
            LOG.debug("could not record a reflection for this failure", exc_info=True)

    def _reflect_on_slowness(self, text: str, t: Turnlog) -> None:
        """Record a turn that took far too long, even if it eventually answered.

        **A slow success is the failure nobody escalates**, and it is the one LB actually asked
        to be recorded — "a task takes longer than expected" was his third case. It is also the
        one with no error to catch: the turn worked, the log line scrolled past, and the only
        record that it took ninety seconds was a number nobody was reading.

        Not recorded when the turn already failed: `_reflect_on_failure` has written a better
        entry, and a timeout logged twice under two headings reads as two problems.
        """
        try:
            from tools import reflections

            if t.total_s < reflections.SLOW_TURN_S:
                return
            if any(e.startswith("error ") for e in t.extras):
                return

            reflections.note(
                kind="slow-turn",
                what=f"answer {text[:120]!r} via the {t.route or 'unrouted'} path",
                why=f"it took {t.total_s:.0f} seconds — {t.route_s:.0f}s to route and "
                    f"{t.agent_s:.0f}s in the agent",
                lesson="prefer the free path for this kind of question where one exists")
        except Exception:                              # noqa: BLE001
            LOG.debug("could not record a reflection for this slow turn", exc_info=True)

    # --- corrections -------------------------------------------------------------------

    def _correction(self, text: str):
        """Is LB correcting him? Returns a `Correction`, or None. **Never raises.**

        Wrapped rather than called directly so that a failure in the detector cannot take the
        answer path with it. `corrections.detect` already swallows its own exceptions; this
        catches the import, which is the part that can fail on a half-deployed box.
        """
        try:
            from tools.corrections import detect
            return detect(text)
        except Exception:                              # noqa: BLE001
            LOG.exception("correction detection unavailable; answering the turn normally")
            return None

    def _last_exchange(self) -> str:
        """What he had just done, for the correction's Context line. "" when there is nothing.

        Read from the conversation log rather than remembered on `self`, so a correction still
        lands with its context after a restart — LB coming back to the Pi and saying "that was
        wrong" about this morning's answer is a real thing, and an engine that only remembers
        the current process would file it against nothing.
        """
        try:
            from tools.memory_manager import load_history

            for message in reversed(load_history()):
                if message.get("role") == "assistant":
                    said = " ".join(str(message.get("content", "")).split())
                    return f'I had just said: "{said[:300]}"'
        except Exception:                              # noqa: BLE001
            LOG.debug("could not read the previous turn for context", exc_info=True)
        return ""

    def _record_correction(self, correction, text: str, t: Turnlog) -> Response:
        """Write the correction down and say so. **Costs no API call.**

        The whole point of this path is that it is free and immediate. LB correcting him is the
        moment a turn must not fail, must not wait on a network round trip, and must not be
        rate-limited — a rebuke that gets answered with "I'm being rate limited" is the worst
        possible reply to it. So nothing here touches a model: the rule is LB's own words,
        sliced out of what he said, and the acknowledgement is a fixed sentence.

        See `tools/corrections.py` for why no model is allowed to paraphrase the rule.
        """
        from tools import corrections
        from tools.memory_manager import add_message

        t.route = "correction"
        t.extras.append("correction")
        add_message("user", text)

        saved = corrections.record(correction, context=self._last_exchange())

        if saved is None:
            # NEVER claim the save happened. `knowledge_vault.VAULT_INSTRUCTION` forbids exactly
            # this lie for the vault, and it matters more here: LB believing a rule is recorded
            # when it is not means he stops repeating it and it never takes effect.
            t.extras.append("correction NOT saved")
            speech = "You're right, and I couldn't write it down. The reason's on the screen."
            cards = [Card(CardKind.ERROR, "Correction NOT saved",
                          f"Could not write to {corrections.LEDGER}. The rule is not in force. "
                          f"Check the log for the cause — most likely file permissions.")]
        elif saved.rule:
            speech = "Got it. I've written that down and I won't do that again."
            # The rule is echoed aloud only when it survives the speakability filter — a rule
            # containing a path reads as "slash home slash l b" and is worse than not repeating
            # it. The card always carries it verbatim, so nothing is lost either way.
            from engine.split import is_speakable
            echo = f"The rule is: {' '.join(saved.rule.split())}"
            if is_speakable(echo) is None:
                speech = f"Got it, I've written that down. {echo}"
            cards = [Card(CardKind.MARKDOWN, "Correction saved",
                          f"**Rule:** {saved.rule}\n\n"
                          f"In force from the next answer onward. "
                          f"{len(corrections.active_rules())} standing rule(s) in "
                          f"`{corrections.LEDGER.name}`.")]
        else:
            # A bare rebuke carries no rule, and a ledger entry that says only "that was wrong"
            # is close to useless as a standing instruction. Asking is not deflection — it is
            # the one question that turns this into something he can actually follow, and LB's
            # answer will itself be detected as a directive and filed as its own rule.
            t.extras.append("rebuke — no rule yet")
            speech = ("You're right, and I've noted it. What should I have done instead?")
            cards = [Card(CardKind.MARKDOWN, "Correction saved",
                          f"**Noted:** {saved.said}\n\nNo rule yet — tell him what to do "
                          f"instead and that becomes the standing rule.")]

        add_message("assistant", speech)
        return Response(speech=speech, cards=cards, route="correction",
                        raw=f"Correction recorded: {correction.rule or correction.said}")

    # --- the three paths ---------------------------------------------------------------

    def _routed_turn(self, text: str, t: Turnlog) -> Response:
        from tools.memory_manager import add_message

        add_message("user", text)

        # The free tier, BEFORE the router. See `_free_turn`.
        response = self._free_turn(text, t)
        if response is None:
            # Second free pass: the turn needs an agent, but not a model to say WHICH agent.
            # Above the quota latch on purpose — the same reasoning the latch states about the
            # free tier, so "sync Canvas" and "CPU temp" keep working after the router is dry.
            destination = self._hinted_route(text, t)

            # Third free pass: let LB's OWN DOCUMENTS claim the turn. Searching the vector
            # store is local and costs nothing (`all-MiniLM-L6-v2`, no network), so a question
            # that lands close to a datasheet he has actually filed is a FIRMWARE question and
            # needs no model to say so. The chunks come back with the hint and are handed to
            # the agent, so the search happens once rather than twice.
            #
            # Below `route_hint` deliberately: "sync my schedule" and "cpu temp" have already
            # been claimed by then, and this must only ever see what would otherwise have been
            # paid for.
            corpus = None
            if destination is None:
                corpus = self._corpus_route(text, t)
                if corpus is not None:
                    destination = AgentRoute.FIRMWARE

            if destination is None:
                # The router's model may already be known out of quota. Asking again costs a
                # round trip to be told the same thing, and before LLM_MAX_RETRIES went to 0 it
                # cost up to 217 seconds of it. The free paths above still ran, so the time,
                # the date, a conversion and a launch all keep working — the point of the latch.
                from engine import quota
                from engine.models import ROUTER_MODEL
                from router import router_provider

                # **A local router has no quota, so the latch must not silence it.** Added
                # 2026-09-10 with `orchestrator/local_router.py`. Without this line, exhausting
                # `flash-lite`'s twenty a day would skip a router that costs nothing and never
                # sends a request — taking the rig off the air for the rest of the day over a
                # budget it is no longer spending. Escaping that budget is the entire reason
                # the local router exists, so this is not an edge case, it is the feature.
                if quota.exhausted(ROUTER_MODEL) and router_provider() == "google":
                    t.extras.append("router quota latched — not calling")
                    LOG.info("skipping the router: %s is out of quota until %s",
                             ROUTER_MODEL, quota.status().get(ROUTER_MODEL, "?"))
                    raise RuntimeError(
                        "RESOURCE_EXHAUSTED: quotaId GenerateRequestsPerDayPerProjectPerModel-"
                        f"FreeTier for {ROUTER_MODEL} (known locally, no request was sent)")

                t0 = time.monotonic()
                decision = self._route_within_deadline(text)
                t.route_s = time.monotonic() - t0
                if decision is None:
                    # The router never came back. GENERAL is the documented catch-all and the
                    # only route that can still file an upload, so it is the least wrong place
                    # to land — but say so in the log and the Turnlog, because a silent
                    # fallthrough would look exactly like a router that chose GENERAL.
                    destination = AgentRoute.GENERAL
                    t.route = destination.value
                    t.extras.append(f"router deadline {ROUTER_DEADLINE_S:g}s -> general")
                    LOG.warning("router did not answer within %gs; sending %r to GENERAL",
                                ROUTER_DEADLINE_S, text)
                else:
                    t.route = decision.destination.value
                    # **The line has to say who decided, and this is not cosmetic.**
                    # `tools/router_gym_corpus.py` harvests this exact format as its ground
                    # truth — the routes Gemini chose, to score a local candidate against. An
                    # unmarked local decision would be harvested as Gemini's, and the gym would
                    # then be scoring qwen2.5:1.5b against qwen2.5:1.5b's own past answers and
                    # reporting the 100% that follows. A green that means nothing is worse than
                    # a red, and this is the one-word difference between them.
                    #
                    # "no api call" is the marker every free branch in this file already ends
                    # with (`:1076`, `:1108`), so the gym filters all three with one rule and
                    # `tools/verify_local_router.py` asserts the contract on all four lines.
                    from router import router_provider
                    if router_provider() == "local":
                        LOG.info("route %r -> %s (%s — local, no api call)",
                                 text, t.route, decision.reasoning)
                    else:
                        LOG.info("route %r -> %s (%s)", text, t.route, decision.reasoning)
                    destination = decision.destination

            t0 = time.monotonic()
            if corpus is not None:
                # The corpus band dispatches its OWN turn, exactly as `_free_turn` dispatches
                # its own launch. Threading the chunks through `_dispatch` was tried and
                # reverted: that signature is stubbed by `tools/verify_router.py` to record
                # which agent ran, so adding a parameter to it turned four of its checks into
                # a swallowed `TypeError` — a real interface, with test doubles standing on it.
                from agents.firmware_agent import run_firmware_agent_response
                response = run_firmware_agent_response(text, (corpus.context, corpus.sources))
            else:
                response = self._dispatch(destination, text, t)
            t.agent_s = time.monotonic() - t0

        response = self._snapshot_memory_if_due(response, t)
        response = self._with_deadline_reminder(response, t)
        add_message("assistant", response.raw or response.speech)
        return response

    def _route_within_deadline(self, text: str):
        """`router_agent(text)`, but never for longer than `ROUTER_DEADLINE_S`.

        Returns:
            The `RouteDecision`, or **None when the deadline passed** — the caller sends that
            to GENERAL. Exceptions from the router are re-raised unchanged, because the quota
            latch and the 429 handling above both read them.

        ## Why a wall clock and not a retry setting

        Measured 2026-08-29, from `oddball.log`. Four routes on `gemini-3.5-flash-lite`, every
        one of them eventually returning **HTTP 200**:

            "organize the STL files"       90,984 ms
            "add to my note"             126,844 ms
            "read me back the notes"     162,453 ms
            "tell me what notes"         285,985 ms

        Two other routes the same morning took 766 ms and 875 ms. Same model, same key, some
        of them in the same minute. Nothing failed, nothing retried, and `LLM_MAX_RETRIES = 0`
        was already in force — so none of the existing protections applied. A single request
        simply took four and three quarter minutes, and the whole assistant waited for it.

        **The thread is abandoned, not killed**, because there is no way to cancel a blocking
        call inside the google-genai stack. It is a daemon, so it cannot hold up shutdown, and
        its answer is discarded if it ever arrives. The request was already sent, so its quota
        is already spent either way; what this reclaims is LB's time, not his budget.

        The one thing lost by abandoning it: if that late answer would have been a 429, the
        quota latch does not learn from it. That costs one wasted call on some later turn,
        against 286 seconds of silence now.
        """
        result: dict = {}

        def work() -> None:
            try:
                # Resolved from module globals at call time, NOT captured. `verify_router.py`
                # monkeypatches `core.router_agent` to prove which turns reach the router, and
                # binding it at import would route that harness straight past its own stub.
                result["decision"] = router_agent(text)
            except BaseException as exc:                               # noqa: BLE001
                result["error"] = exc

        worker = threading.Thread(target=work, name="router-deadline", daemon=True)
        worker.start()
        worker.join(ROUTER_DEADLINE_S)

        if worker.is_alive():
            return None
        if "error" in result:
            raise result["error"]
        return result.get("decision")

    # Intents allowed to answer WITHOUT consulting the router. Every one is a lookup with a
    # single right answer that no model improves on.
    #
    # `formula` is deliberately ABSENT even though it is free and often correct. Measured
    # 2026-08-21 against a 15-question corpus: it is the only intent that claims questions
    # belonging to an agent — "design a low pass filter with a cutoff of one kilohertz" is a
    # MATH problem and `formula` answers it with a formula. It stays behind the router, where
    # it has always been, until its matcher earns promotion. D38, for the sixth time: the
    # danger is never the intent that fails to match, it is the one that matches too much.
    # The social three, promoted 2026-08-23. `instant.py` already held canned answers for
    # them ("Hey LB.", "Any time.", "I'm Mr Odd Ball...") and still charged a router call to
    # reach a PERSONA agent that would improvise a different one. D3's first listed remedy is
    # "widen UTILITY — every question it absorbs is a free question", and a greeting is the
    # purest case of that.
    #
    # They could not be promoted as written. `hello` fired on a bare "hey", so "hey what's the
    # trace width for 5 amps" was answered "Hey LB." — behind the router that wasted a
    # classification; in front of it, it removes HARDWARE from the answer path entirely. All
    # three now take the end-anchor rule (`instant._is_bare`): the greeting has to BE the
    # utterance. `tools/verify_router.py` mutation-tests that by putting the bare matchers back.
    # What `engine/turn.py` puts in `Timings.intent` and `run_voice.turn_finished` reads to
    # decide the conversation is over. One definition, because a typo here is a dismissal that
    # does not dismiss and there is no error to see.
    SLEEP_ROUTE = "sleep"

    SOCIAL_INTENTS = frozenset({"hello", "thanks", "identity"})

    # Promoted 2026-09-03 off `data/oddball.log`, for the same reason the social three were
    # promoted on 2026-08-23: `instant.py` already had the right answer and the router was
    # being paid to find out. The difference is scale — a greeting cost one wasted call, and
    # an acknowledgement cost **16.2 minutes across thirty turns** because the persona model it
    # reached had no timeout on it. See `_ACK_PHRASES`.
    ACK_INTENT = "ack"

    FREE_INTENTS = frozenset({
        "time", "date", "convert", "constant", "define", "calc",
        ACK_INTENT}) | SOCIAL_INTENTS

    def _free_turn(self, text: str, t: Turnlog) -> Response | None:
        """Answer without spending a Gemini call, or return None to let the router decide.

        **Why this is in front of the router rather than behind it.** `router_agent()` is an
        API call, and it used to run unconditionally — so "what time is it" cost one request to
        be told to use a lookup table, and "open Firefox" cost three (route, write the command,
        paraphrase it aloud). D3 measured the free tier at 20 requests per model per day, which
        made six launches a whole day's quota.

        `orchestrator.instant` already answered all of this for free; the merge simply wired it
        in as the UTILITY *destination* instead of as a pass in front. So the free path existed
        and could only be reached by paying for it.

        A miss costs one `normalise()` and a few table lookups — microseconds — and then the
        turn proceeds exactly as it did before. Nothing here can answer *wrongly* in a new way:
        an intent either matches, in which case it answered before too, or it does not.

        Returns:
            A `Response`, or None when nothing free applies.
        """
        from orchestrator import file_intent, launch_intent, note_intent
        from orchestrator.instant import Router as InstantRouter

        try:
            # `planners` is checked before INTENTS and was built for exactly this. Injected
            # rather than imported, per that class's docstring: a dependency handed in is one a
            # harness can withhold, which is what lets `verify_engine.py` prove nothing runs.
            #
            # **Note before launch, and the order matters.** "start a new note" opens with
            # "start", which is a `launch_intent.LAUNCH_VERB`, so the launch planner would be
            # offered "a new note" as an application name. It resolves to nothing today and the
            # turn would fall through — but the day LB installs something called Notes, asking
            # to start a note would start a program instead. Ordering settles it structurally.
            #
            # **File comes after note, and that ordering is load-bearing too.** "add to my note
            # about the resistor chart quiz" carries a filing verb, a category word and a
            # document name that resolves against the inbox, so the file planner would take it
            # and MOVE A FILE in answer to a request to write two lines in a notebook. The note
            # planner demands the word "note"; letting it look first settles the overlap
            # structurally rather than by making either matcher warier.
            reply = InstantRouter(planners={"note": note_intent.look_up,
                                            "file": file_intent.look_up,
                                            "launch": launch_intent.look_up}).route(text)
        except Exception:                                              # noqa: BLE001
            LOG.exception("free tier failed; falling back to the router")
            return None

        request = reply.action
        if isinstance(request, note_intent.NoteRequest):
            return self._note_turn(request, t)

        if isinstance(request, file_intent.FileRequest):
            return self._file_turn(request, t)

        if isinstance(request, launch_intent.LaunchRequest):
            from agents.os_agent import propose_launch
            t.route = AgentRoute.OS.value
            t.extras.append(f"free launch ({request.app})")
            # Still gated. The free path decides WHAT was asked for, never whether to do it.
            return self._gate(propose_launch(request.app, request.spoken),
                              AgentRoute.OS.value, t)

        # **A dismissal is free, and it is also the thing that ENDS the conversation.**
        #
        # `orchestrator/instant.py` has matched these since it was written, and its own
        # docstring says "the caller watches for `intent == "sleep"` and closes the
        # conversation". Nothing watched. `FREE_INTENTS` did not list "sleep", so every
        # dismissal fell through to the paid router, came back labelled PERSONA, and
        # `Turn` set `t.intent = "persona"` — which `run_voice.turn_finished` reads as NOT
        # dismissed, so the window stayed open and he carried on talking.
        #
        # Measured 2026-08-29. LB said some form of "sleep" twelve times; every one cost a
        # router call and a persona call, and not one of them put him to sleep. He was still
        # answering three turns later. That is the whole of the day's OpenRouter allowance —
        # 50 requests — spent failing to go away.
        #
        # The route is the literal "sleep" rather than an `AgentRoute`: no agent answers a
        # dismissal, the reply is canned, and this string is the signal the voice loop is
        # already looking for.
        if reply.handled and reply.intent == self.SLEEP_ROUTE:
            t.route = self.SLEEP_ROUTE
            t.extras.append("free:sleep")
            return Response(speech=reply.text, route=self.SLEEP_ROUTE, raw=reply.text)

        if reply.handled and reply.intent in self.FREE_INTENTS:
            # The answer is canned either way; what differs is the label on the HUD's route
            # chip and in the Turnlog. "utility" is the wrong word for a greeting — LB was
            # talking TO him, which is what PERSONA means.
            route = (AgentRoute.PERSONA if reply.intent in self.SOCIAL_INTENTS
                     else AgentRoute.UTILITY).value
            t.route = route
            t.extras.append(f"free:{reply.intent}")
            return Response(speech=reply.text, route=route, raw=reply.text)

        return None

    def _hinted_route(self, text: str, t: Turnlog) -> "AgentRoute | None":
        """The destination, when naming it needs no model. See `orchestrator/route_hint.py`.

        The band between `_free_turn` (needs no agent at all) and `router_agent` (needs
        judgement): "sync Canvas" is ACADEMIC and "CPU temp" is OS whatever model you have.

        **Saves the router leg only** — 750 ms on Windows, 9.8 s measured on the Pi. The agent
        behind it still costs what it costs, so this is one call of two or three rather than a
        free turn, and saying otherwise would overstate it.

        `t.route_s` is deliberately left at 0.0, exactly as on a free turn, so the Turnlog
        reads `route 0ms -> academic` and the saving is legible in the log rather than inferred.

        Returns:
            An `AgentRoute`, or None to let the paid router decide — which is the answer for
            anything ambiguous, and for every keyword this repo refuses to match on.
        """
        from orchestrator import route_hint

        try:
            hint = route_hint.look_up(text)
            route = AgentRoute(hint) if hint else None
        except Exception:                                              # noqa: BLE001
            # D10's lesson, stated where it was learned: a silent fall-through to the paid
            # path is how the free tier died for a day without anyone noticing. Logged loudly.
            LOG.exception("route hint failed; falling back to the router")
            return None

        if route is None:
            return None
        t.route = route.value
        t.extras.append(f"free route:{route.value}")
        LOG.info("route %r -> %s (local, no api call)", text, route.value)
        return route

    def _corpus_route(self, text: str, t: Turnlog):
        """FIRMWARE, when LB's own datasheets recognise the question. See corpus_hint.py.

        The third and last free band, below `_free_turn` (needs no agent) and `_hinted_route`
        (the destination is idiomatic). This one asks the CORPUS, and it is the only router in
        the repo whose rule is derived from LB's files rather than written down: add a
        datasheet and its questions start landing here, delete it and they stop.

        **Saves the routing leg only** — one of the two calls a firmware turn costs, and it is
        the `flash-lite` one that `PERSONA_MODEL` shares a 20-a-day bucket with. `t.route_s`
        stays 0.0, as on every other free path, so the log reads `route 0ms -> firmware`.

        Returns:
            A `CorpusHit` carrying the chunks, or None — which is the answer for everything
            that is not about a document he has filed, and for every question at all when no
            store has been built.
        """
        from orchestrator import corpus_hint

        try:
            hit = corpus_hint.look_up(text)
        except Exception:                                              # noqa: BLE001
            LOG.exception("corpus hint failed; falling back to the router")
            return None

        if hit is None:
            return None
        t.route = AgentRoute.FIRMWARE.value
        t.extras.append(f"free route:firmware (corpus d={hit.distance:.2f})")
        LOG.info("route %r -> firmware (corpus, no api call)", text)
        return hit

    def _snapshot_memory_if_due(self, response: Response, t: Turnlog) -> Response:
        """Archive the conversation log into the vault when the 15-day clock comes due.

        **Silent, and no longer a card.** This used to append an ERROR card asking LB to copy
        `conversation_memory.json` somewhere and then type `--backed-up`. It asked because
        nothing in the system could watch him do it, and so the card needed an off switch, and
        the off switch needed explaining on the card itself.

        The vault is inside the system. `snapshot_if_due` writes the file and knows it landed, so
        there is nothing left to ask him for and no card worth showing. A reminder that fires on
        every turn with a chore attached is one he learns to ignore, and then he ignores the next
        one too.

        The response is returned untouched. The turn log still records the archive, so the day it
        happens is visible in `data/oddball.log` without anything interrupting an answer.

        Never raises: `snapshot_if_due` swallows its own failures and returns None. A failed
        archive must not cost him the answer he actually asked for.
        """
        from tools.memory_manager import snapshot_if_due

        target = snapshot_if_due()
        if target is not None:
            t.extras.append(f"memory snapshotted to vault ({target.name})")
        return response

    # How far ahead a deadline has to be before it stops being LB's problem today. His number.
    DEADLINE_WARNING_DAYS = 3

    def _with_deadline_reminder(self, response: Response, t: Turnlog) -> Response:
        """Coursework due within three days, on the card stack — **on every turn**.

        Global on purpose, and LB's explicit call. The alternative was to show it only on
        ACADEMIC-routed turns, which sounds tidier and is exactly wrong: he sees the warning
        only when he was already thinking about his coursework. A deadline reminder that fires
        when you are debugging firmware at 2am is the one that earns its place.

        Shown, never spoken, and unlike the memory snapshot above this one is LB's to act
        on, so it stays a card: an alarm read
        aloud in the middle of an unrelated answer is startling, and this is a reminder rather
        than an emergency. It costs a JSON read and no API call, which is the property that
        lets it sit on the turn path at all — see `tools/academic_calendar.py`.
        """
        from tools.academic_calendar import format_deadlines, get_upcoming_deadlines

        try:
            upcoming = get_upcoming_deadlines(days=self.DEADLINE_WARNING_DAYS)
        except Exception:                              # noqa: BLE001
            # A reminder is not worth a failed turn. `load_calendar` already swallows a
            # malformed file; this catches anything past it.
            LOG.exception("deadline check failed; answering without it")
            return response

        if not upcoming:
            return response

        t.extras.append(f"deadline reminder ({len(upcoming)})")
        title = ("Due today" if any(e["days_away"] == 0 for e in upcoming)
                 else f"Due within {self.DEADLINE_WARNING_DAYS} days")
        return Response(
            speech=response.speech,
            cards=list(response.cards) + [
                Card(CardKind.ERROR, title, format_deadlines(upcoming))],
            route=response.route, pending=response.pending, raw=response.raw)

    def _dispatch(self, route: AgentRoute, text: str, t: Turnlog) -> Response:
        """Hand the question to the one agent that should answer it."""
        if route is AgentRoute.QUIZ:
            # `text` is passed now, where it was not before: "quiz me on calculus" names the
            # deck, and the route alone throws that away. The subject is the difference between
            # a quiz he asked for and a quiz he has to sit through.
            return self._enter_quiz(text, t)

        if route is AgentRoute.UTILITY:
            return self._utility(text, t)

        if route is AgentRoute.OS:
            from agents.os_agent import propose_os_action
            return self._gate(propose_os_action(text), route.value, t)

        if route is AgentRoute.WEB:
            from agents.web_agent import propose_web_search
            return self._gate(propose_web_search(text), route.value, t)

        if route is AgentRoute.SCREEN:
            # Gated like OS and WEB, and it needed no new machinery to be: a screenshot is an
            # action that leaves the machine, which is what `Pending` is for. `screen_agent`
            # returns an ungated Response when ODDBALL_SCREEN_CONFIRM=0, and `_gate` passes
            # that straight through — the off switch lives in one place.
            from agents.screen_agent import propose_screen_look
            return self._gate(propose_screen_look(text), route.value, t)

        if route is AgentRoute.FIRMWARE:
            # The Response form, not the string form: it carries the Sources card naming which
            # datasheet and page grounded the answer, and an ungrounded answer that looks
            # identical to a grounded one is what the retrieval was added to prevent.
            from agents.firmware_agent import run_firmware_agent_response
            return run_firmware_agent_response(text)

        if route is AgentRoute.ACADEMIC:
            # Same shape as FIRMWARE and for the same reason — the Sources card names which
            # syllabus and page the answer came from. It matters more here: there is no public
            # record of LB's course to check an ungrounded answer against.
            from agents.academic_agent import run_academic_agent_response
            return run_academic_agent_response(text)

        if route is AgentRoute.HARDWARE:
            from agents.hardware_agent import run_hardware_agent
            return split(run_hardware_agent(text), route=route.value)

        if route is AgentRoute.MATH:
            from agents.math_agent import run_math_agent
            return split(run_math_agent(text), route=route.value)

        # PERSONA and GENERAL both go to the character. GENERAL used to return a canned
        # "I am a specialized Engineering Copilot" line, which is the one answer that makes
        # him sound like a kiosk — and he is not a kiosk, he is the interface.
        from agents.persona_agent import run_persona_agent
        return split(run_persona_agent(text), route=route.value)

    def _utility(self, text: str, t: Turnlog) -> Response:
        """The free route. Lookup tables, no model, microseconds.

        Falls through to the persona when the tables have nothing — `instant.Router` reports
        that honestly with `handled=False`, and answering "I don't know how to do that yet"
        when the router thought this was a utility question would strand the turn.
        """
        from orchestrator.instant import Router as InstantRouter

        reply = InstantRouter().route(text)
        if reply.handled:
            t.extras.append(f"instant:{reply.intent}")
            return Response(speech=reply.text, route=AgentRoute.UTILITY.value, raw=reply.text)

        t.extras.append(f"instant miss ({reply.intent}) -> persona")
        from agents.persona_agent import run_persona_agent
        return split(run_persona_agent(text), route=AgentRoute.PERSONA.value)

    # --- filing what he uploaded -------------------------------------------------------

    FILE_ROUTE = "file"

    # (singular, plural) for the spoken line. A table rather than a suffix rule, because "quiz"
    # pluralises to "quizzes" and "coursework" does not pluralise at all — and because he SAYS
    # this sentence, where "filed two file(s) as quiz" is the kind of thing that makes a machine
    # sound like a form.
    _CATEGORY_NOUN = {
        "quiz": ("a quiz", "quizzes"),
        "academic": ("coursework", "coursework"),
        "datasheet": ("a datasheet", "datasheets"),
        "schematic": ("a schematic", "schematics"),
    }

    @staticmethod
    def _and_list(names) -> str:
        """"a", "a and b", "a, b and c" — a spoken list, not a printed one.

        A comma before "and" is a typographic argument nobody can hear, and `piper` reads a bare
        comma-separated list as a stall. Two files is the common case here and it wants "and".
        """
        names = list(names)
        if len(names) <= 1:
            return names[0] if names else "nothing"
        return ", ".join(names[:-1]) + f" and {names[-1]}"

    def _file_turn(self, request, t: Turnlog) -> Response:
        """File the documents `orchestrator/file_intent.py` recognised. Costs zero API calls.

        **Every word spoken here is generated from the RESULT of a move that already happened.**
        That is the entire point of the route existing, and it is worth stating as a rule rather
        than leaving as a property of the code, because the bug it replaces was not a crash.

        On 2026-09-08 LB asked for two PDFs to be filed as quizzes. The request missed every
        matcher, went to the general agent, and came back "Filed resistorcharts.pdf and trig
        limits 2.pdf as quizzes. They're being indexed now" — a fluent paraphrase of the real
        tool's return string, which was in that agent's PREVIOUS CONTEXT from a genuine filing
        two days earlier. Both files were still in the inbox the next morning. Nothing in the log
        between the request and the reply. He had described the work instead of doing it.

        A reply built from `process_inbox_file`'s return value cannot do that: the sentence does
        not exist unless the move did. When the move fails, what LB hears is the failure.

        The parse and any question generation still run on the indexer's background thread — a
        scanned paper is seconds of OCR — so this says what was filed and where the rest of the
        story will appear, and never guesses at the outcome of a job that has not finished.
        """
        from tools.file_manager import inbox_files, process_inbox_file

        t.route = self.FILE_ROUTE
        t.extras.append(f"free file ({request.category}: {len(request.filenames)})")

        before = {p.name for p in inbox_files()}
        detail = []
        for name in request.filenames:
            try:
                detail.append(str(process_inbox_file.invoke(
                    {"filename": name, "category": request.category})))
            except Exception as exc:                                  # noqa: BLE001
                # One unfilable document must not cost the others. `process_inbox_file` already
                # returns its own errors as text; this catches the ones that escape it, and says
                # which file rather than abandoning the turn.
                LOG.exception("could not file %s", name)
                detail.append(f"I could not file {name}: {type(exc).__name__}.")

        # **The spoken line is built from the INBOX, not from what the tool said about itself.**
        #
        # Every sentence `process_inbox_file` returns opens with "Filed X to ..." — including,
        # necessarily, the ones where something later went wrong. Reading success out of that
        # text would make this route trust exactly the kind of fluent claim it exists to stop
        # trusting. A file that left `data/inbox/` was moved and a file still sitting in it was
        # not, and that is a fact about the disk rather than a sentence anybody composed.
        moved = [n for n in request.filenames if n not in {p.name for p in inbox_files()}
                 and n in before]
        stuck = [n for n in request.filenames if n not in moved]

        if not moved:
            speech = ("I could not file " + self._and_list(stuck) + ". "
                      + ("They are" if len(stuck) != 1 else "It is") + " still in the inbox.")
        else:
            one, many = self._CATEGORY_NOUN.get(request.category,
                                                (f"a {request.category}", request.category))
            plural = len(moved) != 1
            speech = f"Filed {self._and_list(moved)} as {many if plural else one}."
            if request.category == "quiz":
                speech += (f" I am reading the questions out of {'them' if plural else 'it'} "
                           f"now — ask me for the index status in a minute.")
            if stuck:
                speech += f" I could not move {self._and_list(stuck)}."

        cards = [Card(kind=CardKind.MARKDOWN, title="Filed", body="\n\n".join(detail))] \
            if detail else []
        return Response(speech=speech, cards=cards, route=self.FILE_ROUTE,
                        raw="\n\n".join(detail) or speech)

    # --- the notebook ------------------------------------------------------------------
    #
    # Five operations, reached from `_free_turn` when `orchestrator/note_intent.py` recognises
    # one. **Every path here costs zero API calls**, including the delete — the matcher is a
    # pure function of a string and `tools/knowledge_vault.py` is a folder of Markdown files.
    #
    # That is the whole point rather than a nice property. Dictating a note used to cost three
    # Gemini calls out of D3's measured twenty a day, and the moment LB most needs to write
    # something down is not a moment to discover the quota is gone.

    NOTE_ROUTE = "note"

    def _note_turn(self, request, t: Turnlog) -> Response:
        """Dispatch one recognised note request. Never raises — `ask()` catches, but the
        notebook failing should not look like the assistant crashing."""
        from orchestrator import note_intent

        t.route = self.NOTE_ROUTE
        t.extras.append(f"free note:{request.op}")

        if request.op == note_intent.NEW:
            return self._note_new(request, t)
        if request.op == note_intent.APPEND:
            return self._note_append(request, t)
        if request.op == note_intent.READ:
            return self._note_read(request, t)
        if request.op == note_intent.LIST:
            return self._note_list(request, t)
        return self._note_delete(request, t)

    def _say(self, speech: str, raw: str = "", cards: list[Card] | None = None) -> Response:
        """One notebook answer, on the notebook's route."""
        return Response(speech=speech, cards=cards or [], route=self.NOTE_ROUTE,
                        raw=raw or speech)

    def _one_note(self, target: str, t: Turnlog):
        """Resolve a spoken note name to exactly one file.

        Args:
            target: what LB called it.

        Returns:
            `(path, None)` when exactly one note matched, or `(None, Response)` carrying what to
            say instead. **Zero and two-or-more are different answers and neither is a guess** —
            `knowledge_vault.find_notes` is the resolver and this is the half that talks about
            it. `tools/kicad_parser.py` handles an ambiguous project name the same way, and it
            matters more here because one of the three callers deletes what it is handed.
        """
        from tools.knowledge_vault import VAULT_DIR, find_notes

        hits = find_notes(target)
        if len(hits) == 1:
            return hits[0], None

        if not hits:
            t.extras.append(f"note: no match for {target!r}")
            return None, self._say(
                f"I don't have a note called {target}.",
                raw=f"No note in the vault matches {target!r}.")

        t.extras.append(f"note: {len(hits)} match {target!r}")
        listing = "\n".join(
            f"- {p.resolve().relative_to(VAULT_DIR.resolve()).as_posix()}" for p in hits)
        return None, self._say(
            f"I've got {len(hits)} notes that could be {target}. Which one?",
            raw=f"{len(hits)} notes match {target!r}:\n{listing}",
            cards=[Card(CardKind.MARKDOWN, f"{len(hits)} notes match '{target}'", listing)])

    # --- new, which is the one that holds a turn open ------------------------------------

    def _note_new(self, request, t: Turnlog) -> Response:
        """Start a note. Asks for whatever LB did not say, in the order he will say it."""
        if not request.content:
            self.note_draft = NoteDraft(op="new", awaiting="content",
                                        folder=request.folder, name=request.name)
            t.extras.append("note: awaiting content")
            return self._say("What should I write down?",
                             raw="Waiting for the note's contents.")

        if not request.name:
            self.note_draft = NoteDraft(op="new", awaiting="name",
                                        content=request.content, folder=request.folder)
            t.extras.append("note: awaiting name")
            return self._say("What should I call it?",
                             raw=f"Waiting for a name for: {request.content}")

        return self._write_draft(NoteDraft(op="new", content=request.content,
                                           folder=request.folder, name=request.name), t)

    def _resolve_note(self, text: str, t: Turnlog, truncated: bool = False) -> Response:
        """Read the answer to an open note question.

        **Read and cleared unconditionally, at the top.** The draft cannot survive its own turn
        under any branch below, which is the property `ask()`'s own comment says the permission
        gate got wrong the first time: a held question that stays held eats the next thing LB
        says about anything at all.

        `truncated` is the single exception to that, added 2026-09-03, and it re-opens the draft
        deliberately — see `_write_draft`. The rule it bends is "a draft must not survive its
        own turn"; the reason it is safe to bend HERE is that LB is told, in the same breath,
        that it is still open and that he should carry on. The bug the rule exists against is a
        draft he does not know about.
        """
        from orchestrator.instant import is_sleep
        from orchestrator.note_intent import is_cancel

        draft, self.note_draft = self.note_draft, None
        t.route = self.NOTE_ROUTE
        answer = text.strip()

        # The escape, and it takes BOTH lists. `note_intent.is_cancel` is "never mind", "forget
        # it", "cancel" — stop this. `is_sleep` is "goodnight", "that's all" — stop everything,
        # which necessarily includes this. Both are end-anchored, so a note whose contents
        # genuinely mention forgetting something still gets written.
        if is_cancel(answer) or is_sleep(answer):
            t.extras.append("note: cancelled")
            return self._say("Alright, nothing written down.",
                             raw="Note abandoned by the user.")

        if draft.awaiting == "content":
            # Verbatim. Never normalised, never trimmed — except that a continuation JOINS what
            # came before it, because the previous recording stopped mid-sentence and replacing
            # it would throw away the half he already said.
            draft.content = f"{draft.content} {answer}".strip() if draft.truncated else answer

            # ## Cut off mid-sentence: hold, do not commit
            #
            # Handled HERE rather than in `_write_draft`, and for both operations at once, so
            # that `_write_draft` keeps meaning what its name says — commit a FINISHED draft.
            #
            # Nothing is written yet. That is deliberate and it is not a risk being taken
            # lightly: `append_note` puts a `\n\n---\n\n` rule between blocks, so writing the
            # front half now would put a horizontal rule through the middle of his sentence and
            # the repair would be worse than the damage. Holding it in memory for one more
            # utterance is exactly what the "what should I call it?" turn below has always done
            # with dictated content, so this is the established window, not a new one.
            if truncated:
                self.note_draft = NoteDraft(op=draft.op, awaiting="content",
                                            content=draft.content, folder=draft.folder,
                                            name=draft.name, path=draft.path, truncated=True)
                t.extras.append("note: TRUNCATED, still taking content")
                return self._say(
                    "I ran out of recording time there — keep going. I've got what you said so "
                    "far and I'll add the rest to it.",
                    raw=f"TRUNCATED at the recording cap. Held so far: {draft.content}",
                    cards=[Card(CardKind.ERROR, "Cut off — still listening",
                                f"{draft.content}\n\n*The recording hit its limit here. Carry "
                                f"on and this gets saved as one piece; say 'never mind' to "
                                f"drop it.*")])

            if draft.op == "append":
                return self._write_draft(draft, t)
            if draft.name:
                return self._write_draft(draft, t)

            self.note_draft = NoteDraft(op=draft.op, awaiting="name",
                                        content=draft.content, folder=draft.folder)
            t.extras.append("note: awaiting name")
            return self._say("What should I call it?",
                             raw=f"Waiting for a name for: {draft.content}")

        draft.name = answer
        return self._write_draft(draft, t)

    def _write_draft(self, draft: NoteDraft, t: Turnlog) -> Response:
        """Commit a FINISHED draft — a new note, or an addition to one already found.

        A draft cut off by the recording cap never reaches here; `_resolve_note` holds it open
        instead, so that what lands in the vault is one whole sentence rather than the front of
        one. See the truncation branch there for the recording that made that necessary.
        """
        from tools.knowledge_vault import VAULT_DIR, append_note, write_note

        if draft.op == "append" and draft.path is not None:
            result = append_note(draft.path, draft.content)
            rel = draft.path.resolve().relative_to(VAULT_DIR.resolve()).as_posix()
            ok = result.startswith("Added")
            t.extras.append("note appended" if ok else "note append FAILED")
            speech = ("Added to your note." if ok
                      else "I couldn't add to that note. It's on the screen.")
            return self._say(speech, raw=result,
                             cards=[Card(CardKind.LOG if ok else CardKind.ERROR,
                                         "Vault", f"{rel}\n\n{draft.content}")])

        folder = draft.folder or "notes"
        result = write_note(draft.name, draft.content, folder)
        ok = result.startswith("Successfully")
        t.extras.append("note written" if ok else "note write FAILED")

        # The path is SHOWN, not just spoken. A note filed in the wrong folder is a note LB
        # will not find again, and "saved it" without saying where is exactly the claim
        # `VAULT_INSTRUCTION` forbids a model from making.
        rel = result.split("Vault: ", 1)[-1] if ok else ""
        speech = (f"Written down in {folder}." if ok
                  else "I couldn't write that down. The reason's on the screen.")
        return self._say(speech, raw=result,
                         cards=[Card(CardKind.MARKDOWN if ok else CardKind.ERROR,
                                     rel or "Vault", draft.content if ok else result)])

    # --- add to, read back, list ---------------------------------------------------------

    def _note_append(self, request, t: Turnlog) -> Response:
        path, problem = self._one_note(request.target, t)
        if problem is not None:
            return problem

        if not request.content:
            self.note_draft = NoteDraft(op="append", awaiting="content", path=path)
            t.extras.append("note: awaiting content to append")
            # `path.stem`, not `request.target`. Since `find_notes` gained a word-overlap tier
            # the two can legitimately differ — "my note about the topic for my English
            # research paper" resolves to `english research question` — and the name he hears
            # back is his only chance to catch a wrong resolution BEFORE he dictates into it.
            return self._say(f"What should I add to {path.stem}?",
                             raw=f"Waiting for text to append to {path}.")

        return self._write_draft(
            NoteDraft(op="append", content=request.content, path=path), t)

    def _note_read(self, request, t: Turnlog) -> Response:
        """Read one note back — verbatim, and clipped honestly when it is too long to say."""
        from tools.knowledge_vault import find_notes, list_notes, read_note

        hits = find_notes(request.target)
        if not hits:
            # A name that matches no note may be a FOLDER. "What's in my ECE350 notes" is a
            # perfectly ordinary way to ask for a folder, and answering "I don't have a note
            # called ECE350" when there are four of them in exactly that folder is the kind of
            # literal-mindedness that makes an assistant feel broken.
            in_folder = list_notes(request.target)
            if in_folder:
                t.extras.append("note: read fell through to a folder listing")
                return self._folder_listing(request.target, in_folder, t)

        path, problem = self._one_note(request.target, t)
        if problem is not None:
            return problem

        view = read_note(path)
        t.extras.append(f"note read: {view.rel} ({len(view.entries)} entries)")
        return self._say(view.spoken, raw=f"--- {view.rel} ---\n{view.body}",
                         cards=[Card(CardKind.MARKDOWN, view.rel, view.body)])

    def _note_list(self, request, t: Turnlog) -> Response:
        from tools.knowledge_vault import list_notes

        found = list_notes(request.folder)
        if not found:
            where = f" in {request.folder}" if request.folder else ""
            return self._say(f"You haven't got any notes{where} yet.",
                             raw=f"The vault is empty{where}.")
        return self._folder_listing(request.folder, found, t)

    def _folder_listing(self, folder: str, found: list, t: Turnlog) -> Response:
        """What is in the vault, or in one folder of it. The names go on a card, not into the
        air — reading twelve filenames aloud is fifty seconds of Piper and nobody's idea of an
        answer."""
        from tools.knowledge_vault import VAULT_DIR

        t.extras.append(f"note list: {len(found)}")
        rels = [p.resolve().relative_to(VAULT_DIR.resolve()).as_posix() for p in found]
        where = f" in {folder}" if folder else ""
        plural = "note" if len(found) == 1 else "notes"

        # Name a couple out loud, because "you've got twelve notes" answers nothing.
        sample = ", ".join(Path(r).stem for r in rels[:3])
        tail = f" — {sample}" + (", and more." if len(rels) > 3 else ".")
        return self._say(f"You've got {len(found)} {plural}{where}{tail}",
                         raw="\n".join(rels),
                         cards=[Card(CardKind.LOG, f"{len(found)} {plural}{where}",
                                     "\n".join(rels))])

    # --- delete, which is the only one that asks first -----------------------------------

    def _note_delete(self, request, t: Turnlog) -> Response:
        """Propose deleting a note. **Nothing is removed here.**

        Reuses the permission gate whole rather than inventing a second one, which buys the
        property the gate was built for: the resolved path is rendered on a card BEFORE the
        question is asked, so what LB approves and what gets moved are provably the same file.
        Anything that is not a clear yes is a no, via `orchestrator/classify_yes.py`.
        """
        from tools.knowledge_vault import VAULT_DIR, read_note

        path, problem = self._one_note(request.target, t)
        if problem is not None:
            return problem

        view = read_note(path)
        size = path.stat().st_size if path.exists() else 0
        entries = len(view.entries)
        detail = (f"{view.rel}\n{entries} entr{'y' if entries == 1 else 'ies'}, {size} bytes\n\n"
                  f"{view.body}")
        # The RESOLVED name again, on the one path where getting it wrong destroys something.
        # The card already carries the full path; this makes the spoken question agree with it,
        # so approving by voice approves the file the screen is showing.
        spoken = f"Delete your {path.stem} note? It's got {entries} " \
                 f"{'entry' if entries == 1 else 'entries'} in it."

        proposed = Response(
            speech=spoken,
            cards=[Card(CardKind.MARKDOWN, f"Delete {view.rel}?", detail)],
            route=self.NOTE_ROUTE,
            pending=Pending(kind="note", tool_args={"path": str(path.resolve())},
                            spoken=spoken, shown=str(path.resolve()), tool="trash_note"),
            raw=f"Awaiting approval to delete {view.rel} ({size} bytes).")
        return self._gate(proposed, self.NOTE_ROUTE, t)

    def _trash_approved(self, pending: Pending) -> Response:
        """Carry out an approved delete. Reached only through `_run_pending`."""
        from tools.knowledge_vault import trash_note

        result = trash_note(Path(pending.tool_args["path"]))
        ok = result.startswith("Moved")
        speech = ("Gone. It's in the vault trash if you want it back."
                  if ok else "I couldn't delete that note. The reason's on the screen.")
        return Response(speech=speech,
                        cards=[Card(CardKind.LOG if ok else CardKind.ERROR, "Vault", result)],
                        route=self.NOTE_ROUTE, raw=result)

    # --- gates -------------------------------------------------------------------------

    def _gate(self, proposed: Response, route: str, t: Turnlog) -> Response:
        """Hold an action that wants approval, or pass through one that does not."""
        if proposed.pending is None:
            return proposed
        if not self._confirm_gates:
            t.extras.append(f"gate {route} auto-approved (harness)")
            return self._run_pending(proposed.pending, t)

        self.pending = proposed.pending
        t.extras.append(f"gate {route} waiting")
        return proposed

    def _resolve_pending(self, text: str, t: Turnlog) -> Response:
        """Read the answer to a permission question.

        Silence never reaches here — the voice loop turns that into a decline itself. What
        reaches here is a transcript, and anything that is not a clear yes is a no.
        """
        from orchestrator.classify_yes import is_yes

        pending, self.pending = self.pending, None
        t.route = pending.kind
        t.extras.append("gate answer")

        answer = is_yes(text)
        if answer is not True:
            t.extras.append("declined" if answer is False else f"unclear {text!r}")
            return Response(speech="No problem, I'll leave it.", route=pending.kind,
                            raw="Action aborted by the user.")

        t0 = time.monotonic()
        out = self._run_pending(pending, t)
        t.agent_s = time.monotonic() - t0
        return out

    def _run_pending(self, pending: Pending, t: Turnlog) -> Response:
        if pending.kind == "os":
            from agents.os_agent import resume_os_action
            return resume_os_action(pending)
        if pending.kind == "note":
            # The only gated action with no agent behind it. `kind` selects the AGENT for the
            # other three; here it selects a function, because deleting a Markdown file needs
            # no model and inventing an agent to hold one function would be the tail wagging.
            return self._trash_approved(pending)
        if pending.kind == "screen":
            from agents.screen_agent import resume_screen_look
            return resume_screen_look(pending)
        from agents.web_agent import resume_web_search
        return resume_web_search(pending)
    # --- quiz --------------------------------------------------------------------------
    #
    # ## The rule this whole section is built around
    #
    # LB, 2026-09-02: *"Make sure the question and answer knowledge is internal so it does not
    # have to use an outside AI bot unless I ask for a further explanation of an answer."*
    #
    # Before that, `_quiz_turn` called `agents/quiz_agent.evaluate_quiz_answer` — a Gemini
    # invoke — on **every single answer**. Ten questions was ten requests against a tier
    # counted in requests at 20 per model name per day (D3), spent deciding whether "V = I R"
    # matches "V = I * R". Revising for twenty minutes took the router, the persona agent and
    # the firmware agent down with it for the rest of the day.
    #
    # So: `tools/quiz_grade.grade` marks, locally, for nothing. `_explain_quiz` is the only
    # path out to a model, it runs only on an explicit request, and even then it serves the
    # deck's own stored explanation first and calls the model only when there is none.

    def _enter_quiz(self, text: str, t: Turnlog) -> Response:
        """Start a quiz. The subject, if he named one, comes out of what he said."""
        from tools.quiz_bank import deck_sizes, pick, resolve_subject
        from tools.quiz_manager import bank_summary

        asked_for = quiz_subject_of(text)
        subject = resolve_subject(asked_for)

        # He named a subject and there is no deck for it. Answered rather than silently widened
        # to the whole bank: being asked about circuits after asking for philosophy is the kind
        # of wrong that looks like the machine ignoring him, and the fix — upload the paper —
        # is one he can act on immediately.
        if asked_for and not subject:
            t.extras.append(f"quiz: no deck for {asked_for!r}")
            return Response(
                speech=f"I have no questions on {asked_for}. {bank_summary()}",
                cards=[Card(CardKind.MARKDOWN, "Question bank", _bank_card())],
                route=AgentRoute.QUIZ.value,
                raw=f"No deck for {asked_for!r}.\n\n{bank_summary()}")

        item = pick(subject)
        if item is None:
            t.extras.append("quiz: empty bank")
            return Response(speech=bank_summary(),
                            cards=[Card(CardKind.MARKDOWN, "Question bank", _bank_card())],
                            route=AgentRoute.QUIZ.value, raw=bank_summary())

        sizes = deck_sizes()
        pool = sizes.get(subject, 0) if subject else sum(sizes.values())

        self.mode = "quiz"
        self.quiz = QuizSession(subject=subject, item=item, asked={item.id})
        t.extras.append(f"entered quiz ({subject or 'all subjects'}, {pool} available)")

        scope = f"{subject} — {pool} question(s)" if subject else f"{pool} question(s)"
        opening = f"Quiz time, {subject}." if subject else "Quiz time."

        # The pacing is set ONCE, here, rather than nagged before every question. He is told the
        # rule at the start and then left alone with it — which is the whole point of the
        # change, and a reminder attached to each question would undo it.
        #
        # Only said when the rule is actually in force. Typed mode does not require a commit
        # phrase (Enter is already an end marker), and promising one there would be a lie.
        if self._quiz_commit:
            how = ("Take your time on these — I'll wait. Say 'ready' when you have an answer, "
                   "or just say 'my answer is' and then your answer. Say 'exit quiz' to stop, "
                   "or 'explain that' after an answer.")
        else:
            how = ("Say 'exit quiz' whenever you want to stop, or 'explain that' after an "
                   "answer.")

        return Response(
            speech=f"{opening} {how} First question: {_speakable_question(item)}",
            cards=[Card(CardKind.MARKDOWN, QUIZ_CHIP, _question_card(item, scope))],
            route=AgentRoute.QUIZ.value,
            raw=f"Entering quiz mode ({scope}).\n\n{_question_card(item, scope)}")

    def _quiz_turn(self, text: str, t: Turnlog) -> Response:
        """One utterance while the quiz lock is on. Marked HERE, on this machine.

        The order of the checks is the behaviour, and each one sits above the marking for a
        reason: leaving, being taught, asking the score and skipping are all things LB says
        that are *not* answers, and marking them as answers would be the machine not listening.

            exit / explain / score / skip     unchanged, and still above everything
              |
              +-- awaiting == "answer"        this utterance is the answer, whole
              +-- "I don't know"              an answer too; graded, not ignored
              +-- "my answer is 2x"           graded as "2x" — payload sliced, not normalised
              +-- "ready" (bare)              marks nothing, opens the door, "Go ahead."
              +-- anything else               THINKING ALOUD. Silence. Nothing marked.

        The last row is what `[quiz] commit_required` buys, and it is the whole of LB's
        request. Before it, the next thing he said after a question was graded as his answer to
        it — so working a derivation out loud was marked wrong and the quiz moved on while he
        was still doing the arithmetic. The commit stage only runs when that setting is on,
        which is true for VOICE and false for typing: Enter is already an end marker.
        """
        t.route = "quiz"
        if self.quiz is None:                          # defensive: mode on, session gone
            self.quiz = QuizSession()

        # He said SOMETHING, so he has not walked away. Reset before any of the branches below,
        # including the ones that ignore what he said — a man muttering through a derivation is
        # a man still sitting there, and `quiz_heard_nothing` must not count him out.
        self.quiz.silences = 0

        if _is_quiz_exit(text):
            return self._leave_quiz_reply(t)

        # Above skipping and marking, because "explain that" is a request about the answer
        # ALREADY given, and reading it as a new answer would mark him wrong for asking a
        # question. This is the one branch that can reach a model.
        if _matches(text, _QUIZ_EXPLAIN):
            return self._explain_quiz(t)

        if _matches(text, _QUIZ_SCORE):
            t.extras.append("quiz: score")
            tally = self.quiz.tally() or "nothing yet — you have not answered one"
            return Response(speech=f"You are on {tally}.", route="quiz",
                            raw=f"Score: {tally}.")

        if _matches(text, _QUIZ_SKIP):
            return self._next_quiz_question(t, prefix="Skipping that one.", skipped=True)

        item = self.quiz.item
        if item is None:
            return self._next_quiz_question(t, prefix="Let me put a question to you.")

        # ---- THE COMMIT STAGE. Nothing below this point runs until LB says he is ready. ----
        #
        # Sits BELOW exit, explain, score and skip on purpose: those are all things he says
        # that are not answers, and they must keep working whether or not he has committed.
        # It sits ABOVE the marking because its whole job is to decide what gets marked.
        from tools.quiz_grade import looks_like_pass                  # noqa: PLC0415

        answer = text
        if self._quiz_commit and self.quiz.awaiting == "commit":
            carried = _commit_payload(text)
            if carried:
                # "My answer is 2x" — the phrase and the answer arrived together.
                answer = carried
            elif _matches(text, _QUIZ_COMMIT_BARE):
                # "Ready" — he has it but has not said it. Mark nothing; open the door.
                self.quiz.awaiting = "answer"
                self.quiz.ignored = 0
                t.extras.append("quiz: committed, waiting for the answer")
                return Response(speech="Go ahead.", route="quiz", raw="Go ahead.")
            elif looks_like_pass(text):
                # "I don't know" is an ANSWER and is marked as one — `grade` has a branch for
                # it that replies "No problem. The answer is..." A shrug is not thinking aloud,
                # and making him say "my answer is I don't know" would be absurd.
                pass
            else:
                # Thinking out loud. Not an answer, not a command, not marked.
                return self._quiz_thinking(t)

        # He committed on a previous turn and this utterance is the answer, whole.
        if self.quiz.awaiting == "answer":
            self.quiz.awaiting = "commit"

        # THE MARKING. No network, no key, no quota. `grade` never raises — its own failure
        # path returns an "I could not mark that" verdict rather than an exception, because an
        # exception here would drop him out of quiz mode entirely.
        from tools.memory_manager import add_message
        from tools.quiz_grade import grade

        # The ORIGINAL utterance goes into the conversation memory, not the sliced payload:
        # what he actually said was "my answer is 2x", and a history that records "2x" is a
        # history of something nobody said.
        add_message("user", text)
        t0 = time.monotonic()
        result = grade(item, answer)
        t.agent_s = time.monotonic() - t0
        t.extras.append(f"marked locally: {result.verdict} ({result.method})")

        self.quiz.answered += 1
        self.quiz.score += result.scored
        self.quiz.ignored = 0
        self.quiz.last_item, self.quiz.last_grade = item, result
        self.quiz.last_answer = answer

        # `result.why` is the whole sentence and already states the verdict. Prefixing another
        # verdict word on top produced "Correct. Correct — B, act only on maxims..." and
        # "Not quite. Not quite. The answer is..." in the first end-to-end run — said out loud,
        # a stutter. The verdict still appears visually, as the marking card's title.
        marking = result.why
        add_message("assistant", marking)

        return self._next_quiz_question(t, prefix=marking, marked=result, answered=item)

    def _quiz_thinking(self, t: Turnlog) -> Response:
        """He is working it out aloud. Say nothing, mark nothing, keep listening.

        This is the branch `[quiz] commit_required` exists to create. Before it, the next thing
        LB said after a question was graded as his answer to it — so "okay so one over 4.7k
        plus one over 4.7k" was marked wrong, and the quiz moved on while he was still working.

        **The reply is an empty `speech`, which `engine/turn.py::_deliver` reads as "say
        nothing".** Not a filler line: speaking holds the microphone gate shut, so a reply here
        would deafen him to the very next thing he says, which is the thing he is working
        towards. The right answer to a man thinking is silence.

        The one exception is the reminder, and it is a trap-door rather than a feature. If
        `base.en` mis-hears "my answer is", every utterance lands here and he is answering into
        a machine that will never mark him, with nothing on screen or in the air to say why.
        After `[quiz] nudge_after_ignored` of them he is told the phrase — once, not every time,
        because interrupting him is the behaviour this whole change removes.
        """
        session = self.quiz
        session.ignored += 1
        t.extras.append(f"quiz: thinking aloud ({session.ignored})")

        if self._quiz_nudge_after and session.ignored == self._quiz_nudge_after:
            session.ignored = 0
            t.extras.append("quiz: reminded him of the phrase")
            line = "Say 'ready' when you have it, or 'my answer is' and then your answer."
            return Response(speech=line, route="quiz", raw=line)

        return Response(speech="", route="quiz", raw="")

    def quiz_silence_line(self, attempt: int) -> "str | None":
        """What he says into a silent quiz turn — or None to say nothing at all.

        Called from `engine/turn.py::_silence_line`, which owns the decision for every OTHER
        kind of silence. This one is quiz-specific for a reason: the generic answer is the
        greeting, "What's up LB?", and asking a man in the middle of long division what he
        wants is the single most annoying thing this machine currently does. It is the exact
        behaviour LB reported: *"i need time to answer the question."*

        Args:
            attempt: 1 for the first silent capture of a turn, 2 for the one after it.

        Returns:
            Attempt 1 — three words, at most. Speaking holds `MicGate.speaking` plus
            `gate_tail_s`, so every syllable is one he is not being heard through; a nudge that
            lands as he starts answering eats the start of his answer.

            Attempt 2 — the question again, **stem only, never the options.** Reading four MCQ
            options a second time is twenty seconds of speech at the measured 160 wpm, to
            deliver something already sitting on the card in front of him, and it would arrive
            just as he was about to answer.

            None once the session has been silent for `[quiz] idle_turns` — at which point
            `quiz_heard_nothing` is about to close the session, and a nudge would be the
            machine talking to an empty room on its way out.
        """
        session = self.quiz
        if session is None or session.item is None:
            return None
        # Close to giving up: `quiz_heard_nothing` is counting, and one more silent turn ends
        # the session. Do not spend a line on it.
        if self._quiz_idle_turns and session.silences >= self._quiz_idle_turns - 1:
            return None
        if attempt <= 1:
            return random.choice(_QUIZ_NUDGE)
        return f"The question again. {_speakable_question(session.item, options=False)}"

    def quiz_heard_nothing(self) -> bool:
        """A turn ended without a word while a quiz was open. True to keep waiting.

        Called from `engine/run_voice.py::turn_finished`, which is the only place that knows a
        turn heard nothing — `_quiz_turn` is by definition never reached without an utterance.

        **This deliberately keeps the conversation window open on an UNANSWERED turn**, which
        the rule in `turn_finished` exists to forbid, so the exception has to argue for itself.

        That rule is there because *a false wake captures silence, and without it one of them
        would hold the microphone open indefinitely by re-triggering itself.* **A quiz session
        cannot be opened by a false wake.** It is entered by an utterance that was heard,
        found credible, routed, and answered with a question. The silence here is a question
        being worked on, not a room that made a noise.

        Bounded, and it has to be, because two minutes of a man thinking and two minutes of an
        empty room are the same recording. On the limit the session ENDS — through
        `leave_quiz`, which has existed since 2026-08-19 as the documented escape hatch and
        was called from nowhere in the repo until today. That is how a quiz became a mode with
        exactly one exit, and that exit a phrase `base.en` had to hear correctly first.

        Returns:
            True while the quiz should keep listening. False once it has given up, at which
            point the mode is already back to "normal".
        """
        session = self.quiz
        if self.mode != "quiz" or session is None or session.item is None:
            return False

        session.silences += 1
        # 0 means never give up. Legal, configured, and not recommended — see config.
        if not self._quiz_idle_turns:
            return True
        if session.silences < self._quiz_idle_turns:
            LOG.info("quiz: silent turn %d of %d — still waiting",
                     session.silences, self._quiz_idle_turns)
            return True

        LOG.info("quiz: %d silent turns — closing the session", session.silences)
        self.leave_quiz()
        return False

    def _next_quiz_question(self, t: Turnlog, prefix: str = "", skipped: bool = False,
                            marked=None, answered=None) -> Response:
        """Put the next question up, carrying whatever was said about the last one."""
        from tools.quiz_bank import load_all, load_deck, pick

        session = self.quiz
        if skipped and session.item is not None:
            # A skipped question is still marked as ASKED, so it does not come straight back
            # round. He skipped it; asking it again next is the machine arguing with him.
            session.asked.add(getattr(session.item, "id", ""))
            t.extras.append("quiz: skipped")

        # Going round again, ANNOUNCED. `quiz_bank.pick` wraps silently once every question has
        # been asked, which is the right behaviour for a drill and the wrong thing to do without
        # saying so: the first end-to-end run re-asked question 2 straight after question 3 with
        # no explanation, and that reads as the bug the old `random.choice` actually had.
        pool = load_deck(session.subject) if session.subject else load_all()
        if pool and all(i.id in session.asked for i in pool):
            # The question just answered stays excluded, so "round again" never begins with the
            # one still fresh in his ears.
            session.asked = {getattr(session.item, "id", "")}
            where = f" on {session.subject}" if session.subject else ""
            prefix = (f"{prefix} That is every question I have{where} — going round again."
                      ).strip()
            t.extras.append("quiz: wrapped")

        item = pick(session.subject, exclude=session.asked)
        cards: list[Card] = []

        # The marking goes on screen as well as into the air, because speech is heard once at
        # ~160 words per minute (D32) and an answer he got wrong is exactly the thing worth
        # reading twice. The rule in `engine/response.py`: never let a fact live only in speech.
        if marked is not None and answered is not None:
            cards.append(Card(CardKind.MARKDOWN, _mark_title(marked),
                              _marking_card(answered, marked)))

        if item is None:
            # The bank is EMPTY — the wrap above means exhaustion can no longer land here, so
            # this is the deck having been deleted underneath a running session. Not an error,
            # and not a reason to drop him out of the mode: he can upload a paper and carry on.
            session.item = None
            tally = session.tally()
            done = f" You finished on {tally}." if tally else ""
            speech = (f"{prefix} I have run out of questions — there is nothing left in that "
                      f"subject.{done} Upload a practice quiz and I can keep going.")
            return Response(speech=speech.strip(), cards=cards, route="quiz",
                            raw=f"{prefix}\n\nNo questions left.{done}")

        session.item = item
        session.asked.add(item.id)
        scope = session.subject or "all subjects"
        cards.append(Card(CardKind.MARKDOWN, QUIZ_CHIP, _question_card(item, scope)))

        speech = f"{prefix} Next question: {_speakable_question(item)}".strip()
        return Response(speech=speech, cards=cards, route="quiz",
                        raw=f"{prefix}\n\n{_question_card(item, scope)}")

    def _explain_quiz(self, t: Turnlog) -> Response:
        """Explain the last answer. **The only place in the quiz that may call a model.**

        Two stages, and the staging is the whole point. `explain_locally` returns whatever the
        DECK knows — the worked solution the practice paper shipped with, the option he did not
        pick, the terms his answer missed. That costs nothing and is often better than a model,
        because it is the actual answer from the actual paper.

        Only when the deck has nothing more to say is `agents/quiz_agent` reached, and that is
        precisely the case LB carved out: *unless I ask for a further explanation*.
        """
        from tools.quiz_grade import explain_locally

        session = self.quiz
        # `last_item` before `item`, because by the time he says "explain that" the NEXT
        # question is already on the table. Explaining that one would hand him the answer to a
        # question he has not been given a chance to attempt — the quiz spoiling itself.
        item = session.last_item or session.item
        if item is None:
            t.extras.append("quiz: nothing to explain")
            return Response(speech="I have not asked you anything yet, so there is nothing to "
                                   "explain. Give me an answer first.",
                            route="quiz", raw="Nothing to explain yet.")

        question = getattr(item, "question", "")
        answer = getattr(item, "answer", "")

        local = explain_locally(item, session.last_grade)
        if local:
            t.extras.append("quiz: explained from the deck, no API call")
            return Response(
                speech=local,
                cards=[Card(CardKind.MARKDOWN, "Explanation", local)],
                route="quiz", raw=local)

        # The deck has the answer and nothing behind it. This is the one call.
        from agents.quiz_agent import explain_quiz_answer

        t.extras.append("quiz: explained by the agent (1 API call)")
        t0 = time.monotonic()
        explanation = explain_quiz_answer(question=question, correct_answer=answer,
                                          user_answer=session.last_answer)
        t.agent_s = time.monotonic() - t0

        spoken = split(explanation, route="quiz",
                       fallback="I've put the explanation on the screen.")
        return Response(speech=spoken.speech, cards=list(spoken.cards), route="quiz",
                        raw=explanation)

    def _leave_quiz_reply(self, t: Turnlog) -> Response:
        """Exit, with the score.

        A quiz that does not tell you how you did is a quiz you cannot use to decide what to
        revise next, which is the only reason to sit one.
        """
        tally = self.quiz.tally() if self.quiz else ""
        subject = self.quiz.subject if self.quiz else ""
        self.mode = "normal"
        self.quiz = None
        t.extras.append("left quiz")

        if not tally:
            return Response(speech="Alright, quiz over. Back to normal.", route="quiz",
                            raw="Exiting quiz mode.")
        where = f" on {subject}" if subject else ""
        line = f"Quiz over. You got {tally}{where}."
        return Response(speech=f"{line} Back to normal.", route="quiz", raw=line)

    def leave_quiz(self) -> None:
        """Break the lock from outside — the wake word's escape hatch.

        A mode you cannot see is a mode you get stuck in, and a mode whose only exit is a
        transcript is one `tiny.en` can trap you in. This is the way out that does not depend
        on being heard correctly.
        """
        self.mode = "normal"
        self.quiz = None


# ---------------------------------------------------------------------------------------
# Saying a question out loud, and putting it on screen
#
# Two forms of every question, for the reason `engine/response.py` gives: speech is heard once
# at ~160 words per minute and a card is read at leisure. A multiple-choice question is the
# sharpest case of it in the repo — four options is a lot to hold from one hearing, and the
# letters mean nothing without their text, so both halves carry all four.
# ---------------------------------------------------------------------------------------

def _speakable_question(item, options: bool = True) -> str:
    """The question as it should be READ ALOUD.

    Args:
        item:    the `QuizItem`.
        options: include the lettered options. True everywhere a question is ASKED, because
                 the letters mean nothing without their text and he has one hearing of them.

                 False for a RE-READ into a pause — `Engine.quiz_silence_line(2)`. He has
                 already heard the options once and they are on the card in front of him;
                 reading four of them again is about twenty seconds of speech at the measured
                 160 wpm, and it would arrive just as he was about to answer.
    """
    from orchestrator.math_notation import to_speech

    # Through `to_speech` because Piper reads "sin" as the English noun, so a trig question
    # arrived as a sentence about wrongdoing. LB, 2026-09-12: the questions were hard to
    # follow "because of how it's displayed and spoken". This is the spoken half.
    text = to_speech(getattr(item, "question", "") or "")
    choices = getattr(item, "choices", {}) or {}
    if not choices or not options:
        return text
    spoken = ". ".join(f"{letter}, {to_speech(body)}" for letter, body in sorted(choices.items()))
    return f"{text} Your options are: {spoken}."


def _question_card(item, scope: str = "") -> str:
    """The question as Markdown, for the chat panel."""
    from orchestrator.math_notation import to_unicode

    # `to_unicode` BEFORE the markdown wrapper, never after: it converts "*" between operands
    # and the "**Q:**" added here is emphasis, not multiplication.
    lines = [f"**Q:** {to_unicode(getattr(item, 'question', ''))}"]
    choices = getattr(item, "choices", {}) or {}
    if choices:
        lines.append("")
        lines += [f"- **{letter})** {to_unicode(body)}" for letter, body in sorted(choices.items())]

    # Where it came from, because a question LB thinks is wrong is one he will want to check
    # against the paper — and "page 4 of the review packet" is what makes that a ten-second job
    # rather than a hunt.
    source = getattr(item, "source", "")
    if source:
        page = getattr(item, "page", 0)
        lines += ["", f"*{source}{f', page {page}' if page else ''}*"]
    elif scope:
        lines += ["", f"*{scope}*"]
    return "\n".join(lines)


def _mark_title(result) -> str:
    """The card heading for a marked answer, so the verdict is visible without reading it."""
    return {"correct": "Correct", "partial": "Partly right",
            "incorrect": "Not quite"}.get(getattr(result, "verdict", ""), "Marked")


def _marking_card(item, result) -> str:
    """What was asked, what the answer was, and how it was marked — as Markdown."""
    from orchestrator.math_notation import to_unicode

    # The ANSWER matters as much as the question here — the trig deck stores one as
    # "0 < sin(t) < √2√(1 - cos(t)) < t", which is the line LB is checking his own work
    # against.
    lines = [f"**Q:** {to_unicode(getattr(item, 'question', ''))}", "",
             f"**Answer:** {to_unicode(getattr(item, 'answer', ''))}", "",
             getattr(result, "why", "")]
    if getattr(result, "verdict", "") != "correct":
        lines += ["", "*Say 'explain that' for the working.*"]
    return "\n".join(lines)


def _bank_card() -> str:
    """The question bank as a Markdown table. Shown when a subject does not exist."""
    from tools.quiz_bank import deck_sizes

    sizes = deck_sizes()
    if not sizes:
        return ("The question bank is empty.\n\nUpload a practice quiz or a question-and-answer "
                "PDF with the paperclip and tell me to file it as a **quiz**.")
    lines = ["| Subject | Questions |", "| --- | --- |"]
    lines += [f"| {name} | {count} |" for name, count in sizes.items()]
    return "\n".join(lines)
