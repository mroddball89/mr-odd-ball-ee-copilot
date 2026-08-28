#!/usr/bin/env python3
"""
Module:  knowledge_vault.py
Purpose: Long-term memory as plain Markdown on disk — notes, specs, component inventories.
Author:  LB
Date:    2026-08-21

    python tools/knowledge_vault.py --list
    python tools/knowledge_vault.py --search "robot arm"
    python tools/knowledge_vault.py --find "my amp board note"
    python tools/knowledge_vault.py --read "regulator choice"

## Why a second memory, when `tools/memory_manager.py` already exists

They are different things, and the difference is the whole reason for this file.

`memory_manager.py` is the **conversation log**: the last 40 turns, on the SD card, rotated,
fed to every agent as `PREVIOUS CONTEXT`. It is short-term by construction — say forty more
things and what you said today is gone.

This is the **vault**: things LB decided are worth keeping past that window. A part number he
settled on, a pinout he worked out, what is actually in the parts drawer. It is written only
when an agent is asked to write it, it is never rotated, and it is plain Markdown in a folder
so it survives this program being deleted.

## Markdown, in a folder, deliberately

No database, no embeddings, no index. `vault/**/*.md` is greppable with `grep`, editable in
any text editor, diffable in git, and readable on a phone. The search below is a substring
scan over files that will number in the dozens, not the millions — an index would be a moving
part bought with nothing.

The vector store in `tools/vector_db.py` is the other end of that trade and stays where it is:
it exists for hundreds of pages of datasheet PDF, which is exactly the case a substring scan
cannot serve.

## The notebook — five operations, none of which involves a model

`save_to_vault` and `read_from_vault` are the two an *agent* calls, with a model choosing the
arguments. Below them sit five plain functions that `orchestrator/note_intent.py` and
`engine/core.py` drive directly, so LB can dictate a note and get it back without spending a
Gemini call on any part of it:

    write_note   what he said, verbatim, appended under a rule if the note exists
    find_notes   a spoken name -> paths. Never guesses: 0 says so, 2+ asks which
    read_note    the note back, said whole when it fits and clipped honestly when it does not
    append_note  more of what he said, added under a rule, at the path already resolved
    list_notes   what is in the vault, or in one folder
    trash_note   a delete, done as a MOVE to `.trash/` — see that function for why

`tools/verify_notes.py` is the harness, and it needs no key, no audio and no network because
none of this does.

## Two things this file is careful about, because an LLM supplies the arguments

1. **Paths.** `filename` and `folder` come out of a model. `vault / "../../.ssh/authorized_keys"`
   resolves fine and writes fine, so both are flattened to a single safe path segment before
   they touch the filesystem, and the result is asserted to still be inside the vault.
2. **Size.** `read_from_vault` output goes straight back into a prompt. An unbounded read of a
   vault that has grown for a term would silently blow the context window, and the model would
   answer from whichever half it happened to get. It is capped, and it SAYS when it truncated.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

# Run directly as `python tools/knowledge_vault.py`, this file is not inside a package and the
# repo root is not on the path, so the `from memory.speakable import ...` below fails with
# ModuleNotFoundError. Same guard, same reason, as the head of `tools/corrections.py` — and it
# was needed here the moment this module grew its first repo-root import.
if __package__ in (None, ""):                                          # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.speakable import MAX_WORDS                                 # noqa: E402
from orchestrator.instant import normalise                             # noqa: E402

LOG = logging.getLogger("oddball.vault")

__all__ = ["VAULT_DIR", "TRASH_DIR", "VAULT_TOOLS", "VAULT_INSTRUCTION", "followup_prompt",
           "save_to_vault", "read_from_vault", "run_vault_calls", "write_note",
           "NoteView", "notes", "find_notes", "read_note", "append_note", "list_notes",
           "trash_note"]

# Anchored to the repo, not to the working directory. `Path("vault")` would put the vault
# wherever the process happened to start — one place under `python main.py`, a different place
# under a service that starts from its own WorkingDirectory. Same reasoning as `HUD_DIR` in
# orchestrator/hud_bridge.py.
#
# `ODDBALL_VAULT_DIR` overrides it, exactly as in `tools/corrections.py` and
# `tools/reflections.py`, and this module was the ONE of the three that did not honour it —
# which made every harness driving a vault write a writer to LB's real vault. That is L22
# ("a new persistent file makes every existing harness a writer to it") with the file already
# in place and nobody having noticed, and `tools/verify_notes.py` is the harness that would
# have proved it the hard way.
VAULT_DIR = Path(os.environ.get("ODDBALL_VAULT_DIR")
                 or Path(__file__).resolve().parents[1] / "vault")
VAULT_DIR.mkdir(parents=True, exist_ok=True)

# Where a deleted note goes. **A delete is a move, not a shred** — see `trash_note`. Dotted so
# it sorts out of the way in a file manager, and so `notes()` skips it by the general rule
# below rather than by naming this directory specially.
TRASH_DIR = VAULT_DIR / ".trash"

# How much vault text one search may hand back to a prompt. ~24k characters is roughly 6k
# tokens at the 4-chars-per-token rule this repo already uses elsewhere — big enough to hold
# several notes whole, small enough to leave room for the datasheet excerpts the firmware
# agent puts in the same prompt.
MAX_RESULT_CHARS = 24_000

# What may appear in a vault path segment. Everything else becomes an underscore, which turns
# "../../etc/passwd" into a filename rather than a traversal.
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe_segment(text: str, fallback: str) -> str:
    """One filesystem-safe path component. Never empty, never `.` or `..`, never nested."""
    cleaned = _SAFE_SEGMENT.sub("_", (text or "").strip().replace("\\", "/").split("/")[-1])
    cleaned = cleaned.strip(". ").strip()
    return cleaned or fallback


def _resolve(folder: str, filename: str) -> Path:
    """The path to write, guaranteed to be inside `VAULT_DIR`.

    Args:
        folder:   subdirectory the model asked for, e.g. "projects".
        filename: the note's name, with or without a `.md` suffix.

    Returns:
        An absolute path under `VAULT_DIR`.

    Raises:
        ValueError: if the result somehow escaped the vault. Belt and braces over
                    `_safe_segment` — a guard whose only check is the guard above it is a
                    guard nobody notices has stopped working.
    """
    safe_folder = _safe_segment(folder, "notes")
    safe_name = _safe_segment(filename, "note")
    if not safe_name.lower().endswith(".md"):
        safe_name += ".md"

    path = (VAULT_DIR / safe_folder / safe_name).resolve()
    if VAULT_DIR.resolve() not in path.parents:
        raise ValueError(f"refusing to write outside the vault: {path}")
    return path


def notes() -> list[Path]:
    """Every live note in the vault, sorted. **Dot-directories are skipped.**

    The one walk, used by search, listing, resolution and the CLI, so they cannot come to
    disagree about what is in the vault.

    Skipping dotted directories is what makes `trash_note` safe to have built. `read_from_vault`
    used a bare `rglob("*.md")`, so the instant anything moved a note aside it would still be
    found by search and fed into a prompt as current — two versions of one fact reaching one
    model, which is the failure D22 and D23 both exist to prevent. A deleted note that keeps
    answering questions is worse than no delete at all.

    Sorted for the same reason `read_from_vault` sorted before: rglob's order is filesystem
    order and differs between machines, so an unsorted walk makes the same search return
    different things twice running.
    """
    try:
        return sorted(p for p in VAULT_DIR.rglob("*.md")
                      if not any(part.startswith(".") for part in p.relative_to(VAULT_DIR).parts))
    except OSError:                                                    # noqa: BLE001
        LOG.exception("vault WALK failed under %s", VAULT_DIR)
        return []


def write_note(filename: str, content: str, folder: str = "notes",
               replace: bool = False) -> str:
    """Write one vault note. The shared implementation behind `save_to_vault`.

    Args:
        filename: the note's name, with or without `.md`.
        content:  markdown to store.
        folder:   subdirectory inside the vault.
        replace:  overwrite instead of appending. **Not reachable from a model** — see below.

    Returns a sentence, never raises. Same contract as the tool.

    ## Why `replace` exists and why the tool does not expose it

    Appending is right for `save_to_vault`, and the comment below says why: the model does not
    know what is already in a note, so "save the pinout" arriving twice must not lose the first
    one.

    It is exactly wrong for a **derived** note. `tools/syllabus_to_vault.py` regenerates a course
    note from a PDF, and re-uploading a corrected syllabus would otherwise stack the old late
    policy and the new one in the same file, separated by a rule — so a later `read_from_vault`
    returns both and cannot tell which is current. That is the conflicting-data failure D22 and
    D23 exist to prevent, reintroduced inside the vault.

    So the flag is a Python argument on a module function, and the `@tool` wrapper below never
    passes it. A build step can rebuild its own artifact; a model cannot silently erase a note
    LB dictated.
    """
    try:
        filepath = _resolve(folder, filename)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Append, never overwrite. A note is a record, and the model does not know what is
        # already in it — "save the pinout" arriving twice must not lose the first one. The
        # rule separates the entries so a later read can tell where one stops.
        existing = filepath.exists() and not replace
        with open(filepath, "a" if existing else "w", encoding="utf-8") as handle:
            if existing:
                handle.write("\n\n---\n\n")
            handle.write(content.rstrip() + "\n")

        rel = filepath.relative_to(VAULT_DIR.resolve()).as_posix()
        verb = "Rewrote" if replace else "Successfully saved memory to"
        return f"{verb} Vault: {rel}"
    except Exception as exc:                                              # noqa: BLE001
        # Tools in this repo report failure as text rather than raising: an exception here
        # becomes a crash in whichever agent called it, and "I could not write that down" is
        # a far better thing for LB to hear than a traceback read out loud.
        #
        # But the RETURNED string is all the model sees, and the model paraphrases it — so the
        # spoken version of a failure is one sentence with the cause smoothed out of it. The
        # log is the only place the actual errno survives, and a vault that silently fails to
        # write is indistinguishable from one that saved nothing worth reading back.
        LOG.exception("vault WRITE failed: folder=%r filename=%r", folder, filename)
        if isinstance(exc, PermissionError):
            # Called out by name because it is the failure LB is most likely to hit and the
            # least likely to guess: the vault lives under the repo, and a repo deployed by
            # `tar` over ssh can arrive owned by another user or read-only.
            LOG.error("  -> PERMISSION DENIED writing under %s. Check ownership and mode: "
                      "ls -la %s", VAULT_DIR, VAULT_DIR)
            return (f"Failed to write to Vault: permission denied on {VAULT_DIR}. "
                    f"The directory is not writable by the user running me.")
        return f"Failed to write to Vault: {exc}"


@tool
def save_to_vault(filename: str, content: str, folder: str = "notes") -> str:
    """
    Saves persistent long-term memory notes, project specs, or component inventories
    to the local Markdown Vault (`vault/`).
    `filename`: name of the file (e.g., 'robot_arm.md')
    `content`: markdown text to store
    `folder`: subdirectory inside vault (e.g., 'projects', 'components', 'lab_notes')
    """
    # `replace` is deliberately not passed and not in this signature. A model must be able to
    # ADD to a note and never to silently erase one LB dictated. See `write_note`.
    return write_note(filename, content, folder)


@tool
def read_from_vault(search_term: str) -> str:
    """
    Searches the Markdown Vault (`vault/`) for files containing the search_term or matching filenames.
    """
    try:
        needle = (search_term or "").lower().strip()
        if not needle:
            return "No search term was given, so there is nothing to look up in the Vault."

        results: list[str] = []
        used = 0
        truncated = 0

        # `notes()` rather than a bare rglob: sorted, and with the trash excluded. A note LB
        # deleted must stop answering questions — see that function.
        for file in notes():
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue                       # an unreadable note is not a failed search

            if needle not in file.name.lower() and needle not in text.lower():
                continue

            rel = file.relative_to(VAULT_DIR).as_posix()
            block = f"--- File: {rel} ---\n{text}"
            if used + len(block) > MAX_RESULT_CHARS:
                truncated += 1
                continue
            results.append(block)
            used += len(block)

        if not results and not truncated:
            return f"No Vault memories found matching '{search_term}'."

        out = "\n\n".join(results)
        if truncated:
            # Named, not silent. A prompt that was quietly cut is a prompt the model answers
            # confidently from half the evidence.
            out += (f"\n\n[{truncated} more matching note(s) were left out to stay inside the "
                    f"context budget. Narrow the search term to see them.]")
        return out
    except Exception as exc:                                              # noqa: BLE001
        LOG.exception("vault READ failed: search_term=%r under %s", search_term, VAULT_DIR)
        return f"Failed to search Vault: {exc}"


# --------------------------------------------------------------------------------------
# The notebook: resolve a note by name, read one back, list them, throw one away.
#
# These four are what `orchestrator/note_intent.py` and `engine/core.py` drive, and none of
# them involves a model. They live here because this module owns the vault — a sixth file
# holding half the vault's operations is how two of them come to disagree about what a note is.
#
# **Every one of them is a pure-ish function of the filesystem**, so the whole notebook is
# testable with no audio, no key and no network, which is the property that lets
# `tools/verify_notes.py` drive the real `Engine` end to end.
# --------------------------------------------------------------------------------------

# Words that carry no identity when LB names a note out loud. "my regulator choice note" and
# "the regulator choice" are the same note, and a resolver that does not know that makes him
# say the filename instead of the thing.
_NAME_FILLER = frozenset({"my", "the", "a", "an", "note", "notes", "file", "one", "called",
                          "named", "titled", "about", "on"})


def _plain(text: str) -> str:
    """A note name reduced to comparable words. Never empty-safe — callers check.

    **Filler comes off the ENDS only.** A note called `notes on filters.md` keeps its middle;
    stripping filler wherever it appeared would reduce that stem to "filters" and the spoken
    name "my notes on filters note" to the same thing — which happens to match, right up until
    the day two notes reduce to the same word and `find_notes` starts offering LB a choice
    between a file and itself. `orchestrator/note_intent._clean_target` trims the same way, and
    the two have to agree or nothing resolves.
    """
    words = normalise(text).split()
    while words and words[0] in _NAME_FILLER:
        words.pop(0)
    while words and words[-1] in _NAME_FILLER:
        words.pop()
    return " ".join(words)


def _folder_of(path: Path) -> str:
    """The note's folder name, or "" for a note sitting at the top of the vault."""
    parent = path.parent.resolve()
    return "" if parent == VAULT_DIR.resolve() else parent.name


