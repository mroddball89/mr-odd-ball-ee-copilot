#!/usr/bin/env python3
"""
Module:  launch_intent.py
Purpose: Recognise "open Firefox" without spending a Gemini call on it.
Author:  LB
Date:    2026-08-21

    python -m orchestrator.launch_intent "open firefox" "how do I open a file in python"

## Why this exists

Asking him to open Firefox cost **three** Gemini calls: one for the router to decide it was an
OS request, one for the OS agent to write `firefox`, and one more to paraphrase that command
into a speakable question. Against D3's measured free tier — 20 requests per model per day —
that is six launches before he stops working for the rest of the day.

None of the three is doing anything a model is needed for. "Open Firefox" has one right answer,
the machine already publishes the list of applications (`tools/app_catalogue.py`), and the
question to ask out loud is "Want me to open Firefox?" whatever model you have.

So this is a **pure function of a string**, injected into `orchestrator.instant.Router` as a
planner, which is exactly the seam that class was built with and never had wired up. Nothing
here imports `agents/`, `engine/` or any model. Cost: one glob of two directories, cached.

## The end-anchor rule is the whole safety argument

A launch needs a **verb** AND a **target**, and removing both must leave nothing but filler.
That is inherited from `~/oddball/hardware/apps.py` and it is what separates a request from a
question about the same words:

    "open firefox"                      -> verb + target + ""            LAUNCH
    "how do I open a file in Python"    -> "how do i" + "in python" left  no
    "why did my browser crash"          -> no launch verb at all          no
    "is firefox installed"              -> no launch verb                 no

D38 has returned five times on bare-keyword matching in this repo. This is the first place
where the consequence of getting it wrong is *starting a program* rather than misanswering, so
the trigger has to BE the utterance rather than merely appear inside it.

**Edit-distance matching is refused**, here and in `app_catalogue.resolve()`, for the reason
that file states: a threshold loose enough to catch `tawny -> thonny` is loose enough to catch
`shut up -> shut down`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

LOG = logging.getLogger("oddball.launch")

__all__ = ["LaunchRequest", "look_up", "LAUNCH_VERBS", "FILLER", "DESCRIPTOR"]

# Every one is a bare imperative. The multi-word ones are listed in full rather than relying on
# "up" being filler, because "bring" and "fire" alone are not launch verbs.
LAUNCH_VERBS: tuple[str, ...] = (
    "bring up", "fire up", "pull up", "boot up", "start up", "open up",
    "open", "launch", "start", "run", "load",
)

# What may be left over and still count as nothing. Deliberately short: every word added here
# widens what counts as a command, and the cost of a wrong widening is a program starting.
FILLER: frozenset[str] = frozenset({
    "please", "can", "could", "would", "you", "u", "me", "my", "the", "a", "an",
    "for", "now", "up", "and", "then", "just", "hey", "ok", "okay", "lets", "let",
    "i", "want", "to", "need", "would", "like", "will",
})


@dataclass(frozen=True)
class LaunchRequest:
    """A recognised request to open something. **Carries no authority to run it.**

    Args:
        app:    the phrase that matched, passed on to `app_catalogue.resolve()`. Not an argv —
                resolution to a program happens in `tools/app_launcher.py`, at launch time.
        verb:   which launch verb matched, for the log.
        spoken: the question to ask LB out loud. Built here, with no model, because
                "Want me to open Firefox?" needs none.
    """

    app: str
    verb: str
    spoken: str


def _strip(text: str, phrase: str) -> str:
    """Remove `phrase` from `text` as whole words, or return `text` unchanged."""
    words, target = text.split(), phrase.split()
    for i in range(len(words) - len(target) + 1):
        if words[i:i + len(target)] == target:
            return " ".join(words[:i] + words[i + len(target):])
    return text


# Words that may TRAIL a matched app and still mean the same app — the appositive shape:
# "open firefox, the internet browser". Measured 2026-08-22: that exact utterance failed the
# end anchor, fell through to the paid router, reached the OS agent's shell path and came back
# with an invented excuse ("this Raspberry Pi is running headless"). Clarifying made it worse.
#
# Deliberately NOUNS ONLY, and that is the safety property. A leftover built entirely from
# these cannot express a second action, so "open firefox and delete my files" still refuses on
# "delete". Adding a verb here would break that argument, so do not.
DESCRIPTOR: frozenset[str] = frozenset({
    "internet", "web", "browser", "editor", "terminal", "calculator", "manager",
    "app", "application", "program", "window", "file", "files", "text", "document",
})


def _strip_joined(text: str, phrase: str) -> str:
    """Remove `phrase` from `text` when the speaker said it as SEPARATE WORDS.

    `tiny.en` writes "firefox" as **"fire fox"**, and measured 2026-08-22 that alone broke the
    free launch path: "Open fire fox." missed the catalogue, fell through to the paid router,
    and reached the OS agent. Three of LB's four Firefox attempts failed exactly here.

    Only EXACT concatenation counts. This is not edit-distance matching, which this module and
    `app_catalogue.resolve()` both refuse on purpose: a threshold loose enough to catch
    "tawny" -> "thonny" is loose enough to catch "shut up" -> "shut down". Concatenation cannot
    do that — "shutup" is not "shutdown" — so the D38 hazard does not apply.

    Bounded to runs of 2 and 3 words: an app name spoken as four fragments is a transcript too
    broken to act on.
    """
    if " " in phrase:                 # multi-word targets are handled by `_strip` already
        return text
    words = text.split()
    for run in (2, 3):
        for i in range(len(words) - run + 1):
            if "".join(words[i:i + run]) == phrase:
                return " ".join(words[:i] + words[i + run:])
    return text


def only_filler(text: str) -> bool:
    """Is there nothing meaningful left? The end anchor.

    Filler OR a trailing descriptor. The anchor still holds — a launch is still verb + target
    + nothing that means anything else — it is just no longer confused by LB naming the thing
    twice, which is a normal way to speak and was costing a free launch and an API call.
    """
    return all(w in FILLER or w in DESCRIPTOR for w in text.split())


def _targets(catalogue, roles) -> tuple[str, ...]:
    """Every phrase that could name an app, longest first.

    Longest-first is what makes "file manager" reachable when "files" also exists — the same
    reason `app_catalogue._spelling_order` sorts that way.
    """
    from tools.app_catalogue import ALIASES, _norm

    phrases = {_norm(a.name) for a in catalogue}
    phrases |= {_norm(a.entry_id) for a in catalogue}
    phrases |= set(roles)
    # Spoken nicknames — "vscode" for "Visual Studio Code". `resolve()` expands them, but it
    # never sees the utterance: this function decides what counts as naming an app at all, so
    # a nickname missing from here is a nickname the free path cannot recognise.
    phrases |= set(ALIASES)

    # CONTIGUOUS sub-phrases of a multi-word Name, so "KiCad Schematic Editor (Standalone)" is
    # reachable as "schematic editor". Measured 2026-08-23: that utterance and "pcb editor"
    # both fell through to the paid router, even though `app_catalogue.resolve()` answers them
    # on its tier 4 (a whole-word phrase in the Name). The catalogue knew; this function, which
    # decides what counts as naming an app at all, never asked.
    #
    # **Trailing runs are not enough, and the Pi is why.** The first fix here took only the
    # tail of a name, which works for "KiCad Schematic Editor" and fails on the name this Pi
    # actually ships: the distinguishing words sit in the MIDDLE, and a trailing run yields
    # "schematic editor standalone" — a phrase nobody says. Every contiguous run is the general
    # form, and the qualifier in brackets stops mattering.
    #
    # **Two words minimum.** A single word is "editor", "manager", "files", "calculator" —
    # exactly what `ROLES` owns and deliberately maps to a CATEGORY rather than to whichever
    # app happens to contain it. Adding them here would let "open the editor" pick KiCad's
    # schematic editor over a text editor, which is the ambiguity ROLES exists to resolve.
    #
    # Ambiguity is still never guessed — `resolve()` returns every hit and `propose_launch`
    # asks. This widens what can be NAMED, not what can be assumed.
    for app in catalogue:
        words = _norm(app.name).split()
        for size in range(2, len(words)):
            for i in range(len(words) - size + 1):
                phrases.add(" ".join(words[i:i + size]))

    return tuple(sorted((p for p in phrases if p), key=len, reverse=True))


def look_up(query) -> LaunchRequest | None:
    """Is this utterance a request to launch something? Never raises, never launches.

    Args:
        query: an `orchestrator.instant.Query`. Matched on `.text` — normalised, which is what
               makes it survive `tiny.en`'s punctuation and capitals.

    Returns:
        A `LaunchRequest`, or None. Requires a launch verb AND a known app AND nothing left
        over but filler — all three.
    """
    text = getattr(query, "text", "") or ""
    if not text:
        return None

    try:
        from tools.app_catalogue import ROLES, cached_catalogue
        catalogue = cached_catalogue()
    except Exception:                                                  # noqa: BLE001
        # A broken or unreadable catalogue costs the free path, not the turn: fall through and
        # let the Gemini router handle it exactly as it did before this module existed.
        LOG.warning("catalogue unavailable; launch intent disabled for this turn")
        return None

    for phrase in _targets(catalogue, ROLES):
        rest = _strip(text, phrase)
        if rest == text:
            # Not named as written. Try it as separate words — "fire fox" for "firefox".
            rest = _strip_joined(text, phrase)
        if rest == text:                       # the app is not named in there at all
            continue
        for verb in LAUNCH_VERBS:
            without = _strip(rest, verb)
            if without == rest:                # this verb is not the one
                continue
            if only_filler(without):
                spoken = _spoken_for(phrase, catalogue)
                LOG.info("launch intent: %r + %r -> %s", verb, phrase, spoken)
                return LaunchRequest(app=phrase, verb=verb, spoken=spoken)
    return None


def _spoken_for(phrase: str, catalogue) -> str:
    """The permission question, said the way LB would say it.

    Built from the catalogue's own `Name` when the phrase resolves to exactly one app, so he
    says "Want me to open Firefox?" rather than echoing back "want me to open the browser".
    When it is ambiguous the phrase is used as-is — he is about to be told it is ambiguous
    anyway, and naming one of the candidates in the question would be misleading.
    """
    from tools.app_catalogue import ROLES, resolve

    match = resolve(phrase, catalogue)
    if match.ok:
        return f"Want me to open {match.app.name}?"
    # Ambiguous or unknown. A role word needs its article back or he says "Want me to open
    # browser?", which is the one place this feature would sound like a machine.
    article = "the " if phrase in ROLES else ""
    return f"Want me to open {article}{phrase}?"


def main(argv: list[str] | None = None) -> int:
    import sys

    from orchestrator.instant import Query, normalise

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        args = ["open firefox", "launch thonny", "bring up the file manager",
                "how do I open a file in python", "why did my browser crash",
                "is firefox installed", "open the browser"]
    for utterance in args:
        req = look_up(Query(raw=utterance, text=normalise(utterance)))
        verdict = f"LAUNCH {req.app!r} -> {req.spoken}" if req else "(not a launch)"
        print(f"  {utterance!r:44} {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
