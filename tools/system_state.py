#!/usr/bin/env python3
"""
Module:  system_state.py
Purpose: What machine he is on and how it is doing, in a sentence he can be told on every turn.
Author:  LB
Date:    2026-08-25

    python tools/system_state.py
    python tools/system_state.py --prompt

## What it is for

This block rides on **every** agent prompt, so he can answer "what machine are you on", "how
much disk is left" and "can you actually do X" without a routed turn — against D3's measured
**20 requests per model per day**, spending a call to ask the machine about itself is a
meaningful fraction of a day's budget.

## Everything here is free, and that constraint is what shapes the file

`shutil.disk_usage`, two loopback TCP connects with a 50 ms timeout, and a check of which
modules exist on disk. No subprocess anywhere on this path, and `psutil` is not a dependency
and is not being added. Readings are cached for `TTL_S` so an agent that builds two prompts in
one turn — the firmware agent's bounded two-step — reads once rather than twice.

## What was removed on 2026-09-06, and why

CPU temperature, load average, memory and uptime all came from `/proc` and `/sys`. That is a
Linux interface; the Pi was retired on 2026-08-26 and this machine is Windows, where all four
returned `None` on every single turn. The block spent a line of every agent prompt saying
"CPU temperature: you cannot read it on this machine", which is true, honest, and of no use to
anybody several thousand times a day.

They are deleted rather than ported. `vcgencmd`, WMI and OpenHardwareMonitor can all report a
Windows CPU temperature and every one of them costs a subprocess or a COM call on the turn
path — which is the trade this file was written to refuse. If the number earns its place later
it belongs behind the OS route, not in front of every prompt.

**The rule that survives them is the one that mattered**: a missing reading is reported as
missing, never guessed. Nothing here reports a number it did not read.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("oddball.state")

__all__ = ["Snapshot", "read_state", "for_prompt", "TTL_S"]

REPO_ROOT = Path(__file__).resolve().parents[1]

# How long a reading stays fresh. A CPU temperature does not move meaningfully in fifteen
# seconds, and this bounds the cost of the block to one read per turn rather than one per agent
# call. Short enough that "it is getting hot" is still true when he says it.
TTL_S = 15.0

# The two ports this system serves, and what is on them. Named here rather than imported from
# `orchestrator/settings.py` because that loader raises on a malformed config and this module
# must never be the reason a turn fails — it reports what is LISTENING, which is a fact about
# the machine and not about what the config intended.
#
# Kept in step with `config/oddball.toml` [hud] port / upload_port and with `engine/server.py`
# DEFAULT_PORT. `tools/verify_awareness.py` asserts these match the config that ships.
SERVICES: tuple[tuple[int, str], ...] = (
    (8765, "your face and its WebSocket, and the chat panel"),
    (8767, "the file-upload endpoint the paperclip posts to"),
)

# How long to wait for a loopback connect before calling a port closed. 50 ms is enormous for
# 127.0.0.1 — a listening socket answers in microseconds — and it bounds the whole probe at
# 100 ms in the worst case where both are down.
_PROBE_TIMEOUT_S = 0.05

# What he can do, and the file that does it. **Presence is checked on disk rather than
# asserted**, the way `tools/app_catalogue.py` reads apps from the desktop database and
# `route_hint.known_courses` reads codes from the vault. A hand-written capability list is a
# list that lies the first time a module is removed, and it lies in the worst direction: he
# claims an ability he no longer has, and only finds out mid-answer.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("run PowerShell commands on this PC, after LB approves each one", "tools/os_controller.py"),
    ("open desktop applications", "tools/app_launcher.py"),
    ("look at what is on the screen", "tools/screen_capture.py"),
    ("save and search long-term notes in the Markdown vault", "tools/knowledge_vault.py"),
    ("file documents LB uploads — syllabi, datasheets, schematics", "tools/file_manager.py"),
    ("read LB's own KiCad schematics and boards", "tools/kicad_parser.py"),
    ("search datasheets he has uploaded", "tools/vector_db.py"),
    ("read his course calendar and Canvas deadlines", "tools/academic_calendar.py"),
    ("remember corrections LB gives him", "tools/corrections.py"),
    ("record his own mistakes and read them back", "tools/reflections.py"),
)


@dataclass(frozen=True)
class Snapshot:
    """One reading of the machine. Every numeric field is None when it could not be read.

    Args:
        disk_free_gb:   free space on the filesystem holding the repo, in gibibytes.
        disk_total_gb:  its size, in gibibytes.
        host:           hostname.
        system:         "Linux", "Windows".
        listening:      {port: True/False} for each of `SERVICES`.
        capabilities:   the abilities whose module is actually present.
        taken:          monotonic time this snapshot was read, for the cache.
    """

    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    host: str = ""
    system: str = ""
    listening: dict[int, bool] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ()
    taken: float = 0.0

_cache: Snapshot | None = None


def _disk_gb() -> tuple[float | None, float | None]:
    """(free, total) in GiB for the filesystem holding the repo.

    The repo's filesystem, not `/`, because on the Pi they are the same SD card and on LB's
    Windows box they are not — and the space that matters is the space the vault, the inbox and
    the datasheet index are written into.
    """
    try:
        usage = shutil.disk_usage(REPO_ROOT)
        return usage.free / 2**30, usage.total / 2**30
    except OSError:
        return None, None


def _is_listening(port: int) -> bool:
    """Is something serving on 127.0.0.1:`port` right now?

    A connect, not a bind test. Binding to check would race with the real server and, on the
    turn path, could briefly steal the port from the thing being asked about.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(_PROBE_TIMEOUT_S)
            return probe.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def _capabilities() -> tuple[str, ...]:
    """The abilities whose implementing module is present on disk. See `CAPABILITIES`."""
    return tuple(what for what, module in CAPABILITIES if (REPO_ROOT / module).exists())