def find_notes(name: str) -> list[Path]:
    """Every note whose name matches `name`. **Never guesses.**

    Args:
        name: what LB called it — "regulator choice", "my amp board note", "ECE350 midterm".

    Returns:
        Exact matches when there are any, otherwise partial ones, otherwise []. The caller
        handles all three counts and they mean different things: **zero says so, one acts, and
        two or more asks which** — the same rule `tools/kicad_parser.py` follows for an
        ambiguous project name, and the one that matters most for `trash_note`, where guessing
        wrong destroys something.

    Exact beats partial outright rather than being merged with it. "amp" would otherwise pull
    in "preamp notes" alongside the note actually called "amp", and offering LB a choice he
    did not need is how a resolver teaches him to stop using short names.
    """
    needle = _plain(name)
    if not needle:
        return []

    exact: list[Path] = []
    partial: list[Path] = []
    for path in notes():
        stem = _plain(path.stem)
        folder = _plain(_folder_of(path))
        if needle in {stem, f"{folder} {stem}".strip()}:
            exact.append(path)
        elif stem and needle in stem:
            partial.append(path)
    return exact or partial


@dataclass(frozen=True)
class NoteView:
    """One note, ready for both channels.

    Args:
        path:    where it is.
        body:    the file, verbatim.
        entries: the body split on the `---` rules `write_note` writes between appends, so
                 "how many times have I added to this" is answerable without a model.
        spoken:  what he says out loud. At most `speakable.MAX_WORDS` words.
    """

    path: Path
    body: str
    entries: list[str]
    spoken: str

    @property
    def rel(self) -> str:
        """The vault-relative path, which is what goes on the card and in the log."""
        return self.path.resolve().relative_to(VAULT_DIR.resolve()).as_posix()


