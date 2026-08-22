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
`tools/memory_manager.py`, on the SD card, and having two of them is how they disagree.

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
import re
import time
from dataclasses import dataclass, field

from engine.response import Card, CardKind, Pending, Response
from engine.split import split
from router import AgentRoute, router_agent

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


def _failure_line(exc: Exception) -> str:
    """What he says when a turn fails. Names the layer, because "something went wrong" helps
    nobody and costs LB the debugging time of finding out which layer it was.

    The quota case is called out on its own because it is **not a fault** — it is the free
    tier doing exactly what it says, and reporting it as a crash sends LB looking for a bug
    that is not there. `brains/gemini.py` in the standalone assistant made the same
    distinction for the same reason.
    """
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        from engine.models import FREE_TIER_DAILY_LIMIT
        return (f"I've used up my {FREE_TIER_DAILY_LIMIT} free questions for today. "
                "The utility stuff still works — ask me the time.")
    if "NOT_FOUND" in text or "404" in text:
        return "That model name isn't valid any more. The details are on the screen."
    if "PERMISSION_DENIED" in text or "API key" in text:
        return "My API key isn't working. The details are on the screen."
    return "Something went wrong on my end. It's on the screen."


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


class Engine:
    """The copilot, with no terminal attached.

    Args:
        confirm_gates: when False, OS and WEB run without asking. **Only for harnesses.**
                       The default is the safe one, and it is not configurable from the UI —
                       a permission gate with an off switch on the surface is not a gate.
    """

    def __init__(self, confirm_gates: bool = True) -> None:
        self.mode = "normal"
        self.quiz_item: dict | None = None
        self.pending: Pending | None = None
        self._confirm_gates = confirm_gates
        self.last: Turnlog = Turnlog()

    # --- the one entry point -----------------------------------------------------------

    def ask(self, text: str) -> Response:
        """Answer one question. Never raises; a failure comes back as a spoken sentence.

        Args:
            text: what LB said or typed. Already transcribed.
        """
        t = Turnlog(mode=self.mode)
        self.last = t
        text = (text or "").strip()

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
            return Response(speech="I didn't catch that.", route="", raw="")

        try:
            if self.pending is not None:
                return self._resolve_pending(text, t)
            if self.mode == "quiz":
                return self._quiz_turn(text, t)
            return self._routed_turn(text, t)
        except Exception as exc:                       # noqa: BLE001 — the answer path never dies
            LOG.exception("turn failed")
            t.extras.append(f"error {type(exc).__name__}")
            return Response(
                speech=_failure_line(exc),
                cards=[Card(CardKind.ERROR, type(exc).__name__, str(exc))],
                route=t.route,
            )
        finally:
            LOG.info("%s", t.line())

    # --- the three paths ---------------------------------------------------------------

    def _routed_turn(self, text: str, t: Turnlog) -> Response:
        from tools.memory_manager import add_message

        add_message("user", text)

        # The free tier, BEFORE the router. See `_free_turn`.
        response = self._free_turn(text, t)
        if response is None:
            t0 = time.monotonic()
            decision = router_agent(text)
            t.route_s = time.monotonic() - t0
            t.route = decision.destination.value
            LOG.info("route %r -> %s (%s)", text, t.route, decision.reasoning)

            t0 = time.monotonic()
            response = self._dispatch(decision.destination, text, t)
            t.agent_s = time.monotonic() - t0

        response = self._with_backup_reminder(response, t)
        response = self._with_deadline_reminder(response, t)
        add_message("assistant", response.raw or response.speech)
        return response

    # Intents allowed to answer WITHOUT consulting the router. Every one is a lookup with a
    # single right answer that no model improves on.
    #
    # `formula` is deliberately ABSENT even though it is free and often correct. Measured
    # 2026-08-21 against a 15-question corpus: it is the only intent that claims questions
    # belonging to an agent — "design a low pass filter with a cutoff of one kilohertz" is a
    # MATH problem and `formula` answers it with a formula. It stays behind the router, where
    # it has always been, until its matcher earns promotion. D38, for the sixth time: the
    # danger is never the intent that fails to match, it is the one that matches too much.
    FREE_INTENTS = frozenset({"time", "date", "convert", "constant", "define", "calc"})

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
        from orchestrator import launch_intent
        from orchestrator.instant import Router as InstantRouter

        try:
            # `planners` is checked before INTENTS and was built for exactly this. Injected
            # rather than imported, per that class's docstring: a dependency handed in is one a
            # harness can withhold, which is what lets `verify_engine.py` prove nothing runs.
            reply = InstantRouter(planners={"launch": launch_intent.look_up}).route(text)
        except Exception:                                              # noqa: BLE001
            LOG.exception("free tier failed; falling back to the router")
            return None

        request = reply.action
        if isinstance(request, launch_intent.LaunchRequest):
            from agents.os_agent import propose_launch
            t.route = AgentRoute.OS.value
            t.extras.append(f"free launch ({request.app})")
            # Still gated. The free path decides WHAT was asked for, never whether to do it.
            return self._gate(propose_launch(request.app, request.spoken),
                              AgentRoute.OS.value, t)

        if reply.handled and reply.intent in self.FREE_INTENTS:
            t.route = AgentRoute.UTILITY.value
            t.extras.append(f"free:{reply.intent}")
            return Response(speech=reply.text, route=AgentRoute.UTILITY.value, raw=reply.text)

        return None

    def _with_backup_reminder(self, response: Response, t: Turnlog) -> Response:
        """The 15-day clock. Appended to the SHOWN half, never the spoken one: a system alarm
        read aloud in the middle of an answer is startling, and this is a reminder rather than
        an emergency. It stays on screen until LB deals with it.

        Applies to free turns too — a reminder that only fires when he happens to make an API
        call is a reminder that stops firing on exactly the days he is being careful with quota.
        """
        from tools.memory_manager import check_for_backup_reminder

        if not check_for_backup_reminder():
            return response
        t.extras.append("backup reminder")
        return Response(
            speech=response.speech,
            cards=list(response.cards) + [Card(
                CardKind.ERROR, "Back up your memory",
                "sd_card_memory.json is more than 15 days old. Copy it to the portable "
                "drive before the SD card is the only copy of it.")],
            route=response.route, pending=response.pending, raw=response.raw)

    # How far ahead a deadline has to be before it stops being LB's problem today. His number.
    DEADLINE_WARNING_DAYS = 3

    def _with_deadline_reminder(self, response: Response, t: Turnlog) -> Response:
        """Coursework due within three days, on the card stack — **on every turn**.

        Global on purpose, and LB's explicit call. The alternative was to show it only on
        ACADEMIC-routed turns, which sounds tidier and is exactly wrong: he sees the warning
        only when he was already thinking about his coursework. A deadline reminder that fires
        when you are debugging firmware at 2am is the one that earns its place.

        Shown, never spoken, for the same reason as `_with_backup_reminder`: an alarm read
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
            return self._enter_quiz(t)

        if route is AgentRoute.UTILITY:
            return self._utility(text, t)

        if route is AgentRoute.OS:
            from agents.os_agent import propose_os_action
            return self._gate(propose_os_action(text), route.value, t)

        if route is AgentRoute.WEB:
            from agents.web_agent import propose_web_search
            return self._gate(propose_web_search(text), route.value, t)

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
        from agents.web_agent import resume_web_search
        return resume_web_search(pending)

    # --- quiz --------------------------------------------------------------------------

    def _enter_quiz(self, t: Turnlog) -> Response:
        from tools.quiz_manager import get_random_question

        self.mode = "quiz"
        self.quiz_item = get_random_question()
        t.extras.append("entered quiz")
        question = self.quiz_item["question"]
        return Response(
            speech=f"Quiz time. Say 'exit quiz' whenever you want to stop. First question: {question}",
            cards=[Card(CardKind.MARKDOWN, QUIZ_CHIP, f"**Q:** {question}")],
            route=AgentRoute.QUIZ.value,
            raw=f"Entering Quiz Mode.\n\nFirst Question: {question}")

    def _quiz_turn(self, text: str, t: Turnlog) -> Response:
        from agents.quiz_agent import evaluate_quiz_answer
        from tools.memory_manager import add_message
        from tools.quiz_manager import get_random_question

        t.route = "quiz"

        if _is_quiz_exit(text):
            self.mode = "normal"
            self.quiz_item = None
            t.extras.append("left quiz")
            return Response(speech="Alright, quiz over. Back to normal.", route="quiz",
                            raw="Exiting Quiz Mode.")

        add_message("user", text)
        t0 = time.monotonic()
        evaluation = evaluate_quiz_answer(
            question=self.quiz_item["question"],
            correct_answer=self.quiz_item["answer"],
            user_answer=text)
        t.agent_s = time.monotonic() - t0

        graded = split(evaluation, route="quiz",
                       fallback="I've put the marking on the screen.")

        self.quiz_item = get_random_question()
        nxt = self.quiz_item["question"]

        add_message("assistant", f"{evaluation}\n\nNext: {nxt}")
        return Response(
            speech=f"{graded.speech} Next question: {nxt}",
            cards=list(graded.cards) + [Card(CardKind.MARKDOWN, QUIZ_CHIP, f"**Q:** {nxt}")],
            route="quiz",
            raw=f"{evaluation}\n\nNext Question: {nxt}")

    def leave_quiz(self) -> None:
        """Break the lock from outside — the wake word's escape hatch.

        A mode you cannot see is a mode you get stuck in, and a mode whose only exit is a
        transcript is one `tiny.en` can trap you in. This is the way out that does not depend
        on being heard correctly.
        """
        self.mode = "normal"
        self.quiz_item = None
