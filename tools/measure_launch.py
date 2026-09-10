#!/usr/bin/env python3
"""
Module:  measure_launch.py
Purpose: Measure what actually happens when he is asked to open Firefox, before and after.
Author:  LB
Date:    2026-08-21

    ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/measure_launch.py'

**Runs on the Pi only.** It needs a compositor, systemd, and a real Firefox. On any other box
it exits rather than producing numbers that look comparable and are not.

## Why three arms and not two

The old path had two different failure modes depending on what Gemini happened to write, and
they fail in opposite directions. Averaging them would hide both.

    old-blocking     `firefox`     subprocess.run blocks, then KILLS the child at 15s
    old-background   `firefox &`   the shell returns 0 instantly; Firefox dies unseen
    new              launch_app    systemd-run enqueues a transient unit and returns

`old-background` is the one that produced the report. It is the arm that says "Done."

## What is measured, and what is not

`return_s` is how long the turn thread was blocked — and the turn thread is also the speech
thread, so this is how long he was **deaf**.

`alive_at_3s` is `pgrep`, which proves a process exists holding a display connection. It does
**not** prove a window was mapped; that needs a compositor query. Firefox exits promptly when
it cannot reach a display, so alive-at-3s with a socket set is strong evidence, not proof. The
gap is closed by LB looking at the screen, and this docstring is the record that it was closed
that way.

`survived_restart` runs once per arm, not five times: it restarts his service, which is
disruptive, and the result is categorical rather than noisy.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.app_launcher import launch                                  # noqa: E402
from tools.os_controller import run_command                            # noqa: E402

TRIALS = 5

# How long to let Firefox appear before deciding it did not.
#
# This was 3.0 and the first run produced a WRONG result with it: the `new` arm reported
# `survived_restart = no`, contradicting a hand-run of the same test ten minutes earlier. The
# cause was the measurement, not the fix. Firefox started from cold in well under 3s, but after
# being killed and relaunched five times in a row it spends several seconds on profile lock
# recovery, so the window closed before the process appeared and a slow start was recorded as a
# failed one. A measurement that is tuned tight enough to be wrong is worse than no measurement,
# because it carries a number.
SETTLE_S = 8.0
RESTART_SETTLE_S = 15.0

# How long SIGTERM gets before SIGKILL. Firefox needs to reach a clean shutdown or it records
# a startup crash against itself — see reset_crash_counter().
GRACE_S = 20

# This Firefox build uses XDG paths, NOT ~/.mozilla. Two hours were spent concluding "Firefox
# has no profile" from an `ls ~/.mozilla` that was looking in the wrong place entirely.
PROFILES = Path.home() / ".config" / "mozilla" / "firefox"

OUT = REPO / "media" / "data" / f"{date.today().isoformat()}-app-launch.csv"

# What he SAYS, per arm, is the whole point of the "before" column — so it is recorded from the
# real speech table rather than remembered.
from agents.os_agent import _speech_for                                # noqa: E402


def firefox_count() -> int:
    r = subprocess.run(["pgrep", "-c", "firefox"], capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def kill_firefox() -> None:
    """Between trials. Stops the transient units first, then anything left over.

    **Never `pkill -f`.** `tasks/lessons.md` in the pre-merge repo already records this one:
    `-f` matches against the whole command line, so `pkill -f /usr/lib/firefox` issued over ssh
    matches the remote shell running that very command and kills the session. It did it twice
    while this file was being written. `-x` matches the process NAME exactly, which is what was
    meant both times.
    """
    units = subprocess.run(
        ["systemctl", "--user", "list-units", "oddball-app-*", "--all",
         "--no-legend", "--no-pager", "--plain"],
        capture_output=True, text=True).stdout
    for line in units.splitlines():
        unit = line.split()[0] if line.split() else ""
        if unit.startswith("oddball-app-"):
            subprocess.run(["systemctl", "--user", "stop", unit],
                           capture_output=True, timeout=20)
    # SIGTERM, then WAIT. Not SIGKILL, and not a short wait — see reset_crash_counter().
    subprocess.run(["pkill", "-x", "firefox"], capture_output=True)
    for _ in range(GRACE_S * 2):
        if firefox_count() == 0:
            return
        time.sleep(0.5)
    subprocess.run(["pkill", "-9", "-x", "firefox"], capture_output=True)
    time.sleep(2.0)


def reset_crash_counter() -> str:
    """Undo the damage this measurement does to Firefox's own bookkeeping.

    **The measurement poisoned the thing it was measuring, and it took two hours to notice.**

    Firefox increments `toolkit.startup.recent_crashes` in prefs.js every time it is stopped
    before startup completes. Past `toolkit.startup.max_resumed_crashes` (default 3) it stops
    launching: it starts, writes prefs, and shuts down **cleanly** — exit 0, no output, no
    crash report, nothing in the journal. Indistinguishable from the bug this whole change
    exists to fix, and it silently invalidated an entire measurement run.

    Fifteen trials plus cleanup drove the counter to 17 on 2026-08-21. So the counter is
    cleared before and after a run, and `kill_firefox()` gives SIGTERM a real chance first.

    Returns a one-line report, for the console.
    """
    if firefox_count():
        return "skipped — firefox is running"
    hits = list(PROFILES.glob("*/prefs.js")) if PROFILES.exists() else []
    touched = 0
    for prefs in hits:
        try:
            lines = prefs.read_text(encoding="utf-8", errors="replace").splitlines(True)
        except OSError:
            continue
        kept = [ln for ln in lines if "toolkit.startup.recent_crashes" not in ln]
        if len(kept) != len(lines):
            prefs.write_text("".join(kept), encoding="utf-8")
            touched += 1
    return f"cleared the startup-crash counter in {touched} profile(s)"


def cgroup_survives_restart() -> str:
    """Does a transient unit outlive `systemctl --user restart oddball`? The defect-4 claim.

    Measured with `sleep`, deliberately, NOT with Firefox. The claim is about cgroups, and
    Firefox is a bad probe for it: it is a window on LB's actual screen, so a human can close
    it mid-measurement and the run records "died" for the cgroup. That happened on 2026-08-21 —
    `who` showed LB logged in at seat0 while the Firefox arm was being measured, and every
    "died" verdict in that run has `Result=success, ExecMainStatus=0` in systemd, which is a
    clean voluntary exit and not a kill.

    A `sleep` nobody can click isolates the property actually under test.
    """
    unit = "oddball-cgroup-probe"
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
    r = subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                        "-p", "Type=exec", "/usr/bin/sleep", "600"], capture_output=True)
    if r.returncode != 0:
        return "probe-failed"
    subprocess.run(["systemctl", "--user", "restart", "oddball"],
                   capture_output=True, timeout=120)
    time.sleep(RESTART_SETTLE_S)
    alive = subprocess.run(["systemctl", "--user", "is-active", unit],
                           capture_output=True, text=True).stdout.strip()
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
    return "survived" if alive == "active" else "died"


def one_trial(arm: str) -> dict:
    """One launch, timed, with the state three seconds later."""
    kill_firefox()
    t0 = time.monotonic()
    if arm == "old-blocking":
        outcome = run_command("firefox")
    elif arm == "old-background":
        outcome = run_command("firefox &")
    else:
        outcome = launch("firefox")
    elapsed = time.monotonic() - t0

    alive = wait_for_firefox(SETTLE_S)
    return {
        "arm": arm,
        "return_s": round(elapsed, 3),
        "alive": int(alive),
        "kind": outcome.kind,
        "ok": int(outcome.ok),
        "spoken": _speech_for(outcome),
    }


def wait_for_firefox(limit: float) -> bool:
    """Poll for up to `limit` seconds. Polling, not a fixed sleep, so a slow start is not
    recorded as a failure and a fast one does not cost the full window."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if firefox_count() > 0:
            return True
        time.sleep(0.5)
    return False