# The rules `write_note` puts between appended entries. A line of three or more dashes and
# nothing else — matched loosely because LB is free to edit these files by hand.
_RULE = re.compile(r"\n\s*-{3,}\s*\n")

# Markdown that is punctuation on screen and noise in the ear. Piper says "hash hash" for a
# heading and "star" for a bullet, so a note an agent wrote through `save_to_vault` — which is
# real Markdown, with headings — is unlistenable read literally. Stripped for SPEECH ONLY;
# `NoteView.body` stays byte-for-byte what is on disk, because the card is the copy LB checks.
_SPEECH_NOISE = re.compile(r"^[#>\s]*[-*+]?\s*|[`*_]+", re.MULTILINE)


def _say(text: str) -> str:
    """One flat line, with the Markdown taken out of it. Speech only, never storage."""
    return " ".join(_SPEECH_NOISE.sub(" ", text).split())


def _join_spoken(entries: list[str]) -> str:
    """Several appended entries, said as consecutive sentences rather than run together.

    `write_note` separates entries with a `---` rule, which is correct on screen and is read
    out as "dash dash dash". Splitting on the rule and re-joining with a full stop is what
    turns a note appended to four times into four sentences instead of one long one.
    """
    said = []
    for entry in entries:
        flat = _say(entry)
        if flat and flat[-1] not in ".!?:;":
            flat += "."
        if flat:
            said.append(flat)
    return " ".join(said)


