#!/usr/bin/env python3
"""
Module:  reflections.py
Purpose: The mistake ledger — what failed, why it failed, and what to check before trying again.
Author:  LB
Date:    2026-08-25

    python tools/reflections.py --list
    python tools/reflections.py --similar "open firefox"
    python tools/reflections.py --clear

## What this is, and what it is not

`vault/reflections.md` is a record of **his own failures**: a tool that errored, a command the
blocklist refused, a turn that took far longer than it should have, an exception that reached
`Engine.ask`. It is written by the code that observed the failure, at the moment it happened,
and it is read back into every agent prompt by `tools/self_context.py`.

It is deliberately NOT the same file as `vault/corrections.md`:

    reflections.md   things that went wrong on their own. HE noticed.
    corrections.md   things LB told him were wrong. LB noticed.

They are different evidence with different authority and they must not be averaged together.
A correction is an instruction and is followed. A reflection is a datum and is *considered* —
"the last time you tried this it timed out" is worth knowing and is not a prohibition. Merging
them would either soften LB's rules into suggestions or harden a single timeout into a refusal,
and both of those are worse than keeping two files.

## Why plain Markdown, again

Same argument as `tools/knowledge_vault.py`, and it applies more strongly here: this file is
the record of what the system got wrong, so it is the first thing LB will want to read when
something is misbehaving, and the first thing worth putting on screen in a vlog. `grep`, a text
editor and a phone all open it. A database does not survive being useful to a person.

## Two things this file is careful about

1. **It can never fail a turn.** Every public function swallows its own exceptions and returns
   an empty result. A ledger that raises while recording a failure would turn one bad turn into
   a crash, which is the exact opposite of the job. The log is the only place a write error is
   reported, because the caller is already handling something that went wrong.
2. **It is bounded.** `MAX_ENTRIES` rotates the file and `MAX_PROMPT_CHARS` bounds what reaches
   a model. An unbounded ledger injected into every prompt is a context window that shrinks a
   little every day until answers start getting worse for no visible reason — the failure mode
   `knowledge_vault.MAX_RESULT_CHARS` exists to prevent, arriving by a different door.

## The third thing, added 2026-09-04: it counts instead of repeating

Bounding the file turned out not to bound the *information*. A measurement of the real ledger
found 37 entries of which **22 were the same slow-turn with the same lesson**, and six of those
were what every agent prompt was carrying. The cap was being honoured perfectly and the block was
still worthless.

So `note` merges an entry with the same signature rather than appending, and carries a `count`
and a `first` — see `_QUOTED` for what "the same" means, and `note` for why the merge is done on
text rather than on a reparse. `PROMPT_MAX_AGE_DAYS` then ages out entries that have stopped
recurring, which is the other half: a mistake stays in the prompt exactly as long as it keeps
happening, and nothing has to decide when a problem is over.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

LOG = logging.getLogger("oddball.reflect")

__all__ = ["LEDGER", "Reflection", "note", "recent", "similar", "for_prompt", "clear",
           "SLOW_TURN_S", "PROMPT_MAX_AGE_DAYS", "compact"]

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
LEDGER = VAULT_DIR / "reflections.md"

# How many entries the file keeps. 200 is roughly a fortnight of ordinary use and about 40 kB —
# small enough to read end to end when something is wrong, large enough that a pattern repeating
# every few days is still visible in it.
MAX_ENTRIES = 200

# How much of the ledger may reach a prompt. Far tighter than the file's own cap: this rides on
# EVERY agent call, so it is charged against the context budget of every question LB asks, not
# just the ones about failures. ~2k characters is about 500 tokens.
MAX_PROMPT_CHARS = 2_000

# How many past mistakes to put in front of the model on one turn. Beyond a handful this stops
# being "check what went wrong before" and becomes a wall of text that gets skimmed.
PROMPT_ENTRIES = 6

# How stale an entry may be and still ride on every prompt. **The decay half of the fix made on
# 2026-09-04**, and it applies only to the "Recently:" list — never to `similar()`, which answers
# "have I broken this exact thing before" and for which a three-week-old failure is just as much
# an answer as a three-hour-old one.
#
# Seven days because that is the horizon over which this repo actually changes: the wake
# threshold moved three times inside one week, and a mistake from before a fix is a mistake the
# model should stop being told about. Entries older than this stay in the FILE — LB reads it, and
# rotation is what bounds the file — they simply stop being injected.
PROMPT_MAX_AGE_DAYS = 7.0

# How much of one entry's "why" may reach a prompt. `_flatten` caps a field at 400 characters,
# which is right for the FILE — LB wants the whole error when he is reading it — and far too
# generous for a line charged against every question he asks.
#
# Measured after the 2026-09-04 compaction: two OpenRouter 429s, each carrying a full JSON body
# with rate-limit headers and a reset epoch, were eating 1,000 of the 2,000-character budget
# between them. Six mistakes were being summarised, and two of them had the floor. The model
# needs "RateLimitError: 429 free-models-per-day"; it does not need `X-RateLimit-Reset`.
LINE_MAX_CHARS = 160

# A turn slower than this is logged as a mistake in its own right. The number is LB's measured
# ceiling for a paid turn on the Pi: the router alone measured 9.8 s there (see
# `orchestrator/route_hint.py`), and a two-call turn on top of that lands near 25 s. Past 45 s
# something is wrong — a retry storm, a stalled tool, a model that is not answering — and it is
# worth a line in the ledger even when the turn eventually succeeded.
SLOW_TURN_S = 45.0

# The entry header. Parsed back by `_parse`, so the two must move together; they are next to
# each other for that reason.
_HEADER = "## "
_HEADER_RE = re.compile(r"^## (?P<when>[0-9T:\- ]+) — (?P<kind>[^\n]+)$")

# The occurrence count, parsed back out of `Reflection.render`. Kept next to the renderer's
# wording for the same reason `_HEADER_RE` is kept next to `_HEADER`: the two have to move
# together, and separating them is how a format change silently starts reading as "seen once".
_SEEN_RE = re.compile(r"^(?P<count>\d+)\s+times?,\s*first at\s*(?P<first>.*)$")

# One `- **Name:** value` line. Shared by `_parse`, which reads them, and by `note`, which needs
# to know which lines in a block it is REPLACING and which are LB's and must be carried over.
_FIELD_RE = re.compile(r"^- \*\*(?P<name>[^:*]+):\*\*\s*(?P<value>.*)$")

# The field names `Reflection.render` writes and `_parse` reads back. Anything else in that
# shape belongs to whoever typed it — see `_annotations`.
_OWN_FIELDS = ("Tried", "Went wrong", "Next time", "Seen")

# How many characters of one field reach the FILE. Named because `Reflection.signature` has to
# apply the same bound at the same point, and the two drifting apart is what makes a long
# transcript stop merging with itself.
_FIELD_CHARS = 400

_BANNER = """# Reflections — what went wrong, and what it taught

