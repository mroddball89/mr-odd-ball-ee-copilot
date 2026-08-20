#!/usr/bin/env python3
"""
Module:  instant.py
Purpose: Answer what can be answered with no model at all.
Author:  LB
Date:    2026-08-12 (renamed from orchestrator/router.py 2026-08-19)

**Renamed, not repurposed.** This was `orchestrator/router.py` and Tier 0 of the standalone
assistant. In the merged copilot the *tier system* is gone — `router.py` at the repo root is
the one thing that decides who answers, and it does it with a Gemini structured-output call.
What survives here is the other half of the old file's job: the instant-answer tables. It is
now reached as one destination among nine, `AgentRoute.UTILITY`, rather than as a tier that
sits in front of everything.

The rename matters because two modules called `router.py` doing different jobs is a trap, and
because "Tier 0" no longer names anything in this repo.

D2's claim is that most of what you ask a desk assistant every day needs no intelligence:
the time, the date, an acknowledgement. Those answers should be **instant and free**, and the
language model should only ever see what is left. That claim is unchanged by the merge — it is
now the argument for why `UTILITY` exists at all, and why it is the one route that costs
nothing and returns in microseconds.

Everything here is a **pure function of a string**, so the whole tier is tested with no audio,
no model, and no clock — `now` is injected for exactly that reason.

## Matching is deliberately loose

The transcript arrives from `tiny.en`, which is fast and imperfect. Measured on the Pi, it
turned "What is the date?" into "What is today?" and "Set a timer..." into "at a timer...".
**Matching on keywords rather than whole sentences is what makes those survivable** — and it is
why a stricter matcher would be a downgrade dressed as rigour. Exact phrase matching would have
failed both.

The cost is honest: loose matching answers confidently when it is wrong. The mitigation is
ordering — the most specific intents are checked first — and a fallback that admits ignorance
rather than guessing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from orchestrator import calc, constants, convert, define, formulas

LOG = logging.getLogger("oddball.router")


@dataclass(frozen=True)
class Reply:
    """What he should say, and what it came from."""

    text: str
    intent: str
    handled: bool = True        # False for the fallback, so Phase 2 knows what to pick up
    # A side effect to perform — a `hardware.actions.Plan`, or None for the 13 intents that
    # only speak. **Nothing in this module ever performs it**; `Turn._act` does, after the
    # confirm policy. Typed loosely because `hardware` imports `orchestrator.calc`, so naming
    # `Plan` here would be a cycle — and because a router that cannot see the type it is
    # carrying is a router that cannot accidentally act on it.
    action: object | None = None


@dataclass(frozen=True)
class Query:
    """One question, in both the forms an intent might need.

    Every intent here matches on `text` — normalised, which is exactly right for keyword and
    phrase matching and is why the tier survives `tiny.en`'s mistakes.

    `raw` exists for **one** intent, and it is not a convenience. `normalise()` strips
    everything outside `[a-z0-9 ]`, which deletes the decimal point: "3.5 plus 2" arrives as
    "35 plus 2". A calculator reading `text` would answer 37 — instantly, confidently and
    wrongly, which is the D30/D31 failure the whole tier structure exists to prevent. So
    `calc` reads `raw` and does its own preparation. See `orchestrator/calc.py`.
    """

    raw: str        # exactly what was heard or typed
    text: str       # normalise(raw)


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Whisper punctuates and capitalises; none of that should change what he does. Apostrophes
    are dropped rather than kept so "what's" and "whats" are the same word.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", text.lower())).strip()


def _has(text: str, *words: str) -> bool:
    """True if every word (or phrase) appears. Whole-word matching, so 'time' misses 'timer'."""
    return all(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text) for w in words)


def _any(text: str, *words: str) -> bool:
    return any(_has(text, w) for w in words)


# --- the handlers. Each takes the Query and `now`, and returns a line. ---

def _say_time(_q: Query, now: datetime) -> str:
    hour = now.hour % 12 or 12
    minute = now.minute
    if minute == 0:
        return f"It's {hour} o'clock."
    if minute < 10:
        return f"It's {hour} oh {minute}."
    return f"It's {hour} {minute}."


def _say_date(q: Query, now: datetime) -> str:
    """Answer the part that was actually asked for.

    Asking "what month is it" and being told "It's Thursday, August 13" sounds like he
    misheard the question — LB reported exactly that. Answering the narrower question when a
    narrower one was asked costs nothing and is what a person would do.

    %-d is not portable to Windows, and this has to run on both boxes.
    """
    text = q.text
    if _has(text, "month") and not _any(text, "date", "day"):
        return f"It's {now.strftime('%B')}."
    if _has(text, "year") and not _any(text, "date", "day", "month"):
        return f"It's {now.year}."
    if _has(text, "day") and not _any(text, "date", "month", "year"):
        return f"It's {now.strftime('%A')}."
    return f"It's {now.strftime('%A')}, {now.strftime('%B')} {now.day}."


def _say_hello(_q: Query, _now: datetime) -> str:
    return "Hey LB."


def _say_identity(_q: Query, _now: datetime) -> str:
    return "I'm Mr Odd Ball. I live on the Pi and I do what I can."


def _say_welcome(_q: Query, _now: datetime) -> str:
    return "Any time."


def _say_ok(_q: Query, _now: datetime) -> str:
    return "Okay."


def _say_goodnight(_q: Query, _now: datetime) -> str:
    """Dismissal. The caller watches for `intent == "sleep"` and closes the conversation.

    It names the way back on purpose. Conversation mode means he now stays listening between
    exchanges, so "he is asleep" and "he is waiting" stop looking the same from across the
    room — and the one thing LB needs to know at that moment is what to say next.
    """
    return "Okay. Say my name when you need me."


def _say_formula(q: Query, _now: datetime) -> str:
    """The looked-up answer. `look_up` runs twice per turn — matcher then handler.

    Deliberate: it keeps INTENTS a plain (name, matches, handle) triple like every other row,
    and the table is ~20 entries of whole-word phrase matching, so the second pass is free.
    Threading a match object through would complicate the tier for no measurable gain.
    """
    hit = formulas.look_up(q.text)
    return hit.spoken if hit else FALLBACK


def _say_calc(q: Query, _now: datetime) -> str:
    """The computed answer, from the RAW transcript. See `orchestrator/calc.py`.

    Like `_say_formula` this runs the lookup a second time, but `calc.evaluate` is `lru_cache`d
    on its only input, so the matcher's parse is the one that ran and this call is free.
    """
    hit = calc.evaluate(q.raw)
    return hit.spoken if hit else FALLBACK


def _say_constant(q: Query, _now: datetime) -> str:
    """A physical or mathematical constant. See `orchestrator/constants.py`."""
    hit = constants.look_up(q.text)
    return hit.spoken if hit else FALLBACK


def _say_definition(q: Query, _now: datetime) -> str:
    """What a word means. See `orchestrator/define.py`."""
    hit = define.look_up(q.text)
    return hit.spoken if hit else FALLBACK


def _say_conversion(q: Query, _now: datetime) -> str:
    """A converted quantity, from the RAW transcript. See `orchestrator/convert.py`.

    Reads `q.raw` for the identical reason `_say_calc` does — `normalise()` deletes the
    decimal point, so "3.3 volts in millivolts" would convert 33 volts. `convert` is
    `lru_cache`d on its only input, so the matcher's parse is the one that ran.
    """
    hit = convert.convert(q.raw)
    return hit.spoken if hit else FALLBACK


# Ways of asking for the clock. "what time is it" and friends — never a bare "time", which
# appears in "what time do the Warriors play", "time constant" and "how long does it take".
_TIME_PHRASES = (
    "what time is it", "whats the time", "what is the time", "time is it",
    "what time it is", "know the time", "know what time",     # "do you know what time it is"
    "got the time", "have the time", "tell me the time", "the time now",
    "what time now", "clock say", "on the clock",
)

# Ways of asking for the calendar, as whole phrases. `normalise()` has already dropped the
# apostrophes, so "what's" arrives as "whats" and both spellings are listed.
_DATE_PHRASES = (
    "what day", "what date", "what month", "what year",
    "whats the day", "whats the date", "whats the month", "whats the year",
    "what is the day", "what is the date", "what is the month", "what is the year",
    "whats todays date", "whats today", "what is today", "todays date",
    "which day", "which month", "tell me the date", "tell me the day",
)

# Ways of telling him to go away — the counterpart to the wake word, and the thing that closes
# a conversation. `normalise()` has already dropped apostrophes, so "that's all" arrives as
# "thats all" and "I'm done" as "im done"; both spellings are listed where it matters.
#
# Chosen from how LB actually talks rather than invented: `captures/173508_that-s-all-.wav` is
# him saying "that's all" to a build that had no idea what to do with it.
#
# Deliberately NOT here: a bare "sleep" ("how much sleep did I get"), a bare "night"
# ("what are you doing tonight"), and "enough" ("that's enough sugar"). Each is a whole word
# that means something else in an ordinary sentence — D38, for the fifth time.
_SLEEP_PHRASES = (
    "go to sleep", "go back to sleep", "back to sleep", "get some sleep", "sleep now",
    "goodnight", "good night", "night night", "nighty night",
    "go to bed", "take a nap", "have a nap",
    "thats all", "that is all", "thats it", "that is it", "that will be all",
    "im done", "i am done", "were done", "we are done", "all done",
    "nothing else", "that is everything", "thats everything",
    "goodbye", "good bye", "bye", "see you later", "talk to you later", "catch you later",
    "dismissed", "you can go", "you can rest", "leave me alone", "go away",
)

# Words that may sit around a dismissal without changing what it is. Deliberately small —
# every addition here widens what counts as "go away", and the cost of a wrong dismissal is a
# conversation ending mid-sentence.
_DISMISS_FILLER = frozenset({
    "ok", "okay", "alright", "alrighty", "well", "so", "now", "then", "please",
    "thanks", "thank", "you", "hey", "mr", "odd", "ball", "oddball", "buddy", "man",
    "um", "uh", "just", "and", "for", "cool", "great", "fine",
})


# The typed equivalent of the wake word. Spoken, waking him is openWakeWord's job and this is
# never consulted; typed, there is no audio to score, so the phrase has to be matched as text.
#
# LB asked for this because the microphone is the weak link — measured 2026-08-19 on the Pi,
# his wake utterances peaked 0.17-0.28 against a 0.76 threshold and mostly did not fire. Typing
# is the channel that always works, so it has to be able to do everything the voice can,
# including the two things that are not questions: waking him and dismissing him.
_WAKE_PHRASES = (
    "hey mr odd ball", "hey mister odd ball", "hey mr oddball", "hey oddball",
    "mr odd ball", "mister odd ball", "mr oddball", "oddball", "odd ball",
    "wake up", "wakeup", "hey you", "you awake", "are you awake", "you there",
    "are you there", "hello there",
)

# Same idea as _DISMISS_FILLER and a DIFFERENT set, deliberately. "mr", "odd" and "ball" are
# filler around a dismissal ("Mr Odd Ball, that's all") and are the whole point of a wake
# phrase, so sharing one set would make "hey mr odd ball" reduce to nothing and match every
# wake phrase at once.
_WAKE_FILLER = frozenset({
    "ok", "okay", "alright", "hey", "hi", "hello", "yo", "um", "uh", "please",
    "so", "now", "then", "just", "buddy", "man", "there",
})


def is_wake(text: str) -> bool:
    """Is this typed line asking him to wake up, rather than a sentence that mentions him?

    Args:
        text: the raw typed line. Normalised here, so callers pass what was typed.

    The end-anchor rule from `_is_dismissal`, for the opposite job: the wake phrase has to BE
    the line. "hey mr odd ball" wakes him; "what does mr odd ball run on" is a question about
    him and must be answered, not treated as a doorbell.
    """
    flat = normalise(text)
    if not flat:
        return False
    for phrase in _WAKE_PHRASES:
        if not _has(flat, phrase):
            continue
        rest = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", flat)
        if not [w for w in rest.split() if w not in _WAKE_FILLER]:
            return True
    return False


def is_sleep(text: str) -> bool:
    """Is this typed line dismissing him? The typed door out, matching the spoken one exactly.

    A thin wrapper on `_is_dismissal` so callers do not reach for a private name, and so the
    typed and spoken paths cannot drift onto different phrase lists.
    """
    return _is_dismissal(normalise(text))


def _is_dismissal(text: str) -> bool:
    """Is this utterance a dismissal, rather than a sentence that mentions one?

    **The dismissal has to BE the utterance.** Matching the phrase anywhere is not enough, and
    `tools/verify_turn.py` caught the difference on a fixture written to probe exactly this:
    *"I bought it at the goodbye sale"* contains "goodbye" and ended the conversation.

    This is the same shape as `define._is_definition`'s end-anchor rule, arrived at from the
    other direction: a person asking what a word means finishes on the word, and a person
    dismissing you says only the dismissal. So a phrase counts when removing it leaves nothing
    behind but filler — which allows "okay, goodnight" and "Mr Odd Ball, that's all for now"
    while refusing any sentence that is *about* something else.
    """
    for phrase in _SLEEP_PHRASES:
        if not _has(text, phrase):
            continue
        rest = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", text)
        if not [w for w in rest.split() if w not in _DISMISS_FILLER]:
            return True
    return False

# Ordered, and the order is load-bearing: the first match wins, so anything specific must sit
# above anything general. "what time is it" would otherwise be caught by a bare "what".
INTENTS: list[tuple[str, Callable[[Query], bool], Callable[[Query, datetime], str]]] = [
    # FIRST, and this is not a preference — it is a collision.
    #
    # "What's the time constant of an RC circuit?" contains the whole word "time", so the
    # `time` intent below matches it and he cheerfully announces the clock. Anywhere lower in
    # this list and the single most-asked formula in the table is unreachable.
    #
    # Safe at the top because formula triggers are multi-word phrases ("time constant",
    # "voltage divider", "ohms law") rather than bare keywords, so they do not swallow
    # ordinary questions. tools/verify_formulas.py asserts both halves of that claim.
    ("formula",  lambda q: formulas.look_up(q.text) is not None, _say_formula),
    # SECOND — constants. Under `formula` because the formula triggers are the narrower
    # phrases, and because `formulas.gravity` already owns the bare word "gravity"; anything
    # here mentioning it would be permanently unreachable. `constants.py` says so at the point
    # where the entry would have gone.
    #
    # Safe this high for the same reason `formula` is: every trigger is a multi-word phrase or
    # a word that appears nowhere else ("avogadro", "boltzmann"), never a bare keyword. D38.
    ("constant", lambda q: constants.look_up(q.text) is not None, _say_constant),
    # THIRD — the glossary. 169 entries, ~316 spellings, and it is safe above `time` and
    # `identity` for ONE reason: `define.look_up` returns None unless the question carries a
    # definition FRAME ("what is a", "define", "what does X mean").
    #
    # Without that gate this would be the worst intent in the table. It holds single words
    # like "power", "period", "work" and "field", and matching them bare would swallow "what
    # time is it", "who are you", and — worst of all — "what do you think of capacitors",
    # which `classify.py` deliberately routes to personality BEFORE subject matter. A bare
    # glossary would eat that question three intents before classify ever sees it.
    #
    # tools/verify_define.py asserts both directions: every entry reachable with a frame,
    # and every existing router fixture untouched without one.
    ("define",   lambda q: define.look_up(q.text) is not None, _say_definition),
    # FOURTH — unit conversion. Above `calc` because they cannot collide (calc refuses
    # anything with letters left in it, and every conversion names two units), and because a
    # conversion is the more specific claim of the two.
    #
    # Note it reads `q.raw`, not `q.text`, exactly as `calc` does and for the same reason:
    # normalising first turns "3.3 volts" into "33 volts" and hands LB a confident answer
    # that is out by a factor of ten.
    ("convert",  lambda q: convert.convert(q.raw) is not None, _say_conversion),
    # FIFTH, under the formula table and above everything else. He used to send sums to
    # Google: "with five plus five" classified as `ask (no rule matched)`, so he spoke "I'd
    # have to look that one up online. Want me to?", captured a second utterance, transcribed
    # it, and made a network round trip — to add 5 and 5. Measured live 2026-08-13.
    #
    # Safe this high because `calc.evaluate` REQUIRES an operator and refuses everything else.
    # "set a timer for five minutes" holds a number and no operator, so it returns None and
    # `timer` below still wins. tools/verify_calc.py asserts that in both directions, the way
    # verify_formulas.py pins the time / time-constant collision.
    #
    # Note it reads `q.raw`, not `q.text` — see Query. Normalising first would turn "3.5" into
    # "35" and hand LB a confident wrong answer.
    ("calc",     lambda q: calc.evaluate(q.raw) is not None, _say_calc),
    # Above `time`, because "set a timer for five minutes" contains neither "time" as a whole
    # word nor anything else useful — but saying "I can't do timers yet" is far better than
    # cheerfully announcing the time at someone who asked for a timer.
    ("timer",    lambda q: _any(q.text, "timer", "alarm", "remind me"),
     lambda q, n: "I can't set timers yet. That's on the list."),
    # Phrases, for the third time in this table, and for the third identical reason.
    # "time constant" was the first (it announced the clock at a formula question), a bare
    # "today" was the second (the date intent hijacked an Orioles fixture). This is the third:
    # LB asked "what time do the Warriors play today" on 2026-08-13 and was told "It's 3 40."
    #
    # A bare "time" is a substring of half the questions worth asking. What makes it a request
    # for the clock is the clock being the SUBJECT — "what time is it", "the time now" — not
    # the word appearing somewhere in a sentence about a basketball game.
    ("time",     lambda q: _any(q.text, *_TIME_PHRASES), _say_time),
    # PHRASES, not bare keywords, and this is the same lesson as the formula/time collision
    # above. The old matcher fired on "today" or "day" appearing ANYWHERE, so LB asking
    # "do you know if the Orioles have a baseball game today" was told the date — measured
    # live 2026-08-13. A bare "today" is almost never a request for the date; "what's the
    # date" is. Keeping the subject next to the interrogative is what tells them apart, and
    # it also stops "what's the weather today" being answered with the calendar.
    ("date",     lambda q: _any(q.text, *_DATE_PHRASES), _say_date),
    # DISMISSAL — the other half of the wake word, added 2026-08-14 with conversation mode.
    #
    # Until now a turn always ended by going straight back to sleep, so there was nothing to
    # dismiss. Now he stays listening between exchanges, which means he needs a way to be told
    # to stop — and LB asked for one by name.
    #
    # ABOVE `stop`, and that is a collision, not a preference. "that's all" and "never mind"
    # are both ways of calling something off, but only one of them means "and go away": `stop`
    # answers "Okay." and leaves him listening, which is exactly wrong for a dismissal. The
    # specific reading has to be tried first. tools/verify_turn.py asserts both directions.
    #
    # ALSO above `hello`, which matches "good morning" and "good evening" — "good night" is
    # one word away from those and means the opposite.
    #
    # Phrases, never bare keywords (D38). A bare "sleep" appears in "how much sleep did I get"
    # and "sleep mode", and a bare "bye" is a whole-word match that "goodbye" does not trip.
    ("sleep",    lambda q: _is_dismissal(q.text), _say_goodnight),
    ("stop",     lambda q: _any(q.text, "stop", "cancel", "never mind", "nevermind",
                               "forget it"), _say_ok),
    ("identity", lambda q: _has(q.text, "who", "you") or _has(q.text, "your", "name")
     or _has(q.text, "what", "are", "you"), _say_identity),
    ("thanks",   lambda q: _any(q.text, "thanks", "thank you", "cheers"), _say_welcome),
    ("hello",    lambda q: _any(q.text, "hello", "hi", "hey", "good morning", "good evening"),
     _say_hello),
]

# What he says when Tier 0 has nothing. Phase 2 replaces this branch with the local model
# rather than replacing the tier — that is the whole point of D2.
FALLBACK = "I don't know how to do that yet."
NOTHING_HEARD = "I didn't catch that."


class Router:
    """Maps a transcript to a spoken reply, and — from Phase 6 — sometimes to an action.

    Args:
        now: injectable clock returning a datetime. Tests pin it; nothing else should.
        planners: ordered `{intent name: callable(Query) -> Plan | None}`, checked BEFORE
            `INTENTS`. Empty by default, which is Phase 1 through 3 behaviour exactly: with
            nothing injected this class is byte-for-byte what it was, which is what keeps
            every existing harness green and what makes the capability switchable off at the
            one place that composes it (`run_wake.py`).

            **Injected rather than imported.** `hardware/` imports `orchestrator.calc`, so a
            module-level import the other way would be a cycle — but the better reason is the
            one `Turn` already demonstrates with `brains=None`: a dependency you hand in is a
            dependency a harness can withhold, and "route 300 utterances and prove nothing
            ran" needs exactly that.
    """

    def __init__(self, now: Callable[[], datetime] = datetime.now,
                 planners: "dict[str, Callable[[Query], object | None]] | None" = None) -> None:
        self._now = now
        self._planners = dict(planners or {})

    def route(self, transcript: str) -> Reply:
        """Answer, or admit it cannot. Never raises.

        **This method never performs an action.** It returns one in `Reply.action` and the
        caller decides. `tools/verify_actions.py` routes the whole corpus with a live runner
        attached and asserts it was never called — the single check that matters most in that
        file, because it is the property that keeps this module a pure function of a string.
        """
        text = normalise(transcript)
        if not text:
            return Reply(text=NOTHING_HEARD, intent="empty", handled=False)

        query = Query(raw=transcript, text=text)
        when = self._now()

        # ABOVE `formula`, and the ordering is not what makes it safe. The costs here are
        # asymmetric in a way no other pair in INTENTS is: a lookup beating an action means LB
        # asks for the volume and hears the formula for a sphere, which is one retry; an action
        # beating a lookup means a QUESTION EXECUTES SOMETHING. So the trigger shape carries
        # the safety instead — every planner below demands the trigger BE the utterance
        # (hardware/actions.py, D38 for the sixth time) — and once that holds, first is the
        # right slot because reachability is then guaranteed by construction rather than by
        # having re-checked every table underneath.
        for name, plan_of in self._planners.items():
            plan = plan_of(query)
            if plan is not None:
                LOG.info("intent %s -> action %s %s", name, plan.name, plan.argv)
                return Reply(text=plan.echo, intent=name, action=plan)

        for name, matches, handle in INTENTS:
            if matches(query):
                reply = handle(query, when)
                LOG.info("intent %s -> %r", name, reply)
                return Reply(text=reply, intent=name)

        LOG.info("no intent matched %r", text)
        return Reply(text=FALLBACK, intent="unknown", handled=False)


if __name__ == "__main__":
    import sys

    router = Router()
    for arg in sys.argv[1:] or ["what time is it", "what day is it today", "who are you", "xyzzy"]:
        r = router.route(arg)
        print(f"  {arg!r:<40} -> [{r.intent}] {r.text!r}")
