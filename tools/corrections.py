#!/usr/bin/env python3
"""
Module:  corrections.py
Purpose: What LB told him he got wrong, kept forever and read back before every answer.
Author:  LB
Date:    2026-08-25

    python tools/corrections.py --list
    python tools/corrections.py --detect "no, always use absolute paths instead"
    python tools/corrections.py --prompt

## The two halves

**Detection** decides whether a line is LB correcting him rather than asking him something.
**The ledger** (`vault/corrections.md`) keeps what was said, verbatim, forever.

They are in one file because they share the vocabulary — the phrases that mark a correction are
the same phrases the entry is sliced out of — and splitting them would put that vocabulary in
two places that had to agree.

## The ledger is verbatim, and no model touches it

The obvious design is to hand the correction to Gemini and ask it to distil a rule. It was
refused, twice over:

1. **Quota.** D3 measured the free tier at 20 requests per model per day. Spending one to
   rewrite a sentence LB already wrote clearly is the worst trade in the repo — and it would be
   spent at exactly the moment he is annoyed, which is the moment a turn must not fail.
2. **Authority.** LB's words are the rule. A paraphrase of "always use absolute paths" is a
   paraphrase of an instruction, and a paraphrase that drifts is an instruction that quietly
   became something else. `agents/os_agent.py` already documents what approving-a-paraphrase
   costs; this is the same hazard with nobody watching for it.

So the rule stored is a **slice of the raw text**, not of the normalised text. `normalise()`
strips `/` and `-`, which would turn "always use absolute paths like /home/lb" into "always use
absolute paths like homelb". Detection runs on the normalised form because that is what matching
needs; extraction runs on the original because that is what LB actually said.

## Why this is not `tasks/lessons.md`

`tasks/lessons.md` is where **I** record what I learned from LB correcting me while building
this. This is where **he** records what he is correcting the running assistant about, at his
desk, out loud, without a repo checkout in front of him. Same idea, different author, different
lifetime — and this one has to be writable by a voice turn in under a second.

## What detection refuses to do

Every matcher in this repo carries the same warning and this one needs it most: **the danger is
never the rule that fails to match, it is the one that matches too much.** A correction detector
that fires on "why was that answer wrong" eats a question and answers it with "I've written that
down", which is both useless and smug.

So there are two shapes and both are anchored:

    a bare rebuke      the rebuke IS the utterance — `instant._is_bare`, the same end-anchor
                       rule as wake and dismissal. "that was wrong" corrects; "why was my
                       calculation wrong" is a question.
    a standing rule    the utterance OPENS with a directive marker — "always", "never",
                       "from now on", "stop doing". A question that merely contains "always"
                       does not, because a question does not begin with an order.

`tools/verify_corrections.py --probe` removes both anchors and shows the negatives going red.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Run directly as `python tools/corrections.py`, this file is not inside a package and the repo
# root is not on the path, so the `from orchestrator.instant import ...` below fails with
# ModuleNotFoundError. The guard fires ONLY in that case — imported normally as
# `tools.corrections`, `__package__` is "tools" and nothing here runs.
#
# Every documented CLI in this repo has to work when it is typed, and `--prompt` is the one to
# reach for when he starts behaving oddly: everything it prints is in front of every agent.
if __package__ in (None, ""):                                          # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.instant import _has, _is_bare, normalise

LOG = logging.getLogger("oddball.correct")

__all__ = ["LEDGER", "Correction", "detect", "record", "active_rules", "for_prompt", "clear"]

# Anchored to the repo, not the working directory — same reasoning as `knowledge_vault.VAULT_DIR`.
# A ledger that lands in a different folder under systemd than under `python main.py` is two
# ledgers, and the one with the answer in it is always the other one.
#
# `ODDBALL_VAULT_DIR` overrides it, and the override exists for one reason worth stating. These
# ledgers are written from `Engine.ask` and from `agents/os_agent.py`, so **any harness that
# drives a failure writes to them** — including harnesses written long before these files
# existed. That is not hypothetical: it is how two junk entries from a test's deliberate 400s
# reached LB's real ledger and were then injected into every agent prompt as things that had
# "gone wrong". See tasks/lessons.md L22.
#
# So a harness sets `ODDBALL_VAULT_DIR` to a temp directory before importing anything under
# `tools/`, and both ledgers follow it. One line, both files, no per-module monkeypatching.
VAULT_DIR = Path(os.environ.get("ODDBALL_VAULT_DIR")
                 or Path(__file__).resolve().parents[1] / "vault")
LEDGER = VAULT_DIR / "corrections.md"

# How many standing rules reach a prompt. Beyond this he is being handed a policy document on
# every turn; if LB has given forty rules the oldest are the ones he is least likely to still
# mean. Newest win — see `active_rules`.
MAX_RULES = 40

# The injected block's ceiling. Bigger than the reflection ledger's on purpose: a correction is
# an instruction and is followed, a reflection is a datum and is considered, so this is the one
# of the two that has earned the context it costs.
MAX_PROMPT_CHARS = 3_000

_HEADER = "## "
_HEADER_RE = re.compile(r"^## (?P<when>[0-9T:\- ]+)$")

_BANNER = """# Corrections — LB's standing rules

