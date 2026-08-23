#!/usr/bin/env python3
"""
Module:  verify_gestures.py
Purpose: Prove the six gestures are told apart, and that NOTHING but a thumbs up approves.
Author:  LB
Date:    2026-08-23

    python tools/verify_gestures.py
    python tools/verify_gestures.py --probe

No camera, no model file, no mediapipe. `_classify` and `_classify_frame` are pure functions
of 21 landmarks per hand, so every case below is a hand built out of arithmetic.

## Why this file exists at all

`docs/DECISIONS.md` has claimed since 2026-08-19 that the classifier is "tested with no camera
at all — six cases, including the one above, all green". **There was no such test in the
repo.** The claim was true when it was written and then the test was never committed, which is
the worst of both worlds: a reviewer reads the sentence and stops looking.

It matters more now than it did then. On 2026-08-23 the vocabulary went from two gestures to
six, and two of the four new ones — `PINCH` and `CLAW` — land inside the region of hand-space
that the thumbs-up test used to accept. `THUMBS_UP` is what `agents/os_agent.py` runs a shell
command on. So the ordering in `_classify` is now load-bearing in the security sense, and an
ordering that is load-bearing and untested is a bug waiting for a refactor.

## The case that matters is section 3

A claw is four fingertips below their PIP joints, which means no finger is *extended*, which
means the old thumbs-up test's "and not any(extended)" clause was satisfied by it. Hold a claw
with the thumb anywhere above the index knuckle and the old classifier said `THUMBS_UP`.

That was tolerable while nobody made claws at the camera on purpose. `barehands` makes the
claw a gesture LB performs deliberately, so the collision went from theoretical to routine in
the same commit that this file is testing.

`--probe` proves the harness bites: it monkeypatches `_classify` back to the pre-2026-08-23
branch order and expects section 3 to go red. A regression test for an ordering bug that still
passes when the ordering is wrong is not a test.
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
# Building a hand out of arithmetic
# --------------------------------------------------------------------------------------
#
# mediapipe hands out 21 landmarks with `.x`, `.y`, `.z` normalised to the frame, y-DOWN. That
# is the entire contract `_classify` depends on, so a namedtuple satisfies it and the tests
# need neither a camera nor mediapipe installed.
#
# The base hand is upright, palm to camera, wrist at (0.50, 0.80) and the knuckle row at
# y = 0.60 — so `_hand_scale` is 0.20, and every ratio below can be checked by hand on paper.

class LM:
    """One landmark. `.z` is present because mediapipe has it, and unused because `_dist` is 2-D."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float = 0.0) -> None:
        self.x, self.y, self.z = x, y, z

    def __repr__(self) -> str:
        return f"({self.x:.2f},{self.y:.2f})"


WRIST_XY = (0.50, 0.80)
MCP_Y = 0.60
MCP_X = {5: 0.44, 9: 0.50, 13: 0.56, 17: 0.62}      # index, middle, ring, pinky knuckles

# Where each finger's tip and PIP sit, per pose, as offsets from the knuckle row.
#   extended  tip well ABOVE the knuckles          -> _extended True
#   fist      tip BELOW the knuckles, curled in    -> not extended, not a claw
#   claw      tip between the PIP and the knuckles -> not extended, IS a claw
POSES = {
    "extended": {"pip": -0.10, "tip": -0.24},
    "fist":     {"pip": -0.05, "tip": +0.04},
    "claw":     {"pip": -0.15, "tip": -0.08},
}


def hand(fingers: str = "extended", thumb: tuple[float, float] = (0.30, 0.55),
         dx: float = 0.0, dy: float = 0.0) -> list[LM]:
    """A 21-landmark hand.

    Args:
        fingers: a key of POSES, applied to all four fingers.
        thumb:   (x, y) of landmark 4, the thumb tip. The gesture-relevant one.
        dx, dy:  translate the whole hand. Used for CLAP (two hands apart) and FLICK (motion).

    Returns:
        A list of 21 LM, indexed exactly as mediapipe indexes them.
    """
    pose = POSES[fingers]
    pts = [None] * 21
    pts[0] = LM(WRIST_XY[0] + dx, WRIST_XY[1] + dy)

    # Thumb chain 1..4. Only landmark 4 is read by the classifier; 1..3 are placed on the
    # line to the tip so the hand is a plausible shape if anything ever draws it.
    for i in range(1, 5):
        f = i / 4.0
        pts[i] = LM(pts[0].x + (thumb[0] + dx - pts[0].x) * f,
                    pts[0].y + (thumb[1] + dy - pts[0].y) * f)

    # Four fingers: MCP, PIP, DIP, TIP at 5..8, 9..12, 13..16, 17..20.
    for mcp, pip, tip in gc.FINGERS:
        x = MCP_X[mcp] + dx
        y_mcp = MCP_Y + dy
        y_pip = y_mcp + pose["pip"]
        y_tip = y_mcp + pose["tip"]
        pts[mcp] = LM(x, y_mcp)
        pts[pip] = LM(x, y_pip)
        pts[pip + 1] = LM(x, (y_pip + y_tip) / 2)          # DIP, unused, kept plausible
        pts[tip] = LM(x, y_tip)
    return pts


