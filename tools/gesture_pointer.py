#!/usr/bin/env python3
"""
Module:  gesture_pointer.py
Purpose: Drive the desktop pointer from a pinch. Motion and dragging ONLY - it cannot click.
Author:  LB
Date:    2026-08-23

    .venv-gesture/bin/python tools/gesture_pointer.py            run it
    .venv-gesture/bin/python tools/gesture_pointer.py --dry-run  print, inject nothing
    .venv-gesture/bin/python tools/gesture_pointer.py --check    can this box do it at all
    .venv-gesture/bin/python tools/gesture_pointer.py --gain 700 slower pointer

Holds the camera open, reads `GestureRecognizer.track()` every frame, and turns a `Motion` into
a virtual mouse. A pinch grabs, moving the pinch drags, two pinches scroll. That is the whole
feature.

## Two backends, because two operating systems say no in different ways

**On the Pi (labwc/Wayland).** Wayland deliberately forbids one client from moving another
client's windows - a security property of the display server, not a gap to patch. `wmctrl`
reaches only XWayland windows; `wtype` does keyboard and no pointer; `ydotool` is not packaged
for that Debian at all. So this goes UNDER the compositor instead of arguing with it.
`evdev.UInput` creates a virtual input device in the kernel, and labwc sees an ordinary mouse -
indistinguishable from the real one, on native Wayland and XWayland windows alike.
`/dev/uinput` is root-only as shipped; `docs/DEPLOY.md` records the udev rule that opens it to
the `input` group.

**On Windows.** There is no compositor to go under and no capability model to lean on:
`SendInput` moves the cursor and that is the whole story. `tools/win_input.py` is the emitter,
and it is a much more careful file than that makes it sound, because guarantee 1 below is the
one thing the Pi got from the kernel for free and Windows does not offer at all. Read its
header before touching it.

Both backends present the same four methods - `move`, `button`, `wheel`, `close` - so
everything below this line, including every guard, is platform-free. That is deliberate: a
safety property that has to be re-read on two code paths is a safety property with two chances
to be wrong.

# ==========================================================================================
# THE SECURITY MODEL - read this before changing anything below
# ==========================================================================================

A synthetic pointer that can click is a way to answer questions that were meant for a human.
This repo already goes to some trouble over that: `tools/gesture_control.py` will approve a
shell command on a thumbs up, and `engine/turn.py` accepts an `approve` click from the HUD. A
gesture-driven mouse that could press buttons would route straight around both.

So it cannot press buttons. Not by policy, and not by a list of windows it declines to touch -
**by what the device is physically able to emit.** Four guarantees, in descending order of how
much they would hurt to lose:

## 1. It cannot type - and on Windows this is WEAKER than it was on the Pi

This matters more than anything else here, because the security gate in `agents/os_agent.py`
is a terminal prompt reading `input()`. Approving it means typing `y` and pressing Enter. A
pointer that cannot type cannot answer it, however wrong the rest of this file goes.

**On the Pi it was enforced by the kernel.** `capabilities()` below declares `REL_X`, `REL_Y`,
`REL_WHEEL` and `BTN_LEFT`. That is the entire device. It has **no `EV_KEY` capability for any
keyboard key**, so there is no code path - including a bug in this file - by which it can type
a character. The guarantee did not depend on this code being correct.

**On Windows it is enforced by inspection instead, and that is a real downgrade.** `SendInput`
is one call that takes mouse structures or keyboard structures; there is no capability to
withhold. What `tools/win_input.py` does instead is refuse to declare the keyboard struct at
all, so typing is not something this process can do wrong - it is something this process has
no vocabulary for. `python tools/win_input.py --check` prints the union's members and one
member is the proof.

That is the smallest surface the guarantee reduces to on this platform. It is not the same
promise, and the difference is written down rather than glossed: on the Pi a bug could not
type; on Windows an *edit* could. Guarantees 2, 3 and 4 are unaffected - they were always
logic in this file, and they run identically on both.

## 2. A press and a release can never happen in the same place, so it cannot click

A click is a press and a release at one position. Toolkits activate a button on a release that
lands on the same widget as the press - so if press and release are always far apart, no widget
ever activates.

    * the button is pressed only after the pinch has already travelled DRAG_ARM_PX
    * before releasing, if the pointer is within CLICK_GUARD_PX of where it was pressed, the
      daemon MOVES IT CLICK_GUARD_PX AWAY and only then releases

`CLICK_GUARD_PX` is 160, comfortably wider than any confirmation button on this screen. The
displacement on release looks slightly odd in use; that oddity is the guarantee, and it is
cheap. Dragging still works, because a real drag has already moved much further than this.

## 3. Only the left button exists

No `BTN_RIGHT`, no `BTN_MIDDLE`. It cannot open a context menu, so it cannot reach a menu item.

## 4. It gets out of the way of the security gate

The camera is a single resource and this daemon holds it. `tools/gesture_control.py` opens
`VideoCapture(0)` for each approval, so a running daemon would make every approval read
`NO_CAMERA` and fall to the keyboard - safe, but it would quietly delete the feature.

`PAUSE_FILE` is the handshake. While it exists the daemon releases the camera, releases any held
button, and injects nothing at all. `gesture_control._ask_sidecar()` writes it before spawning
its reader and removes it after. **So while an approval is on screen, this daemon is inert** -
which is exactly when a synthetic pointer would be most dangerous.

## What is deliberately NOT built

**Zoom by Ctrl+scroll.** Holding Ctrl needs a keyboard capability on the device, and guarantee 1
is worth more than convenience. Two-hand scale emits a plain wheel, which already zooms in the
applications that treat scroll that way.

**Rotation.** There is no pointer event for it. `track()` measures it and it is written to the
state file, so whatever wires this up later can use it; nothing is injected.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LOG = logging.getLogger("oddball.pointer")

# The handshake with the security gate. See guarantee 4.
PAUSE_FILE = REPO_ROOT / "data" / "gesture_pointer.pause"
STATE_FILE = REPO_ROOT / "data" / "gesture_pointer.state"

# Pixels of pointer travel per palm span of hand travel. A palm span is roughly the width of
# LB's hand, so at 900 a hand movement of about 8 cm crosses most of the screen.
POINTER_GAIN = 900.0

# Guarantee 2. Both in pixels of pointer travel.
DRAG_ARM_PX = 12.0        # the pinch must travel this far before the button goes down
CLICK_GUARD_PX = 160.0    # press and release are always at least this far apart

# Wheel clicks per unit of log-scale change. Two hands moving apart by a factor of e is
# ~4 clicks of wheel.
WHEEL_GAIN = 4.0

# A frame that asks for more than this many pixels is a tracking glitch, not a hand.
MAX_JUMP_PX = 320.0


def open_backend():
    """The one place the operating system is chosen.

    Returns:
        Something with `move(dx, dy)`, `button(down)`, `wheel(notches)` and `close()`.

    Raises:
        RuntimeError: with an actionable sentence naming what to install or which udev rule to
                      add. Never a bare ImportError - a gesture daemon that dies on
                      "No module named evdev" has told LB nothing he can act on.
    """
    if sys.platform != "win32":                                        # pragma: no cover
        # LOUD. The evdev/uinput backend was DELETED 2026-08-26, not disabled. It matters more
        # here than anywhere else in the prune: the uinput device was what made guarantee 1
        # kernel-enforced, and a stub that quietly did nothing would leave this file's header
        # making four security claims with nothing behind any of them.
        raise RuntimeError(
            f"the gesture pointer is Windows-only since 2026-08-26 and this is {sys.platform}. "
            f"The evdev backend was deleted; restore `capabilities()` and `_UInputMouse` from "
            f"git history to run it on the Pi.")
    from tools.win_input import MouseOnlyInput
    return MouseOnlyInput()


class Pointer:
    """A virtual mouse that cannot click and cannot type.

    Every guarantee in the header that concerns *emission* is enforced here rather than in the
    loop, so there is one place to audit. The loop decides what the hand did; this decides what
    the kernel is allowed to hear about it.
    """

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.ui = None
        self.down = False
        self.since_press = 0.0        # pixels travelled since the button went down
        self.armed = 0.0              # pixels travelled during this pinch, before pressing
        if dry_run:
            return
        self.ui = open_backend()

    # -- raw emission ----------------------------------------------------------------------
    def _move(self, dx: float, dy: float) -> None:
        dx, dy = int(round(dx)), int(round(dy))
        if not dx and not dy:
            return
        if self.down:
            self.since_press += math.hypot(dx, dy)
        if self.dry_run:
            print(f"    move {dx:+5d} {dy:+5d}"
                  f"{'  [dragging]' if self.down else ''}")
            return
        self.ui.move(dx, dy)

    def _button(self, down: bool) -> None:
        if self.dry_run:
            print(f"    button {'DOWN' if down else 'UP'}")
        else:
            self.ui.button(down)
        self.down = down

    # -- the guarded interface the loop uses -----------------------------------------------
    def travel(self, dx: float, dy: float) -> None:
        """Move the pointer, and arm the drag once it has gone far enough."""
        step = math.hypot(dx, dy)
        if step > MAX_JUMP_PX:                      # a glitch frame, not a hand
            LOG.debug("dropped a %.0f px jump", step)
            return
        self._move(dx, dy)
        if not self.down:
            self.armed += step
            if self.armed >= DRAG_ARM_PX:
                # GUARANTEE 2, first half: the press happens only after real travel, so a
                # motionless pinch - the shape a click would have - never presses at all.
                self._button(True)
                self.since_press = 0.0

    def let_go(self) -> None:
        """End the drag. GUARANTEE 2, second half.

        If the pointer has not travelled `CLICK_GUARD_PX` since the press, it is moved that far
        before the button comes up, so that press and release can never land on one widget.
        The jump is visible and is meant to be: it is the guarantee, made of pixels.
        """
        if self.down:
            short = CLICK_GUARD_PX - self.since_press
            if short > 0:
                LOG.debug("release guard: displacing %.0f px", short)
                self._move(short, 0.0)
            self._button(False)
        self.armed = self.since_press = 0.0

    def wheel(self, clicks: int) -> None:
        if not clicks:
            return
        if self.dry_run:
            print(f"    wheel {clicks:+d}")
            return
        self.ui.wheel(clicks)

    def close(self) -> None:
        try:
            self.let_go()
        finally:
            if self.ui is not None:
                self.ui.close()
                self.ui = None


def write_state(motion, paused: bool) -> None:
    """Publish what the daemon is doing, for the HUD or anything else that wants to know."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            f"{'PAUSED' if paused else motion.name} {motion.dx:+.4f} {motion.dy:+.4f} "
            f"{motion.scale:.4f} {motion.rotation:+.4f} {motion.hands}\n", encoding="utf-8")
    except OSError:
        pass                                        # a state file is a convenience, not a duty


