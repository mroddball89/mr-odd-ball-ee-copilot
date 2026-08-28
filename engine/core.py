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
from pathlib import Path

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
        from engine import quota
        from engine.models import FREE_TIER_DAILY_LIMIT

        if quota.is_daily_exhaustion(text):
            # LB's words, first, because this is the sentence he asked to hear and the one
            # that stops him debugging a fault that is not there.
            return ("API quota exceeded for today. That's my "
                    f"{FREE_TIER_DAILY_LIMIT} free questions gone until it resets. "
                    "The utility stuff still works — ask me the time.")
        # A per-MINUTE 429 is a different animal: it clears in seconds, and telling him to
        # come back tomorrow over a burst would be wrong.
        return "I'm being rate limited for a moment. Ask me again in a few seconds."
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
        self.note_draft: NoteDraft | None = None
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
                return self._resolve_note(text, t)

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
                speech=_failure_line(exc),
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

            if destination is None:
                # The router's model may already be known out of quota. Asking again costs a
                # round trip to be told the same thing, and before LLM_MAX_RETRIES went to 0 it
                # cost up to 217 seconds of it. The free paths above still ran, so the time,
                # the date, a conversion and a launch all keep working — the point of the latch.
                from engine import quota
                from engine.models import ROUTER_MODEL

                if quota.exhausted(ROUTER_MODEL):
                    t.extras.append("router quota latched — not calling")
                    LOG.info("skipping the router: %s is out of quota until %s",
                             ROUTER_MODEL, quota.status().get(ROUTER_MODEL, "?"))
                    raise RuntimeError(
                        "RESOURCE_EXHAUSTED: quotaId GenerateRequestsPerDayPerProjectPerModel-"
                        f"FreeTier for {ROUTER_MODEL} (known locally, no request was sent)")

                t0 = time.monotonic()
                decision = router_agent(text)
                t.route_s = time.monotonic() - t0
                t.route = decision.destination.value
                LOG.info("route %r -> %s (%s)", text, t.route, decision.reasoning)
                destination = decision.destination

            t0 = time.monotonic()
            response = self._dispatch(destination, text, t)
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
    SOCIAL_INTENTS = frozenset({"hello", "thanks", "identity"})

    FREE_INTENTS = frozenset({
        "time", "date", "convert", "constant", "define", "calc"}) | SOCIAL_INTENTS

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
        from orchestrator import launch_intent, note_intent
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
            reply = InstantRouter(planners={"note": note_intent.look_up,
                                            "launch": launch_intent.look_up}).route(text)
        except Exception:                                              # noqa: BLE001
            LOG.exception("free tier failed; falling back to the router")
            return None

        request = reply.action
        if isinstance(request, note_intent.NoteRequest):
            return self._note_turn(request, t)

        if isinstance(request, launch_intent.LaunchRequest):
            from agents.os_agent import propose_launch
            t.route = AgentRoute.OS.value
            t.extras.append(f"free launch ({request.app})")
            # Still gated. The free path decides WHAT was asked for, never whether to do it.
            return self._gate(propose_launch(request.app, request.spoken),
                              AgentRoute.OS.value, t)

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

    def _resolve_note(self, text: str, t: Turnlog) -> Response:
        """Read the answer to an open note question.

        **Read and cleared unconditionally, at the top.** The draft cannot survive its own turn
        under any branch below, which is the property `ask()`'s own comment says the permission
        gate got wrong the first time: a held question that stays held eats the next thing LB
        says about anything at all.
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
            draft.content = answer                 # verbatim. Never normalised, never trimmed.
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
        """Commit a finished draft — a new note, or an addition to one already found."""
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
            return self._say(f"What should I add to {request.target}?",
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
        spoken = f"Delete your {request.target} note? It's got {entries} " \
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