<!-- Written by tools/corrections.py the moment LB corrects him, in his own words, verbatim.
     Every rule here is injected into EVERY agent prompt by tools/self_context.py, so what is
     written here changes every answer he gives from the next turn onward.

     Safe to edit by hand — that is the intended way to withdraw a rule. Delete the entry. -->
"""

# --- detection: form A, a bare rebuke --------------------------------------------------------

# The rebuke has to BE the utterance. Ordered longest-first is not required — `_is_bare` tries
# every phrase — but they are grouped by shape so a missing variant is easy to spot.
_REBUKES: tuple[str, ...] = (
    "that was wrong", "that is wrong", "thats wrong", "that was incorrect",
    "thats incorrect", "that was not right", "thats not right", "that is not right",
    "you got that wrong", "you were wrong", "you are wrong", "youre wrong",
    "that was a mistake", "you made a mistake", "wrong answer", "that answer was wrong",
    "dont do that", "do not do that", "dont do that again", "stop doing that",
    "thats not what i asked", "that is not what i asked", "thats not what i wanted",
    "thats not what i meant", "you didnt do what i asked",
    "that was bad", "that was not helpful", "that was useless",
)

# Words allowed to survive around a rebuke and still count as nothing left over. Its OWN set,
# not shared with the wake or dismissal sets — `instant._is_bare` documents why sharing one is
# how "hey mr odd ball" reduces to nothing and matches everything.
_REBUKE_FILLER = frozenset({
    "no", "nope", "hey", "ok", "okay", "well", "actually", "mr", "mister", "odd", "ball",
    "oddball", "please", "man", "dude", "really", "just", "again", "completely", "totally",
    "very", "so", "and", "but", "um", "uh", "yeah", "look", "listen", "come", "on",
})

# --- detection: form B, a standing rule ------------------------------------------------------

# A directive marker only counts at the START of the utterance, after any leading rebuke and
# filler have been stripped. "Always use absolute paths" is an order; "does it always do that"
# is a question that happens to contain the word.
#
# `stop` is deliberately absent as a bare marker and present only as "stop <verb>ing" below:
# "stop the quiz" and "stop the music" are commands about the machine, not corrections, and a
# bare `stop` claims both.
#
# **The `remember` family is deliberately absent, and that is the most important line here.**
# `agents/persona_agent.py` documents "Remember that I'm using the 2N3904" as the archetypal
# thing LB says in passing that should reach `save_to_vault`. A correction detector that claims
# "remember to" and "remember that" does not add a feature — it silently takes the vault's
# traffic and files LB's notes as rebukes. D38, again: the danger is the rule that matches too
# much, and here it would match something that already works.
#
# "make sure to" went for the same reason and is a closer call: "make sure to use absolute
# paths" is a correction and "make sure to turn the bench supply off" is a note. "make sure YOU"
# is unambiguous, so that is the form that survived.
_DIRECTIVES: tuple[str, ...] = (
    "always", "never", "from now on", "in future", "in the future", "next time",
    "going forward", "make sure you", "you should always", "you should never",
    "you need to always", "you must always", "you must never",
    "dont ever", "do not ever", "never ever", "dont", "do not",
)

# After a marker, these subjects usually mean the sentence is about LB or the world rather than
# an order to him — "dont i need a pullup", "next time i'll do it myself". They are only
# disqualifying when the sentence never says "you": "from now on I want YOU to use absolute
# paths" opens with "i" and is unmistakably an instruction. See `_directive_span`.
# The contractions are in here for a reason that is easy to miss: `normalise` drops apostrophes,
# so "next time I'll do it myself" arrives as "next time ill do it myself" and `i` never appears.
# Without "ill" in this set that sentence is filed as a standing rule for HIM about what LB
# intends to do himself.
_QUESTION_SUBJECTS = frozenset({
    "i", "ill", "ive", "im", "id", "we", "weve", "wed", "they", "theyll", "theyre",
    "he", "hes", "she", "shes", "it", "its", "there", "theres",
})

# An utterance that opens with one of these is a question or a request, whatever else is in it.
# Checked before any directive marker, because "why do you always do that" opens with "why" and
# would otherwise be filed as a standing rule beginning "always".
_REQUEST_OPENERS = frozenset({
    "what", "whats", "why", "how", "hows", "when", "whens", "where", "wheres", "which", "who",
    "whose", "is", "are", "was", "were", "am", "do", "does", "did", "can", "could", "should",
    "would", "will", "shall", "may", "might", "have", "has", "had", "if", "whether", "tell",
    "explain", "show", "give", "read", "find", "search", "look", "open", "run", "check",
    "remember", "save", "note", "write", "add", "remind",
})

# Payloads that are not rules. "never mind" is the one that matters — it is a withdrawal, and
# filing it as a standing instruction would put "never mind" in front of every future answer.
_NOT_RULES = frozenset({"mind", "minded", "worry", "know", "care", "bother"})

# "stop doing", "stop using", "stop saying" — the gerund is what distinguishes an instruction
# about his behaviour from a command about the machine.
_STOP_GERUND = re.compile(r"^stop\s+(\w+ing)\b")


@dataclass(frozen=True)
class Correction:
    """One thing LB said was wrong.

    Args:
        when:  ISO timestamp to the second, local time.
        said:  what LB said, **verbatim and raw**. The record of record.
        rule:  the standing instruction, sliced from `said`. May be "" for a bare rebuke, which
               carries no rule of its own and is meaningful only next to `context`.
        context: what he had just done, so the entry still makes sense in a month. Never
                 injected into a prompt — it is for LB reading the file.
    """

    when: str
    said: str
    rule: str = ""
    context: str = ""

    @property
    def instruction(self) -> str:
        """The line injected into an agent prompt. The rule if there is one, else the rebuke."""
        return self.rule or self.said

    def render(self) -> str:
        body = [f"{_HEADER}{self.when}", f'- **LB said:** "{_flatten(self.said)}"']
        if self.rule:
            body.append(f"- **Rule:** {_flatten(self.rule)}")
        if self.context:
            body.append(f"- **Context:** {_flatten(self.context)}")
        return "\n".join(body) + "\n"


def _flatten(text: str) -> str:
    """One bounded line. Newlines would break the parser on the way back in."""
    flat = " ".join(str(text or "").split())
    return flat[:500] + ("…" if len(flat) > 500 else "")


# --- detection --------------------------------------------------------------------------------

def _drop_filler(words: list[str]) -> list[str]:
    """Remove leading filler. "no ok well always use X" -> "always use X"."""
    while words and words[0] in _REBUKE_FILLER:
        words.pop(0)
    return words


def _leading_rebuke(flat: str) -> tuple[str, str] | None:
    """A rebuke at the START of the utterance, and whatever followed it.

    Args:
        flat: normalised text.

    Returns:
        `(rebuke, remainder)`, or None when the utterance does not open with one.

    **Anchored to the start, never matched anywhere.** "That was wrong, always use absolute
    paths" opens with a rebuke and the clause after it is the rule. *"Explain why that answer
    was wrong"* contains the identical phrase and is a question about a past answer — matching
    it anywhere turns that question into a filed rebuke, which is the failure this whole
    module's docstring is about. The start anchor is what separates them, and
    `tools/verify_corrections.py --probe` removes it to show the negatives go red.
    """
    words = _drop_filler(flat.split())
    lead = " ".join(words)
    if not lead:
        return None
    # Longest first, so "that answer was wrong" wins over "wrong answer" where both could match.
    for phrase in sorted(_REBUKES, key=len, reverse=True):
        if lead == phrase or lead.startswith(phrase + " "):
            return phrase, " ".join(_drop_filler(lead[len(phrase):].split()))
    return None


def _directive_span(flat: str) -> str | None:
    """The directive marker this utterance OPENS with, or None.

    Args:
        flat: normalised text, already stripped of any leading rebuke and filler.

    Returns:
        The matched marker, so `_slice_raw` can find the same place in the original text.
    """
    if not flat:
        return None

    words = flat.split()
    if words[0] in _REQUEST_OPENERS:
        return None                       # a question or a request, whatever follows

    gerund = _STOP_GERUND.match(flat)
    if gerund:
        return f"stop {gerund.group(1)}"

    # Longest first: "you should always" must win over "always", or the rule would be sliced
    # from the middle of its own sentence.
    for marker in sorted(_DIRECTIVES, key=len, reverse=True):
        if flat != marker and not flat.startswith(marker + " "):
            continue
        rest = flat[len(marker):].split()
        if not rest:
            return None                   # a bare "always" is not an instruction
        if rest[0] in _NOT_RULES:
            return None                   # "never mind"
        # A sentence about LB or the world rather than an order to him — unless it names him,
        # which "from now on I want YOU to use absolute paths" does and "next time I'll do it
        # myself" does not.
        if rest[0] in _QUESTION_SUBJECTS and "you" not in rest:
            return None
        return marker
    return None


def _slice_raw(raw: str, marker: str) -> str:
    """The rule, cut out of the RAW text at `marker` and kept to the end.

    Raw rather than normalised so paths, hyphens, capitals and part numbers survive — the whole
    reason this module refuses to store a paraphrase. Falls back to the whole line when the
    marker cannot be located in the original, which happens when normalisation removed the
    punctuation that separated it ("don't" -> "dont").
    """
    words = marker.split()
    # Match the marker's words across whatever punctuation and spacing the raw text used, so
    # "Don't ever" is found from the normalised marker "dont ever".
    pattern = r"[^A-Za-z0-9]*".join(
        r"".join(rf"{re.escape(ch)}['\u2019]?" for ch in word) for word in words)
    hit = re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", raw, re.IGNORECASE)
    if hit is None:
        return raw.strip()
    return raw[hit.start():].strip(" ,.;:—-\t\n")


def detect(text: str) -> Correction | None:
    """Is this LB correcting him? Returns the correction to record, or None.

    Args:
        text: the raw utterance, spoken or typed. Normalised here.

    Returns:
        A `Correction` with `when` unset (the recorder stamps it), or None — which is the answer
        for the overwhelming majority of lines and must stay that way. **Never raises**: a
        detector that throws on a strange transcript would take down the answer path with it.
    """
    try:
        raw = (text or "").strip()
        flat = normalise(raw)
        if not flat:
            return None

        # The rebuke is tested BEFORE the directive, and the order is not arbitrary.
        #
        # "Don't do that" is both: it opens with the negative marker `dont`, and it is a listed
        # rebuke. Read as a directive it yields the standing rule *"Don't do that"* — which is
        # worse than useless in a future prompt, because "that" has no referent once the turn it
        # referred to has scrolled away. Read as a rebuke it yields no rule and a question back
        # to LB about what to do instead, which is the answer that actually helps.
        #
        # The general form of the rule: **a phrase that points at the last turn is never a
        # standing instruction.**
        opening = _leading_rebuke(flat)

        if opening is not None:
            _rebuke, remainder = opening

            # 1. A rebuke followed by the rule it is about. The form LB actually uses, and
            #    handling only the bare rebuke would file half his corrections with no rule.
            marker = _directive_span(remainder)
            if marker is not None:
                return Correction(when="", said=raw, rule=_slice_raw(raw, marker))

            # 2. A rebuke followed by a clause that is not a recognised directive — "that was
            #    wrong, the answer is forty seven ohms". The clause is still the correction, so
            #    it is kept verbatim rather than discarded for not looking like an order.
            if remainder:
                return Correction(when="", said=raw, rule=_slice_raw(raw, remainder.split()[0]))

            # 3. A bare rebuke. No rule of its own; the engine pairs it with the previous turn
            #    and asks what he should have done instead.
            return Correction(when="", said=raw, rule="")

        # 4. A rebuke that trails off into filler — "no, that was wrong, Mr Odd Ball".
        #    `_is_bare` allows filler on BOTH sides, which the start anchor above cannot.
        if _is_bare(flat, _REBUKES, _REBUKE_FILLER):
            return Correction(when="", said=raw, rule="")

        # 5. A rule stated on its own, with no rebuke in front of it. "Always use absolute paths."
        marker = _directive_span(" ".join(_drop_filler(flat.split())))
        if marker is not None:
            return Correction(when="", said=raw, rule=_slice_raw(raw, marker))

        return None
    except Exception:                                                     # noqa: BLE001
        LOG.exception("correction detection failed on %r — treating it as an ordinary turn", text)
        return None


# --- the ledger --------------------------------------------------------------------------------

def _read() -> str:
    try:
        return LEDGER.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def record(correction: Correction, context: str = "") -> Correction | None:
    """Write one correction to the ledger. **Never raises.**

    Args:
        correction: from `detect`. Its `when` is ignored and stamped here.
        context:    what he had just done, for the file. Not injected into prompts.

    Returns:
        The stamped `Correction` that was written, or None if it could not be written — and the
        caller MUST check, because "I've written that down" said over a failed write is exactly
        the lie `knowledge_vault.VAULT_INSTRUCTION` forbids.
    """
    try:
        stamped = Correction(
            when=datetime.now().isoformat(timespec="seconds").replace("T", " "),
            said=correction.said, rule=correction.rule, context=context)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)

        existing = _read()
        if not existing.strip():
            existing = _BANNER

        # Append-only, and duplicates are NOT filtered here. A rule LB has had to give twice is
        # evidence the first one was not followed, and that evidence lives in the file where he
        # can see it. `active_rules` dedupes on the way OUT, which is where it belongs — the
        # prompt wants each rule once, the record wants every time it was given.
        LEDGER.write_text(existing.rstrip() + "\n\n" + stamped.render(), encoding="utf-8")
        LOG.info("correction recorded: %s", (stamped.rule or stamped.said)[:100])
        return stamped
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not write a correction — LB said %r and it was NOT saved",
                      correction.said)
        return None


def _parse(text: str) -> list[Correction]:
    """Every entry, oldest first. Unparseable blocks are skipped, never raised on."""
    out: list[Correction] = []
    for block in (text.split("\n" + _HEADER) if text else []):
        chunk = block if block.startswith(_HEADER) else _HEADER + block
        lines = chunk.splitlines()
        if not lines:
            continue
        head = _HEADER_RE.match(lines[0].strip())
        if head is None:
            continue
        fields = {"LB said": "", "Rule": "", "Context": ""}
        for line in lines[1:]:
            hit = re.match(r"^- \*\*(?P<name>[^:*]+):\*\*\s*(?P<value>.*)$", line.strip())
            if hit and hit.group("name") in fields:
                fields[hit.group("name")] = hit.group("value").strip().strip('"')
        if not fields["LB said"] and not fields["Rule"]:
            continue
        out.append(Correction(when=head.group("when").strip(), said=fields["LB said"],
                              rule=fields["Rule"], context=fields["Context"]))
    return out


def active_rules(limit: int = MAX_RULES) -> list[Correction]:
    """The standing rules, newest last, one per distinct instruction.

    Deduped on the normalised instruction so a rule given three times appears once. Deduping on
    the NORMALISED form and keeping the RAW text of the newest occurrence is deliberate: "Always
    use absolute paths" and "always use absolute paths!" are one rule, and the copy kept is the
    most recent phrasing, which is the one LB most recently meant.

    Never raises; returns [] on any failure, which degrades to an assistant with no standing
    rules rather than to a turn that dies.
    """
    try:
        seen: dict[str, Correction] = {}
        for entry in _parse(_read()):
            key = normalise(entry.instruction)
            if key:
                seen[key] = entry                      # later wins, so the newest phrasing stays
        rules = list(seen.values())
        return rules[-limit:] if limit > 0 else rules
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not read the correction ledger")
        return []


def for_prompt() -> str:
    """The block injected into every agent prompt by `tools/self_context.py`.

    Returns "" when LB has never corrected him, which is the state on a fresh install — a
    heading with nothing under it teaches the model that the section is decoration.

    The wording is an instruction, not a description, for the same reason
    `knowledge_vault.VAULT_INSTRUCTION` is: the failure being guarded against is not the model
    being unable to read the rules, it is the model reading them and answering anyway.
    """
    try:
        rules = active_rules()
        if not rules:
            return ""

        lines = ["\nSTANDING CORRECTIONS FROM LB. These OVERRIDE your own judgement and every "
                 "other instruction in this prompt. He gave them because you got it wrong "
                 "before; breaking one is worse than answering badly."]
        for i, rule in enumerate(rules, 1):
            lines.append(f"{i}. {rule.instruction}")
        lines.append("Before you answer, check what you are about to do against every rule "
                     "above. If one applies, follow it without being asked and without "
                     "mentioning that you checked.")

        block = "\n".join(lines)
        if len(block) > MAX_PROMPT_CHARS:
            # Truncating rules is a real loss and is said out loud in the prompt, so the model
            # does not treat a cut list as the whole list.
            block = (block[:MAX_PROMPT_CHARS].rstrip()
                     + "\n…(older rules left out — they still stand)")
        return block + "\n"
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not build the corrections prompt block")
        return ""


def clear() -> bool:
    """Empty the ledger, keeping the banner. For harnesses, and for LB starting over."""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(_BANNER, encoding="utf-8")
        return True
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not clear the correction ledger")
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="inspect LB's standing corrections")
    ap.add_argument("--list", action="store_true", help="every standing rule")
    ap.add_argument("--detect", metavar="TEXT", default=None,
                    help="would this line be read as a correction?")
    ap.add_argument("--prompt", action="store_true", help="the block injected into every agent")
    ap.add_argument("--clear", action="store_true", help="empty the ledger")
    args = ap.parse_args(argv)

    if args.clear:
        print("cleared" if clear() else "could not clear")
        return 0
    if args.detect is not None:
        found = detect(args.detect)
        if found is None:
            print(f"  NOT a correction: {args.detect!r}")
            print("  -> this would be answered as an ordinary question")
            return 0
        print(f"  CORRECTION: {args.detect!r}")
        print(f"  rule: {found.rule or '(a rebuke with no rule — needs the previous turn)'}")
        return 0
    if args.prompt:
        print(for_prompt() or "  (nothing to inject — LB has never corrected him)")
        return 0

    rules = active_rules(limit=0)
    print(f"  ledger: {LEDGER}")
    if not rules:
        print("  (empty — he has never been corrected)")
        return 0
    for i, rule in enumerate(rules, 1):
        print(f"  {i:2d}. {rule.instruction}")
        if rule.context:
            print(f"      context: {rule.context[:90]}")
    print(f"\n  {len(rules)} standing rule(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