def _check_windows() -> bool:
    """The Windows preconditions. There is only one, and it is not about permissions.

    Nothing has to be installed and no device node has to exist - `SendInput` is in `user32`
    on every Windows box. What IS worth printing is the state of guarantee 1, because on this
    platform it is a property of the code rather than of the kernel, and a check that does not
    look at it is checking the easy half.
    """
    from tools import win_input

    if not win_input.available():
        print(f"  SendInput        UNAVAILABLE - {win_input.why_unavailable()}")
        return False
    print("  SendInput        present (user32)")

    # Guarantee 1, verified rather than asserted. One union member means no keyboard struct.
    members = [name for name, _ in win_input._INPUTUNION._fields_]
    if members == ["mi"]:
        print("  keyboard struct  absent - the pointer has no vocabulary for typing")
    else:
        # Not a warning. A second union member means somebody added the ability to type to a
        # daemon whose header promises it cannot, and that is a failed check, not a note.
        print(f"  keyboard struct  PRESENT - INPUT union has {members}, so guarantee 1 is "
              f"BROKEN. See the header of tools/win_input.py.")
        return False
    return True


def check() -> int:
    """Can this box do it at all? Prints what is missing and how to fix it."""
    print()
    ok = _check_windows()

    try:
        import tools.gesture_control as gc
        rec = gc.GestureRecognizer()
        print(f"  detector         {rec.backend or 'none'}"
              f"{'' if rec.available else '  (' + rec.why + ')'}")
        ok = ok and rec.available
        rec.close()
    except Exception as exc:                                              # noqa: BLE001
        ok = False
        print(f"  detector         FAILED ({type(exc).__name__}: {exc})")

    print(f"\n  {'ready' if ok else 'NOT ready'}\n")
    return 0 if ok else 1


