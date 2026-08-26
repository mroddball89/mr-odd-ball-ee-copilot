#!/usr/bin/env python3
r"""
Module:  win_input.py
Purpose: Emit mouse events to Windows. Motion, wheel and the LEFT button. Nothing else.
Author:  LB
Date:    2026-08-26

    python tools/win_input.py --check      can this box do it at all
    python tools/win_input.py --demo       move the pointer in a small square

This replaces `evdev.UInput` for `tools/gesture_pointer.py` on Windows. It is the entire
operating-system surface of the gesture pointer: if it is not in this file, the pointer
cannot do it.

# ==========================================================================================
# WHAT THIS COSTS, STATED PLAINLY - read before changing anything below
# ==========================================================================================

`tools/gesture_pointer.py` makes four guarantees about a synthetic pointer that lives on the
same machine as a security gate. Three of them are pure logic and survive this port untouched.
**The first one does not survive intact, and this file is where it was lost.**

On the Pi, guarantee 1 was "the device has no keys, so it cannot type", and it was enforced by
the kernel. `evdev.UInput(capabilities)` declares what a virtual device is physically able to
emit; the gesture pointer declared `REL_X`, `REL_Y`, `REL_WHEEL` and `BTN_LEFT` and **no
`EV_KEY` capability for any keyboard key at all**. No bug in that file, and no bug in this one,
could have typed a `y` into the `input()` prompt that approves a shell command in
`agents/os_agent.py`. The guarantee did not depend on the code being correct.

Windows has no user-mode equivalent. `SendInput` is one call that takes mouse structures or
keyboard structures, chosen by a tag on a union, and any process that can move the cursor can
also type. There is no capability to withhold and nothing to ask the kernel to enforce.

## So this is what replaces it, and it is weaker

**`KEYBDINPUT` is not declared in this process.** The union below has one member. There is no
struct to fill in, no flag constant to pass, and no function to call by mistake - so typing is
not a thing this code can do wrong, it is a thing this code has no vocabulary for. To make the
gesture pointer type, somebody would have to add a struct definition to this file.

That turns guarantee 1 from *kernel-enforced* into *auditable by inspection*, and the
inspection is one line long:

    python tools/win_input.py --check       # prints the union's members

`_INPUTUNION` has exactly one field. That is the whole proof, and `--check` prints it rather
than asserting it, so the claim is re-checked every time anybody runs it. A grep is NOT the
audit here and the obvious one is actively misleading - `grep KEYBDINPUT tools/win_input.py`
matches six times, all of them in this docstring explaining its absence.

One file, one union member: the smallest surface the guarantee can be reduced to on this
platform. But it is an inspection standing where a kernel guarantee used to be, and it has to
be re-run on every future edit. Anybody reading `gesture_pointer.py`'s header on Windows should
read it as "cannot type, because nothing here knows how" rather than "cannot type, because it
is not allowed to".

**Why not pyautogui or pynput.** Both were considered and both are the opposite trade: one
import that can move, click, right-click, scroll AND type, restricted only by which of its
functions the caller happens to use. With either one in the import graph the inspection above
has nothing to inspect: the capability arrives inside a package, so "can this type?" stops being
a question about code anybody reads and becomes a question about which functions get called, on
every future edit, forever. They are also larger - pyautogui pulls Pillow, pytweening, pyscreeze
and mouseinfo for features this file exists to not have.

## Guarantee 3 lives here too

`MOUSEEVENTF_RIGHTDOWN`, `MOUSEEVENTF_RIGHTUP`, `MOUSEEVENTF_MIDDLEDOWN` and
`MOUSEEVENTF_MIDDLEUP` are deliberately not defined below, for the same reason and with the
same weakness. No context menu means no menu item.

## Guarantee 2 is NOT here

The click guard - press and release can never happen at the same position - is enforced in
`gesture_pointer.Pointer`, above this file, and stays there. This layer is deliberately dumb:
it emits what it is told. Putting the guard here as well would spread one safety property
across two files, and the audit question ("where is the click guard?") must have one answer.

# ==========================================================================================

## Two Windows details worth knowing before tuning --gain

**Relative motion goes through pointer ballistics.** `MOUSEEVENTF_MOVE` without
`MOUSEEVENTF_ABSOLUTE` is a delta in mickeys, and Windows applies its acceleration curve
("Enhance pointer precision") to it before the cursor moves. So a given `--gain` does not
produce the same travel it did under libinput on the Pi, and it is not linear either: fast hand
movement is amplified more than slow. `POINTER_GAIN` was fitted on the Pi and **must be
re-fitted here**; `--dry-run` prints the deltas this layer was asked for, not the pixels the
cursor actually moved.

**The struct is the right size despite the missing union members.** `INPUT` is
`DWORD type` plus a union, and Windows checks `cbSize`. `MOUSEINPUT` is the LARGEST of the
three union members (32 bytes on x64, against 24 for `KEYBDINPUT` and 8 for `HARDWAREINPUT`),
so a union containing only `MOUSEINPUT` has exactly the size and alignment of the full one -
40 bytes on x64, 28 on x86. Leaving `KEYBDINPUT` out costs nothing structurally. That is luck
rather than design, and it is checked at import time below rather than trusted.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

LOG = logging.getLogger("oddball.wininput")

__all__ = ["MouseOnlyInput", "available", "why_unavailable"]

# --- the constants this file is allowed to know -------------------------------------------
# Mouse only, and left button only. See guarantees 1 and 3 in the header. Adding a constant
# here is the same act as adding a capability to the uinput device it replaces.
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800

# One notch of the wheel, as Windows defines it. `REL_WHEEL` on Linux counts notches directly,
# so the caller's units are unchanged and the conversion happens here.
WHEEL_DELTA = 120

# `ULONG_PTR` is not in `ctypes.wintypes` - it is pointer-sized, so it differs between a 32-bit
# and a 64-bit interpreter, and getting it wrong silently corrupts the tail of every struct.
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    """`MOUSEINPUT`, verbatim from `winuser.h`.

    `dx`/`dy` are a RELATIVE delta in mickeys when `dwFlags` omits `MOUSEEVENTF_ABSOLUTE`,
    which is how this file always uses them - see the ballistics note in the module docstring.
    """

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),      # wheel notches * WHEEL_DELTA, or 0
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),           # 0 = let Windows stamp it
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    """The `INPUT` union with ONE member.

    The real union has three: `mi`, `ki` and `hi`. `ki` is `KEYBDINPUT` and its absence is
    guarantee 1 on this platform - see the module docstring. `hi` is `HARDWAREINPUT`, absent
    because nothing here injects hardware messages.

    Both omissions are free structurally: `MOUSEINPUT` is the largest of the three, so this
    union is already the size and alignment the full one would be. `_check_struct_size()`
    asserts that rather than trusting it.
    """

    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


def _check_struct_size() -> None:
    """Fail at import if `INPUT` is not the size Windows expects.

    `SendInput` takes `cbSize` and rejects the call outright if it disagrees - it returns 0 and
    sets `ERROR_INVALID_PARAMETER`, which presents as "the pointer silently does nothing" and
    is a miserable thing to chase from a gesture loop. Checking the arithmetic here turns that
    into one clear error at startup.

    40 bytes on x64: DWORD type (4) + 4 padding to align the union to 8 + MOUSEINPUT (32).
    28 bytes on x86: DWORD type (4) + MOUSEINPUT (24), no padding needed.
    """
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    actual = ctypes.sizeof(INPUT)
    if actual != expected:
        raise RuntimeError(
            f"INPUT is {actual} bytes and Windows expects {expected} on this interpreter "
            f"({8 * ctypes.sizeof(ctypes.c_void_p)}-bit). SendInput would reject every call "
            f"and the pointer would do nothing, silently.")


def available() -> bool:
    """Can this process emit input at all? False everywhere that is not Windows."""
    return sys.platform == "win32"


def why_unavailable() -> str:
    """One actionable sentence, or "" when it IS available.

    Actionable, because `gesture_pointer.py --check` prints this and "False" is not a
    diagnosis. Same convention as `tools/screen_capture.available_backend()`.
    """
    if available():
        return ""
    return (f"SendInput is a Windows API and this is {sys.platform}. On the Pi the gesture "
            f"pointer uses evdev.UInput instead - see tools/gesture_pointer.py.")


class MouseOnlyInput:
    """The only thing in this repo that can move the Windows cursor.

    Holds no handle and needs no cleanup - `SendInput` is a stateless call - but it is a class
    rather than three module functions so that `tools/verify_pointer.py` can substitute a fake
    for it the same way it substitutes `Pointer._move`, and so `close()` exists for symmetry
    with the `evdev.UInput` it replaces.

    Every method here is a THIN emitter. No guards, no thresholds, no click protection: those
    live in `gesture_pointer.Pointer`, deliberately, so there is exactly one place to audit
    them. See "Guarantee 2 is NOT here" in the module docstring.
    """

    def __init__(self) -> None:
        if not available():
            raise RuntimeError(why_unavailable())
        _check_struct_size()

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        # Declared explicitly. Without argtypes, ctypes guesses from the Python values it is
        # handed, and on x64 that quietly truncates a pointer-sized argument.
        self._user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._user32.SendInput.restype = wintypes.UINT
        LOG.info("SendInput ready (mouse only, no keyboard struct in this process)")

    # -- the one place an event leaves this repo -------------------------------------------
    def _send(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        """Emit one mouse event. The single chokepoint; everything below calls this.

        Args:
            flags: a bitwise OR of the MOUSEEVENTF_* constants defined in this module. There
                   is no path by which a keyboard flag can arrive here - none is defined.
            dx:    relative horizontal motion, in mickeys. Meaningful only with
                   MOUSEEVENTF_MOVE.
            dy:    relative vertical motion.
            data:  wheel notches * WHEEL_DELTA. Meaningful only with MOUSEEVENTF_WHEEL.

        Raises:
            OSError: SendInput rejected or blocked the event. Notably, UIPI blocks injection
                     into a window owned by a more-privileged process, and a call made while
                     the secure desktop (UAC, Ctrl+Alt+Del) is up is discarded. Both are the
                     OS doing its job, so they are reported rather than swallowed.
        """
        event = INPUT(type=INPUT_MOUSE)
        # `mouseData` is a DWORD (unsigned) and a scroll-down is a NEGATIVE notch count. Python
        # would raise on the negative int, so it is wrapped to two's complement here - the
        # struct field is where the signedness is actually decided.
        event.union.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF,
                                    dwFlags=flags, time=0, dwExtraInfo=0)
        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        if sent != 1:
            err = ctypes.get_last_error()
            raise OSError(err, f"SendInput emitted {sent} of 1 events "
                               f"(WinError {err}: {ctypes.FormatError(err)})")

    # -- the interface gesture_pointer.Pointer uses ----------------------------------------
    def move(self, dx: int, dy: int) -> None:
        """Move the cursor by a relative delta. The direct analogue of REL_X / REL_Y."""
        if dx or dy:
            self._send(MOUSEEVENTF_MOVE, dx=dx, dy=dy)

    def button(self, down: bool) -> None:
        """Press or release the LEFT button. There is no other button - guarantee 3."""
        self._send(MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)

    def wheel(self, notches: int) -> None:
        """Scroll. `notches` is in the same units REL_WHEEL used, positive being away."""
        if notches:
            self._send(MOUSEEVENTF_WHEEL, data=notches * WHEEL_DELTA)

    def close(self) -> None:
        """Nothing to release. Present so the caller does not branch on platform."""


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="the Windows mouse emitter, on its own")
    ap.add_argument("--check", action="store_true", help="report whether this box can do it")
    ap.add_argument("--demo", action="store_true",
                    help="trace a small square with the pointer, then put it back")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="  %(levelname)-7s %(message)s")

    if args.check or not args.demo:
        print()
        if not available():
            print(f"  SendInput        UNAVAILABLE - {why_unavailable()}")
            print("\n  NOT ready\n")
            return 1
        print(f"  platform         {sys.platform}")
        print(f"  interpreter      {8 * ctypes.sizeof(ctypes.c_void_p)}-bit")
        try:
            _check_struct_size()
            print(f"  INPUT struct     {ctypes.sizeof(INPUT)} bytes, as Windows expects")
        except RuntimeError as exc:
            print(f"  INPUT struct     WRONG - {exc}")
            print("\n  NOT ready\n")
            return 1
        # The guarantee, checked rather than claimed. See the module docstring.
        print(f"  keyboard struct  absent ({len(_INPUTUNION._fields_)} union member: "
              f"{_INPUTUNION._fields_[0][0]})")
        print("\n  ready\n")
        return 0

    mouse = MouseOnlyInput()
    print("\n  tracing a 200 px square - do not touch the mouse\n")
    for dx, dy in ((200, 0), (0, 200), (-200, 0), (0, -200)):
        for _ in range(20):
            mouse.move(dx // 20, dy // 20)
            time.sleep(0.01)
    print("  done - the pointer should be back where it started "
          "(it will not be exactly, because of pointer acceleration)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
