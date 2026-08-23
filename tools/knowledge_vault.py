#!/usr/bin/env python3
"""
Module:  knowledge_vault.py
Purpose: Long-term memory as plain Markdown on disk — notes, specs, component inventories.
Author:  LB
Date:    2026-08-21

    python tools/knowledge_vault.py --list
    python tools/knowledge_vault.py --search "robot arm"

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
import re
from pathlib import Path

from langchain_core.tools import tool

LOG = logging.getLogger("oddball.vault")

__all__ = ["VAULT_DIR", "VAULT_TOOLS", "VAULT_INSTRUCTION", "followup_prompt",
           "save_to_vault", "read_from_vault", "run_vault_calls"]

# Anchored to the repo, not to the working directory. `Path("vault")` would put the vault
# wherever the process happened to start — one place under `python main.py`, a different place
# under the systemd unit, which starts from its own WorkingDirectory. Same reasoning as
# `HUD_DIR` in orchestrator/hud_bridge.py.
VAULT_DIR = Path(__file__).resolve().parents[1] / "vault"
VAULT_DIR.mkdir(exist_ok=True)

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

        # Sorted, so the same search returns the same thing twice running. rglob's order is
        # filesystem order and differs between the Pi's SD card and the Windows box.
        for file in sorted(VAULT_DIR.rglob("*.md")):
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
  Choose a `folder` of 'projects', 'components', 'lab_notes' or 'notes'.
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
    args = ap.parse_args(argv)

    if args.search:
        print(read_from_vault.invoke({"search_term": args.search}))
        return 0

    notes = sorted(VAULT_DIR.rglob("*.md"))
    print(f"  vault: {VAULT_DIR}")
    if not notes:
        print("  (empty — nothing has been saved yet)")
        return 0
    for note in notes:
        print(f"  {note.relative_to(VAULT_DIR).as_posix():48s} {note.stat().st_size:>7d} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