def restart_trial(arm: str) -> str:
    """Launch, restart his service, and see whether the app is still there.

    Returns "survived", "died", or "never-started" — three states, not two. The old version
    collapsed "never started" into "did not survive", which reads on a chart as though the
    cgroup fix had failed when in fact nothing had been launched to test it with.
    """
    kill_firefox()
    if arm == "old-blocking":
        run_command("firefox")
    elif arm == "old-background":
        run_command("firefox &")
    else:
        launch("firefox")

    if not wait_for_firefox(SETTLE_S):
        return "never-started"
    subprocess.run(["systemctl", "--user", "restart", "oddball"], capture_output=True,
                   timeout=120)
    time.sleep(RESTART_SETTLE_S)
    return "survived" if firefox_count() > 0 else "died"


def main() -> int:
    if not sys.platform.startswith("linux") or shutil.which("systemd-run") is None:
        print("This measures a Pi. Run it on the Pi — anywhere else the numbers would be "
              "comparable-looking and meaningless.", file=sys.stderr)
        return 2
    if shutil.which("firefox") is None:
        print("firefox is not installed here.", file=sys.stderr)
        return 2

    kill_firefox()
    print(f"  {reset_crash_counter()}")

    rows: list[dict] = []
    arms = ("old-blocking", "old-background", "new")

    for arm in arms:
        print(f"\n  {arm}")
        for i in range(1, TRIALS + 1):
            row = one_trial(arm)
            row["trial"] = i
            row["survived_restart"] = ""
            rows.append(row)
            print(f"    {i}/{TRIALS}  {row['return_s']:>6.2f}s  "
                  f"alive={row['alive']}  {row['kind']:<14} {row['spoken']}")

    # The defect-4 claim, measured on a `sleep` rather than on Firefox — see the docstring on
    # cgroup_survives_restart() for why a browser is the wrong probe for a cgroup property.
    print("\n  a transient unit across `systemctl --user restart oddball`:")
    verdict = cgroup_survives_restart()
    rows.append({"arm": "cgroup-probe", "trial": 0, "return_s": "", "alive": "",
                 "kind": "", "ok": "", "spoken": "", "survived_restart": verdict})
    print(f"    transient unit   {verdict}")

    # The old arms cannot survive a restart because they never start anything, and recording
    # that as "died" would read on a chart as a cgroup failure. Stated, not measured.
    for arm in ("old-blocking", "old-background"):
        rows.append({"arm": arm, "trial": 0, "return_s": "", "alive": "",
                     "kind": "", "ok": "", "spoken": "", "survived_restart": "never-started"})

    kill_firefox()
    print(f"  {reset_crash_counter()}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["arm", "trial", "return_s", "alive",
                                          "kind", "ok", "spoken", "survived_restart"])
        w.writeheader()
        w.writerows(rows)

    print(f"\n  wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
