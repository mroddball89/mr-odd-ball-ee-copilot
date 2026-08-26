#!/usr/bin/env python3
r"""
Module:  app_launcher.py
Purpose: Open an application on LB's desktop, from a process that must not hold it open.
Author:  LB
Date:    2026-08-21 (ported from systemd/Wayland to Windows 2026-08-26)

    python tools/app_launcher.py "visual studio code"
    python tools/app_launcher.py --display      # just report what screen it can see

## The bug this exists to fix

Asking him to open Firefox did nothing, and he said "Done." Five things were wrong at once.
Two of them were about the Pi's display server and are gone with it; the other three are
platform-independent, and every one of them would come straight back if this file were
replaced with a `subprocess.run` and a path:

1. **A blocking capture with a 15-second kill.** `subprocess.run(capture_output=True,
   timeout=15)` blocks until the child exits, and on timeout it *kills* the child. A GUI app
   would appear and then die at fifteen seconds — and for those fifteen seconds the turn
   thread, which is also the speech thread, is deaf. **This is not a Linux problem.** Doing it
   on Windows produces exactly the same fifteen-second-old corpse.
2. **The wrong process tree.** On the Pi, anything spawned from the service landed in the
   service's cgroup and died on the next restart. On Windows the equivalent is a child that
   is killed when its parent exits, or one holding inherited pipe handles open. Same failure,
   different mechanism: the app must not be OUR child in any sense that outlives this call.
3. **Silence reported as success.** Covered in `tools/os_controller.py`; the fix is `Outcome`.

## The shape of the fix: hand the process to the operating system and return

Nothing here holds a running application.

On the Pi this took `systemd-run --user` and a transient unit, with `--property=Type=exec` to
tell "started" from "started and instantly died", `--collect` so failures did not pile up in
`list-units --failed`, and a careful argument about services versus scopes. All of that was
machinery for one goal: **start it, disown it, and know whether the exec worked.**

Windows gives the same three properties in one call. `os.startfile(path)` is `ShellExecuteW`,
which hands the request to Explorer. The launched process is a child of *Explorer*, not of us:
it does not die when this process exits, it inherits none of our handles, and there is no PID
to track or reap. It raises `OSError` when the shell cannot start the thing, which is the
`Type=exec` guarantee — did the launch actually happen — arriving as an exception instead of
as an exit code.

So the three hardest parts of launching a long-lived process are, again, simply not this
program's problem. Roughly 120 lines of systemd argv construction deleted, and nothing that
was protecting anybody went with them.

## Why the shortcut is launched, and not the .exe it points at

`tools/app_catalogue.py` sets `argv[0]` to the `.lnk`, not to its target, and this is the file
that depends on that choice.

A Start Menu shortcut carries more than a path: the arguments, the working directory, the
"run as administrator" flag, and for a Store app an AppUserModelID rather than a file at all.
Reading the target out and executing it directly throws every one of those away — which is how
you get a program that starts with the wrong working directory, or a Store app that cannot be
started by path because there is no path.

`os.startfile` on the `.lnk` makes Windows resolve all of it, exactly as double-clicking the
Start Menu entry would. That is also why there is no field-code parsing here and no
`Terminal=true` handling: `exec_argv()` has no Windows counterpart, because the shortcut
already knows.

## The one guard that survived the port, and the one that could not

`_which()` caught *not installed* — the `nautilus` case, where a desktop entry promised a
program the machine did not have. Its Windows equivalent is `DesktopApp.target`, checked with
`os.path.exists`, and it is kept for exactly the same reason.

**It cannot always run.** 14 of the 41 applications in this machine's catalogue are
shell-namespace links
whose target is an IDList rather than a path, so `target` is `""` and there is nothing to
check. Those still launch correctly — `ShellExecuteW` resolves an IDList fine. Refusing to
launch them because we could not read a path would break every Control Panel entry on the box,
so the check is SKIPPED rather than failed, and `Outcome.detail` says which of the two
happened. A guard that cannot run must say so; a guard that silently passes is worse than none.

## What he may claim

`os.startfile` returning proves the shell accepted the request. It does **not** prove a window
was mapped — that would need a compositor query on the Pi and an `EnumWindows` poll here. So
the sentence stays "Opening Firefox now", a claim about what he did, and never "Firefox is
open", a claim about a screen he cannot see.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import tool

# Run as a script (`python tools/app_launcher.py firefox`) sys.path[0] is `tools/`, not the
# repo root, and the absolute import below fails with "No module named 'tools'". Imported
# normally, `__package__` is "tools" and this does nothing.
if __package__ in (None, ""):                                          # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.os_controller import Outcome                                # noqa: E402

LOG = logging.getLogger("oddball.launcher")

__all__ = ["Display", "find_display", "launch", "launch_app", "start", "TIMEOUT_S"]

# Kept only because `agents/os_agent.py` and the harnesses import it. Nothing here blocks on a
# child any more — `os.startfile` returns as soon as the shell has accepted the request — so
# there is no operation left for a timeout to bound. It is a constant with no remaining reader
# rather than a lie about a wait that happens.
TIMEOUT_S = 10

# --- injection seam -------------------------------------------------------------------------
# The one module-level name a harness rebinds. Everything that reaches the operating system
# goes through it, which is what lets `tools/verify_launch.py` prove the launch without opening
# a window — the same trick `os_controller.refuse()` uses to test a blocklist without running
# anything.
#
# On the Pi these were `_run` and `_which`. They are `_start` and `_exists` now — still two,
# because there are still exactly two questions this module asks the operating system: "is
# that program really there?" and "start this". What went away is the argv between them.
_start = getattr(os, "startfile", None)

# The second seam, and it is a genuine second touchpoint rather than a testing convenience:
# "does this file exist" is a question about the machine, asked at launch time, and it is the
# whole of guard 2. It was `shutil.which` on the Pi and it is `os.path.exists` here, for the
# same reason and with the same seam around it.
_exists = os.path.exists


@dataclass(frozen=True)
class Display:
    """Whether there is a screen to open something on, as of right now.

    A much smaller thing than it was. On the Pi this held `XDG_RUNTIME_DIR`, the Wayland socket
    name, the D-Bus address and every socket found, because the socket name changed whenever
    the compositor restarted and a value baked into a config file was right until the first
    session restart and silently wrong forever after.

    Windows has no such moving part: a logged-in interactive session has a desktop, and
    `GetSystemMetrics(SM_CMONITORS)` counts the monitors attached to it.

    **The `no-display` outcome is kept even so**, and not out of sentiment. It is reachable
    here: a Remote Desktop session that has been disconnected rather than logged off still runs
    processes and has no console to draw on, and that is precisely when he would announce
    "Opening Firefox now" about a window nobody will ever see.

    Args:
        monitors: how many display monitors the session has. 0 means nothing to draw on.
        detail:   what was looked at, for the card.
    """

    monitors: int = 0
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.monitors > 0

    def describe(self) -> str:
        """What he looked at, for the card. The difference between a report and an apology —
        "I couldn't find the screen" with no detail is not diagnosable."""
        return self.detail or f"monitors = {self.monitors}"


def find_display(environ: dict | None = None) -> Display:
    """Is there a screen, at launch time?

    Discovery rather than configuration, which is the one principle that carried over intact
    from the Wayland version: the answer can change while he is running (a monitor is unplugged,
    an RDP session disconnects), so it is asked at the moment it is needed and never cached.

    Args:
        environ: accepted and ignored on Windows; the Pi read the compositor socket out of it.
                 Kept in the signature because `launch()` passes it and the harness pins it.

    Returns:
        A `Display`. Never raises. `usable` is False when there is nothing to draw on, and the
        caller must then refuse — launching into a session with no desktop starts a process
        nobody can see, which is indistinguishable from doing nothing.
    """
    if sys.platform != "win32":                                        # pragma: no cover
        # The Pi's path is gone. Say so honestly rather than returning a plausible answer.
        return Display(monitors=0, detail=f"no display detection for {sys.platform}")

    try:
        import ctypes

        SM_CMONITORS = 80
        SM_REMOTESESSION = 0x1000
        user32 = ctypes.windll.user32
        monitors = int(user32.GetSystemMetrics(SM_CMONITORS))
        remote = bool(user32.GetSystemMetrics(SM_REMOTESESSION))
    except Exception as exc:                                           # noqa: BLE001
        # A failure to ASK is not a failure to have a screen. Assume one monitor and say what
        # went wrong: refusing every launch because a ctypes call misbehaved would be a much
        # worse bug than the one this function exists to prevent.
        LOG.warning("could not count monitors (%s) — assuming a display is present", exc)
        return Display(monitors=1, detail=f"monitor count unavailable ({exc}); assumed present")

    detail = f"monitors = {monitors}"
    if remote:
        detail += "\nsession = Remote Desktop"
    return Display(monitors=monitors, detail=detail)


def start(path: str) -> None:
    """Hand `path` to the shell. The entire operating-system surface of this module.

    Wrapped in a function of its own so that the injection seam has exactly one call site and
    the harness has exactly one thing to replace. `os.startfile` exists only on Windows, so the
    attribute is looked up rather than called directly — importing this module on the Pi must
    not fail, because `tools/verify_launch.py` runs there too.

    Raises:
        OSError: the shell could not start it. This is the honesty guarantee — the equivalent
                 of `Type=exec` on the Pi, which was what distinguished "started" from
                 "started and instantly died".
        RuntimeError: this platform has no `os.startfile`.
    """
    if _start is None:                                                 # pragma: no cover
        raise RuntimeError(f"os.startfile is Windows-only and this is {sys.platform}")
    _start(path)


def launch(name: str, environ: dict | None = None, now: str | None = None) -> Outcome:
    """Open the application LB asked for.

    Four things are checked before anything is started, because every one of them is a way to
    say "Done" about a window that never appeared:

        1. the catalogue knows the name          -> unknown-app / ambiguous
        2. the shortcut's target exists          -> not-installed   (skipped when unreadable)
        3. there is a screen to open it on       -> no-display
        4. the shell accepted the request        -> launch-failed

    Args:
        name:    the application, as the model named it ("firefox", "the browser").
        environ: passed to `find_display()`. Defaults to the real environment.
        now:     accepted and ignored. The Pi used it to timestamp a transient unit name; there
                 are no unit names now. Kept so existing callers and harnesses do not change.

    Returns:
        An `Outcome`. `kind="launched"` is the only success. Never raises.
    """
    # Imported here, not at module scope, so a broken catalogue costs the launch feature rather
    # than the whole OS route — `~/oddball/hardware/actions.py` set this pattern.
    from tools.app_catalogue import load_catalogue, resolve

    catalogue = load_catalogue()
    match = resolve(name, catalogue)

    if match.ambiguous:
        names = "\n".join(f"  {a.name}" for a in match.candidates)
        return Outcome(ok=False, kind="ambiguous", subject=name,
                       detail=f"{name!r} matches more than one application:\n{names}")
    if not match.ok:
        listing = "\n".join(f"  {a.name}" for a in catalogue) or "  (the catalogue is empty)"
        return Outcome(ok=False, kind="unknown-app", subject=name,
                       detail=f"No application matches {name!r}.\n\nHe can open:\n{listing}")

    app = match.app

    # GUARD 2. The `nautilus` case: the entry exists and promises a program the machine does
    # not have. `target` is "" for a shell-namespace shortcut, and then there is nothing to
    # check — see the module docstring. Skipped, never silently passed.
    checked = "not checked (this shortcut names no local path)"
    if app.target:
        if not _exists(app.target):
            return Outcome(ok=False, kind="not-installed", subject=app.name,
                           detail=f"{app.path}\npoints at {app.target!r}, "
                                  f"which is not on this machine")
        checked = f"target verified: {app.target}"

    # GUARD 3.
    display = find_display(environ)
    if not display.usable:
        # Nothing is started. Launching into a session with no desktop starts a process nobody
        # can see, which is the exact symptom this whole module exists to remove.
        LOG.warning("no display; refusing to launch %s", app.entry_id)
        return Outcome(ok=False, kind="no-display", subject=app.name, detail=display.describe())

    # GUARD 4. The shortcut, not its target — see the module docstring.
    target = app.argv[0] if app.argv else app.path
    try:
        start(target)
    except OSError as exc:
        return Outcome(ok=False, kind="launch-failed", subject=app.name,
                       detail=f"the shell refused to start\n  {target}\n\n{exc}")
    except Exception as exc:                                           # noqa: BLE001
        return Outcome(ok=False, kind="launch-failed", subject=app.name, detail=str(exc))

    LOG.info("launched %s via %s", app.entry_id, target)
    return Outcome(ok=True, kind="launched", subject=app.name,
                   detail=f"{target}\n\n{checked}\n{display.describe()}")


@tool
def launch_app(app: str) -> str:
    """
    Opens a desktop application on this PC's SCREEN and leaves it running.

    Use this for any request to open, start, launch, bring up or run a graphical program — a
    browser, an editor, a file manager, a media player. Pass the application's NAME, not a
    path or a command: launch_app(app="firefox").
    """
    return launch(app).text


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if not args or args[0] == "--display":
        d = find_display()
        print(f"\n{d.describe()}\n\nusable: {d.usable}\n")
        return 0 if d.usable else 1

    for name in args:
        outcome = launch(name)
        print(f"\n  {name!r} -> {outcome.kind}  (ok={outcome.ok})")
        print("  " + outcome.detail.replace("\n", "\n  "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
