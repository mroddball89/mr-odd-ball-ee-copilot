#!/usr/bin/env python3
"""
Module:  app_launcher.py
Purpose: Open a desktop application on the Pi's screen, from a service that has no screen.
Author:  LB
Date:    2026-08-21

    python tools/app_launcher.py firefox        # on the Pi
    python tools/app_launcher.py --display      # just report what screen it can see

## The bug this exists to fix

Asking him to open Firefox did nothing, and he said "Done." Five things were wrong at once, and
four of them are here:

1. **No display.** `config/oddball.service` is a systemd USER unit that sets no `Environment=`
   at all, and `Linger=yes` starts it at BOOT — before labwc exists. `WAYLAND_DISPLAY` was never
   in his environment and could not have been inherited even by luck. Firefox launched into
   nothing and died.
2. **A blocking capture with a 15-second kill.** `subprocess.run(capture_output=True,
   timeout=15)` blocks until the child exits, and on timeout it *kills* the child. A GUI app
   that did find a display would appear and then die at fifteen seconds — and for those fifteen
   seconds the turn thread, which is also the speech thread, is deaf.
3. **The wrong cgroup.** Anything spawned from the service lands in the service's cgroup, and
   `KillMode=control-group` — deliberate, and the right default for that unit — kills it on the
   next `systemctl --user restart oddball`. The app would not survive a deploy.
4. **Silence reported as success.** Covered in `tools/os_controller.py`; the fix is `Outcome`.

## The shape of the fix: hand the process to systemd and return

Nothing here holds a running application. `systemd-run --user` enqueues a **transient service**
and returns in milliseconds; systemd owns the lifetime from there. So `subprocess.run` with a
capture and a timeout stays, and is now *correct* — the thing being run is a control-plane
command that finishes, not an app.

That means no `Popen`, no `start_new_session`, no orphan reaping, no PID tracking. The three
hardest parts of launching a long-lived process are simply not this program's problem.

**A service, not `--scope`**, and the distinction is not cosmetic:

  - `--scope` runs the command **synchronously** in our own context, so defect 2 would be
    entirely unfixed.
  - A scope is forked from *us*, so it inherits our environment wholesale — including
    `Nice=-5`. Firefox at higher priority than the audio thread, on a four-core Pi, on the one
    unit whose comments say audio must never be starved.
  - `--setenv` on a service becomes `Environment=` on the unit, readable afterwards with
    `systemctl --user show`. A scope's environment is gone the moment it starts.

**`--property=Type=exec` is the honesty guarantee**, and it was measured on the Pi rather than
assumed, because the obvious version of the claim turned out to be wrong:

    case                                        Type=simple   Type=exec
    binary missing entirely                       rc=1          rc=1
    binary present but cannot exec                rc=0  <--     rc=1
      (bad shebang, corrupt ELF, missing .so)

A *missing* binary is caught either way — systemd validates the `ExecStart` path when it loads
the unit, so the widely-repeated "Type=simple returns success for a missing program" is not
true on systemd 257. What `Type=simple` really returns success for is a program that **exists,
is executable, and then fails to exec** — the start job completes at `fork()`, before `execve`,
so the failure happens a millisecond after `systemd-run` has already reported rc=0. That is
"he says he opened it and nothing appears", and it is the realistic case: a package mid-upgrade,
a broken symlink, a missing shared library.

So the two guards are complementary and both are kept: `_which()` catches *not installed*,
`Type=exec` catches *installed but broken*.

**`--collect`** unloads the unit when it exits, so failed launches do not pile up in
`systemctl --user list-units --failed` — the surface LB will be reading to diagnose the *next*
problem. The journal entry survives, which is where the evidence belongs.

## Why the Exec line is parsed here rather than handed to `gio launch`

`gio` is installed on the Pi and `gio launch` handles field codes correctly, so it looks like
the right primitive. It is not, for one reason: **it spawns the app and returns.** It would be
the transient unit's main process, and when it exited systemd's default `KillMode=control-group`
would kill the application it had just started. Exec'ing the app directly makes the *app* the
main process, which is what makes `Type=exec`, the cgroup, and `systemctl --user stop
oddball-app-firefox-…` each mean the thing they appear to mean.

(`gtk-launch` is not installed on this Pi at all, so it was never an option.)

## What he may claim

`Type=exec` proves `execve` succeeded. It does **not** prove a window was mapped — that would
need a compositor query. So the sentence is "Opening Firefox now", a claim about what he did,
and never "Firefox is open", a claim about a screen he cannot see.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
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

__all__ = ["Display", "find_display", "systemd_run_argv", "launch", "launch_app",
           "PINNED_PATH", "TIMEOUT_S"]

# Never the inherited PATH. `~/oddball/hardware/apps.py` set this rule and it is kept: a launch
# must not be steerable by whatever happens to be on PATH when the service starts.
PINNED_PATH = "/usr/local/bin:/usr/bin:/bin"

# systemd-run returns as soon as the start job completes. On this Pi that is ~150ms; ten seconds
# is a generous ceiling that still cannot make the voice loop feel hung.
TIMEOUT_S = 10

# Terminal emulators, in preference order, for entries with `Terminal=true` (htop, vim). Only
# `lxterminal` is installed on this Pi — the others are here so the row does not have to be
# edited on a machine that ships something else.
TERMINALS = ("lxterminal", "x-terminal-emulator", "foot", "xfce4-terminal", "gnome-terminal")

# Unit names may hold alphanumerics and `:-_.\`. Desktop ids like `org.thonny.Thonny` are already
# legal; this exists so a hand-written id or a future entry cannot produce an invalid unit.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")

# --- injection seams ---------------------------------------------------------------------
# The two module-level names a harness rebinds. Everything that touches the operating system
# goes through one of them, which is what lets `tools/verify_launch.py` prove the argv without
# starting a process — the same trick `refuse()` uses to test a blocklist without running it.
_run = subprocess.run
_which = shutil.which


@dataclass(frozen=True)
class Display:
    """Where the screen is, as of right now.

    Args:
        runtime_dir: `XDG_RUNTIME_DIR`. The Wayland socket path is relative to it.
        wayland:     the socket name ("wayland-0"), or "" if none was found.
        dbus:        `DBUS_SESSION_BUS_ADDRESS`, or "" if absent. Never invented.
        home:        the user's home directory.
        seen:        every socket found, for the card. Absence is evidence too.
    """

    runtime_dir: str
    wayland: str = ""
    dbus: str = ""
    home: str = ""
    seen: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.wayland)

    def describe(self) -> str:
        """What he looked at, for the card. This is the difference between a report and an
        apology — "I couldn't find the screen" with no detail is not diagnosable."""
        found = ", ".join(self.seen) if self.seen else "(none)"
        return (f"XDG_RUNTIME_DIR = {self.runtime_dir}\n"
                f"wayland sockets = {found}\n"
                f"DBUS_SESSION_BUS_ADDRESS = {self.dbus or '(unset)'}")


def find_display(environ: dict | None = None) -> Display:
    """Find the compositor socket, at launch time.

    Discovery, not configuration, and the reason is the same one that makes `wake.device
    = "C270"` better than a card index: **the socket name changes when the compositor
    restarts.** labwc picks `wayland-1` after a session restart, so a value baked into
    `oddball.service` or `oddball.toml` is right until the first time LB restarts the desktop
    and then silently wrong forever — presenting as "he stopped being able to open anything."

    Putting it in the unit is wrong for a second reason: the unit starts before labwc exists,
    and its own comments say it deliberately has nothing to do with the screen.

    Args:
        environ: the environment to read. Defaults to `os.environ`; the harness passes a dict.

    Returns:
        A `Display`. Never raises. `usable` is False when no socket was found, and the caller
        must then refuse — launching into a missing display kills the app instantly with an
        error nobody sees, which is indistinguishable from doing nothing.
    """
    env = dict(os.environ if environ is None else environ)

    # Windows has no getuid. D7 requires every harness to run on the authoring box.
    uid = getattr(os, "getuid", lambda: 1000)()
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    home = env.get("HOME") or str(Path.home())

    seen: tuple[str, ...] = ()
    wayland = env.get("WAYLAND_DISPLAY", "")
    if not wayland:
        try:
            seen = tuple(sorted(p.name for p in Path(runtime).glob("wayland-[0-9]")))
        except OSError:
            seen = ()
        # Lowest-numbered socket, matching `tools/measure_face.py`. More than one means more
        # than one compositor; the card lists them all so a wrong pick is visible.
        wayland = seen[0] if seen else ""

    return Display(runtime_dir=runtime, wayland=wayland,
                   dbus=env.get("DBUS_SESSION_BUS_ADDRESS", ""), home=home, seen=seen)


def systemd_run_argv(unit: str, program: str, args: tuple[str, ...],
                     display: Display, description: str) -> list[str]:
    """The transient-unit argv. A pure function: it builds a list and runs nothing.

    Built element by element and never as a string. There is no shell anywhere on this path,
    so an application name cannot become shell syntax no matter what the model wrote.

    Args:
        unit:        the transient unit name, without `.service`.
        program:     absolute path to the binary, already resolved on `PINNED_PATH`.
        args:        the rest of the argv from the desktop entry.
        display:     from `find_display()`. Must be `usable`.
        description: what shows in `systemctl --user show`.

    Returns:
        The argv for `subprocess.run`.
    """
    argv = [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        f"--description={description}",
        "--property=Type=exec",
        # THE fix. Without this line the app has no compositor to connect to and dies silently.
        f"--setenv=WAYLAND_DISPLAY={display.wayland}",
        f"--setenv=XDG_RUNTIME_DIR={display.runtime_dir}",
        # Several toolkits branch on this; without it a client can guess X11, find no DISPLAY
        # and exit. Cheap, and it removes a whole class of "it works for some apps".
        "--setenv=XDG_SESSION_TYPE=wayland",
        f"--setenv=PATH={PINNED_PATH}",
        f"--setenv=HOME={display.home}",
        f"--working-directory={display.home}",
    ]
    # Only if it is really there. Inventing a bus address gets a confusing failure deep inside
    # GTK rather than an honest one here.
    if display.dbus:
        argv.append(f"--setenv=DBUS_SESSION_BUS_ADDRESS={display.dbus}")

    # DISPLAY is deliberately NOT set. Setting it invites a Wayland-capable app onto Xwayland,
    # which on this Pi means worse fractional scaling and a separate clipboard. GDK_BACKEND is
    # never set either — forcing it breaks apps that only ship an X11 build.

    argv.append("--")
    argv.append(program)
    argv.extend(args)
    return argv


def _unit_name(entry_id: str, now: str | None = None) -> str:
    """A transient unit name for this launch.

    Timestamped so two launches of the same app do not collide. Passed in rather than read from
    the clock so the harness can pin it.
    """
    stamp = now or time.strftime("%Y%m%d-%H%M%S")
    return f"oddball-app-{_UNSAFE.sub('-', entry_id)}-{stamp}"


def launch(name: str, environ: dict | None = None, now: str | None = None) -> Outcome:
    """Open the application LB asked for.

    Four things are checked before anything is run, because every one of them is a way to say
    "Done" about a window that never appeared:

        1. the catalogue knows the name        -> unknown-app / ambiguous
        2. the binary exists on the pinned PATH -> not-installed
        3. a compositor socket exists           -> no-display
        4. systemd-run reports a good execve    -> launch-failed

    Args:
        name:    the application, as the model named it ("firefox", "the browser").
        environ: passed to `find_display()`. Defaults to the real environment.
        now:     timestamp for the unit name. Defaults to the clock.

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

    # `shutil.which` handles both shapes an Exec line produces: a bare name is searched along
    # PINNED_PATH, and an absolute path is access-checked directly (it ignores `path=` when the
    # argument has a directory part). An earlier version added an `os.path.exists` fallback for
    # the absolute case — dead code, since which() already did it, and it quietly bypassed the
    # injection seam so the harness went green on Windows and red on the Pi.
    program = _which(app.argv[0], path=PINNED_PATH)
    if program is None:
        # This is the `nautilus` case, caught rather than spoken as success. The desktop entry
        # exists and promises a program the machine does not have.
        return Outcome(ok=False, kind="not-installed", subject=app.name,
                       detail=f"{app.path} runs {app.argv[0]!r}, "
                              f"which is not on {PINNED_PATH}")

    args = app.argv[1:]
    if app.terminal:
        # `Terminal=true` entries are console programs and need an emulator wrapped round them.
        term = next((t for t in TERMINALS if _which(t, path=PINNED_PATH)), None)
        if term is None:
            return Outcome(ok=False, kind="not-installed", subject=app.name,
                           detail=f"{app.name} needs a terminal window and none of "
                                  f"{', '.join(TERMINALS)} is installed")
        args = ("-e", program, *args)
        program = _which(term, path=PINNED_PATH)

    display = find_display(environ)
    if not display.usable:
        # Nothing is run. Launching into a missing display kills the app instantly with an error
        # nobody sees, which is the exact symptom this whole change exists to remove.
        LOG.warning("no wayland socket; refusing to launch %s", app.entry_id)
        return Outcome(ok=False, kind="no-display", subject=app.name, detail=display.describe())

    unit = _unit_name(app.entry_id, now)
    argv = systemd_run_argv(unit, program, args, display,
                            description=f"Mr Odd Ball opened {app.name}")

    try:
        result = _run(argv, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return Outcome(ok=False, kind="launch-failed", subject=app.name,
                       detail=f"systemd-run did not return within {TIMEOUT_S} seconds")
    except Exception as e:                                             # noqa: BLE001
        return Outcome(ok=False, kind="launch-failed", subject=app.name, detail=str(e))

    if result.returncode != 0:
        return Outcome(ok=False, kind="launch-failed", subject=app.name,
                       detail=f"{(result.stderr or '').strip()}\n\n"
                              f"unit: {unit}\njournalctl --user -u {unit}")

    LOG.info("launched %s as %s", app.entry_id, unit)
    return Outcome(ok=True, kind="launched", subject=app.name,
                   detail=f"{' '.join(argv)}\n\nunit: {unit}\njournalctl --user -u {unit}")


@tool
def launch_app(app: str) -> str:
    """
    Opens a desktop application on the Raspberry Pi's SCREEN and leaves it running.

    Use this for any request to open, start, launch, bring up or run a graphical program — a
    browser, an editor, a file manager, a media player. Pass the application's NAME, not a
    command: launch_app(app="firefox").
    """
    return launch(app).text


def main(argv: list[str] | None = None) -> int:
    import sys

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