def pinch_hand(others: str = "extended") -> list[LM]:
    """A pinch: the index tip curled down to meet the thumb tip.

    `others` is what the middle, ring and pinky are doing. Both variants are tested — the
    natural pinch with the other fingers out, and the one with them curled, which is the
    variant that geometrically resembles a thumbs up and used to be read as one.
    """
    h = hand(fingers=others, thumb=(0.42, 0.50))
    h[6] = LM(0.44, 0.48)                                   # index PIP, above the tip
    h[7] = LM(0.43, 0.49)
    h[8] = LM(0.42, 0.50)                                   # index tip, on the thumb tip
    return h


# --------------------------------------------------------------------------------------
def run() -> None:
    section("1. the two original gestures, unchanged")

    open_palm = hand("extended")
    check(gc._classify(open_palm) == "OPEN_PALM",
          "an open hand is OPEN_PALM", gc._classify(open_palm))

    thumbs = hand("fist", thumb=(0.40, 0.40))
    check(gc._classify(thumbs) == "THUMBS_UP",
          "a fist with the thumb up is THUMBS_UP", gc._classify(thumbs))

    # The case docs/DECISIONS.md is about: the naive test ("thumb above the index knuckle and
    # above the wrist") is TRUE for this hand. It must still not be an approval.
    naive = open_palm
    thumb_passes_naive = (naive[gc.THUMB_TIP].y < naive[gc.FINGERS[0][0]].y
                          and naive[gc.THUMB_TIP].y < naive[gc.WRIST].y)
    check(thumb_passes_naive and gc._classify(naive) == "OPEN_PALM",
          "an open palm passes the NAIVE thumbs-up test and is still not an approval",
          f"naive says {thumb_passes_naive}, classifier says {gc._classify(naive)}")

    check(gc._classify(hand("fist", thumb=(0.40, 0.85))) == "NONE",
          "a fist with the thumb DOWN is NONE, not an approval",
          gc._classify(hand("fist", thumb=(0.40, 0.85))))

    section("2. the four gestures from barehands")

    for others in ("extended", "fist"):
        got = gc._classify(pinch_hand(others))
        check(got == "PINCH", f"thumb tip on index tip is PINCH (others {others})", got)

    claw = hand("claw", thumb=(0.34, 0.52))
    check(gc._classify(claw) == "CLAW", "a strained claw is CLAW", gc._classify(claw))

    near = [hand("extended", dx=-0.08), hand("extended", dx=+0.08)]
    check(gc._classify_frame(near) == "CLAP",
          "two upright palms held together is CLAP", gc._classify_frame(near))

    far = [hand("extended", dx=-0.25), hand("extended", dx=+0.25)]
    check(gc._classify_frame(far) == "OPEN_PALM",
          "two palms far apart is NOT a clap", gc._classify_frame(far))

    sideways = [hand("extended", dx=-0.08, dy=-0.44), hand("extended", dx=+0.08, dy=-0.44)]
    # Both hands raised so far that the fingertips no longer clear the wrist by CLAP_MIN_
    # FINGER_RISE would break the rise test; here they still do, so this must stay a clap.
    check(gc._classify_frame(sideways) == "CLAP",
          "a clap higher in the frame is still a clap", gc._classify_frame(sideways))

    section("3. the collision — nothing but a thumbs up may approve")

    # THE case. This claw has its thumb above the index knuckle AND above the wrist, so it
    # satisfies the thumbs-up test's own geometry, and every finger is un-extended, so it
    # satisfies the curl clause too. Only the branch ORDER keeps it from being an approval.
    assert claw[gc.THUMB_TIP].y < claw[gc.FINGERS[0][0]].y, "test setup: thumb must be high"
    assert claw[gc.THUMB_TIP].y < claw[gc.WRIST].y, "test setup: thumb must clear the wrist"
    assert not any(gc._extended(claw, pip, tip) for _m, pip, tip in gc.FINGERS)

    check(gc._classify(claw) != "THUMBS_UP",
          "a CLAW that passes every thumbs-up test is NOT an approval",
          f"classified {gc._classify(claw)}")

    check(gc._classify(pinch_hand("fist")) != "THUMBS_UP",
          "a PINCH with the other fingers curled is NOT an approval",
          f"classified {gc._classify(pinch_hand('fist'))}")

    # Exhaustive over the shapes this harness can make: the ONLY pose that may return
    # THUMBS_UP is the fist with the thumb up.
    approvals = []
    for pose in POSES:
        for tx, ty in ((0.40, 0.40), (0.34, 0.52), (0.30, 0.55), (0.42, 0.50), (0.40, 0.85)):
            h = hand(pose, thumb=(tx, ty))
            if gc._classify(h) == "THUMBS_UP":
                approvals.append((pose, (tx, ty)))
    check(all(pose == "fist" for pose, _ in approvals),
          f"across all {len(POSES) * 5} generated poses, only a fist approves",
          f"approving poses: {approvals}")

    check(gc._classify_frame([]) == "NONE", "no hands in frame is NONE")

    section("4. two hands never combine into an approval")

    # Neither hand is a thumbs up, so no arrangement of them may produce one.
    for a in (open_palm, claw, pinch_hand()):
        for b in (open_palm, claw, pinch_hand()):
            got = gc._classify_frame([a, b])
            if got == "THUMBS_UP":
                check(False, "two non-approving hands produced an approval", got)
                break
    else:
        check(True, "no pair of non-approving hands classifies as THUMBS_UP")

    # A deliberate thumbs up alongside an open hand IS the approval — _PRIORITY's one
    # judgement call, and the reason it is not the safe-looking default.
    check(gc._classify_frame([open_palm, thumbs]) == "THUMBS_UP",
          "a thumbs up beside an open hand is still an approval",
          gc._classify_frame([open_palm, thumbs]))

    section("5. FLICK needs motion, and jitter is not motion")

    def stream(samples):
        """Feed (t, dx) pairs of an open palm to a fresh recogniser; return the last token."""
        rec = gc.GestureRecognizer.__new__(gc.GestureRecognizer)     # no camera, no mediapipe
        rec._history = __import__("collections").deque(maxlen=gc.FLICK_HISTORY)
        rec.last_flick = ""
        out = []
        for t, dx in samples:
            out.append(rec.classify_stream([hand("extended", dx=dx)], now=t))
        return out, rec

    got, rec = stream([(0.00, -0.20), (0.20, +0.10)])
    check(got[-1] == "FLICK", "an open palm crossing the frame in 200 ms is FLICK", str(got))
    check(rec.last_flick == "RIGHT", "and its direction is recorded", rec.last_flick)

    got, _ = stream([(0.00, -0.20), (1.00, +0.10)])
    check(got[-1] == "OPEN_PALM",
          "the same travel over a full second is NOT a flick (outside the window)", str(got))

    got, _ = stream([(0.00, 0.00), (0.05, 0.03), (0.10, 0.00), (0.15, 0.03)])
    check("FLICK" not in got, "landmark jitter is not a flick (below the travel floor)", str(got))

    got, rec = stream([(0.00, +0.20), (0.20, -0.10)])
    check(got[-1] == "FLICK" and rec.last_flick == "LEFT",
          "a flick the other way is LEFT", f"{got[-1]} {rec.last_flick}")

    # Edge-triggered: one swipe is one flick, not a flick per frame.
    got, _ = stream([(0.00, -0.20), (0.20, +0.10), (0.40, +0.40)])
    check(got.count("FLICK") == 1,
          "one continuous swipe fires exactly ONE flick", str(got))

    section("6. the sidecar protocol carries every token")

    for token in ("PINCH", "CLAP", "CLAW", "FLICK", "THUMBS_UP", "OPEN_PALM", "NONE"):
        check(token in gc._VALID, f"{token} survives the sidecar whitelist")
    check(set(gc._PRIORITY) <= set(gc._VALID), "_PRIORITY names no token _VALID would reject")
    check(gc.MAX_HANDS == 2, "the detector is allowed both hands, or CLAP can never fire")