def run(args) -> int:
    import cv2
    import tools.gesture_control as gc

    rec = gc.GestureRecognizer()
    if not rec.available:
        print(f"\n  No detector: {rec.why}\n")
        return 1

    pointer = Pointer(dry_run=args.dry_run)
    cap = None
    paused = False
    frames = 0
    print(f"\n  backend {rec.backend}   gain {args.gain:.0f} px per palm span"
          f"{'   DRY RUN, nothing injected' if args.dry_run else ''}")
    print("  pinch and move to drag, two pinches to scroll, ctrl-C to stop\n")

    try:
        while True:
            # GUARANTEE 4. Checked before anything else in the frame, so an approval on screen
            # can never coincide with an injected event.
            if PAUSE_FILE.exists():
                if not paused:
                    LOG.info("paused - releasing the camera for a security prompt")
                    pointer.let_go()
                    if cap is not None:
                        cap.release()
                        cap = None
                    paused = True
                    write_state(gc.Motion("PAUSED"), True)
                time.sleep(0.15)
                continue
            if paused:
                LOG.info("resumed")
                paused = False

            if cap is None:
                cap = cv2.VideoCapture(args.camera)
                if not cap.isOpened():
                    print(f"\n  Camera {args.camera} would not open.\n")
                    return 1
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, gc.FRAME_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, gc.FRAME_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS, gc.FRAME_FPS)

            ok, frame = cap.read()
            if not ok or frame is None:
                LOG.warning("the camera stopped returning frames")
                break
            frames += 1

            frame = cv2.flip(frame, 1)              # mirror, so moving right moves right
            motion = rec.track(rec.detect_hands(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))

            if motion.name == "MOVE":
                pointer.travel(motion.dx * args.gain, motion.dy * args.gain)
            elif motion.name == "PINCH":
                pointer.travel(0.0, 0.0)            # holding: keeps the drag, adds no travel
            elif motion.name == "SCALE":
                pointer.let_go()
                pointer.wheel(int(round(math.log(max(motion.scale, 1e-6)) * WHEEL_GAIN)))
            else:
                pointer.let_go()                    # the hand opened, or left the frame

            write_state(motion, False)
            if args.verbose and motion.moving:
                print(f"  {motion!r}")
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        pointer.close()
        if cap is not None:
            cap.release()
        rec.close()
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    print(f"  {frames} frames, pointer released\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="drive the desktop pointer from a pinch (motion and dragging only)")
    ap.add_argument("--camera", type=int, default=0, metavar="N")
    ap.add_argument("--gain", type=float, default=POINTER_GAIN, metavar="PX",
                    help=f"pixels per palm span (default {POINTER_GAIN:.0f})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be injected and inject nothing")
    ap.add_argument("--check", action="store_true",
                    help="report whether this box can run it, and exit")
    ap.add_argument("--verbose", action="store_true", help="print every motion")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="  %(levelname)-7s %(message)s")
    if args.check:
        return check()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
