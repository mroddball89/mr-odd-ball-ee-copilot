#!/usr/bin/env python3
"""
Module:  measure_face.py
Purpose: A/B the cost of drawing Mr Odd Ball — Chromium kiosk vs a native GTK4 + WebKitGTK
         window — under conditions identical enough that the two numbers are comparable.
Author:  LB
Date:    2026-08-13

Run on the Pi, from the repo root:

    venv/bin/python tools/measure_face.py --duration 60 --state idle \\
        --out media/data/2026-08-13-face-renderer-cost.csv

## What is controlled, and why each one had to be

- **The same rig, the same state.** Both arms load the same `hud/face-preview.html` from the
  same `face_stage.py`, pinned to one state. His states cost different amounts — `speaking`
  drives the jaw every frame, `sleeping` animates three Zs and little else — so an
  uncontrolled state would be measuring the animation, not the renderer.
- **The same pixels.** Both arms run fullscreen. A windowed app compositing 800x800 against a
  kiosk compositing the whole panel is not a comparison, it is a smaller picture.
- **One arm at a time**, with a cooldown between. Two renderers on four cores contend, and a
  contended measurement is wrong rather than conservative — a download once pushed a
  time-to-first-token from 5s to 32.7s in this project.
- **A warmup before sampling.** Both renderers do first-paint work — font loading, shader
  compilation, layer setup — that is real but is not the steady-state cost of watching him
  idle on the desk all day, which is the thing being decided.

## PSS, not RSS — this is the part that would have given a wrong answer

Chromium is ten processes that share a great deal of memory with each other; the GTK spike is
two or three that share almost nothing. **Summing RSS across a process tree counts every
shared page once per process**, so it inflates the many-process program and flatters the
few-process one — exactly the direction that would make this experiment tell us what we
already wanted to hear.

`Pss` from `/proc/PID/smaps_rollup` divides each shared page by the number of processes
mapping it, so a tree's PSS sums to something meaningful. Both figures are recorded, so the
size of that distortion is visible rather than assumed.

## What this does NOT measure

GPU time. Both renderers composite through the Pi's V3D, and nothing here samples it, so a
result that moves work from the CPU to the GPU would look like a free win and would not be
one. The system-wide CPU column is the guard against the most obvious version of that.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
NPROC = os.cpu_count() or 4

# argv patterns that identify each arm's processes. Every browser here forks and reparents to
# init, so its children cannot be found by walking down from the PID we spawn — they are found
# by pattern instead.
#
# `firefox` is the browser arm because it is the one that actually renders his face on this Pi
# (see the note in config/oddball.toml). **Chromium cannot be measured against the rig at all**
# — it fetches the page over http:// and then renders nothing, reporting an empty document
# title to its own DevTools endpoint, while rendering the identical bytes from file:// -->
# perfectly. Both hud_bridge and a stock `python -m http.server` reproduce it, so the fault is
# not in our server. Left here so the arm is not silently re-added by someone who assumes it
# was an oversight; see docs/DECISIONS.md.
ARMS = {
    "firefox": r"firefox",
    "gtk": r"oddball-spike",
    "chromium": r"/chromium",
}


@dataclass
class Sample:
    """One instant of one arm."""
    t_s: float
    procs: int
    pss_mb: float
    rss_mb: float
    cpu_pct_core: float
    labwc_cpu_pct_core: float
    labwc_pss_mb: float
    system_cpu_pct: float
    temp_c: float


@dataclass
class Arm:
    name: str
    samples: list[Sample] = field(default_factory=list)


def pids_matching(pattern: str) -> list[int]:
    """Every PID whose cmdline matches `pattern`. Ours is excluded so the harness that
    carries the pattern on its own command line never measures itself."""
    rx = re.compile(pattern)
    me = os.getpid()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue                     # vanished between listing and reading; normal
        if cmd and rx.search(cmd):
            found.append(pid)
    return found


def proc_jiffies(pid: int) -> int | None:
    """utime + stime for one process, in clock ticks."""
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
    except OSError:
        return None
    # The comm field is parenthesised and may itself contain spaces or brackets, so the split
    # has to start after the LAST ')' rather than at the second field.
    tail = stat[stat.rfind(")") + 2:].split()
    try:
        return int(tail[11]) + int(tail[12])          # utime, stime (fields 14, 15)
    except (IndexError, ValueError):
        return None


def proc_memory_kb(pid: int) -> tuple[int, int]:
    """(Pss, Rss) in KiB for one process. Falls back to statm when smaps_rollup is absent."""
    base = Path("/proc") / str(pid)
    pss = rss = 0
    try:
        for line in (base / "smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                pss = int(line.split()[1])
            elif line.startswith("Rss:"):
                rss = int(line.split()[1])
        return pss, rss
    except OSError:
        pass
    try:
        resident = int((base / "statm").read_text().split()[1])
        rss = resident * PAGE_KB
        return rss, rss              # no PSS available; say so by making them equal
    except (OSError, IndexError, ValueError):
        return 0, 0


def system_jiffies() -> tuple[int, int]:
    """(busy, total) jiffies across all cores, from /proc/stat."""
    fields = [int(x) for x in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
    total = sum(fields)
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)      # idle + iowait
    return total - idle, total


def temperature_c() -> float:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
    except (OSError, ValueError):
        return float("nan")


class CpuTracker:
    """Turns per-PID jiffy counters into a percentage of one core.

    Only PIDs present in BOTH the previous and current reading contribute, because a process
    that appeared mid-interval has no baseline and counting its lifetime total as if it were
    one interval's work would report a spike that never happened.
    """

    def __init__(self) -> None:
        self._prev: dict[int, int] = {}
        self._prev_t = time.monotonic()

    def update(self, pids: list[int]) -> float:
        now = time.monotonic()
        dt = max(now - self._prev_t, 1e-6)
        cur = {}
        delta = 0
        for pid in pids:
            j = proc_jiffies(pid)
            if j is None:
                continue
            cur[pid] = j
            if pid in self._prev:
                delta += max(0, j - self._prev[pid])
        self._prev = cur
        self._prev_t = now
        return delta / CLK_TCK / dt * 100.0


def env_for_display() -> dict[str, str]:
    """The environment a GUI client needs when it is launched from an SSH session."""
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    if "WAYLAND_DISPLAY" not in env:
        run = Path(env["XDG_RUNTIME_DIR"])
        socks = sorted(p.name for p in run.glob("wayland-[0-9]"))
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    return env


def launch(arm: str, url: str, env: dict[str, str]) -> subprocess.Popen:
    if arm == "firefox":
        cmd = ["firefox", "--kiosk", url]
    elif arm == "chromium":
        # Through the distribution's own wrapper, not the binary in /usr/lib: the wrapper is
        # what adds the platform flags this Pi actually runs with, so measuring anything else
        # would be measuring a browser LB never uses.
        cmd = ["chromium", "--kiosk", "--ozone-platform=wayland", url]
    else:
        cmd = [sys.executable, str(REPO / "tools" / "spike_gtk_face.py"),
               "--url", url, "--fullscreen", f"--marker={ARMS['gtk']}"]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def stop(arm: str, proc: subprocess.Popen) -> None:
    """Stop every process of an arm, including the ones its launcher reparented away."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        left = pids_matching(ARMS[arm])
        if not left:
            return
        time.sleep(0.3)
    for pid in pids_matching(ARMS[arm]):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(1.0)


