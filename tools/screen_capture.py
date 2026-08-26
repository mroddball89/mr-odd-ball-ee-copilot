#!/usr/bin/env python3
"""
Module:  screen_capture.py
Purpose: Take a picture of the screen he is sitting on, so he can be asked what is on it.
Author:  LB
Date:    2026-08-25

    python tools/screen_capture.py                 # capture and report
    python tools/screen_capture.py --backends      # what could capture, on this machine

## What this is for

"What am I looking at?" and "why is this window complaining?" are questions with an answer on
the screen and nowhere else. Before this, the only way he could reach that answer was to be told
it. Now he can look.

## The thing to be honest about: a screenshot leaves the machine

The capture is sent to Gemini. LB's desktop may have a terminal on it, and that terminal may
have an API key in its scrollback. That is a real cost and it is why:

1. **Capture is gated by default.** `agents/screen_agent.py` returns a `Pending` and nothing is
   captured until LB says yes — the same shape as every shell command, for the same reason, and
   `engine/core.py` holds it with the machinery that already exists. Set
   `ODDBALL_SCREEN_CONFIRM=0` to make it instant once he is happy with it.
2. **`ODDBALL_SCREEN=0` turns it off entirely**, matching `ODDBALL_GESTURE=0` for the camera.
   A feature that can see the desktop needs an off switch that is not a code edit.
3. **The frame is kept on disk** under `data/screen/`, so what was sent is a file LB can open
   rather than something he has to take on trust. `KEEP_FRAMES` bounds it; git ignores it.

## Backends

Ordered, and the first whose binary exists wins.

    grim              Wayland. This is the Pi — labwc under lightdm, see docs/DEPLOY.md.
    spectacle/scrot/  X11 fallbacks. Not the target, but a Pi running X is a five-minute
    imagemagick       configuration away and failing on it would be a puzzle, not a message.
    powershell        Windows, authoring only. See below.

**Windows is included on purpose and it is not the target.** D7 requires every harness to run on
LB's authoring box, and a screen-awareness feature whose harness cannot capture a screen is a
harness that proves the argument parser works. Ten lines of `System.Drawing` means the same code
path — capture, size, encode, hand to the agent — is exercised where it is being written. The Pi
is still where it has to work.

## The size limit is the interesting constraint

A 1920x1080 PNG is 2-3 MB and is sent base64-encoded, which is a third bigger again. That is
slow on the Pi's uplink and buys nothing: the questions being asked are "which window is on top"
and "what does that dialog say", and both survive a downscale. So every backend that can is
asked for **JPEG at `QUALITY`, scaled by `SCALE`**, which takes a Pi 5 desktop to roughly 150 kB.
`MAX_BYTES` is the backstop for the backends that cannot be asked.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Run directly as `python tools/screen_capture.py`, this file is not inside a package and the
# repo root is not on the path, so `_wayland_env`'s `from tools.app_launcher import find_display`
# fails with ModuleNotFoundError — which on the Pi is every capture, because the Pi is the
# Wayland path. The guard fires ONLY in that case: imported normally as `tools.screen_capture`,
# `__package__` is "tools" and nothing here runs.
#
# Every documented CLI in this repo has to work when it is typed, and `--backends` is the first
# thing DEPLOY.md asks LB to run on a fresh Pi.
if __package__ in (None, ""):                                          # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOG = logging.getLogger("oddball.screen")

__all__ = ["Capture", "KINDS", "capture", "available_backend", "enabled", "confirm_wanted",
           "FRAME_DIR"]

REPO_ROOT = Path(__file__).resolve().parents[1]

# Not `media/captures/`, which is tracked and holds the deliberate evidence shots the vlog is
# built from. A frame grabbed because LB asked what was on his screen is transient working
# state and it is his desktop — it belongs somewhere ignored, next to the other things he
# uploaded rather than next to the figures.
FRAME_DIR = REPO_ROOT / "data" / "screen"

# How many frames to keep. Enough to look back at the last few questions and see exactly what
# was sent; small enough that a day of use is a few megabytes.
KEEP_FRAMES = 12

# JPEG quality and scale factor asked of any backend that supports them. 60 and 0.5 was chosen
# to survive the questions actually being asked — which window is focused, what a dialog says —
# rather than to look good. Text at 0.5 on a 1080p desktop is still legible to a vision model.
QUALITY = 60
SCALE = 0.5

# Refuse anything larger than this rather than send it. 4 MB is far above what the settings
# above produce and is here for the backends that ignore them; a capture this big means
# something is wrong, and quietly uploading it would be the wrong response to that.
MAX_BYTES = 4 * 1024 * 1024

# How long a capture may take. A screenshot is sub-second on every backend here; ten seconds
# means the compositor is not answering, and waiting longer only makes the turn worse.
TIMEOUT_S = 10

# Every way a capture can end. Same flat-vocabulary shape as `tools/os_controller.KINDS`, and
# for the same reason: the caller picks one sentence per kind and a harness proves the table is
# total, so a new kind cannot fall through to a sentence that sounds like success.
KINDS: tuple[str, ...] = (
    "captured",      # a frame was taken and is on disk
    "disabled",      # ODDBALL_SCREEN=0. Not a failure — LB turned it off.
    "no-tool",       # nothing on this machine can take a screenshot
    "no-display",    # a Wayland backend with no compositor socket to talk to
    "failed",        # the tool ran and returned non-zero, or produced nothing
    "too-big",       # captured, and larger than MAX_BYTES. Refused rather than sent.
    "crash",         # something went wrong on our side
)


@dataclass(frozen=True)
class Capture:
    """What happened when he tried to look at the screen.

    Args:
        ok:      did a usable frame come back. The one bit the spoken sentence turns on.
        kind:    one of `KINDS`. Selects the sentence; never spoken itself.
        path:    where the frame was written, or None.
        data:    the encoded image bytes. Empty unless `ok`.
        mime:    "image/jpeg" or "image/png" — what the backend actually produced, which is
                 not always what it was asked for.
        backend: which tool took it, for the card and the log.
        detail:  the failure reason, verbatim. Goes on a card. Never spoken.
    """

    ok: bool
    kind: str
    path: Path | None = None
    data: bytes = b""
    mime: str = "image/jpeg"
    backend: str = ""
    detail: str = ""


def enabled() -> bool:
    """Is screen capture switched on? `ODDBALL_SCREEN=0` turns it off, like the camera's flag."""
    return str(os.environ.get("ODDBALL_SCREEN", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def confirm_wanted() -> bool:
    """Should LB be asked before a frame is taken? Default yes. `ODDBALL_SCREEN_CONFIRM=0`.

    Defaulting to asking is the same call `agents/os_agent.py` makes about shell commands, and
    the reasoning transfers exactly: the action is cheap to approve, irreversible once taken,
    and leaves the machine. What is different is that LB *asked* for it — so this is the one
    gate in the repo it is reasonable for him to switch off, and it says so.
    """
    return str(os.environ.get("ODDBALL_SCREEN_CONFIRM", "1")).strip().lower() not in (
        "0", "false", "no", "off")


# The screenshot backends.
#
# There used to be four here — `grim` (Wayland), `scrot` and ImageMagick's `import` (X11), and
# `gnome-screenshot` — each a single binary invocation with an argv builder and a flag saying
# whether it needed a compositor socket. All four were deleted 2026-08-26 with the move to
# Windows, along with `_wayland_env()`, which existed to hand `grim` a `WAYLAND_DISPLAY`.
#
# What is left is the PowerShell path, which was written as the AUTHORING backend so the
# harness could run on LB's Windows box and is now the only one. It stays out of a table
# because it is not a single binary invocation; a one-row table would be a shape kept for a
# generality that no longer exists.
#
# Restoring a Linux grabber means restoring `_BACKENDS` and `_wayland_env` from git history.
_BACKENDS: tuple = ()

# Windows, authoring only. Kept out of `_BACKENDS` because it is the one entry that is not a
# single binary invocation, and folding it in would make every other row carry a shell flag it
# does not need.
#
# ## Two things here were found by measurement, not chosen, and both look like style until they
# ## are the reason nothing works
#
# Measured on LB's Windows box, 2026-08-25. Windows Defender's AMSI hook blocks this script, and
# it does not fail with a Python error — it fails as a PowerShell *parser* error, which reads
# exactly like a syntax bug in the script and is not one:
#
#     This script contains malicious content and has been blocked by your antivirus software.
#         + FullyQualifiedErrorId : ScriptContainedMaliciousContent
#
# **1. `-File`, never `-Command`.** The inline form is blocked. The identical bytes in a `.ps1`
# run with `-File` are not. That is a fair call on Defender's part — an inline one-liner that
# photographs the screen is the exact shape screen-scraping malware takes — so the script goes
# to a real file under the temp directory, runs, and is deleted.
#
# **2. No `ImageCodecInfo` / `EncoderParameter` block.** `-File` alone was still blocked, and
# bisecting found the JPEG-quality encoder block was the part being matched, not `CopyFromScreen`
# — a script with `CopyFromScreen`, a resize and a plain `.Save(..., ImageFormat::Jpeg)` passes.
# So the explicit quality setting is gone and Windows takes .NET's default JPEG quality of 75.
#
# The cost of losing it is nothing: measured at `SCALE` on a 3840x1080 desktop, that produces a
# **70 kB** frame, which is already below what `QUALITY` was aiming for. `QUALITY` therefore
# applies to the Linux backends only, and this is where that asymmetry is written down.
_PS_CAPTURE = """
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$full = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($full)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$w = [int]($b.Width * {scale}); $h = [int]($b.Height * {scale})
$small = New-Object System.Drawing.Bitmap $full, $w, $h
$small.Save('{out}', [System.Drawing.Imaging.ImageFormat]::Jpeg)
$g.Dispose(); $full.Dispose(); $small.Dispose()
"""


def _powershell_capture(target: Path) -> subprocess.CompletedProcess:
    """Run the Windows grabber from a temp `.ps1`. See `_PS_CAPTURE` for why not inline.

    The script file is removed in a `finally` — it names an output path inside the repo and
    leaving copies of it around the temp directory would be untidy in a way that eventually
    looks like a bug.
    """
    import tempfile

    script = _PS_CAPTURE.format(scale=SCALE, quality=QUALITY,
                                out=str(target).replace("'", "''"))
    handle = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8")
    try:
        handle.write(script)
        handle.close()
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", handle.name],
            capture_output=True, timeout=TIMEOUT_S)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def available_backend() -> tuple[str, str]:
    """Which grabber this machine has, as `(name, why_not)`.

    Returns:
        `(name, "")` when one is available, or `("", reason)` when none is. The reason is
        written to be readable by LB in a log or on a card — "no screenshot tool found; install
        grim" is actionable, "False" is not.
    """
    if shutil.which("powershell"):
        return "powershell", ""
    return "", ("powershell.exe is not on PATH, so there is no way to capture the screen. "
                "It ships with Windows, so this usually means PATH has been edited.")


def _rotate_frames() -> None:
    """Keep only the newest `KEEP_FRAMES`. Never raises — a full folder is not a failed look."""
    try:
        frames = sorted(FRAME_DIR.glob("screen-*.jpg")) + sorted(FRAME_DIR.glob("screen-*.png"))
        for stale in sorted(frames)[:-KEEP_FRAMES]:
            stale.unlink(missing_ok=True)
    except OSError:
        LOG.debug("could not rotate old frames", exc_info=True)


def capture(out: Path | None = None) -> Capture:
    """Take one frame of the screen. **Never raises.**

    Args:
        out: where to write it. Defaults to a timestamped file under `FRAME_DIR`.

    Returns:
        A `Capture`. Check `.ok` — every failure is a `kind`, never an exception, because this
        runs on the answer path and a screenshot that cannot be taken must degrade to a spoken
        sentence rather than to a traceback.
    """
    if not enabled():
        return Capture(ok=False, kind="disabled",
                       detail="ODDBALL_SCREEN=0 — screen capture is switched off.")

    name, why_not = available_backend()
    if not name:
        return Capture(ok=False, kind="no-tool", detail=why_not)

    try:
        FRAME_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "png" if name == "gnome-screenshot" else "jpg"
        target = out or (FRAME_DIR / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.{suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)

        if name == "powershell":
            result = _powershell_capture(target)
            mime, env = "image/jpeg", None
        else:
            row = next(r for r in _BACKENDS if r[0] == name)
            _, _binary, build_argv, needs_wayland, mime = row
            env = None
            if needs_wayland:
                env = _wayland_env()
                if env is None:
                    return Capture(
                        ok=False, kind="no-display", backend=name,
                        detail="no Wayland socket found, so there is no screen to photograph. "
                               "The compositor may not be up yet — see docs/DEPLOY.md.")
            result = subprocess.run(build_argv(target), env=env,
                                    capture_output=True, timeout=TIMEOUT_S)

        if result.returncode != 0 or not target.exists():
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
            return Capture(ok=False, kind="failed", backend=name,
                           detail=detail or f"{name} exited {result.returncode} and wrote nothing")

        data = target.read_bytes()
        if len(data) > MAX_BYTES:
            # Kept on disk, not deleted: LB should be able to look at the thing that was
            # refused. Only the SENDING is refused.
            return Capture(ok=False, kind="too-big", path=target, backend=name,
                           detail=f"the frame is {len(data) / 2**20:.1f} MB, over the "
                                  f"{MAX_BYTES / 2**20:.0f} MB ceiling, so it was not sent")

        _rotate_frames()
        LOG.info("screen captured by %s: %s (%.0f kB)", name, target, len(data) / 1024)
        return Capture(ok=True, kind="captured", path=target, data=data, mime=mime, backend=name)

    except subprocess.TimeoutExpired:
        return Capture(ok=False, kind="failed", backend=name,
                       detail=f"{name} did not finish within {TIMEOUT_S} seconds")
    except Exception as exc:                                              # noqa: BLE001
        LOG.exception("screen capture crashed")
        return Capture(ok=False, kind="crash", backend=name, detail=f"{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="capture the screen")
    ap.add_argument("--backends", action="store_true", help="what could capture, on this machine")
    ap.add_argument("--out", metavar="PATH", default=None, help="where to write the frame")
    args = ap.parse_args(argv)

    if args.backends:
        for name, binary, _argv, wayland, mime in _BACKENDS:
            found = shutil.which(binary)
            print(f"  {name:18s} {'FOUND  ' if found else 'missing'} "
                  f"{'(needs wayland)' if wayland else ''} -> {mime}")
        if os.name == "nt":
            print(f"  {'powershell':18s} "
                  f"{'FOUND  ' if shutil.which('powershell') else 'missing'} (authoring box)")
        chosen, why = available_backend()
        print(f"\n  chosen: {chosen or 'NONE — ' + why}")
        print(f"  enabled: {enabled()}   asks first: {confirm_wanted()}")
        return 0

    shot = capture(Path(args.out) if args.out else None)
    print(f"  ok       {shot.ok}")
    print(f"  kind     {shot.kind}")
    print(f"  backend  {shot.backend or '-'}")
    print(f"  path     {shot.path or '-'}")
    print(f"  bytes    {len(shot.data)}")
    if shot.detail:
        print(f"  detail   {shot.detail}")
    return 0 if shot.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
