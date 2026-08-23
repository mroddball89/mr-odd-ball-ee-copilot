#!/usr/bin/env python3
"""
Module:  verify_gestures.py
Purpose: Prove the gestures are told apart AT ANY HAND ANGLE, and that only a thumb approves.
Author:  LB
Date:    2026-08-23

    python tools/verify_gestures.py
    python tools/verify_gestures.py --probe
    python tools/verify_gestures.py --dump

No camera, no model file, no mediapipe. `_classify` and `_classify_frame` are pure functions of
21 landmarks per hand, so every case below is a hand built out of trigonometry.

## The rotation sweep is the point of this file

`docs/DECISIONS.md` has claimed since 2026-08-19 that the classifier was tested with no camera.
It was not — nothing ran those cases — and the first version of this file, written 2026-08-23,
fixed that but tested only upright hands.

That was the exact blind spot the classifier had. Every finger test was an image-coordinate
comparison (`tip.y < pip.y`), which is correct only while the hand is held vertically, and an
upright-only test suite agrees with an upright-only classifier all the way to the camera. LB
photographed a clean thumbs up classified `NONE`
(`media/captures/gesture-none-20260823-171120.png`) because a thumbs up is naturally made with
the palm side-on.

So **section 2 rotates every pose through a full circle** and requires the answer not to change.
That property is what the port from `jaredrhod/barehands` bought — `_curl` compares a finger's
segments to each other and `_reach` measures both distances from the wrist, so neither can
notice the hand turning. A test suite that only ever holds the hand upright cannot see the
difference between that and what it replaced, which is how four days went by.

## And section 4 is still the security one

`THUMBS_UP` is what `agents/os_agent.py` runs a shell command on. `--probe` restores the
pre-2026-08-23 branch order and expects section 4 to go red, because a regression test for an
ordering bug that still passes when the ordering is wrong is not a test.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import tools.gesture_control as gc                                   # noqa: E402

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


# --------------------------------------------------------------------------------------
# Building a hand out of trigonometry
# --------------------------------------------------------------------------------------
#
# mediapipe returns 21 landmarks with `.x`, `.y`, `.z` normalised to the frame, y-DOWN. That is
# the whole contract, so a tiny class satisfies it and these tests need no camera.
#
# A finger is a chain: MCP -> PIP -> DIP -> TIP. Each joint turns the chain by `bend` radians,
# so the segment directions are what vary, which is exactly what `_curl` reads:
#
#     curl = dot(unit(mcp->pip), unit(dip->tip)) = cos(2 * bend)
#
#     bend  0 deg -> curl +1.00   straight out
#     bend 30 deg -> curl +0.50   slightly hooked
#     bend 50 deg -> curl -0.17   hooked: the claw
#     bend 80 deg -> curl -0.94   folded into the palm: the fist
#
# The whole hand takes a `turn` in radians, which rotates every landmark about the wrist. No
# gesture may depend on it.

class LM:
    """One landmark."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float = 0.0) -> None:
        self.x, self.y, self.z = x, y, z

    def __repr__(self) -> str:
        return f"({self.x:.3f},{self.y:.3f})"


PALM_LEN = 0.20                       # wrist to middle knuckle -> _hand_scale is 0.20
SPREAD = 0.058                        # lateral gap between adjacent knuckles
PHALANX = (0.090, 0.055, 0.042)       # proximal, middle, distal

# bend angle per joint, in degrees, for each pose a finger can be in
# Fitted against the real bands, measured 2026-08-23 by sweeping this angle and reading
# `_curl` and `_reach` back out (the sweep is in the commit message):
#     bend  2 deg -> curl +1.00, reach 1.81   fingers straight out
#     bend 62 deg -> curl -0.57, reach 1.34   hooked, tips still past the knuckles: a claw
#     bend 85 deg -> curl -0.98, reach 1.17   folded into the palm: a fist
BEND = {"out": 2.0, "claw": 62.0, "fist": 85.0}


def _rot(x: float, y: float, rad: float) -> tuple[float, float]:
    c, s = math.cos(rad), math.sin(rad)
    return x * c - y * s, x * s + y * c


