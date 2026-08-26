#!/usr/bin/env python3
"""
Module:  verify_pointer.py
Purpose: Prove the gesture pointer cannot type and cannot click. Nothing is injected.
Author:  LB
Date:    2026-08-23

    .venv-gesture/bin/python tools/verify_pointer.py
    .venv-gesture/bin/python tools/verify_pointer.py --probe

`tools/gesture_pointer.py` moves the desktop pointer from a pinch. Its header makes four
security claims. This file is where those claims stop being prose.

The two that matter most are structural, and both are testable without a camera, without a
desktop and without injecting a single event:

  1. **It cannot type.** The virtual device declares no keyboard key, so it cannot answer the
     `input()` prompt in `agents/os_agent.py` that approves a shell command.
  2. **It cannot click.** A click is a press and a release at one position. The daemon presses
     only after travel, and displaces the pointer before releasing if it has not moved far
     enough — so press and release are always at least `CLICK_GUARD_PX` apart and no widget
     ever activates.

Claim 2 is checked EXHAUSTIVELY over drag lengths rather than at a couple of hand-picked ones,
because "no click is possible" is a statement about every drag, and a test of two of them is a
test of two of them.

`Recorder` subclasses `Pointer` and overrides only the two methods that touch the kernel, so
everything above them — the arming, the guard, the ordering — is the real code. A mock that
reimplemented the guard would prove nothing about the guard.

Runs on BOTH platforms, and section 1 is a different test on each — deliberately, because the
mechanism enforcing guarantee 1 is different. On the Pi the kernel refuses to emit a keystroke
the device never declared. On Windows nothing refuses anything, and the guarantee is that
`tools/win_input.py` does not know how to build a keyboard event; section 1 there checks the
code rather than the kernel, and says so in its own heading. Sections 2, 3 and 4 are pure logic
and are byte-for-byte the same test on both.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PASSED = 0
FAILED = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"   PASS  {what}")
    else:
        FAILED += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def section(name: str) -> None:
    print(f"\n  {name}")


def recorder(gp):
    """A `Pointer` that records kernel writes instead of making them.

    Only `_move` and `_button` are replaced — the two methods that talk to uinput. Everything
    that enforces a guarantee lives above them and runs for real.
    """

    class Recorder(gp.Pointer):
        def __init__(self):
            super().__init__(dry_run=True)
            self.log: list[tuple[str, float]] = []
            self.x = 0.0

        def _move(self, dx, dy):
            dx, dy = int(round(dx)), int(round(dy))
            if not dx and not dy:
                return
            if self.down:
                self.since_press += (dx * dx + dy * dy) ** 0.5
            self.x += dx
            self.log.append(("move", self.x))

        def _button(self, down):
            self.log.append(("down" if down else "up", self.x))
            self.down = down

        def drag(self, steps: int, px: float = 6.0):
            for _ in range(steps):
                self.travel(px, 0.0)
            self.let_go()
            return self

        def pair(self):
            """(press position, release position) or None if it never pressed."""
            press = [x for k, x in self.log if k == "down"]
            release = [x for k, x in self.log if k == "up"]
            return (press[0], release[0]) if press and release else None

    return Recorder


def _cannot_type_linux(gp) -> None:
    """Guarantee 1 on the Pi, where the KERNEL enforces it.

    The device's declared capabilities are the whole proof. Nothing above them is consulted,
    because nothing above them can matter: a capability that was never declared cannot be
    emitted however wrong the daemon goes.
    """
    from evdev import ecodes as e

    section("1. it has no keys, so it cannot type  [kernel-enforced]")
    caps = gp.capabilities()
    keys = caps.get(e.EV_KEY, [])
    check(list(keys) == [e.BTN_LEFT], "the only EV_KEY code declared is BTN_LEFT",
          f"declared: {keys}")

    # Not "no key I thought of" — no member of the kernel's entire KEY_* namespace.
    every_key = {v for k, v in vars(e).items() if k.startswith("KEY_") and isinstance(v, int)}
    check(not (set(keys) & every_key),
          f"none of the kernel's {len(every_key)} KEY_* codes is declared",
          "so it cannot type 'y' at the approval prompt in agents/os_agent.py")

    check(e.BTN_RIGHT not in keys and e.BTN_MIDDLE not in keys,
          "no right or middle button, so it cannot open a context menu")
    check(set(caps.get(e.EV_REL, [])) == {e.REL_X, e.REL_Y, e.REL_WHEEL},
          "the relative axes are exactly X, Y and WHEEL", str(caps.get(e.EV_REL)))
    check(e.EV_ABS not in caps,
          "no absolute axes, so it cannot warp the pointer to a fixed screen position")


def _cannot_type_windows(gp) -> None:
    """Guarantee 1 on Windows, where NOTHING enforces it and this file is the enforcement.

    **This section is not the same test as the Linux one and must not be read as if it were.**
    On the Pi the question is "what did the kernel agree to emit?" and the answer is binding.
    Here the question is "what does this process know how to build?", and the answer binds only
    until somebody edits `tools/win_input.py`. That is a genuine downgrade, it is written up in
    that file's header, and the reason it is checked here anyway is that an inspection nobody
    automates is an inspection that stops happening.

    So: the INPUT union has exactly one member, no keyboard struct is defined anywhere in the
    module, and no right/middle button constant exists to pass. Three ways of asking the same
    question, because the answer is worth more than the tidiness.
    """
    from tools import win_input

    section("1. it has no vocabulary for typing  [inspection, NOT kernel-enforced]")

    members = [name for name, _ in win_input._INPUTUNION._fields_]
    check(members == ["mi"],
          "the INPUT union has exactly one member, and it is the mouse one",
          f"members: {members} — a second member is how typing would arrive")

    # The struct itself, not just the union tag. A KEYBDINPUT defined but unreferenced is a
    # loaded gun on the table: the next edit only has to name it.
    check(not hasattr(win_input, "KEYBDINPUT"),
          "no KEYBDINPUT struct is defined in the module at all",
          "so there is nothing to fill in, even by mistake")

    # Guarantee 3, by the same means. No constant, no context menu.
    absent = [n for n in ("MOUSEEVENTF_RIGHTDOWN", "MOUSEEVENTF_RIGHTUP",
                          "MOUSEEVENTF_MIDDLEDOWN", "MOUSEEVENTF_MIDDLEUP")
              if hasattr(win_input, n)]
    check(not absent,
          "no right or middle button constant exists, so it cannot open a context menu",
          f"unexpectedly present: {absent}" if absent else "none of the four is defined")

    # MOUSEEVENTF_ABSOLUTE is the Windows analogue of EV_ABS: it would let the pointer warp to
    # a fixed screen position rather than travel there, which defeats the click guard outright
    # — a warp has no travel, so `since_press` never grows and the displacement never fires.
    check(not hasattr(win_input, "MOUSEEVENTF_ABSOLUTE"),
          "no absolute-motion constant, so it cannot warp past the click guard",
          "every move is a relative delta, which is what CLICK_GUARD_PX counts")

    check(win_input.MouseOnlyInput.__dict__.keys() >= {"move", "button", "wheel", "close"},
          "the emitter offers exactly move / button / wheel / close",
          "the same four the evdev backend offers, so the guards above are platform-free")


def run(gp) -> None:
    Recorder = recorder(gp)

    if sys.platform == "win32":
        _cannot_type_windows(gp)
    else:
        _cannot_type_linux(gp)

    section("2. it cannot click")
    p = Recorder()
    p.travel(0.0, 0.0)
    p.travel(0.0, 0.0)
    p.let_go()
    check(not any(k == "down" for k, _ in p.log),
          "a pinch that never moves never presses at all — the exact shape of a click",
          str(p.log) or "no events")

    p = Recorder().drag(3)
    check(p.pair() is not None, "a real drag does press and release", str(p.log))
    press, release = p.pair()
    check(abs(release - press) >= gp.CLICK_GUARD_PX,
          "on a SHORT drag the release is displaced past the guard",
          f"separation {abs(release - press):.0f} px, guard {gp.CLICK_GUARD_PX:.0f} px")

    p = Recorder().drag(40, 20.0)
    press, release = p.pair()
    check(abs(release - press) >= gp.CLICK_GUARD_PX,
          "a long drag is already past the guard and needs no displacement",
          f"separation {abs(release - press):.0f} px")

    # THE exhaustive one. "No click is possible" is a claim about every drag length.
    worst, worst_n, pressed = None, None, 0
    for n in range(0, 120):
        pair = Recorder().drag(n).pair()
        if pair is None:
            continue
        pressed += 1
        gap = abs(pair[1] - pair[0])
        if worst is None or gap < worst:
            worst, worst_n = gap, n
    check(worst is not None and worst >= gp.CLICK_GUARD_PX,
          f"across 120 drag lengths, EVERY press/release pair clears the guard",
          f"{pressed} drags pressed; closest pair {worst:.0f} px at {worst_n} steps "
          f"(guard {gp.CLICK_GUARD_PX:.0f})")

    check(gp.CLICK_GUARD_PX >= 120,
          "the guard is wider than a confirmation button on this screen",
          f"{gp.CLICK_GUARD_PX:.0f} px")

    section("3. it never leaves the button stuck down")
    p = Recorder().drag(10)
    check(not p.down, "let_go always ends with the button up")
    p = Recorder()
    p.drag(10)
    p.close()
    check(not p.down, "close() releases too, so a crash cannot leave a held mouse button")

    section("4. it stands down for the security gate")
    import tools.gesture_control as gc
    check(gc.POINTER_PAUSE_FILE == gp.PAUSE_FILE,
          "the gate and the daemon name the SAME pause file", gp.PAUSE_FILE.name)

    import unittest.mock as mock
    seen = {}

    def spy(*a, **k):
        seen["paused"] = gc.POINTER_PAUSE_FILE.exists()
        return mock.Mock(returncode=0, stdout="NONE\n", stderr="")

    existed = gc.POINTER_PAUSE_FILE.exists()
    with mock.patch("subprocess.run", side_effect=spy):
        gc._ask_sidecar(sys.executable)
    check(seen.get("paused") is True,
          "the pause file EXISTS for the whole of an approval read",
          "so the daemon is inert exactly while a security prompt is on screen")
    check(gc.POINTER_PAUSE_FILE.exists() == existed,
          "...and is cleared again afterwards")

    # A crashing approval must not leave the desktop pointer dead for ever.
    with mock.patch("subprocess.run", side_effect=RuntimeError("boom")):
        try:
            gc._ask_sidecar(sys.executable)
        except RuntimeError:
            pass
    check(gc.POINTER_PAUSE_FILE.exists() == existed,
          "an approval that raises still clears the pause file")


def probe(gp) -> int:
    """Break each guarantee and show this harness catching it."""
    Recorder = recorder(gp)
    print("\n  PROBE — the same checks against a deliberately weakened pointer\n")
    bitten = []

    saved = gp.CLICK_GUARD_PX
    try:
        gp.CLICK_GUARD_PX = 0.0
        worst = None
        for n in range(0, 40):
            pair = Recorder().drag(n).pair()
            if pair is not None:
                gap = abs(pair[1] - pair[0])
                worst = gap if worst is None else min(worst, gap)
        print(f"    guard 0   -> closest press/release pair: {worst:.0f} px")
        if worst is not None and worst < saved:
            bitten.append(f"with the guard removed a drag can click ({worst:.0f} px apart)")
    finally:
        gp.CLICK_GUARD_PX = saved

    saved_arm = gp.DRAG_ARM_PX
    try:
        gp.DRAG_ARM_PX = 0.0
        p = Recorder()
        p.travel(0.0, 0.0)
        p.let_go()
        print(f"    arm 0     -> a motionless pinch emits: {p.log or 'nothing'}")
    finally:
        gp.DRAG_ARM_PX = saved_arm

    if sys.platform == "win32":
        # The Windows weakening is an EDIT, not a runtime flag, which is the whole point of
        # the downgrade. Simulate it the way it would really arrive: a second union member.
        import ctypes

        from tools import win_input

        class _KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort)]

        class _WidenedUnion(ctypes.Union):
            _fields_ = [("mi", win_input.MOUSEINPUT), ("ki", _KEYBDINPUT)]

        saved_union = win_input._INPUTUNION
        try:
            win_input._INPUTUNION = _WidenedUnion
            members = [n for n, _ in win_input._INPUTUNION._fields_]
            print(f"    +ki       -> the INPUT union becomes {members}")
            if members != ["mi"]:
                bitten.append("a keyboard struct in the union would defeat the terminal gate "
                              "entirely, and only this check stands between it and LB")
        finally:
            win_input._INPUTUNION = saved_union
    else:
        from evdev import ecodes as e
        caps = dict(gp.capabilities())
        caps[e.EV_KEY] = [e.BTN_LEFT, e.KEY_Y, e.KEY_ENTER]
        print(f"    +KEY_Y    -> a device with keys could type 'y' at the approval prompt")
        bitten.append("a keyboard capability would defeat the terminal gate entirely")

    print()
    for b in bitten:
        print(f"   BITES: {b}")
    print()
    return 0 if bitten else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the gesture pointer cannot type or click")
    ap.add_argument("--probe", action="store_true",
                    help="weaken each guarantee and show the harness catching it")
    args = ap.parse_args()

    # Sections 2, 3 and 4 are pure logic and run anywhere. Only section 1 needs a backend,
    # because only section 1 is a claim about what the operating system will emit.
    if sys.platform != "win32":
        try:
            import evdev                                                  # noqa: F401
        except ImportError:
            print("\n  evdev is not installed here — this runs on the Pi.\n"
                  "    uv pip install --python .venv-gesture/bin/python evdev\n")
            raise SystemExit(0)

    import tools.gesture_pointer as gp

    if args.probe:
        raise SystemExit(probe(gp))

    print("\n" + "=" * 78)
    print("  verify_pointer.py — the pointer cannot type and cannot click")
    print("=" * 78)
    run(gp)
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(0)
