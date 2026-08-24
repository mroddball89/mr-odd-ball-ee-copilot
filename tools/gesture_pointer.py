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

## Why a kernel virtual device and not a Wayland client

This Pi runs **labwc**, a Wayland compositor, and Wayland deliberately forbids one client from
moving another client's windows - a security property of the display server, not a gap to patch.
`wmctrl` reaches only XWayland windows; `wtype` does keyboard and no pointer; `ydotool` is not
packaged for this Debian at all.

So this goes UNDER the compositor instead of arguing with it. `evdev.UInput` creates a virtual
input device in the kernel, and labwc sees an ordinary mouse - indistinguishable from the real
one, working on native Wayland and XWayland windows alike. `/dev/uinput` is root-only as
shipped; `docs/DEPLOY.md` records the one udev rule that opens it to the `input` group.

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

## 1. The device has no keys, so it cannot type

`CAPABILITIES` below declares `REL_X`, `REL_Y`, `REL_WHEEL` and `BTN_LEFT`. That is the entire
device. It has **no `EV_KEY` capability for any keyboard key**, so there is no code path -
including a bug in this file - by which it can type a character.

That matters more than anything else here, because the security gate in `agents/os_agent.py` is
a terminal prompt reading `input()`. Approving it means typing `y` and pressing Enter. A device
that cannot type cannot answer it, however wrong the rest of this file goes.

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


def capabilities():
    """The whole device. Deliberately tiny - see guarantees 1 and 3."""
    from evdev import ecodes as e
    return {
        e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
        e.EV_KEY: [e.BTN_LEFT],
    }


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
        from evdev import UInput
        self.ui = UInput(capabilities(), name="oddball-gesture-pointer", version=0x1)
        LOG.info("virtual pointer at %s", self.ui.device.path)

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
        from evdev import ecodes as e
        self.ui.write(e.EV_REL, e.REL_X, dx)
        self.ui.write(e.EV_REL, e.REL_Y, dy)
        self.ui.syn()

    def _button(self, down: bool) -> None:
        if self.dry_run:
            print(f"    button {'DOWN' if down else 'UP'}")
        else:
            from evdev import ecodes as e
            self.ui.write(e.EV_KEY, e.BTN_LEFT, 1 if down else 0)
            self.ui.syn()
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
        from evdev import ecodes as e
        self.ui.write(e.EV_REL, e.REL_WHEEL, clicks)
        self.ui.syn()

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


def check() -> int:
    """Can this box do it at all? Prints what is missing and how to fix it."""
    ok = True
    print()
    try:
        import evdev                                # noqa: F401
        print("  evdev            present")
    except ImportError:
        ok = False
        print("  evdev            MISSING - uv pip install --python .venv-gesture/bin/python evdev")

    node = Path("/dev/uinput")
    if not node.exists():
        ok = False
        print("  /dev/uinput      MISSING - sudo modprobe uinput")
    elif not os.access(node, os.W_OK):
        ok = False
        print(f"  /dev/uinput      NOT WRITABLE by {os.environ.get('USER', 'you')} - see "
              f"docs/DEPLOY.md for the udev rule")
    else:
        print("  /dev/uinput      writable")

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