def read_note(path: Path) -> NoteView:
    """Read one note back. **Verbatim, and never summarised by a model.**

    Args:
        path: a note, normally from `find_notes`.

    Returns:
        A `NoteView`. An unreadable file comes back as a NoteView saying so rather than raising,
        because this is called from inside a turn and "I couldn't read that one" is a far better
        thing for LB to hear than a traceback.

    ## Why the spoken form is a truncation and not a summary

    `memory/speakable.py` exists to make a *retrieved passage* sayable, and it does that by
    scoring sentences and picking the best one. That is the right algorithm for a datasheet
    chunk and the wrong one for this: a note is LB's own words, in the order he chose, and the
    "best" sentence of his own note is not a thing he asked for. Reordering or dropping the
    middle of something he dictated would be the paraphrase problem `tools/corrections.py`
    refuses, arriving on the way out instead of the way in.

    So the whole note is said when it fits, and when it does not he is told **how much there
    is** and read the **most recent** entry — the one he is most likely to be asking about —
    with the complete text going on a card. `MAX_WORDS` is imported rather than restated: at
    the ~160 wpm D32 measured for Piper, 40 words is about 15 seconds of audio, and that
    ceiling belongs in one place.
    """
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        LOG.exception("vault READ failed: %s", path)
        return NoteView(path=path, body="", entries=[],
                        spoken=f"I found that note but couldn't read it: {exc.strerror or exc}.")

    entries = [e.strip() for e in _RULE.split(body) if e.strip()]
    flat = _join_spoken(entries)
    words = flat.split()

    if not words:
        return NoteView(path=path, body=body, entries=entries,
                        spoken="That note is empty.")

    if len(words) <= MAX_WORDS:
        return NoteView(path=path, body=body, entries=entries, spoken=flat)

    # Too long to say. Say the size, then the latest entry, clipped — and the clip is marked
    # out loud, because a sentence that stops early without saying so reads as him losing his
    # place rather than as him being brief.
    #
    # **The budget is measured, not guessed.** It was a constant (`MAX_WORDS - 14`) and the
    # harness caught the result at 41 words against a 40-word ceiling — the framing sentence is
    # longer for a multi-entry note than for a single one, and one subtraction cannot be right
    # for both. Building the frame first and giving the clip what is left over cannot drift.
    head = (f"That note has {len(entries)} entries, {len(words)} words. The latest says:"
            if len(entries) > 1 else
            f"That note is {len(words)} words, too long to read out. It starts:")
    tail = "It's all on screen."

    latest = _say(entries[-1]) if entries else flat
    budget = MAX_WORDS - len(head.split()) - len(tail.split())
    clipped = " ".join(latest.split()[:budget])
    if len(latest.split()) > budget:
        clipped += "…"

    return NoteView(path=path, body=body, entries=entries,
                    spoken=f"{head} {clipped} {tail}")


