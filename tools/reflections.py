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
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger("oddball.reflect")

__all__ = ["LEDGER", "Reflection", "note", "recent", "similar", "for_prompt", "clear",
           "SLOW_TURN_S"]

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
    """

    when: str
    kind: str
    what: str
    why: str
    lesson: str = ""

    def render(self) -> str:
        """The Markdown form written to the ledger."""
        body = [f"{_HEADER}{self.when} — {self.kind}",
                f"- **Tried:** {_flatten(self.what)}",
                f"- **Went wrong:** {_flatten(self.why)}"]
        if self.lesson:
            body.append(f"- **Next time:** {_flatten(self.lesson)}")
        return "\n".join(body) + "\n"

    def line(self) -> str:
        """The one-line form injected into a prompt. Compact — this rides on every call."""
        tail = f" Next time: {self.lesson}" if self.lesson else ""
        return f"- [{self.when[:16]}] {self.what} -> {self.why}.{tail}"


def _flatten(text: str) -> str:
    """One line, no newlines, bounded.

    Newlines would break the parser on the way back in — a traceback pasted into a field turns
    one entry into thirty unparseable ones. The cap is per field so a 40 kB stack trace cannot
    become the whole ledger.
    """
    flat = " ".join(str(text or "").split())
    return flat[:400] + ("…" if len(flat) > 400 else "")


def _tokens(text: str) -> set[str]:
    """Content words, lowercased. Used only for the overlap score in `similar`."""
    return {w for w in _WORD.findall(str(text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def note(kind: str, what: str, why: str, lesson: str = "") -> bool:
    """Record one mistake. **Never raises.**

    Args:
        kind:   a short slug — "tool-failure", "slow-turn", "exception", "blocked".
        what:   what he was trying to do.
        why:    what went wrong.
        lesson: what to do differently, or "" when there is nothing honest to say.

    Returns:
        True if it was written. False on any failure, which is logged and otherwise ignored —
        see the module docstring: a ledger that raises while recording a failure is worse than
        no ledger at all.
    """
    try:
        entry = Reflection(when=datetime.now().isoformat(timespec="seconds").replace("T", " "),
                           kind=str(kind or "unknown"), what=what, why=why, lesson=lesson)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)

        existing = _read()
        if not existing.strip():
            existing = _BANNER

        text = existing.rstrip() + "\n\n" + entry.render()
        LEDGER.write_text(_rotate(text), encoding="utf-8")
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


def _rotate(text: str) -> str:
    """Drop the oldest entries so the file stays at `MAX_ENTRIES`.

    Rotation keeps the BANNER and the newest entries. Written as a pure function of the text so
    it can be tested without a filesystem, which is what `tools/verify_reflections.py` does.
    """
    head, _, rest = text.partition(_HEADER)
    if not rest:
        return text                                    # nothing but the banner yet
    # `partition` consumed the first header and `split` consumes the rest, so every block comes
    # back headerless and gets one put back. Uniform, which is why there is no special case for
    # the first entry.
    blocks = [_HEADER + b for b in rest.split("\n" + _HEADER)]
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
        fields = {"Tried": "", "Went wrong": "", "Next time": ""}
        for line in lines[1:]:
            hit = re.match(r"^- \*\*(?P<name>[^:*]+):\*\*\s*(?P<value>.*)$", line.strip())
            if hit and hit.group("name") in fields:
                fields[hit.group("name")] = hit.group("value").strip()
        out.append(Reflection(when=head.group("when").strip(), kind=head.group("kind").strip(),
                              what=fields["Tried"], why=fields["Went wrong"],
                              lesson=fields["Next time"]))
    return out


def recent(limit: int = PROMPT_ENTRIES) -> list[Reflection]:
    """The newest `limit` mistakes, newest last. Never raises; returns [] on any failure."""
    try:
        entries = _parse(_read())
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
    args = ap.parse_args(argv)

    if args.clear:
        print("cleared" if clear() else "could not clear")
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

    entries = recent(limit=0)
    print(f"  ledger: {LEDGER}")
    if not entries:
        print("  (empty — nothing has gone wrong yet, or it was never recorded)")
        return 0
    for entry in entries:
        print(f"  {entry.line()}")
    print(f"\n  {len(entries)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
