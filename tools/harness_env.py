#!/usr/bin/env python3
"""
Module:  harness_env.py
Purpose: One import that points a harness at a temp directory instead of at LB's real data.
Author:  LB
Date:    2026-09-04

    python tools/harness_env.py            # what the current process would resolve to

## The leak this closes, for the third time

`tools/verify_notes.py` was writing its own test utterances into the conversation log until
2026-08-29. `tools/verify_academic.py` was doing the same and was found a day later.
`tools/verify_engine.py` stubs the whole module out. Three harnesses, three different hand-rolled
fixes, and on 2026-09-04 the conversation log was measured **60% test fixture** — 24 of 40 turns
were "quiz me on underwater basket weaving" and "something no free tier can answer about quantum
widgets", written by the quiz harnesses added on 2026-09-02, which knew nothing about any of it.

That is tasks/lessons.md L22 arriving for the third time, and it will arrive a fourth time by the
same route: **a harness does not have to mention a file to write to it.** It drives `Engine.ask`,
`Engine.ask` calls `add_message`, and the harness author never sees the word "memory" anywhere.

So the fix cannot be "remember to set the variable". It is two things:

    isolate()                  what a harness CALLS, explicitly, to get a temp everything
    running_under_harness()    what memory_manager ASKS, so forgetting to call isolate()
                               still cannot reach conversation_memory.json

The second is the one that closes the hole. The first is the one that makes the temp directory
named, cleaned up, and shared between the vault and the log so a harness gets a coherent world
rather than two unrelated temp paths.

## Why detection is allowed to be crude

`running_under_harness()` looks at the name of the script that was run. That is a heuristic, and
a heuristic guarding production data usually deserves suspicion — so note which way it fails.
A false positive sends a harness's writes to a temp file, which is what the harness wanted. A
false negative leaves it exactly where it is today. **It has no failure mode that is worse than
the status quo**, which is the property that makes a crude test acceptable here.

`ODDBALL_HARNESS=1` forces it on, `ODDBALL_HARNESS=0` forces it off, for the cases the name
cannot see: a harness that runs under an unusual launcher, and a diagnostic that genuinely wants
to read LB's real log.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

LOG = logging.getLogger("oddball.harness")

__all__ = ["isolate", "running_under_harness", "harness_memory_file", "PREFIXES"]

# A script whose name starts with one of these is a harness. `verify_` and `measure_` are the
# repo's two conventions (27 and 11 files); `test_` catches the three at the repo root and
# anything pytest collects.
PREFIXES = ("verify_", "measure_", "test_")

# The two variables that redirect persistent state. `ODDBALL_VAULT_DIR` covers three files —
# `knowledge_vault`, `reflections.LEDGER` and `corrections.LEDGER` all resolve against it — which
# is exactly why it was given a single name in the first place.
_VAULT_VAR = "ODDBALL_VAULT_DIR"
_MEMORY_VAR = "ODDBALL_MEMORY_FILE"

# Set once per process. `isolate()` is idempotent so a harness that imports another harness does
# not get two temp worlds, and the second call does not silently move the first one's files.
_ISOLATED: Path | None = None
_AUTO_MEMORY: str = ""


def running_under_harness() -> bool:
    """True when this process looks like a test or a measurement, not like the assistant.

    Never raises. Reads `sys.argv[0]`, `sys.modules` and one environment variable; any of them
    being strange resolves to False, which is the status quo.
    """
    try:
        forced = os.environ.get("ODDBALL_HARNESS", "").strip().lower()
        if forced:
            return forced not in ("0", "no", "false", "off")

        # pytest imports before it runs anything, so this catches a harness collected as a test
        # even when argv[0] is the pytest launcher.
        if "pytest" in sys.modules:
            return True

        name = Path(str(sys.argv[0] or "")).name.lower()
        if name in ("pytest", "py.test", "pytest.exe"):
            return True
        return name.startswith(PREFIXES)
    except Exception:                                                     # noqa: BLE001
        return False


def isolate(name: str = "harness") -> Path:
    """Point the vault and the conversation log at a fresh temp directory. Returns the directory.

    **Call this BEFORE importing anything under `tools/`.** `knowledge_vault.VAULT_DIR`,
    `reflections.LEDGER`, `corrections.LEDGER` and `memory_manager.MEMORY_FILE` are all module
    constants read at import time, so setting the environment afterwards rebinds nothing and
    every write still lands in LB's real data. That ordering requirement is the one thing this
    function cannot enforce for the caller, which is why `running_under_harness()` exists as the
    backstop underneath it.

    An environment variable that is ALREADY set is left alone. A harness that wants its vault
    somewhere specific — `verify_notes.py` keeps fixtures next to the ledger — stays in charge of
    its own directory, and this only fills in what nobody asked for.

    Args:
        name: goes in the temp directory's name, so the temp folder is readable when several
              harnesses have been run and one of them left a directory behind.

    Returns:
        The temp directory. It is removed at interpreter exit; a harness that wants it gone
        sooner can remove it itself and nothing here will mind.
    """
    global _ISOLATED
    if _ISOLATED is not None:
        return _ISOLATED

    tmp = Path(tempfile.mkdtemp(prefix="oddball-" + str(name or "harness") + "-"))
    os.environ.setdefault(_VAULT_VAR, str(tmp))
    os.environ.setdefault(_MEMORY_VAR, str(tmp / "harness_memory.json"))

    atexit.register(shutil.rmtree, str(tmp), True)
    LOG.debug("harness isolated: vault=%s memory=%s",
              os.environ[_VAULT_VAR], os.environ[_MEMORY_VAR])

    # Return where the vault ACTUALLY is, which is not `tmp` when the caller had already set
    # `ODDBALL_VAULT_DIR` — `setdefault` left theirs in place. Handing back a directory nothing
    # points at is how a harness writes fixtures into one folder and reads them from another.
    _ISOLATED = Path(os.environ[_VAULT_VAR])
    return _ISOLATED


def harness_memory_file() -> str:
    """A temp path for the conversation log when this process is a harness, else "".

    Called by `tools.memory_manager` at import time, and it is the whole backstop: a harness that
    never heard of this module still cannot append to `conversation_memory.json`. The path
    is stable
    for the life of the process, so a harness that writes a turn and reads it back still works —
    it is a redirect, not a mute, and a mute would break the harnesses that assert on what was
    remembered.

    Never raises. Returning "" means "no opinion", and the caller falls back to the real file.
    """
    global _AUTO_MEMORY
    try:
        if not running_under_harness():
            return ""
        if _AUTO_MEMORY:
            return _AUTO_MEMORY

        tmp = Path(tempfile.mkdtemp(prefix="oddball-automem-"))
        _AUTO_MEMORY = str(tmp / "harness_memory.json")
        atexit.register(shutil.rmtree, str(tmp), True)

        # PUBLISHED, not just returned. Five agents import `format_memory_for_llm` at module
        # scope, so merely importing one of them resolves `MEMORY_FILE` through here — and a
        # later `isolate()` would then `setdefault` the variable to a DIFFERENT temp directory.
        # `memory_manager.MEMORY_FILE` and `ODDBALL_MEMORY_FILE` would disagree, and a harness
        # looking for its own turns under the path `isolate()` handed back would find nothing.
        os.environ.setdefault(_MEMORY_VAR, _AUTO_MEMORY)

        # WARNING, not debug. A harness silently writing somewhere other than where its author
        # thinks is the failure this module exists to prevent, and the cure must not be silent
        # in the same way the disease was.
        LOG.warning("harness detected (%s) — the conversation log is redirected to %s",
                    Path(str(sys.argv[0] or "?")).name, _AUTO_MEMORY)
        return _AUTO_MEMORY
    except Exception:                                                     # noqa: BLE001
        return ""


def main() -> int:
    print("  argv[0]                " + repr(sys.argv[0]))
    print("  running_under_harness  " + str(running_under_harness()))
    print("  " + _VAULT_VAR.ljust(21)
          + (os.environ.get(_VAULT_VAR) or "(unset - the real vault)"))
    print("  " + _MEMORY_VAR.ljust(21)
          + (os.environ.get(_MEMORY_VAR) or "(unset - conversation_memory.json)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