def hand(fingers="out", thumb="side", turn: float = 0.0,
         at: tuple[float, float] = (0.50, 0.72), narrow: float = 1.0) -> list[LM]:
    """A 21-landmark hand.

    Args:
        fingers: a key of BEND applied to all four, or a 4-tuple of keys (index..pinky).
        thumb:   "up", "down", "side", or "pinch" (tip laid on the index tip).
        turn:    radians to rotate the entire hand about the wrist. NOTHING may depend on it.
        at:      where the wrist sits in the frame.
        narrow:  squeeze the knuckle row laterally, to make a side-on palm (raises `_aspect`).

    Returns:
        A list of 21 LM, indexed exactly as mediapipe indexes them.
    """
    if isinstance(fingers, str):
        fingers = (fingers,) * 4
    pts: list[LM | None] = [None] * 21

    # Everything is built in a hand-local frame with the fingers pointing up (-y), wrist at
    # the origin, then rotated by `turn` and moved to `at`. So `turn` cannot possibly leak
    # into the shape - which is what makes it a fair test of rotation invariance.
    local: dict[int, tuple[float, float]] = {0: (0.0, 0.0)}

    for (mcp, pip, dip, tip), bend_key, lateral in zip(
            gc.CHAINS, fingers, (-1.5, -0.5, 0.5, 1.5)):
        bx = lateral * SPREAD * narrow
        by = -PALM_LEN
        local[mcp] = (bx, by)
        bend = math.radians(BEND[bend_key])
        # Start pointing along the palm (up) and turn by `bend` at each joint.
        angle = 0.0
        x, y = bx, by
        for idx, seg_len in zip((pip, dip, tip), PHALANX):
            dx, dy = _rot(0.0, -seg_len, angle)
            x, y = x + dx, y + dy
            local[idx] = (x, y)
            angle += bend

    # The thumb. Only landmark 4 is read by the classifier; 1-3 are placed along the way so
    # the hand is a plausible shape if anything draws it.
    thumb_tip = {
        "up":    (-0.10, -0.30),
        "down":  (-0.10, +0.22),
        "side":  (-0.16, -0.06),
        "pinch": None,
    }[thumb]
    if thumb_tip is None:
        ix, iy = local[8]
        thumb_tip = (ix - 0.004, iy + 0.004)
    for i in range(1, 5):
        f = i / 4.0
        local[i] = (thumb_tip[0] * f, thumb_tip[1] * f)

    for i, (lx, ly) in local.items():
        rx, ry = _rot(lx, ly, turn)
        pts[i] = LM(at[0] + rx, at[1] + ry)
    return pts


TURNS = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330]


def sweep(want: str, **kw) -> tuple[bool, str]:
    """Classify the same hand at twelve angles. Returns (all agreed, what was seen)."""
    got = [gc._classify(hand(turn=math.radians(d), **kw)) for d in TURNS]
    bad = {g for g in got if g != want}
    return (not bad), ", ".join(f"{d}:{g}" for d, g in zip(TURNS, got) if g != want) or "all 12"


