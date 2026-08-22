#!/usr/bin/env python3
"""
Module:  gesture_control.py
Purpose: Approve an action with your hand instead of the keyboard. Camera in, gesture out.
Author:  LB
Date:    2026-08-21 (ported to the mediapipe Tasks API 2026-08-22)

    python tools/gesture_control.py --fetch-model    # once, ~7.8 MB
    python tools/gesture_control.py                  # one shot: what does the camera see
    python tools/gesture_control.py --watch          # keep reading, ctrl-C to stop
    python tools/gesture_control.py --backend        # which mediapipe API is in use

Returns exactly one of `THUMBS_UP`, `OPEN_PALM`, `NONE` or `NO_CAMERA`. Wired into the
terminal security prompts in `agents/os_agent.py` and `agents/web_agent.py`: a thumbs up is a
yes, and anything else falls through to the keyboard.

## Two mediapipe APIs, because the wheel situation forced it (D14)

mediapipe **1.x removed `mp.solutions`** — the legacy Solutions API — and replaced it with
`mediapipe.tasks`. It also stopped building per-interpreter wheels: 1.0.1 ships one
`py3-none-manylinux_2_28_aarch64.whl`, which installs on **any** Python 3, the Pi's 3.13.5
included. The last 0.10.x with an aarch64 wheel at all is 0.10.18, capped at cp312.

So the two are not "old and new", they are a real fork in what you can install:

| | `mp.solutions.hands` | aarch64 wheel | Python |
|---|---|---|---|
| mediapipe 0.10.18 | yes | cp39–cp312 | ≤ 3.12 |
| mediapipe 1.0.1 | **gone** | `py3-none` | any 3.x |

This module supports **both**, preferring Tasks. That is not hedging: it is what lets the Pi
run gesture control on the venv it already has, while a 3.12 box pinned to 0.10.18 keeps
working unchanged. `_classify()` is shared — both APIs hand back the same 21 normalised
landmarks in the same order, so the decision logic never had to be written twice.

The Tasks path needs a model file, `models/hand_landmarker.task` (7.8 MB). It is NOT fetched
automatically at approval time — a security prompt is the last place to start a download. It
is gitignored and re-downloadable, exactly like the whisper models; `--fetch-model` gets it.

## Pi budget

640x480 at a 15fps cap, one frame per call, camera opened and released around it. The camera
open dominates — measured 3 ms for `detect()` on a blank frame — which is why the landmarker
is built once at module level and reused rather than rebuilt per call.

The camera is NOT held open between calls. An approval prompt happens a few times an hour and
a held `VideoCapture` is a device nobody else can use. Reopening costs ~200-400ms and buys the
device back.

## The thumbs-up test is stricter than the obvious one, and it has to be

The obvious test is "thumb tip is above the index knuckle and above the wrist". **An open palm
passes that test** — with your hand up and open, the thumb is above both. So the obvious test
turns a wave into an approval, and what it approves is a shell command on `os_agent`'s path.

So THUMBS_UP additionally requires the other four fingers to be **curled**: each tip below its
own PIP joint. Open palm is checked first, and the two are mutually exclusive by construction.
A gesture that fails both is `NONE`, which declines to the keyboard — the safe direction.

Image coordinates run y-DOWN, so "above" is a smaller y. Every comparison below is in
normalised landmark space (0..1 of the frame), so it is resolution-independent.

## What this is not

It is not a second authority. The blocklist in `tools/os_controller.py` runs regardless of how
approval arrived, and the exact command is still printed before the question is asked. A
gesture replaces the keystroke, not the review.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG = logging.getLogger("oddball.gesture")

__all__ = ["GestureRecognizer", "get_gesture", "gesture_approves",
           "approve_by_gesture_or_keyboard", "MODEL_PATH", "MODEL_URL", "fetch_model"]

REPO_ROOT = Path(__file__).resolve().parents[1]

# The Tasks-API hand landmarker. Google's published float16 build — the same file the
# mediapipe docs point at, pinned to revision 1 of the URL so a silent upstream reroll cannot
# change what the gate is running.
MODEL_PATH = REPO_ROOT / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")

# Pi camera budget. 640x480 is what mediapipe wants anyway — it downscales internally — and
# the fps cap keeps the driver from negotiating a 30fps mode we immediately throw away.
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_FPS = 15

# The first frame off a freshly opened camera is auto-exposure garbage: on the Pi's module it
# is usually near-black, and a black frame reliably detects no hand. Pull and discard a few.
WARMUP_FRAMES = 4

# Hand landmark indices, named. `landmarks[8]` in a boolean expression is how the open-palm
# and thumbs-up tests come to look identical to a reviewer. Identical in both mediapipe APIs.
WRIST = 0
THUMB_TIP = 4
# (mcp, pip, tip) for each of the four fingers that must curl for a thumbs up.
FINGERS = (
    (5, 6, 8),      # index
    (9, 10, 12),    # middle
    (13, 14, 16),   # ring
    (17, 18, 20),   # pinky
)

# Set ODDBALL_GESTURE=0 to keep the camera shut and go straight to the keyboard. Wanted on the
# Windows authoring box, where `--text` mode is used for debugging agents and opening a webcam
# on every approval prompt is friction with no purpose.
_DISABLED = os.environ.get("ODDBALL_GESTURE", "1").strip().lower() in ("0", "false", "no", "off")


def fetch_model(dest: Path = MODEL_PATH) -> Path:
    """Download the hand landmarker model. Returns its path.

    Called by `--fetch-model` and by the deploy docs, never on the approval path.
    """
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("downloading %s -> %s", MODEL_URL, dest)
    urllib.request.urlretrieve(MODEL_URL, dest)
    return dest


def _extended(landmarks, pip: int, tip: int) -> bool:
    """True when this finger points up — its tip is above its own middle joint."""
    return landmarks[tip].y < landmarks[pip].y


def _classify(landmarks) -> str:
    """One hand's 21 landmarks -> 'THUMBS_UP', 'OPEN_PALM' or 'NONE'.

    Pure, so it is testable without a camera, without a model file and without mediapipe
    installed at all: hand it any sequence of 21 objects with a `.y`. Shared by both backends,
    which is the reason the fork above costs almost nothing.
    """
    extended = [_extended(landmarks, pip, tip) for _mcp, pip, tip in FINGERS]

    # Open palm FIRST. It is the permissive gesture and it overlaps the naive thumbs-up test,
    # so checking it first is what stops a wave being read as a yes.
    if all(extended):
        return "OPEN_PALM"

    # Thumbs up: thumb above the index knuckle AND above the wrist, with every other finger
    # curled. The curl requirement is the whole difference between this and a raised hand.
    thumb_up = (landmarks[THUMB_TIP].y < landmarks[FINGERS[0][0]].y
                and landmarks[THUMB_TIP].y < landmarks[WRIST].y)
    if thumb_up and not any(extended):
        return "THUMBS_UP"

    return "NONE"


class GestureRecognizer:
    """One hand detector, reused across calls, over whichever mediapipe API is installed.

    `cv2` and `mediapipe` are imported inside `__init__`, not at module scope, and that is
    load-bearing: `agents/os_agent.py` imports this module, so a top-level `import cv2` on a
    box without OpenCV would take out the entire OS route rather than just the camera. Same
    reasoning as `tools/kicad_parser.py` wrapping `kiutils`.

    `self.backend` is "tasks", "solutions" or "" — reported by `--backend`, because "which
    mediapipe am I on" is the first question any problem here will raise.
    """

    def __init__(self) -> None:
        self._detect = None
        self._close = None
        self._cv2 = None
        self.backend = ""
        self.why = ""

        try:
            import cv2
        except Exception as exc:                                          # noqa: BLE001
            self.why = f"opencv is not installed ({type(exc).__name__})"
            LOG.info("gesture control unavailable: %s — the keyboard still works", self.why)
            return
        self._cv2 = cv2

        try:
            import mediapipe as mp
        except Exception as exc:                                          # noqa: BLE001
            self.why = f"mediapipe is not installed ({type(exc).__name__})"
            LOG.info("gesture control unavailable: %s — the keyboard still works", self.why)
            return

        # mediapipe 1.x — the Tasks API. Preferred: it is the only one with a wheel that
        # installs on the Pi's Python 3.13.
        if hasattr(mp, "tasks"):
            if not MODEL_PATH.exists():
                self.why = (f"{MODEL_PATH.name} is missing — run "
                            f"`python tools/gesture_control.py --fetch-model`")
                LOG.info("gesture control unavailable: %s", self.why)
                return
            try:
                from mediapipe.tasks import python as mpp
                from mediapipe.tasks.python import vision

                landmarker = vision.HandLandmarker.create_from_options(
                    vision.HandLandmarkerOptions(
                        base_options=mpp.BaseOptions(model_asset_path=str(MODEL_PATH)),
                        running_mode=vision.RunningMode.IMAGE,
                        num_hands=1,
                        min_hand_detection_confidence=0.6,
                        min_tracking_confidence=0.6,
                    ))
            except Exception as exc:                                      # noqa: BLE001
                self.why = f"the Tasks landmarker would not load ({type(exc).__name__}: {exc})"
                LOG.warning("gesture control unavailable: %s", self.why)
                return

            def detect(rgb):
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                return landmarker.detect(image).hand_landmarks

            self._detect, self._close, self.backend = detect, landmarker.close, "tasks"
            return

        # mediapipe 0.10.x — the legacy Solutions API. Still correct on a Python <= 3.12 box
        # pinned to 0.10.18, and it needs no model file.
        if hasattr(mp, "solutions"):
            try:
                hands = mp.solutions.hands.Hands(
                    max_num_hands=1,
                    min_detection_confidence=0.6,
                    min_tracking_confidence=0.6,
                )
            except Exception as exc:                                      # noqa: BLE001
                self.why = f"Hands() would not construct ({type(exc).__name__}: {exc})"
                LOG.warning("gesture control unavailable: %s", self.why)
                return

            def detect(rgb):
                found = hands.process(rgb).multi_hand_landmarks
                return [h.landmark for h in (found or [])]

            self._detect, self._close, self.backend = detect, hands.close, "solutions"
            return

        self.why = (f"mediapipe {getattr(mp, '__version__', '?')} has neither `tasks` nor "
                    f"`solutions` — this module knows no third API")
        LOG.warning("gesture control unavailable: %s", self.why)

    @property
    def available(self) -> bool:
        """True when a detector was built. False means keyboard only, and `why` says why."""
        return self._detect is not None

    def get_gesture(self) -> str:
        """
        Captures a single frame from the camera and returns:
        'THUMBS_UP', 'OPEN_PALM', or 'NONE'.
        Optimized for Raspberry Pi camera processing.

        Returns 'NO_CAMERA' when there is no camera, when mediapipe or OpenCV are missing, or
        when the model file has not been fetched — none of which is ever an approval.
        """
        if not self.available:
            return "NO_CAMERA"

        cv2 = self._cv2
        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                return "NO_CAMERA"
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, FRAME_FPS)

            ret, frame = False, None
            for _ in range(WARMUP_FRAMES):
                ret, frame = cap.read()
                if not ret:
                    break
        finally:
            cap.release()

        if not ret or frame is None:
            return "NO_CAMERA"

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        for landmarks in self._detect(rgb_frame):
            gesture = _classify(landmarks)
            if gesture != "NONE":
                return gesture
        return "NONE"

    def close(self) -> None:
        """Release the detector. Safe to call twice."""
        if self._close is not None:
            try:
                self._close()
            except Exception:                                             # noqa: BLE001
                pass
            self._detect = self._close = None


# One recogniser for the process. Built on first use rather than at import, so importing this
# module costs nothing until something actually looks at the camera.
_RECOGNIZER: GestureRecognizer | None = None


def _recognizer() -> GestureRecognizer:
    global _RECOGNIZER
    if _RECOGNIZER is None:
        _RECOGNIZER = GestureRecognizer()
    return _RECOGNIZER


def get_gesture() -> str:
    """What the camera sees right now. Never raises; returns 'NO_CAMERA' on any failure."""
    if _DISABLED:
        return "NO_CAMERA"
    try:
        return _recognizer().get_gesture()
    except Exception as exc:                                              # noqa: BLE001
        LOG.warning("gesture read failed (%s: %s)", type(exc).__name__, exc)
        return "NO_CAMERA"


def gesture_approves() -> bool:
    """True only for a clear thumbs up. Every other outcome, including error, is False."""
    return get_gesture() == "THUMBS_UP"


def approve_by_gesture_or_keyboard(prompt: str = "   Allow execution? (y/n): ") -> bool:
    """Ask for approval by camera, then by keyboard. **Only a yes returns True.**

    The order matters and only in one direction: a thumbs up short-circuits the keyboard, but
    nothing else short-circuits anything. No camera, no hand, an open palm, an exception —
    all of them fall through to `input()`, so the worst a broken camera can do is make LB type
    a letter he was already going to type.

    Args:
        prompt: what to print when falling back to the keyboard.

    Returns:
        True if approved.
    """
    seen = get_gesture()
    if seen == "THUMBS_UP":
        print("   👍 Thumbs up — approved by gesture.")
        return True
    if seen not in ("NO_CAMERA", "NONE"):
        print(f"   (camera saw {seen}, which is not an approval)")

    try:
        return input(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        # No stdin, or ctrl-C at the prompt. Both are declines. A gate that defaults open
        # under an unexpected condition is not a gate.
        print()
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="read a hand gesture from the camera")
    ap.add_argument("--fetch-model", action="store_true",
                    help=f"download {MODEL_PATH.name} (~7.8 MB) and exit")
    ap.add_argument("--backend", action="store_true",
                    help="report which mediapipe API is in use, and exit")
    ap.add_argument("--watch", action="store_true", help="keep reading until ctrl-C")
    ap.add_argument("--interval", type=float, default=1.0, metavar="S",
                    help="seconds between reads under --watch (default 1.0)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.fetch_model:
        path = fetch_model()
        print(f"  {path}  ({path.stat().st_size / 1e6:.2f} MB)")
        return 0

    rec = _recognizer()
    if args.backend:
        print(f"  backend:   {rec.backend or '(none)'}")
        print(f"  available: {rec.available}")
        if rec.why:
            print(f"  why not:   {rec.why}")
        print(f"  model:     {MODEL_PATH} {'present' if MODEL_PATH.exists() else 'MISSING'}")
        print(f"  disabled:  {_DISABLED} (ODDBALL_GESTURE)")
        return 0 if rec.available else 1

    if not rec.available:
        print(f"  gesture control is off: {rec.why}")
        return 1
    if _DISABLED:
        print("  ODDBALL_GESTURE=0 is set, so get_gesture() will report NO_CAMERA.")

    if not args.watch:
        print(f"  {rec.get_gesture()}")
        return 0

    print(f"  watching on the {rec.backend} backend — thumbs up, open palm, ctrl-C to stop")
    try:
        while True:
            print(f"  {time.strftime('%H:%M:%S')}  {rec.get_gesture()}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