def read_state(force: bool = False) -> Snapshot:
    """The current machine state, cached for `TTL_S`. **Never raises.**

    Args:
        force: ignore the cache. For the CLI and for harnesses.

    Returns:
        A `Snapshot`. On any failure the fields are None and the snapshot is still returned —
        an assistant that cannot read its own temperature must still be able to answer a
        question about resistors.
    """
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and (now - _cache.taken) < TTL_S:
        return _cache

    try:
        free_gb, size_gb = _disk_gb()
        snapshot = Snapshot(
            disk_free_gb=free_gb,
            disk_total_gb=size_gb,
            host=platform.node(),
            system=platform.system(),
            listening={port: _is_listening(port) for port, _ in SERVICES},
            capabilities=_capabilities(),
            taken=now,
        )
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not read the machine state")
        snapshot = Snapshot(taken=now)

    _cache = snapshot
    return snapshot


def for_prompt() -> str:
    """The block injected into every agent prompt by `tools/self_context.py`.

    Written in the second person and as facts, not as a table. The model is being told what it
    is, so "You are running on Windows, a machine called DESKTOP-XXXXXXX" is the register that
    works;
    a CSV row is not something a persona can speak from.

    **Anything that could not be read is stated as unknown rather than omitted.** An absent line
    reads as "normal" to a model, and "I could not read my temperature" is a different answer
    from "my temperature is fine" — the whole point of the file is that he stops guessing.
    """
    try:
        state = read_state()
        lines = ["\nWHAT YOU ARE RIGHT NOW. Read from this machine just now; it is current."]

        where = f"You are running on {state.system or 'an unknown system'}"
        if state.host:
            where += f", a machine called {state.host}"
        lines.append(where + ".")

        if state.disk_free_gb is not None and state.disk_total_gb is not None:
            lines.append(f"- Disk: {state.disk_free_gb:.1f} GB free of "
                         f"{state.disk_total_gb:.0f} GB on the drive holding your files.")

        if state.listening:
            for port, what in SERVICES:
                up = state.listening.get(port)
                lines.append(f"- Port {port} ({what}): "
                             f"{'running' if up else 'NOT running'}.")

        if state.capabilities:
            lines.append("Things you can actually do, because the code for them is installed: "
                         + "; ".join(state.capabilities) + ".")
            lines.append("Do not offer to do anything that is not on that list, and do not "
                         "refuse anything that is.")

        return "\n".join(lines) + "\n"
    except Exception:                                                     # noqa: BLE001
        LOG.exception("could not build the system state prompt block")
        return ""


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="what machine he is on and how it is doing")
    ap.add_argument("--prompt", action="store_true",
                    help="print the block injected into every agent prompt")
    args = ap.parse_args(argv)

    if args.prompt:
        print(for_prompt())
        return 0

    state = read_state(force=True)
    print(f"  host       {state.host or '?'} ({state.system or '?'})")
    if state.disk_total_gb:
        print(f"  disk       {state.disk_free_gb:.1f} GB free of {state.disk_total_gb:.0f} GB")
    for port, what in SERVICES:
        print(f"  port {port}  {'up  ' if state.listening.get(port) else 'down'}  {what}")
    print(f"  can do     {len(state.capabilities)} of {len(CAPABILITIES)} capabilities installed")
    for able in state.capabilities:
        print(f"             - {able}")
    missing = [what for what, module in CAPABILITIES if not (REPO_ROOT / module).exists()]
    for gone in missing:
        print(f"             ! MISSING: {gone}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