def probe() -> int:
    """Restore the pre-2026-08-23 branch order and prove section 3 goes red.

    The bug being guarded against is an ORDERING bug, and an ordering bug is invisible to a
    test that only ever sees the fixed order. So put the old order back and watch the claw
    become an approval.
    """
    print("\n  PROBE — restoring the pre-2026-08-23 branch order (no PINCH/CLAW guard)\n")

    def old_classify(landmarks) -> str:
        extended = [gc._extended(landmarks, pip, tip) for _m, pip, tip in gc.FINGERS]
        if all(extended):
            return "OPEN_PALM"
        thumb_up = (landmarks[gc.THUMB_TIP].y < landmarks[gc.FINGERS[0][0]].y
                    and landmarks[gc.THUMB_TIP].y < landmarks[gc.WRIST].y)
        if thumb_up and not any(extended):
            return "THUMBS_UP"
        return "NONE"

    claw = hand("claw", thumb=(0.34, 0.52))
    pinch = pinch_hand("fist")

    bitten = []
    if old_classify(claw) == "THUMBS_UP":
        bitten.append("CLAW")
    if old_classify(pinch) == "THUMBS_UP":
        bitten.append("PINCH")

    for name, h in (("CLAW", claw), ("PINCH", pinch)):
        print(f"   {name:9s} old order -> {old_classify(h):10s}   "
              f"new order -> {gc._classify(h)}")

    print()
    if bitten:
        print(f"   The harness BITES: {' and '.join(bitten)} approve a shell command "
              f"under the old order.\n")
        return 0
    print("   The harness is VACUOUS: the old order classified these safely too, so "
          "section 3 proves nothing.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the gesture classifier")
    ap.add_argument("--probe", action="store_true",
                    help="restore the old branch order and prove section 3 catches it")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    print("\n" + "=" * 78)
    print("  verify_gestures.py — pure geometry, no camera")
    print("=" * 78)
    run()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(0)