def screenshot(path: Path, env: dict[str, str]) -> bool:
    """Capture the screen, so the rig's own fps meter is on the record.

    Frame rate is the one number that matters here which cannot be read from `/proc`: a
    renderer that halves its memory by dropping half his frames has not won anything. The rig
    already prints its measured fps on screen, so photographing it is the one method that is
    identical for both arms and needs no code inside either of them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["grim", str(path)], env=env, capture_output=True, timeout=15)
        return r.returncode == 0 and path.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def measure(arm_name: str, url: str, warmup: float, duration: float,
            interval: float, env: dict[str, str], shots: Path | None = None) -> Arm:
    """Launch one arm, let it settle, then sample it for `duration` seconds."""
    arm = Arm(arm_name)
    pattern = ARMS[arm_name]

    stale = pids_matching(pattern)
    if stale:
        raise SystemExit(f"{arm_name}: {len(stale)} process(es) already running "
                         f"(pids {stale[:5]}) — stop them first, or the baseline is not one")

    proc = launch(arm_name, url, env)
    print(f"  {arm_name}: launched pid {proc.pid}, warming up {warmup:.0f}s", flush=True)
    time.sleep(warmup)

    pids = pids_matching(pattern)
    if not pids:
        stop(arm_name, proc)
        raise SystemExit(f"{arm_name}: nothing matched {pattern!r} after warmup — it failed "
                         f"to start. Run it by hand to see the error.")
    print(f"  {arm_name}: {len(pids)} process(es), sampling {duration:.0f}s", flush=True)

    app_cpu = CpuTracker()
    labwc_cpu = CpuTracker()
    app_cpu.update(pids)
    labwc_cpu.update(pids_matching("labwc"))
    sys_prev = system_jiffies()
    t0 = time.monotonic()

    while (t := time.monotonic() - t0) < duration:
        time.sleep(interval)
        pids = pids_matching(pattern)
        labwc_pids = pids_matching("labwc")

        pss = rss = 0
        for pid in pids:
            p, r = proc_memory_kb(pid)
            pss += p
            rss += r
        labwc_pss = sum(proc_memory_kb(p)[0] for p in labwc_pids)

        sys_now = system_jiffies()
        d_busy = sys_now[0] - sys_prev[0]
        d_total = max(1, sys_now[1] - sys_prev[1])
        sys_prev = sys_now

        arm.samples.append(Sample(
            t_s=round(time.monotonic() - t0, 2),
            procs=len(pids),
            pss_mb=round(pss / 1024, 1),
            rss_mb=round(rss / 1024, 1),
            cpu_pct_core=round(app_cpu.update(pids), 1),
            labwc_cpu_pct_core=round(labwc_cpu.update(labwc_pids), 1),
            labwc_pss_mb=round(labwc_pss / 1024, 1),
            system_cpu_pct=round(d_busy / d_total * 100, 1),
            temp_c=round(temperature_c(), 1),
        ))

    if shots is not None:
        shot = shots / f"{time.strftime('%Y-%m-%d')}-face-{arm_name}.png"
        print(f"  {arm_name}: {'captured ' + str(shot) if screenshot(shot, env) else 'screenshot FAILED'}",
              flush=True)

    stop(arm_name, proc)
    return arm


def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return float("nan")
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def conditions(state: str, url: str, warmup: float, duration: float) -> dict:
    def out(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5
                                  ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "?"

    rig = REPO / "hud" / "face-preview.html"
    return {
        "taken_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": out("hostname"),
        "model": Path("/proc/device-tree/model").read_bytes().rstrip(b"\0").decode(
            errors="replace") if Path("/proc/device-tree/model").exists() else "?",
        "os": next((l.split("=", 1)[1].strip('"\n') for l in
                    Path("/etc/os-release").read_text().splitlines()
                    if l.startswith("PRETTY_NAME=")), "?"),
        "kernel": out("uname", "-r"),
        "cores": NPROC,
        "compositor": out("labwc", "--version"),
        "chromium": out("chromium", "--version"),
        "python": sys.version.split()[0],
        "rig_sha256": out("sha256sum", str(rig)).split()[0] if rig.exists() else "?",
        "rig_state": state,
        "url": url,
        "warmup_s": warmup,
        "duration_s": duration,
        "loadavg": Path("/proc/loadavg").read_text().split()[:3],
        "note": "both arms fullscreen, one at a time; PSS is the memory figure that counts",
    }


def write_csv(path: Path, arms: list[Arm], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {k}: {v}" for k, v in meta.items()]
    lines.append("arm,t_s,procs,pss_mb,rss_mb,cpu_pct_core,labwc_cpu_pct_core,"
                 "labwc_pss_mb,system_cpu_pct,temp_c")
    for arm in arms:
        for s in arm.samples:
            lines.append(f"{arm.name},{s.t_s},{s.procs},{s.pss_mb},{s.rss_mb},"
                         f"{s.cpu_pct_core},{s.labwc_cpu_pct_core},{s.labwc_pss_mb},"
                         f"{s.system_cpu_pct},{s.temp_c}")
    path.write_text("\n".join(lines) + "\n")


def report(arms: list[Arm]) -> None:
    print("\n" + "=" * 74)
    print(f"{'arm':<10}{'procs':>6}{'PSS MB':>10}{'RSS MB':>10}{'CPU %core':>11}"
          f"{'%machine':>10}{'labwc %':>9}{'sys %':>8}")
    print("-" * 74)
    rows = {}
    for arm in arms:
        if not arm.samples:
            continue
        pss = median([s.pss_mb for s in arm.samples])
        rss = median([s.rss_mb for s in arm.samples])
        cpu = median([s.cpu_pct_core for s in arm.samples])
        rows[arm.name] = (pss, cpu)
        print(f"{arm.name:<10}{arm.samples[-1].procs:>6}{pss:>10.1f}{rss:>10.1f}"
              f"{cpu:>11.1f}{cpu / NPROC:>10.1f}"
              f"{median([s.labwc_cpu_pct_core for s in arm.samples]):>9.1f}"
              f"{median([s.system_cpu_pct for s in arm.samples]):>8.1f}")
    print("=" * 74)
    browser = next((n for n in ("firefox", "chromium") if n in rows), None)
    if browser and "gtk" in rows:
        (bp, bc), (gp, gc) = rows[browser], rows["gtk"]
        if bp and bc:
            print(f"native GTK vs {browser} — memory {gp - bp:+.0f} MB "
                  f"({gp / bp * 100:.0f}% of it), CPU {gc - bc:+.1f} points of one core "
                  f"({gc / bc * 100:.0f}% of it)")
    print("all figures are medians over the sampling window\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arms", default="chromium,gtk", help="comma-separated, in order")
    ap.add_argument("--state", default="idle", help="rig state to pin for both arms")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--warmup", type=float, default=15.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--cooldown", type=float, default=8.0)
    ap.add_argument("--out", type=Path,
                    default=REPO / "media" / "data" / "face-renderer-cost.csv")
    ap.add_argument("--shots", type=Path, default=REPO / "media" / "captures",
                    help="where to put the end-of-window screenshot of each arm")
    args = ap.parse_args(argv[1:])

    names = [a.strip() for a in args.arms.split(",") if a.strip()]
    for n in names:
        if n not in ARMS:
            raise SystemExit(f"unknown arm {n!r}; choose from {', '.join(ARMS)}")

    url = f"http://127.0.0.1:{args.port}/"
    env = env_for_display()
    if "WAYLAND_DISPLAY" not in env:
        raise SystemExit("no Wayland display found — run this on the Pi's own session")

    stage = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "face_stage.py"),
         "--state", args.state, "--port", str(args.port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)
    if stage.poll() is not None:
        raise SystemExit(f"the stage exited immediately — is port {args.port} already taken?")

    print(f"stage: {url} pinned to {args.state!r}")
    results: list[Arm] = []
    try:
        for i, name in enumerate(names):
            if i:
                print(f"  cooling down {args.cooldown:.0f}s")
                time.sleep(args.cooldown)
            results.append(measure(name, url, args.warmup, args.duration,
                                   args.interval, env, args.shots))
    finally:
        stage.terminate()

    meta = conditions(args.state, url, args.warmup, args.duration)
    write_csv(args.out, results, meta)
    report(results)
    print(f"wrote {args.out}")
    print(json.dumps({k: meta[k] for k in ("model", "os", "compositor", "rig_sha256")},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