def append_note(path: Path, content: str) -> str:
    """Add to a note that already exists, at the path `find_notes` resolved.

    Args:
        path:    an existing note.
        content: what LB said, verbatim.

    Returns a sentence, never raises. Same contract as `write_note`.

    **Separate from `write_note` on purpose.** `write_note` takes a *name* and a *folder* and
    rebuilds the path through `_safe_segment`, which is right when a model supplied them and
    wrong here: this path was resolved from the filesystem, and round-tripping it through the
    safer can only change it. A note called `ECE 350.md` re-derived from its own stem is still
    `ECE 350.md` today, and the day the safer changes it would silently start a second file
    beside the one LB meant — with his addition in the wrong one and nothing going red.
    """
    try:
        resolved = path.resolve()
        if VAULT_DIR.resolve() not in resolved.parents:
            raise ValueError(f"refusing to write outside the vault: {resolved}")

        existing = resolved.exists() and resolved.stat().st_size > 0
        with open(resolved, "a", encoding="utf-8") as handle:
            if existing:
                handle.write("\n\n---\n\n")
            handle.write(content.rstrip() + "\n")

        rel = resolved.relative_to(VAULT_DIR.resolve()).as_posix()
        return f"Added to Vault note: {rel}"
    except Exception as exc:                                              # noqa: BLE001
        LOG.exception("vault APPEND failed: %s", path)
        return f"Failed to add to that note: {exc}"