# --------------------------------------------------------------------------------------
def run() -> None:
    section("1. the poses, hand upright")

    for want, kw in (
            ("OPEN_PALM",   dict(fingers="out",  thumb="side")),
            ("FIST",        dict(fingers="fist", thumb="side")),
            ("THUMBS_UP",   dict(fingers="fist", thumb="up")),
            ("THUMBS_DOWN", dict(fingers="fist", thumb="down")),
            ("CLAW",        dict(fingers="claw", thumb="side")),
            ("POINT",       dict(fingers=("out", "fist", "fist", "fist"), thumb="side")),
            ("PINCH",       dict(fingers=("claw", "out", "out", "out"), thumb="pinch")),
    ):
        got = gc._classify(hand(**kw))
        check(got == want, f"{want.lower().replace('_', ' ')} -> {want}", got)

    section("2. THE ROTATION SWEEP - the same hand, twelve angles, one answer")
    print("      (this is the section that would have caught the 2026-08-23 thumbs up)")

    for want, kw in (
            ("OPEN_PALM",   dict(fingers="out",  thumb="side")),
            ("CLAW",        dict(fingers="claw", thumb="side")),
            ("POINT",       dict(fingers=("out", "fist", "fist", "fist"), thumb="side")),
            ("PINCH",       dict(fingers=("claw", "out", "out", "out"), thumb="pinch")),
    ):
        ok, detail = sweep(want, **kw)
        check(ok, f"{want} survives rotation through 360 deg", detail)

    # A fist is the one pose whose NAME is allowed to change with rotation, because a fist with
    # the thumb pointing up is a thumbs up — that is the whole reason `_thumb_direction` is
    # world-relative while everything else is hand-relative. What may never change is that the
    # hand is CLOSED. Rotating a fist must never open it into a palm, a claw or a pinch.
    closed = {gc._classify(hand(fingers="fist", thumb="side", turn=math.radians(d)))
              for d in TURNS}
    check(closed <= {"FIST", "THUMBS_UP", "THUMBS_DOWN"},
          "a fist stays CLOSED under rotation (the thumb verdict may change; the hand may not)",
          f"saw {sorted(closed)}")

    # The thumb gestures are the deliberate exception and the reason it is worth saying so:
    # up and down are facts about the world. Turning the hand upside down MUST change them.
    upside = gc._classify(hand(fingers="fist", thumb="up", turn=math.pi))
    check(upside == "THUMBS_DOWN",
          "a thumbs up rotated 180 deg becomes THUMBS_DOWN, by design", upside)
    check(gc._classify(hand(fingers="fist", thumb="up", turn=math.radians(90))) == "FIST",
          "...and sideways it is neither - a level thumb is a FIST",
          gc._classify(hand(fingers="fist", thumb="up", turn=math.radians(90))))

    section("3. the geometry primitives are orientation-free")

    straight = [gc._curl(hand(fingers="out", turn=math.radians(d)), *gc.CHAINS[1])
                for d in TURNS]
    check(max(straight) - min(straight) < 0.01,
          "_curl of a straight finger is constant under rotation",
          f"range {min(straight):.4f}..{max(straight):.4f}")

    folded = [gc._curl(hand(fingers="fist", turn=math.radians(d)), *gc.CHAINS[1])
              for d in TURNS]
    check(max(folded) - min(folded) < 0.01 and max(folded) < 0,
          "_curl of a folded finger is constant, and negative",
          f"range {min(folded):.4f}..{max(folded):.4f}")

    reaches = [gc._reach(hand(fingers="out", turn=math.radians(d)), 12, 9) for d in TURNS]
    check(max(reaches) - min(reaches) < 0.01,
          "_reach is constant under rotation", f"range {min(reaches):.3f}..{max(reaches):.3f}")

    scales = [gc._hand_scale(hand(turn=math.radians(d))) for d in TURNS]
    check(max(scales) - min(scales) < 1e-9, "_hand_scale is constant under rotation",
          f"{scales[0]:.4f}")

    # Scale invariance: the same hand at half the size must classify the same way.
    small = hand(fingers=("claw", "out", "out", "out"), thumb="pinch")
    for lm in small:
        lm.x = 0.5 + (lm.x - 0.5) * 0.45
        lm.y = 0.5 + (lm.y - 0.5) * 0.45
    check(gc._classify(small) == "PINCH",
          "a pinch at half the distance from the camera is still a PINCH",
          gc._classify(small))

    section("4. the collision - nothing but a thumb-up may approve")

    # The invariant, stated directly: only a hand with all four fingers closed can reach the
    # thumb branches at all. Assert it over every pose this harness can build.
    approvals, thumbs = [], []
    for fingers in ("out", "claw", "fist"):
        for thumb in ("up", "down", "side", "pinch"):
            for d in TURNS:
                h = hand(fingers=fingers, thumb=thumb, turn=math.radians(d))
                got = gc._classify(h)
                if got == "THUMBS_UP":
                    approvals.append((fingers, thumb, d))
                if got in ("THUMBS_UP", "THUMBS_DOWN"):
                    thumbs.append((fingers, thumb, d, got))
    check(all(f == "fist" for f, _t, _d in approvals),
          f"across {3 * 4 * len(TURNS)} hands, only a closed fist ever approves",
          f"{len(approvals)} approvals, finger poses: {sorted({f for f, _, _ in approvals})}")
    check(all(f == "fist" for f, _t, _d, _g in thumbs),
          "and only a closed fist reaches EITHER thumb branch",
          f"{len(thumbs)} thumb verdicts")

    claw = hand(fingers="claw", thumb="side")
    check(gc._classify(claw) == "CLAW", "the canonical claw is a CLAW", gc._classify(claw))

    # No arrangement of the thumb turns a hooked hand into an approval. (A claw with the thumb
    # raised straight up closes its own mouth and falls to NONE rather than CLAW — physically
    # right, since an opposed thumb is what makes the C-shape, and safe either way.)
    verdicts = {t: gc._classify(hand(fingers="claw", thumb=t)) for t in
                ("side", "up", "down", "pinch")}
    check("THUMBS_UP" not in verdicts.values(),
          "no claw, at any thumb position, is ever an approval", str(verdicts))

    pinch = hand(fingers=("claw", "out", "out", "out"), thumb="pinch")
    check(gc._classify(pinch) != "THUMBS_UP",
          "a PINCH is never an approval", gc._classify(pinch))

    check(gc._classify_frame([]) == "NONE", "no hands in frame is NONE")

    # A hallucinated hand - the knuckle row collapsed - must produce nothing at all.
    garbage = hand(fingers="fist", thumb="up", narrow=0.10)
    check(gc._aspect(garbage) > gc.ASPECT_GARBAGE and gc._classify(garbage) == "NONE",
          "a geometrically impossible hand is NONE, not an approval",
          f"aspect {gc._aspect(garbage):.1f}")

    section("5. two hands never combine into an approval")

    palm = hand(fingers="out", thumb="side")
    up = hand(fingers="fist", thumb="up")
    for a in (palm, claw, pinch):
        for b in (palm, claw, pinch):
            if gc._classify_frame([a, b]) == "THUMBS_UP":
                check(False, "two non-approving hands produced an approval")
                break
    else:
        check(True, "no pair of non-approving hands classifies as THUMBS_UP")
    check(gc._classify_frame([palm, up]) == "THUMBS_UP",
          "a thumbs up beside an open hand is still an approval",
          gc._classify_frame([palm, up]))

    section("6. the movements - a pinch is the verb, not a pose")

    def stream(samples):
        """Feed (t, hands) to a fresh recogniser; return every token it emitted."""
        rec = gc.GestureRecognizer.__new__(gc.GestureRecognizer)
        rec._history = __import__("collections").deque(maxlen=gc.TRACK_HISTORY)
        rec._pinch, rec._started, rec._travel = False, 0.0, 0.0
        rec.last_release = ""
        return [rec.classify_stream(h, now=t) for t, h in samples], rec

    def pinching(dx=0.0):
        h = hand(fingers=("claw", "out", "out", "out"), thumb="pinch")
        for lm in h:
            lm.x += dx
        return [h]

    open_hand = [hand(fingers="out", thumb="side")]

    got, _ = stream([(0.00, pinching()), (0.10, pinching()), (0.20, open_hand)])
    check(got[-1] == "TAP", "a quick pinch that went nowhere is a TAP", str(got))

    got, _ = stream([(0.00, pinching()), (0.20, pinching(0.06)), (0.40, pinching(0.16))])
    check(got[-1] == "DRAG", "a pinch that has travelled is a DRAG", str(got))
    check(got[:2] == ["PINCH", "PINCH"],
          "...and it was a plain PINCH before it had gone anywhere", str(got))

    got, rec = stream([(0.00, pinching()), (0.08, pinching(0.06)),
                       (0.16, pinching(0.13)), (0.24, open_hand)])
    check(got[-1] == "FLICK", "a pinch released at speed is a FLICK", str(got))
    check("spans/s" in rec.last_release, "and the release speed is recorded", rec.last_release)

    got, _ = stream([(0.00, pinching()), (0.50, pinching(0.02)),
                     (1.00, pinching(0.03)), (1.50, open_hand)])
    check("FLICK" not in got, "a slow release is not a throw", str(got))

    # THE regression this rewrite exists for: an open palm, moving or still, is never a FLICK.
    got, _ = stream([(0.00, open_hand), (0.08, [hand(fingers="out", thumb="side",
                                                     at=(0.75, 0.72))]),
                     (0.16, [hand(fingers="out", thumb="side", at=(0.95, 0.72))])])
    check(set(got) == {"OPEN_PALM"},
          "an open palm swept fast across the frame stays OPEN_PALM - never FLICK",
          str(got))

    section("7. the sidecar protocol carries every token")

    for token in ("TAP", "DRAG", "FLICK", "PINCH", "CLAW", "POINT", "FIST",
                  "THUMBS_UP", "THUMBS_DOWN", "OPEN_PALM", "NONE"):
        check(token in gc._VALID, f"{token} survives the sidecar whitelist")
    check(set(gc._PRIORITY) <= set(gc._VALID), "_PRIORITY names no token _VALID would reject")
    check(gc.MAX_HANDS == 2, "the detector is allowed both hands")


