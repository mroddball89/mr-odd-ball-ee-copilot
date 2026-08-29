#!/usr/bin/env python3
"""
Module:  system_state.py
Purpose: What machine he is on and how it is doing, in a sentence he can be told on every turn.
Author:  LB
Date:    2026-08-25

    python tools/system_state.py
    python tools/system_state.py --prompt

## Why he needs this at all

Ask the running assistant "how hot are you" and, before this file, the answer cost a routed OS
turn: a Gemini call to pick the route, a second to compose `cat /sys/class/thermal/thermal_zone0
/temp`, a permission gate, an execution, and a third call to read the number out loud. Against
D3's measured **20 requests per model per day** that is a meaningful fraction of a day's budget
spent asking the machine about itself.

The number is four bytes in a sysfs file. So it is read directly, for free, and put in front of
every agent — which means he can answer "how hot are you" without a tool call, and, more
usefully, he can *notice*: an answer given while the CPU is at 81 °C and swapping is allowed to
mention that, because now he knows.

## Everything here is free, and that constraint is what shapes the file

This block rides on **every** agent prompt. So every reading is a `read()` of a small file in
`/proc` or `/sys`, plus two loopback TCP connects with a 50 ms timeout. There is no subprocess
anywhere on this path and that is deliberate:

- `vcgencmd get_throttled` would report under-voltage, which is a genuinely valuable Pi fact and
  the single most common cause of "it just froze". It is **deliberately absent** because it
  costs a fork and an exec on the turn path, and a 30 ms tax on every question LB asks is a
  worse trade than a fact he can get by asking for it. If it earns its place later it belongs
  behind the OS route, not here.
- `psutil` is not a dependency and is not being added. Everything below is four files and
  `shutil.disk_usage`.

Readings are cached for `TTL_S` so that an agent that builds two prompts in one turn — the
firmware agent's bounded two-step, for instance — reads `/proc` once rather than twice.

## Windows

LB authors on Windows and the target is the Pi (D7: every harness runs on the authoring box).
None of `/proc` or `/sys` exists there, so every reading comes back `None` and the rendered
block says so plainly rather than inventing a temperature. **A missing reading is reported as
missing.** The one thing this file must never do is give a model a number that is not real —
an assistant that confidently states a CPU temperature it could not read is worse than one that
says it cannot see the sensor.
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
        cpu_temp_c:     CPU temperature in degrees Celsius.
        load_1:         one-minute load average.
        cpu_count:      logical cores, for reading `load_1` against.
        mem_total_mb:   total RAM in mebibytes.
        mem_available_mb: RAM available without swapping, in mebibytes.
        disk_free_gb:   free space on the filesystem holding the repo, in gibibytes.
        disk_total_gb:  its size, in gibibytes.
        uptime_s:       seconds since boot.
        host:           hostname.
        system:         "Linux", "Windows".
        listening:      {port: True/False} for each of `SERVICES`.
        capabilities:   the abilities whose module is actually present.
        taken:          monotonic time this snapshot was read, for the cache.
    """

    cpu_temp_c: float | None = None
    load_1: float | None = None
    cpu_count: int | None = None
    mem_total_mb: int | None = None
    mem_available_mb: int | None = None
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    uptime_s: float | None = None
    host: str = ""
    system: str = ""
    listening: dict[int, bool] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ()
    taken: float = 0.0

    @property
    def mem_used_pct(self) -> float | None:
        if not self.mem_total_mb or self.mem_available_mb is None:
            return None
        return 100.0 * (1 - self.mem_available_mb / self.mem_total_mb)

    @property
    def uptime_phrase(self) -> str:
        """"3 days, 4 hours" — for saying out loud, so no decimals and no seconds."""
        if self.uptime_s is None:
            return ""
        days, rest = divmod(int(self.uptime_s), 86_400)
        hours, rest = divmod(rest, 3_600)
        minutes = rest // 60
        if days:
            return f"{days} day{'s' if days != 1 else ''}, {hours} hour{'s' if hours != 1 else ''}"
        if hours:
            return f"{hours} hour{'s' if hours != 1 else ''}, {minutes} minutes"
        return f"{minutes} minute{'s' if minutes != 1 else ''}"