def list_notes(folder: str = "") -> list[Path]:
    """Every note, or every note in one folder.

    Args:
        folder: a folder name as LB said it, matched loosely. "" for the whole vault.

    Returns:
        Sorted paths, trash excluded. Empty is a real answer and the caller says so.
    """
    everything = notes()
    needle = _plain(folder)
    if not needle:
        return everything
    return [p for p in everything if _plain(_folder_of(p)) == needle]


def trash_note(path: Path) -> str:
    """Delete a note — by **moving it to `vault/.trash/`**, never by unlinking it.

    Args:
        path: the note to remove, already resolved and already approved. This function does
              not ask; `engine/core.py` gates it through the same `Pending` an OS command goes
              through, so the resolved path is on a card before the question is asked.

    Returns:
        A sentence, never raises. Same contract as `write_note` and for the same reason.

    ## Why a move rather than a delete

    The vault's own docstring says what it is: things LB decided are worth keeping past the
    conversation window, written only when he asks, never rotated, surviving this program being
    deleted. A voice-triggered destructive operation on *that* store, arriving through
    `tiny.en` — which turned "What is the date?" into "What is today?" — should not be the one
    thing in the repo that cannot be undone.

    A rename costs nothing and buys the whole class of mistake back. The trash is dotted, so
    `notes()` skips it and a deleted note genuinely stops answering questions; recovering one
    is a drag-and-drop in any file manager.
    """
    try:
        resolved = path.resolve()
        root = VAULT_DIR.resolve()
        if root not in resolved.parents:
            raise ValueError(f"refusing to delete outside the vault: {resolved}")
        if TRASH_DIR.resolve() in resolved.parents:
            return "That note is already in the trash."

        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        # The folder is kept in the trashed name rather than as a directory, so two notes
        # called `scratch.md` from different folders cannot land on top of each other.
        folder = _folder_of(resolved)
        dest = TRASH_DIR / f"{stamp}-{folder + '-' if folder else ''}{resolved.name}"
        shutil.move(str(resolved), str(dest))

        rel = resolved.relative_to(root).as_posix()
        LOG.info("trashed %s -> %s", rel, dest.name)
        return f"Moved {rel} to the vault trash as {dest.name}."
    except Exception as exc:                                              # noqa: BLE001
        LOG.exception("vault TRASH failed: %s", path)
        return f"Failed to delete that note: {exc}"


# --------------------------------------------------------------------------------------
# Binding these into an agent.
#
# Three agents get the vault — HARDWARE, FIRMWARE and the GENERAL/persona route — and each one
# had a different shape already: hardware ran a tool table, firmware ran a plain invoke with
# retrieval, persona ran a plain invoke with no tools at all. The two names below are what
# stops that becoming three copies of the same prompt text and three tool-call loops.
# --------------------------------------------------------------------------------------

VAULT_TOOLS = [save_to_vault, read_from_vault]
_BY_NAME = {t.name: t for t in VAULT_TOOLS}

# Appended to the prompt of every agent that gets the tools. Written as rules rather than as a
# description, because the failure mode is not the model being unable to use the vault — it is
# the model narrating that it saved something it never called the tool for.
VAULT_INSTRUCTION = """

LONG-TERM MEMORY (the Vault):
You have two tools, `save_to_vault` and `read_from_vault`, backed by a folder of Markdown
files that survives between sessions.
- Use `save_to_vault` when the user asks you to remember, note, log or write something down,
  or states a decision worth keeping — a part he settled on, a pinout, what is in his drawer.
- For `folder`, **use the folder the user named**, whatever it is — it does not have to exist
  already and it is created on demand. 'projects', 'components' and 'lab_notes' are there if
  he named none and one obviously fits; 'notes' is the fallback. Never talk him out of a
  folder name he chose, and never file into a different one than the one he said.
- Use `read_from_vault` when the user refers to something he told you before and it is not in
  the conversation context above.
- NEVER claim you have saved or recalled something unless the tool actually ran. If you did
  not call the tool, say you have not written it down.
"""


