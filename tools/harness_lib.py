#!/usr/bin/env python3
"""
Module:  harness_lib.py
Purpose: The half of every verify_*.py that was the same in all 39 of them.
Author:  LB
Date:    2026-09-06

    from tools.harness_lib import check, counts, section

## What this replaces

An audit on 2026-09-06 measured the harnesses against each other. `check()` and `section()`
were **defined 39 times**, in six variants of which two covered 37 files, for 422 lines. Around
them sat another ~450 lines of preamble that was identical wherever it appeared: the
`sys.path.insert` that lets a harness run as a script, the UTF-8 `reconfigure` that stops a
Windows console dying on an em-dash, and the `PASSED = FAILED = 0` pair.

That is ~870 lines saying the same thing, and the cost was never the disk space. It was that a
change to how a harness reports had to be made 39 times, and the one that got missed would
not fail — it would just quietly keep the old behaviour.

## Why the counters are an object

`check()` has to increment something the calling harness can read at the end. A module-level
`PASSED` integer cannot do that across a module boundary: `from tools.harness_lib import PASSED`
copies the value once and the caller's name stops tracking. So the counts live on one shared
object and harnesses read `counts.passed` / `counts.failed`.

## What this deliberately does NOT do

**It does not print the summary.** Every harness ends differently — some print a rule and a
count, some print `N/N checks passed - all green`, some print `N RED`, and the exit-code rules
differ too. Those endings are read by a person, and collapsing them into one house style would
change 39 outputs to save a dozen lines. The line between "identical, therefore shared" and
"similar, therefore left alone" is drawn there on purpose.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["check", "section", "counts", "bootstrap", "Counts"]


@dataclass
class Counts:
    """The running tally. One instance, shared, because an int cannot be shared by import."""

    passed: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed

    def reset(self) -> None:
        """Zero the tally. `verify_launch.py` does this between its build and its probe."""
        self.passed = self.failed = 0


counts = Counts()


def bootstrap() -> Path:
    """Make a harness safe to print through. Returns the repo root.

    stdout and stderr reconfigured to UTF-8 with `errors="replace"`. This repo writes em
    dashes, arrows and multiplication signs into check descriptions, and a Windows console in
    its default code page raises `UnicodeEncodeError` on them — failing the harness for a
    reason that has nothing to do with what it was testing.

    **It cannot also own the `sys.path` insert**, however much that belongs here. Importing
    this module is itself the thing that needs the repo root on the path: run as
    `python tools/verify_x.py`, only `tools/` is on `sys.path`, so `from tools.harness_lib
    import ...` raises ModuleNotFoundError before any function in it can run. Each harness
    therefore keeps its own one-line insert ABOVE the import. Tried the other way round on
    2026-09-06; it failed in all 27 files at once.
    """
    root = Path(__file__).resolve().parents[1]

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            # Not a real console, or already wrapped. Both are fine; the point is to try.
            pass

    return root


def check(ok: bool, what: str, detail: str = "") -> None:
    """Record one assertion and print it. Never raises, so a harness always reaches its summary.

    Args:
        ok:     the thing being asserted.
        what:   what it means in words — read by a person deciding whether to care.
        detail: the observed value, printed under the line and only when given. This is the
                field that makes a red check actionable rather than merely red.
    """
    if ok:
        counts.passed += 1
        print(f"   PASS  {what}")
    else:
        counts.failed += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def section(name: str) -> None:
    """A heading between groups of checks."""
    print(f"\n  {name}")