_cache: Snapshot | None = None


def _read_first_line(path: str) -> str:
    """A small /proc or /sys file's first line, or "" when it is not there."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.readline().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _cpu_temp_c() -> float | None:
    """CPU temperature in Celsius, from sysfs. None off the Pi, or when no zone reads.

    `thermal_zone0` is the SoC on a Pi 5 and this reads it directly rather than scanning every
    zone: on a desktop with a dozen zones the first one is as likely to be a disk controller as
    a core, and a temperature attributed to the wrong sensor is a number that misleads rather
    than one that is missing.
    """
    raw = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
    try:
        # Millidegrees. A bare `45` would be a different unit and a different bug, so the
        # divide is unconditional and a plausibility check catches a file that is not this.
        celsius = float(raw) / 1000.0
        return celsius if -40.0 < celsius < 150.0 else None
    except ValueError:
        return None


def _load_1() -> float | None:
    raw = _read_first_line("/proc/loadavg").split()
    try:
        return float(raw[0]) if raw else None
    except ValueError:
        return None


def _meminfo() -> tuple[int | None, int | None]:
    """(total, available) in MiB, from /proc/meminfo. (None, None) when unreadable."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            fields = {}
            for line in handle:
                name, _, rest = line.partition(":")
                if name in ("MemTotal", "MemAvailable"):
                    fields[name] = int(rest.split()[0]) // 1024      # kB -> MiB
                if len(fields) == 2:
                    break
        return fields.get("MemTotal"), fields.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        return None, None


def _uptime_s() -> float | None:
    raw = _read_first_line("/proc/uptime").split()
    try:
        return float(raw[0]) if raw else None
    except ValueError:
        return None


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
        total_mb, available_mb = _meminfo()
        free_gb, size_gb = _disk_gb()
        snapshot = Snapshot(
            cpu_temp_c=_cpu_temp_c(),
            load_1=_load_1(),
            cpu_count=os.cpu_count(),
            mem_total_mb=total_mb,
            mem_available_mb=available_mb,
            disk_free_gb=free_gb,
            disk_total_gb=size_gb,
            uptime_s=_uptime_s(),
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
    is, so "You are running on Windows, a machine called DESKTOP-3NFU5EK" is the register that
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
        if state.uptime_phrase:
            where += f", up for {state.uptime_phrase}"
        lines.append(where + ".")

        if state.cpu_temp_c is not None:
            hot = " — that is hot; say so if it is relevant" if state.cpu_temp_c >= 75 else ""
            lines.append(f"- CPU temperature: {state.cpu_temp_c:.1f} degrees Celsius{hot}.")
        else:
            lines.append("- CPU temperature: you cannot read it on this machine. Say that "
                         "plainly rather than guessing a number.")

        if state.load_1 is not None:
            cores = f" across {state.cpu_count} cores" if state.cpu_count else ""
            lines.append(f"- Load average over the last minute: {state.load_1:.2f}{cores}.")

        used = state.mem_used_pct
        if used is not None:
            lines.append(f"- Memory: {state.mem_available_mb} MB free of "
                         f"{state.mem_total_mb} MB, about {used:.0f} percent in use.")

        if state.disk_free_gb is not None and state.disk_total_gb is not None:
            lines.append(f"- Disk: {state.disk_free_gb:.1f} GB free of "
                         f"{state.disk_total_gb:.0f} GB on the card holding your files.")

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
    print(f"  uptime     {state.uptime_phrase or 'unknown'}")
    print(f"  cpu temp   {f'{state.cpu_temp_c:.1f} C' if state.cpu_temp_c is not None else 'unreadable here'}")
    print(f"  load (1m)  {state.load_1 if state.load_1 is not None else 'unreadable here'}"
          f"  across {state.cpu_count} cores")
    if state.mem_total_mb:
        print(f"  memory     {state.mem_available_mb} MB free of {state.mem_total_mb} MB")
    else:
        print("  memory     unreadable here")
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