<!-- Written by tools/reflections.py when something fails. Newest entries at the bottom.
     Read back into every agent prompt by tools/self_context.py, so what is written here
     changes how the next answer is produced. Safe to edit by hand; safe to delete. -->
"""

# Words that carry no signal when matching one failure against another. Without this, "the",
# "that" and "a" dominate the overlap score and every entry looks similar to every question.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did", "do", "does", "for",
    "from", "get", "had", "has", "have", "how", "i", "if", "in", "is", "it", "its", "me", "my",
    "no", "not", "of", "on", "or", "so", "that", "the", "then", "this", "to", "up", "was",
    "what", "when", "which", "why", "will", "with", "you", "your",
})

_WORD = re.compile(r"[a-z0-9_.-]+")

# The two patterns that turn one entry into a CLASS of entry, for `_signature`.
#
# `_QUOTED` strips the quoted span out of "what" — that is where the transcript sits, and the
# transcript is the volatile part. "answer 'ball.' via the persona path" and "answer 'Okay.' via
# the persona path" are one fact about the persona path, not two facts about two words.
#
# `_NUMBER` strips digits out of "why" — that is where the elapsed seconds, the exit codes and
# the byte counts sit. "it took 85 seconds — 1s to route and 84s in the agent" and the same
# sentence with 149 in it are the same failure measured twice.
#
# **The asymmetry is the whole design and it was nearly got backwards.** Numbers are NOT stripped
# from "what", because `similar()` was measured into treating a digit-bearing token as the
# highest-signal token there is — `ece350`, `stm32`, `2n3904`. Collapsing "read the ECE350
# syllabus" and "read the ECE250 syllabus" into one entry would destroy exactly the token that
# makes the ledger findable. And quotes are not stripped from "why", because an exception names
# what it could not find in quotes: two different AttributeErrors must stay two entries.
# Backticks are deliberately NOT in this class. This repo quotes a TRANSCRIPT with `repr()` and
# marks a COMMAND with backticks — "run `apt-get update`" and "run `rm -rf /tmp/build`" are two
# different mistakes, and collapsing them would be the same error as collapsing two course codes.
_QUOTED = re.compile(r"""(['"])(?:(?!\1).)*\1""")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Reflection:
    """One recorded mistake.

    Args:
        when:   ISO timestamp, to the second. Local time, because LB reads this file himself
                and "which evening was that" is the question he will ask of it.
        kind:   a short slug for the class of failure — "tool-failure", "slow-turn",
                "exception", "blocked". Free-form on purpose: a fixed vocabulary here would
                have to be extended by whoever adds the next call site, and the one that got
                skipped is the one that stops being recorded.
        what:   what he was trying to do, in plain words.
        why:    what actually went wrong. The error text, the exit code, the elapsed seconds.
        lesson: what to do differently. May be "" — a lesson invented to fill a field is worse
                than an honest blank, and the entry is still useful without one.
        count:  how many times this same mistake has been recorded. `when` is the LATEST
                occurrence and `first` is the earliest, so one entry carries the whole run.
        first:  when it first happened. Equals `when` for an entry seen once.
    """

    when: str
    kind: str
    what: str
    why: str
    lesson: str = ""
    count: int = 1
    first: str = ""

    def signature(self) -> str:
        """The identity of the CLASS of mistake this entry belongs to.

        Two entries with the same signature are one recurring problem and are merged by `note`.
        See `_QUOTED` and `_NUMBER` for which parts are treated as volatile and why the two
        fields are treated differently.
        """
        # Substitute BEFORE truncating, not after. `_flatten` caps a field at 400 characters,
        # and a transcript that runs past the cap loses its closing quote — after which
        # `_QUOTED` cannot match, the volatile transcript stays in the signature verbatim, and
        # two recordings of the same failure stop merging. `agents/screen_agent.py` builds
        # `what` from an unbounded question, so that is reachable, not theoretical.
        what = _QUOTED.sub("'…'", _collapse(self.what))[:_FIELD_CHARS].strip().lower()
        why = _numbers_outside_quotes(_collapse(self.why))[:_FIELD_CHARS].strip().lower()
        return f"{str(self.kind or '').strip().lower()}|{what}|{why}"

    def render(self) -> str:
        """The Markdown form written to the ledger."""
        body = [f"{_HEADER}{self.when} — {self.kind}",
                f"- **Tried:** {_flatten(self.what)}",
                f"- **Went wrong:** {_flatten(self.why)}"]
        if self.lesson:
            body.append(f"- **Next time:** {_flatten(self.lesson)}")
        if self.count > 1:
            # Last field, and only when it says something. An entry seen once should read
            # exactly as it did before this field existed — most of them are, and a "Seen: 1
            # time" on every one of them is a column of noise in a file LB reads by eye.
            body.append(f"- **Seen:** {self.count} times, first at {self.first or self.when}")
        return "\n".join(body) + "\n"

    def line(self) -> str:
        """The one-line form injected into a prompt. Compact — this rides on every call.

        The count leads the line when there is one. **That number is the entire point of the
        deduplication**: "this has now happened 22 times" is a statement about the system, and it
        is the one thing twenty-two separate paragraphs of the same text could not say.
        """
        def clip(text: str) -> str:
            text = str(text or "")
            return text if len(text) <= LINE_MAX_CHARS else text[:LINE_MAX_CHARS].rstrip() + "…"

        tail = f" Next time: {clip(self.lesson)}" if self.lesson else ""
        seen = f" (×{self.count} since {self.first[:16]})" if self.count > 1 else ""
        return f"- [{self.when[:16]}]{seen} {clip(self.what)} -> {clip(self.why)}.{tail}"


def _flatten(text: str) -> str:
    """One line, no newlines, bounded.

    Newlines would break the parser on the way back in — a traceback pasted into a field turns
    one entry into thirty unparseable ones. The cap is per field so a 40 kB stack trace cannot
    become the whole ledger.
    """
    flat = _collapse(text)
    return flat[:_FIELD_CHARS] + ("…" if len(flat) > _FIELD_CHARS else "")


def _collapse(text: str) -> str:
    """One line, unbounded. `_flatten` is this plus the 400-character cap."""
    return " ".join(str(text or "").split())


def _numbers_outside_quotes(text: str) -> str:
    """Blank out digits, EXCEPT inside a quoted span.

    The elapsed seconds, the exit codes and the byte counts in a `why` are volatile and should
    not split an entry. The thing an exception could not find is not:

        FileNotFoundError: No such file: 'data/quiz/ece350.json'
        FileNotFoundError: No such file: 'data/quiz/ece250.json'

    Those are two different missing decks, and stripping every digit made them one — which is
    exactly the mistake `_QUOTED` refuses to make in `what`, arriving in the other field. An
    exception names what it was looking for in quotes, and that name is the identity.
    """
    out: list[str] = []
    last = 0
    for match in _QUOTED.finditer(text):
        out.append(_NUMBER.sub("#", text[last:match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(_NUMBER.sub("#", text[last:]))
    return "".join(out)


def _tokens(text: str) -> set[str]:
    """Content words, lowercased. Used only for the overlap score in `similar`."""
    return {w for w in _WORD.findall(str(text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def note(kind: str, what: str, why: str, lesson: str = "") -> bool:
    """Record one mistake, merging it with the same mistake if it has happened before.
    **Never raises.**

    Args:
        kind:   a short slug — "tool-failure", "slow-turn", "exception", "blocked".
        what:   what he was trying to do.
        why:    what went wrong.
        lesson: what to do differently, or "" when there is nothing honest to say.

    Returns:
        True if it was written. False on any failure, which is logged and otherwise ignored —
        see the module docstring: a ledger that raises while recording a failure is worse than
        no ledger at all.

    ## Why this merges rather than appends

    Appending was the original behaviour and it was measured wrong on 2026-09-04: of 37 entries,
    **22 were slow-turns carrying the identical lesson**, eight of them for false-wake noise
    ('ball.', 'Okay.', 'Mr. Albo.'). Six of those rode on every agent prompt, which meant the
    PAST MISTAKES block — the thing that costs tokens on every question LB asks — was six copies
    of one sentence about the persona path being slow.

    One entry with `count = 22` says strictly more than twenty-two entries did, in a
    twenty-second of the space, and it says the thing that matters: this is systemic.

    ## Why the merge is textual and not a rewrite from the parse

    The obvious implementation is to parse the file, drop the match, and write everything back.
    That would quietly delete any block `_parse` cannot read — and the module docstring promises
    LB the file is safe to hand-edit, so an unparseable block is an EXPECTED state, not a
    corruption. Blocks are split as text and only the matching ones are removed, so a
    hand-written note in the middle of the ledger survives a merge that happens around it.
    """
    try:
        now = datetime.now().isoformat(timespec="seconds").replace("T", " ")
        entry = Reflection(when=now, kind=str(kind or "unknown"), what=_flatten(what),
                           why=_flatten(why), lesson=_flatten(lesson) if lesson else "",
                           count=1, first=now)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)

        existing = _read()
        if not existing.strip():
            existing = _BANNER

        head, blocks = _split_blocks(existing)
        signature = entry.signature()
        kept: list[str] = []
        carried: list[str] = []
        merged = 0
        first = ""
        for block in blocks:
            prior = _parse(block)
            if prior and prior[0].signature() == signature:
                merged += max(1, prior[0].count)
                earlier = prior[0].first or prior[0].when
                first = min(first, earlier) if first else earlier
                # The prior lesson is kept when this call has none, so a lesson written once is
                # not lost by a later recording of the same failure that had nothing to add.
                if not entry.lesson and prior[0].lesson:
                    entry = replace(entry, lesson=prior[0].lesson)
                carried.extend(_annotations(block))
            else:
                kept.append(block)

        if merged:
            entry = replace(entry, count=merged + 1, first=first or now)

        # The merged entry goes at the BOTTOM, where the newest entry has always gone. A
        # recurrence is news: it belongs where `recent()` will find it, not frozen in the
        # position it first occupied weeks ago.
        body = "".join(block.rstrip() + "\n\n" for block in kept)
        text = head.rstrip() + "\n\n" + body + entry.render()
        if carried:
            text += "".join(line + "\n" for line in carried)
        LEDGER.write_text(_rotate(text), encoding="utf-8")

        if merged:
            LOG.info("reflection: %s — %s (seen %d times)",
                     entry.kind, entry.what[:80], entry.count)
        else:
            LOG.info("reflection: %s — %s", entry.kind, entry.what[:80])
        return True
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not write a reflection (kind=%r)", kind)
        return False


def _read() -> str:
    """The ledger's text, or "" when it does not exist or cannot be read."""
    try:
        return LEDGER.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _annotations(block: str) -> list[str]:
    """Lines in an entry that this module did not write — LB's, and his to keep.

    A merge replaces the block it matched, and everything in that block goes with it. The header
    and the `- **Field:**` lines are regenerated from the merged entry, so losing those is
    correct. **Anything else in there was typed by a person**, and the module docstring promises
    the file is safe to edit by hand — a note LB added under an entry must not disappear the next
    time that entry happens to recur.

    Returns them in order, stripped of trailing blank lines, so they can be re-attached below the
    merged entry.
    """
    out: list[str] = []
    for line in block.splitlines()[1:]:                # [0] is the "## when — kind" header
        if not line.strip():
            continue
        hit = _FIELD_RE.match(line.strip())
        # Only the fields this module REGENERATES are dropped. Skipping every `- **Name:**`
        # line was wrong in the one shape LB is most likely to use: the file's own house style
        # is `- **Name:** value`, so a hand-added `- **Note:** keep an eye on this` or
        # `- **Ticket:** ODD-14` looked like a field, was neither carried nor rewritten, and
        # vanished on the next recurrence. That is the exact promise `_annotations` exists to
        # keep, broken by the check meant to keep it.
        if hit and hit.group("name") in _OWN_FIELDS:
            continue
        out.append(line.rstrip())
    return out


def _split_blocks(text: str) -> tuple[str, list[str]]:
    """The banner, and every entry block as raw text. Never raises.

    Shared by `_rotate` and by `note`'s merge, and it hands back TEXT rather than parsed
    `Reflection`s on purpose: both callers rewrite the file, and a block that `_parse` cannot
    read must survive being written back out. See `note` for why that matters.
    """
    head, _, rest = text.partition(_HEADER)
    if not rest:
        return text, []                                # nothing but the banner yet
    # `partition` consumed the first header and `split` consumes the rest, so every block comes
    # back headerless and gets one put back. Uniform, which is why there is no special case for
    # the first entry.
    return head, [_HEADER + b for b in rest.split("\n" + _HEADER)]


def _rotate(text: str) -> str:
    """Drop the oldest entries so the file stays at `MAX_ENTRIES`.

    Rotation keeps the BANNER and the newest entries. Written as a pure function of the text so
    it can be tested without a filesystem, which is what `tools/verify_reflections.py` does.
    """
    head, blocks = _split_blocks(text)
    if not blocks:
        return text                                    # nothing but the banner yet
    if len(blocks) <= MAX_ENTRIES:
        return text
    kept = blocks[-MAX_ENTRIES:]
    LOG.info("rotated the reflection ledger: %d entries -> %d", len(blocks), len(kept))
    return head.rstrip() + "\n\n" + "\n".join(b.rstrip() + "\n" for b in kept)


def _parse(text: str) -> list[Reflection]:
    """Every entry in `text`, oldest first. Unparseable blocks are skipped, never raised on.

    A hand-edited ledger is an expected state — the module docstring promises LB can edit it —
    so a block that no longer matches the header shape is dropped quietly rather than taking
    the whole read down with it.
    """
    out: list[Reflection] = []
    for block in (text.split("\n" + _HEADER) if text else []):
        # The first chunk is whatever preceded the first entry — the banner, normally. It gets
        # a header prepended like every other chunk, fails `_HEADER_RE`, and is dropped. That
        # is the intended path, not an accident: it means a file with no banner, a file with a
        # hand-written banner, and a file with none of either all parse the same way.
        chunk = block if block.startswith(_HEADER) else _HEADER + block
        lines = chunk.splitlines()
        if not lines:
            continue
        head = _HEADER_RE.match(lines[0].strip())
        if head is None:
            continue
        fields = {"Tried": "", "Went wrong": "", "Next time": "", "Seen": ""}
        for line in lines[1:]:
            hit = _FIELD_RE.match(line.strip())
            if hit and hit.group("name") in fields:
                fields[hit.group("name")] = hit.group("value").strip()

        when = head.group("when").strip()
        # An absent, hand-mangled or hand-deleted `Seen:` line reads as "seen once", which is
        # what every entry written before 2026-09-04 is, and what LB gets if he edits the count
        # out by hand. There is no state in which a bad count can raise.
        count, first = 1, when
        seen = _SEEN_RE.match(fields["Seen"])
        if seen:
            try:
                count = max(1, int(seen.group("count")))
                first = seen.group("first").strip() or when
            except ValueError:
                count, first = 1, when

        out.append(Reflection(when=when, kind=head.group("kind").strip(),
                              what=fields["Tried"], why=fields["Went wrong"],
                              lesson=fields["Next time"], count=count, first=first))
    return out


def _stale(when: str, cutoff: datetime) -> bool:
    """True when `when` is definitely older than `cutoff`. Unreadable timestamps are NOT stale.

    Failing towards keeping the entry is the safe direction: the cost of keeping one entry too
    long is a line in a prompt, and the cost of dropping one wrongly is a mistake the model
    stops being warned about. A hand-edited date is an expected state in this file.
    """
    try:
        return datetime.fromisoformat(str(when).strip().replace(" ", "T", 1)) < cutoff
    except (ValueError, TypeError):
        return False


def recent(limit: int = PROMPT_ENTRIES,
           max_age_days: float = PROMPT_MAX_AGE_DAYS) -> list[Reflection]:
    """The newest `limit` mistakes, newest last. Never raises; returns [] on any failure.

    Args:
        limit:        how many to return, newest last. 0 or less means every entry.
        max_age_days: entries whose LATEST occurrence is older than this are left out. 0 or less
                      means no age limit — which is what `--list` passes, because the file is
                      LB's to read in full and only the PROMPT is short of room.

    The age filter is what stops a fixed bug being cited forever. An entry that has stopped
    happening ages out of the prompt on its own, and one that is still happening keeps having its
    `when` refreshed by `note` and stays. **That is the decay: it is driven by recurrence, so
    nothing has to decide when a problem is over.**
    """
    try:
        entries = _parse(_read())
        if max_age_days and max_age_days > 0:
            cutoff = datetime.now() - timedelta(days=max_age_days)
            entries = [e for e in entries if not _stale(e.when, cutoff)]
        return entries[-limit:] if limit > 0 else entries
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not read the reflection ledger")
        return []


def similar(text: str, limit: int = 3) -> list[Reflection]:
    """Past mistakes that look like what he is about to try.

    Args:
        text:  the current question or the action about to be taken.
        limit: how many to return.

    Returns:
        Up to `limit` entries, best match first, or [] when nothing overlaps.

    **Word overlap, not embeddings, and that is the whole design.** The question this answers is
    "have I broken this exact thing before" — the same app name, the same command, the same
    course code. Those are literal tokens, and a literal match is both the right tool and one
    that costs no model, no index and no key. `tools/vector_db.py` is where semantic search
    lives, and it exists for hundreds of pages of datasheet, which this is not.

    A score of two is required, and a **token containing a digit counts double**. One plain word
    in common is noise — a single "the pi" would surface an unrelated timeout on every question
    about the machine — but one shared identifier is not noise at all. `ece350`, `stm32`,
    `2n3904` and `8765` are the tokens that name a specific thing, and a question that shares one
    with a past failure is almost certainly about that same thing.

    That asymmetry is the whole rule, and it was found by measurement rather than designed:
    "when is the ECE350 midterm" against a failure recorded as "read the ECE350 syllabus" shares
    exactly one content word, and a flat threshold of two missed it.
    """
    try:
        wanted = _tokens(text)
        if not wanted:
            return []

        scored: list[tuple[int, int, Reflection]] = []
        for i, entry in enumerate(_parse(_read())):
            shared = wanted & _tokens(f"{entry.what} {entry.why} {entry.kind}")
            overlap = len(shared) + sum(1 for w in shared if any(c.isdigit() for c in w))
            if overlap >= 2:
                # `i` breaks ties toward the NEWEST entry: if he made the same mistake twice,
                # the recent one is the one whose lesson is current.
                scored.append((overlap, i, entry))

        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [entry for _, _, entry in scored[:limit]]
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not search the reflection ledger")
        return []


def for_prompt(question: str = "") -> str:
    """The block injected into every agent prompt by `tools/self_context.py`.

    Args:
        question: the current question, used to surface relevant past failures. May be "".

    Returns:
        A bounded Markdown block, or "" when there is nothing to say. **"" is the common case
        and the right one** — a heading with no entries under it is noise in every prompt, and
        it teaches the model that the section is usually empty and can be skipped.

    Relevant entries lead, recent ones follow, and neither list repeats the other. That order is
    the point of the whole file: "the last time you tried to open this app it was not installed"
    has to arrive before "here are the last six things that went wrong".
    """
    try:
        matched = similar(question) if question else []
        seen = {(e.when, e.what) for e in matched}
        history = [e for e in recent() if (e.when, e.what) not in seen]

        if not matched and not history:
            return ""

        parts = ["\nPAST MISTAKES (yours). Check these before you do something similar."]
        if matched:
            parts.append("Closest to what is being asked now:")
            parts.extend(e.line() for e in matched)
        if history:
            parts.append("Most recent:" if matched else "Recently:")
            parts.extend(e.line() for e in history)
        parts.append("Do not repeat these. If one of them applies, say so and take the other "
                     "route rather than trying the same thing again.")

        block = "\n".join(parts)
        if len(block) > MAX_PROMPT_CHARS:
            block = block[:MAX_PROMPT_CHARS].rstrip() + "\n…(older mistakes left out)"
        return block + "\n"
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not build the reflection prompt block")
        return ""


def compact() -> tuple[int, int]:
    """Merge the duplicates already in the ledger. Returns (entries before, entries after).

    `note` only merges what it is given, so a ledger written before 2026-09-04 keeps its
    duplicates until each one happens again. This applies the same rule to the file as it stands
    — a one-off, run by hand from `--compact`, never on the answer path.

    A dated `.bak` is written first, one per run. This rewrites the file from the parse, so
    unlike `note` it CAN drop a block that no longer parses; that is the price of compacting the
    whole file at once, and the backup is what makes the price refundable.

    Returns (0, 0) on an empty ledger or any failure — it never raises, like everything here.
    """
    try:
        before = _parse(_read())
        if not before:
            return 0, 0

        # One backup PER RUN, named for the moment it was taken. A fixed `.bak` overwrites the
        # only copy of the pre-compaction file with the already-compacted one on the second run
        # — and the second run is the likely one: LB compacts, opens the file, wonders whether a
        # block was dropped, and runs it again to look. That is exactly when the refund has to
        # still be there.
        #
        # Writing it only when absent was the other candidate and is worse: a compaction a month
        # later would silently be "backed up" by a file from the first one. A few dated files in
        # `vault/` is the cheaper failure, and `--compact` is a command LB types by hand.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = LEDGER.with_suffix(f"{LEDGER.suffix}.{stamp}.bak")
        backup.write_text(_read(), encoding="utf-8")

        merged: dict[str, Reflection] = {}
        for entry in before:                            # oldest first
            key = entry.signature()
            prior = merged.get(key)
            if prior is None:
                merged[key] = replace(entry, first=entry.first or entry.when)
                continue
            merged[key] = replace(
                entry,
                count=prior.count + entry.count,
                first=min(prior.first or prior.when, entry.first or entry.when),
                # The newest entry's text wins; the oldest surviving lesson is kept when the
                # newer one had nothing to say.
                lesson=entry.lesson or prior.lesson)

        # Newest last, by the latest occurrence — the order the file has always been in.
        out = sorted(merged.values(), key=lambda e: e.when)
        text = _BANNER.rstrip() + "\n\n" + "".join(e.render() + "\n" for e in out)
        LEDGER.write_text(_rotate(text.rstrip() + "\n"), encoding="utf-8")
        LOG.info("compacted the reflection ledger: %d entries -> %d", len(before), len(out))
        return len(before), len(out)
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not compact the reflection ledger")
        return 0, 0


def clear() -> bool:
    """Empty the ledger, keeping the banner. For harnesses and for LB starting fresh."""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(_BANNER, encoding="utf-8")
        return True
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not clear the reflection ledger")
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="inspect the mistake ledger")
    ap.add_argument("--list", action="store_true", help="every entry, oldest first")
    ap.add_argument("--similar", metavar="TEXT", default=None,
                    help="past mistakes that look like TEXT")
    ap.add_argument("--prompt", metavar="TEXT", nargs="?", const="", default=None,
                    help="the block that would be injected into an agent prompt")
    ap.add_argument("--clear", action="store_true", help="empty the ledger")
    ap.add_argument("--compact", action="store_true",
                    help="merge duplicates already in the file (writes a .bak first)")
    args = ap.parse_args(argv)

    if args.clear:
        print("cleared" if clear() else "could not clear")
        return 0
    if args.compact:
        before, after = compact()
        if not before:
            print("  nothing to compact")
            return 0
        print(f"  {before} entries -> {after} ({before - after} duplicates merged)")
        newest = max(LEDGER.parent.glob(LEDGER.name + ".*.bak"), key=lambda p: p.name,
                     default=None)
        print(f"  backup: {newest}" if newest else "  backup: (none written)")
        return 0
    if args.similar is not None:
        found = similar(args.similar, limit=10)
        print(f"  {len(found)} similar past mistake(s) for {args.similar!r}")
        for entry in found:
            print(f"  {entry.line()}")
        return 0
    if args.prompt is not None:
        print(for_prompt(args.prompt) or "  (nothing to inject)")
        return 0

    # No age limit here. The prompt is short of room; the file is LB's and he reads all of it.
    entries = recent(limit=0, max_age_days=0)
    print(f"  ledger: {LEDGER}")
    if not entries:
        print("  (empty — nothing has gone wrong yet, or it was never recorded)")
        return 0
    for entry in entries:
        print(f"  {entry.line()}")
    occurrences = sum(e.count for e in entries)
    print(f"\n  {len(entries)} entries, {occurrences} occurrences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