def run_vault_calls(tool_calls: list[dict]) -> list[tuple[str, str]]:
    """Execute whichever vault tools a model asked for.

    Args:
        tool_calls: LangChain's `response.tool_calls` — dicts with "name" and "args".

    Returns:
        One `(tool_name, result_text)` per call that named a vault tool, in order. Calls
        naming anything else are skipped, so an agent with its own tools can pass the whole
        list through and handle the remainder itself.
    """
    out: list[tuple[str, str]] = []
    for call in tool_calls or []:
        chosen = _BY_NAME.get(call.get("name", ""))
        if chosen is None:
            continue
        try:
            out.append((chosen.name, str(chosen.invoke(call.get("args", {})))))
        except Exception as exc:                                          # noqa: BLE001
            # Binding the arguments is what raises here — the tools themselves never do.
            # Logged with the arguments the MODEL chose, because that is the thing under
            # suspicion: a tool that "does not trigger" is usually a tool being called with a
            # field it does not have, and the returned sentence never says which field.
            LOG.exception("vault tool %s failed to run with args=%r",
                          chosen.name, call.get("args", {}))
            out.append((chosen.name, f"That vault tool could not be run: "
                                     f"{type(exc).__name__}: {exc}"))
    return out


def followup_prompt(base_prompt: str, results: list[tuple[str, str]]) -> str:
    """The second pass, after `run_vault_calls` executed something.

    Every agent that gets the vault runs the same bounded two-step: bind the tools, and if the
    model called one, feed the result back and ask again **with the tools unbound**. The
    unbinding is what makes the loop terminate — a model that can still see a tool on the
    second pass can call it again, and there is no natural stopping point in "remember this".

    Args:
        base_prompt: the prompt that produced the tool call, verbatim.
        results:     what `run_vault_calls` returned.

    Returns:
        The prompt for the second, tool-free invoke.
    """
    block = "\n\n".join(f"`{name}` returned:\n{text}" for name, text in results)
    return (
        f"{base_prompt}\n\n"
        f"VAULT TOOL RESULTS — these have ALREADY RUN. Do not call any tool again.\n{block}\n\n"
        "Answer the user now, using only what is above. If a note was saved, confirm that in "
        "one short sentence and say where it went. If notes were read back, answer from them "
        "and say plainly when they do not cover the question. Never state anything the results "
        "above do not contain."
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="inspect the Markdown vault")
    ap.add_argument("--list", action="store_true", help="every note in the vault")
    ap.add_argument("--search", metavar="TERM", default=None, help="search the vault")
    ap.add_argument("--find", metavar="NAME", default=None,
                    help="resolve a spoken note name, as the notebook would")
    ap.add_argument("--read", metavar="NAME", default=None,
                    help="read one note back, showing what he would SAY and what he would SHOW")
    args = ap.parse_args(argv)

    if args.search:
        print(read_from_vault.invoke({"search_term": args.search}))
        return 0

    if args.find or args.read:
        wanted = args.find or args.read
        hits = find_notes(wanted)
        if not hits:
            print(f"  no note matches {wanted!r}")
            return 1
        if len(hits) > 1 and args.read:
            print(f"  {wanted!r} matches {len(hits)} notes — he would ask which:")
        for hit in hits:
            print(f"  {hit.resolve().relative_to(VAULT_DIR.resolve()).as_posix()}")
        if args.read and len(hits) == 1:
            view = read_note(hits[0])
            print(f"\n  SAYS  ({len(view.spoken.split())} words, ceiling {MAX_WORDS}):")
            print(f"  {view.spoken}")
            print(f"\n  SHOWS ({len(view.entries)} entr"
                  f"{'y' if len(view.entries) == 1 else 'ies'}):")
            print("\n".join(f"  | {line}" for line in view.body.splitlines()))
        return 0

    live = notes()
    print(f"  vault: {VAULT_DIR}")
    if not live:
        print("  (empty — nothing has been saved yet)")
    for note in live:
        rel = note.relative_to(VAULT_DIR).as_posix()
        print(f"  {rel:48s} {note.stat().st_size:>7d} bytes")

    # Named rather than hidden. `notes()` skips the trash on purpose, and a listing that showed
    # nothing at all after a delete would look like the note had been shredded.
    trashed = sorted(TRASH_DIR.glob("*.md")) if TRASH_DIR.exists() else []
    if trashed:
        print(f"\n  {len(trashed)} in the trash ({TRASH_DIR}), not searched and not read back:")
        for note in trashed:
            print(f"  {note.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