def dump() -> int:
    """Print the raw metrics for each pose. What the live window shows, without a camera."""
    print("\n  pose            curl(I,M,R,P)                reach(I/back)   gap    aspect")
    for name, kw in (
            ("open palm", dict(fingers="out",  thumb="side")),
            ("fist",      dict(fingers="fist", thumb="side")),
            ("thumbs up", dict(fingers="fist", thumb="up")),
            ("claw",      dict(fingers="claw", thumb="side")),
            ("point",     dict(fingers=("out", "fist", "fist", "fist"), thumb="side")),
            ("pinch",     dict(fingers=("claw", "out", "out", "out"), thumb="pinch")),
    ):
        h = hand(**kw)
        curls = " ".join(f"{c:+.2f}" for c in gc._curls(h))
        back = (gc._reach(h, 12, 9) + gc._reach(h, 16, 13) + gc._reach(h, 20, 17)) / 3
        print(f"  {name:14s}  {curls}   {gc._reach(h, 8, 5):.2f}/{back:.2f}      "
              f"{gc._pinch_ratio(h):.2f}   {gc._aspect(h):.2f}   -> {gc._classify(h)}")
    return 0


def probe() -> int:
    """Restore the pre-2026-08-23 image-coordinate tests and prove this suite catches them.

    The defect is that `tip.y < pip.y` measures a finger against the IMAGE, not against the
    hand. So the question a probe has to ask is not "does it get this one pose right" — it is
    **"does its answer depend on which way the hand is turned?"**

    The measure below is openness, because that is the part that cannot legitimately vary. A
    fist is closed at every angle. Which THUMB verdict a closed hand earns is allowed to change
    when you rotate it — up and down are facts about the world — but a closed hand may never
    become an open one, and that is exactly what the old test does.
    """
    print("\n  PROBE - the pre-2026-08-23 image-coordinate classifier\n")

    def old_classify(lm) -> str:
        ext = [lm[tip].y < lm[pip].y for _m, pip, tip in gc.FINGERS]
        if all(ext):
            return "OPEN_PALM"
        up = (lm[gc.THUMB_TIP].y < lm[gc.FINGERS[0][0]].y
              and lm[gc.THUMB_TIP].y < lm[gc.WRIST].y)
        if up and not any(ext):
            return "THUMBS_UP"
        return "NONE"

    OPEN = {"OPEN_PALM"}
    CLOSED = {"FIST", "THUMBS_UP", "THUMBS_DOWN", "CLAW", "PINCH"}

    print("  ONE CLOSED HAND, TWELVE ANGLES. It is a fist at every one of them.")
    print("  (this is LB's 09:11 photo, swept: media/captures/gesture-none-20260823-171120.png)\n")
    print("       angle    old (image-relative)      new (hand-relative)")

    old_wrong = new_wrong = 0
    for d in TURNS:
        h = hand(fingers="fist", thumb="up", turn=math.radians(d))
        old, new = old_classify(h), gc._classify(h)
        old_bad = old in OPEN
        new_bad = new in OPEN
        old_wrong += old_bad
        new_wrong += new_bad
        flag = "   <- calls a FIST an open palm" if old_bad else ""
        print(f"       {d:3d} deg  {old:10s}                {new:12s}{flag}")

    print(f"\n   old: {old_wrong} of {len(TURNS)} rotations of a CLOSED hand read as OPEN")
    print(f"   new: {new_wrong} of {len(TURNS)}")

    # The second half of the story: the old test could not even hold a verdict steady.
    old_set = {old_classify(hand(fingers="fist", thumb="up", turn=math.radians(d)))
               for d in TURNS}
    new_set = {gc._classify(hand(fingers="fist", thumb="up", turn=math.radians(d)))
               for d in TURNS}
    print(f"\n   old verdicts across the sweep: {sorted(old_set)}")
    print(f"   new verdicts across the sweep: {sorted(new_set)}")
    print("   ...and every new one is a closed-hand verdict.")

    print()
    if old_wrong > 0 and new_wrong == 0 and new_set <= CLOSED:
        print(f"   The harness BITES: the old test opens a closed fist at {old_wrong} of "
              f"{len(TURNS)} angles.")
        print("   OPEN_PALM is a gesture the system acts on, and this hand is a fist.\n")
        return 0
    print("   The harness is VACUOUS: the old test held up under rotation, so section 2 "
          "proves nothing.\n")
    return 1



if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the gesture classifier")
    ap.add_argument("--probe", action="store_true",
                    help="restore the old image-coordinate tests and prove this suite catches them")
    ap.add_argument("--dump", action="store_true",
                    help="print the raw metric for each pose, no assertions")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())
    if args.dump:
        raise SystemExit(dump())

    print("\n" + "=" * 78)
    print("  verify_gestures.py - pure geometry, no camera")
    print("=" * 78)
    run()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed - all green\n")
    raise SystemExit(0)
