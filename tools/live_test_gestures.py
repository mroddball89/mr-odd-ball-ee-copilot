#!/usr/bin/env python3
"""
Module:  live_test_gestures.py
Purpose: Show what MediaPipe actually sees, live, so gesture thresholds are tuned not guessed.
Author:  LB
Date:    2026-08-23

    python tools/live_test_gestures.py                  the window
    python tools/live_test_gestures.py --pinch 0.35     try a threshold without editing code
    python tools/live_test_gestures.py --mirror off     raw camera orientation
    python tools/live_test_gestures.py --camera 1       a second webcam

    q or ESC   quit, releasing the camera
    s          save the current frame to media/captures/
    h          hide/show the numbers panel

## What this is for

`tools/gesture_control.py` decides gestures from 21 landmarks and a handful of ratio
thresholds. Those thresholds were derived from geometry, not measured, and the module says so
in as many words. **This window is how they stop being guesses.** It draws the skeleton
MediaPipe inferred, names the gesture the real classifier returned, and — the part that
matters — prints the live value of every ratio the decision was made from, beside the
threshold it was compared against.

That last part is the whole point. "The claw isn't detecting" is not something a threshold can
be tuned against. "The claw isn't detecting, and the panel says my ring fingertip is 0.02
BELOW its knuckle when the test wants it above" is. Tune against the number, then move the
constant in `gesture_control.py` and re-run `tools/verify_gestures.py`.

## It calls the real classifier, and that is not a detail

Every gesture name in this window comes from `GestureRecognizer.classify_stream()` — the same
code the security gate and any future gesture command path run. This file contains **no
gesture logic of its own**, deliberately: a debugger with its own copy of the maths is a
debugger that agrees with itself and lies about the program. The numbers panel reads the same
`_is_pinch`, `_hand_scale` and `FINGERS` the decision used.

## Which interpreter this has to run in (the Pi)

mediapipe 1.x on the Pi's Python 3.13 is SIGKILLed the instant a vision task is constructed
(D15). The main venv is 3.13. So on the Pi this must run in `.venv-gesture`, the Python 3.12
venv that `tools/install_gesture_venv.sh` builds — and rather than making that a line in a
README nobody reads, **this script re-executes itself there automatically** when it finds one
and it is not already inside it. `--no-reexec` opts out.

That venv has mediapipe 0.10.18, which still has `mp.solutions` — so `drawing_utils` draws the
skeleton exactly as the MediaPipe docs show. On a box with mediapipe 1.x there is no
`mp.solutions` at all, so the skeleton is drawn from `HAND_CONNECTIONS` below with plain
`cv2.line`. Same picture, and the window still opens on both.

## FLICK only exists here

`get_gesture()` reads one frame from a camera it then closes, in a child process that then
exits. Motion needs two frames and something that remembers the first, so the one-shot
approval path structurally cannot report a flick. This loop holds the camera open and calls
`classify_stream()`, which does remember — so this window is currently the only place FLICK
can be seen at all. Practising it here is how it gets tuned for whatever wires it up later.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# The 21 landmarks joined into a hand, for the branch where `mp.solutions.drawing_utils` does
# not exist. Same edge list mediapipe publishes as `HAND_CONNECTIONS`; written out rather than
# imported so the fallback does not depend on the API that is missing in the case it covers.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),                    # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),                    # index
    (5, 9), (9, 10), (10, 11), (11, 12),               # middle
    (9, 13), (13, 14), (14, 15), (15, 16),             # ring
    (13, 17), (17, 18), (18, 19), (19, 20),            # pinky
    (0, 17),                                           # the base of the palm
)

# BGR, because OpenCV. The approval gesture gets its own colour: on a screen recording it must
# be obvious at a glance which of the six was the one wired to a shell command.
COLOUR = {
    "THUMBS_UP": (80, 220, 80),
    "OPEN_PALM": (230, 200, 90),
    "PINCH":     (200, 140, 255),
    "CLAW":      (90, 170, 255),
    "CLAP":      (255, 190, 120),
    "FLICK":     (120, 255, 255),
    "NONE":      (150, 150, 150),
    "NO_CAMERA": (80, 80, 240),
}


def reexec_into_sidecar() -> None:
    """Re-run this script in the venv that has a working mediapipe, if we are not in it.

    On the Pi, constructing a mediapipe vision task in the main 3.13 venv does not raise — it
    SIGKILLs the process (D15). A tuning tool that dies with exit 137 and no traceback the
    first time LB runs it is worse than useless, so the interpreter is corrected here rather
    than diagnosed afterwards.

    `ODDBALL_GESTURE_SIDECAR=1` in the child is the loop guard: `sidecar_python()` returns ""
    when it is set, so the child can never re-exec again.
    """
    from tools.gesture_control import sidecar_python

    target = sidecar_python()
    if not target:
        return
    if Path(target).resolve() == Path(sys.executable).resolve():
        return
    if not Path(target).exists():
        return

    print(f"  mediapipe lives in another interpreter — re-running there:\n    {target}\n")
    env = dict(os.environ, ODDBALL_GESTURE_SIDECAR="1")
    os.execve(target, [target, str(Path(__file__).resolve()), *sys.argv[1:]], env)


def make_drawer(mp):
    """Return `draw(bgr_frame, landmarks)`, using mediapipe's own drawing when it is there.

    Two branches, one picture:

    * **mediapipe 0.10.x** has `mp.solutions.drawing_utils`, which is what the docs use and
      what LB asked for. It wants a `NormalizedLandmarkList` protobuf, and `detect_hands()`
      hands back a plain list, so the list is repacked into one. That repack is the documented
      bridge in Google's own Tasks-API examples, not a workaround.
    * **mediapipe 1.x** dropped `mp.solutions` entirely. Then the skeleton is drawn with
      `cv2.line` over `HAND_CONNECTIONS`.
    """
    try:
        drawing = mp.solutions.drawing_utils
        styles = mp.solutions.drawing_styles
        from mediapipe.framework.formats import landmark_pb2

        def draw(frame, landmarks):
            proto = landmark_pb2.NormalizedLandmarkList(
                landmark=[landmark_pb2.NormalizedLandmark(x=p.x, y=p.y, z=getattr(p, "z", 0.0))
                          for p in landmarks])
            drawing.draw_landmarks(
                frame, proto, mp.solutions.hands.HAND_CONNECTIONS,
                styles.get_default_hand_landmarks_style(),
                styles.get_default_hand_connections_style())

        return draw, "mp.solutions.drawing_utils"
    except (AttributeError, ImportError):
        pass

    import cv2

    def draw(frame, landmarks):
        h, w = frame.shape[:2]
        pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], (240, 240, 240), 2, cv2.LINE_AA)
        for i, pt in enumerate(pts):
            # Fingertips bigger — they are what every threshold in the classifier is about.
            tip = i in (4, 8, 12, 16, 20)
            cv2.circle(frame, pt, 6 if tip else 4,
                       (60, 120, 255) if tip else (200, 120, 60), -1, cv2.LINE_AA)

    return draw, "cv2.line fallback (no mp.solutions on this mediapipe)"


def label(cv2, frame, text, org, scale=0.6, colour=(235, 235, 235), thick=1) -> None:
    """putText with a dark outline under it, so it stays readable over a bright hand."""
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0),
                thick + 3, cv2.LINE_AA)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick, cv2.LINE_AA)


def hand_numbers(gc, hands) -> list[str]:
    """The live value of every ratio the classifier just decided from.

    This is the tuning surface. Each line pairs what was measured with what it was compared
    against, so a gesture that will not fire says *by how much* it is missing.
    """
    lines: list[str] = []
    for n, h in enumerate(hands):
        scale = gc._hand_scale(h)
        pinch = gc._dist(h[gc.THUMB_TIP], h[gc.INDEX_TIP]) / scale
        lines.append(f"hand {n}   pose {gc._classify(h)}")
        lines.append(f"  palm scale   {scale:.3f}   (the yardstick; all ratios divide by it)")
        lines.append(f"  pinch 4-8    {pinch:.2f}   < {gc.PINCH_MAX_RATIO}  "
                     f"{'YES' if pinch < gc.PINCH_MAX_RATIO else 'no'}")

        # The claw wants pip.y < tip.y < mcp.y on ALL FOUR. Show which finger is refusing.
        bits = []
        for name, (mcp, pip, tip) in zip("IMRP", gc.FINGERS):
            below_pip = h[pip].y < h[tip].y
            above_mcp = h[tip].y < h[mcp].y
            bits.append(f"{name}{'v' if below_pip else '-'}{'^' if above_mcp else '-'}")
        lines.append(f"  claw IMRP    {' '.join(bits)}   "
                     f"{'YES' if gc._is_claw(h) else 'no'}   (v=below PIP, ^=above MCP)")

        ext = "".join(c if gc._extended(h, pip, tip) else "."
                      for c, (_m, pip, tip) in zip("IMRP", gc.FINGERS))
        lines.append(f"  extended     {ext or '....'}")

    if len(hands) == 2:
        ax, ay = gc._palm_centroid(hands[0])
        bx, by = gc._palm_centroid(hands[1])
        gap = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        scale = (gc._hand_scale(hands[0]) + gc._hand_scale(hands[1])) / 2
        up = all(gc._fingers_point_up(h) for h in hands)
        lines.append(f"clap gap     {gap / scale:.2f}   < {gc.CLAP_MAX_GAP_RATIO}   "
                     f"both upright {'YES' if up else 'no'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="live camera window for tuning the gesture classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="q/ESC quit   s save a frame   h hide the numbers")
    ap.add_argument("--camera", type=int, default=0, metavar="N", help="camera index")
    ap.add_argument("--mirror", choices=("on", "off"), default="on",
                    help="flip left-right so the window reads like a mirror (default on)")
    ap.add_argument("--pinch", type=float, metavar="R",
                    help="override PINCH_MAX_RATIO for this run")
    ap.add_argument("--clap-gap", type=float, metavar="R",
                    help="override CLAP_MAX_GAP_RATIO for this run")
    ap.add_argument("--flick-speed", type=float, metavar="V",
                    help="override FLICK_MIN_SPEED for this run")
    ap.add_argument("--no-reexec", action="store_true",
                    help="do not jump into .venv-gesture even if it exists")
    args = ap.parse_args(argv)

    if not args.no_reexec:
        reexec_into_sidecar()               # never returns if it fires

    import tools.gesture_control as gc

    # Overrides land on the module the classifier reads, so the REAL decision changes — the
    # numbers panel and the gesture name can never disagree about which threshold was used.
    for flag, const in (("pinch", "PINCH_MAX_RATIO"), ("clap_gap", "CLAP_MAX_GAP_RATIO"),
                        ("flick_speed", "FLICK_MIN_SPEED")):
        value = getattr(args, flag)
        if value is not None:
            print(f"  {const} = {value}  (was {getattr(gc, const)}, for this run only)")
            setattr(gc, const, value)

    rec = gc.GestureRecognizer()
    if not rec.available:
        print(f"\n  No detector: {rec.why}")
        print(f"  interpreter: {sys.executable}")
        print("\n  On the Pi:  bash tools/install_gesture_venv.sh")
        print("  Then re-run this; it will jump into that venv on its own.\n")
        return 1

    import cv2
    import mediapipe as mp

    draw, how = make_drawer(mp)
    print(f"  backend: {rec.backend}   mediapipe {getattr(mp, '__version__', '?')}")
    print(f"  skeleton: {how}")

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"\n  Camera {args.camera} would not open.\n")
        rec.close()
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, gc.FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, gc.FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, gc.FRAME_FPS)

    window = "MR ODD BALL — gesture tuning"
    show_numbers = True
    fps, last = 0.0, time.monotonic()
    held, held_until = "NONE", 0.0
    saved = 0

    print("\n  q or ESC to quit, s to save a frame, h for the numbers panel\n")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("  the camera stopped returning frames")
                break

            # Mirror BEFORE detection, not after, so the landmarks match what is on screen and
            # a flick to LB's right is reported as RIGHT. Flipping the drawn frame instead
            # would silently invert every x the panel prints.
            if args.mirror == "on":
                frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hands = rec.detect_hands(rgb)
            gesture = rec.classify_stream(hands)

            for landmarks in hands:
                draw(frame, landmarks)

            now = time.monotonic()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last, 1e-6))
            last = now

            # FLICK is one frame wide by construction (it clears its own history so a swipe
            # fires once). One frame at 6.6 fps is 150 ms — too short to read. So it is HELD
            # on screen for a beat. The held banner is the only thing here that is not the
            # instantaneous truth, which is why it says so.
            if gesture == "FLICK":
                held, held_until = f"FLICK {rec.last_flick}", now + 0.8
            banner = held if now < held_until else gesture
            key_colour = COLOUR.get(gesture if now >= held_until else "FLICK", (200, 200, 200))

            h, w = frame.shape[:2]
            label(cv2, frame, banner, (16, 58), scale=1.6, colour=key_colour, thick=4)
            if now < held_until:
                label(cv2, frame, "(held 0.8 s so it can be read)", (18, 84),
                      scale=0.45, colour=(150, 150, 150))

            label(cv2, frame, f"{len(hands)} hand{'s' if len(hands) != 1 else ''}   "
                              f"{fps:4.1f} fps   {rec.backend}",
                  (16, h - 14), scale=0.5, colour=(180, 180, 180))
            label(cv2, frame, "q quit   s save   h numbers", (w - 250, h - 14),
                  scale=0.45, colour=(140, 140, 140))

            if show_numbers:
                for i, line in enumerate(hand_numbers(gc, hands)):
                    label(cv2, frame, line, (16, 118 + i * 19), scale=0.45)

            cv2.imshow(window, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("h"):
                show_numbers = not show_numbers
            if key == ord("s"):
                out = REPO_ROOT / "media" / "captures"
                out.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                path = out / f"gesture-{gesture.lower()}-{stamp}.png"
                cv2.imwrite(str(path), frame)
                saved += 1
                print(f"  saved {path.relative_to(REPO_ROOT)}")

            # The window manager's close button. Without this, clicking X leaves the loop
            # running against a destroyed window and the camera held open.
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        # Every exit path lands here, including the exceptions. A tuning session that leaves
        # /dev/video0 open makes the NEXT run fail with a camera error that has nothing to do
        # with whatever was being tuned.
        cap.release()
        cv2.destroyAllWindows()
        cv2.waitKey(1)                      # some backends need one more tick to actually close
        rec.close()

    if saved:
        print(f"  {saved} frame{'s' if saved != 1 else ''} in media/captures/")
    print("  camera released\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
