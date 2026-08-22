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

## Two mediapipe APIs and a sidecar, because the Pi forced all three (D14, D15)

mediapipe **1.x removed `mp.solutions`** — the legacy Solutions API — and replaced it with
`mediapipe.tasks`. It also stopped building per-interpreter wheels: 1.0.1 ships one
`py3-none-manylinux_2_28_aarch64.whl` that installs on **any** Python 3.

It installs on the Pi's 3.13.5 and then **does not run there.** Measured on the box: every
`vision` task — `HandLandmarker` and `GestureRecognizer` alike — is SIGKILLed the moment the
XNNPACK delegate comes up, with no OOM, no throttling and 6.4 GB free. mediapipe wraps that
construction in `CallWithCoreDumpProtection`, which converts a fatal signal into SIGKILL to
suppress the core dump, so the real fault is masked and exit 137 is all you get.

| | APIs present | aarch64 wheel | installs on 3.13 | **runs on this Pi** |
|---|---|---|---|---|
| 0.10.18 | `solutions` **and** `tasks` | cp39–cp312 | no | **yes** — both of them |
| 0.10.20+ | both | none at all | — | — |
| 1.0.1 | `tasks` only | `py3-none` | yes | **no** — SIGKILL |

Note what the failing variable is: **the mediapipe version, not the API.** 0.10.18 carries both
`mp.solutions` and `mp.tasks`, and on this Pi both work. It is 1.0.1 specifically that dies.

So there are three paths and this module supports all of them, in this order:

1. **Tasks in-process** — mediapipe 1.x. Correct wherever it runs; not this Pi.
2. **Solutions in-process** — mediapipe 0.10.18, on a Python <= 3.12 interpreter.
3. **A sidecar** — `ODDBALL_GESTURE_PYTHON` names a *second* interpreter that has a working
   mediapipe, and this module shells out to it for one token. That is what lets the Pi keep
   its 3.13 venv for whisper, piper and ctranslate2 while gesture detection runs on a small
   3.12 venv beside it, instead of rebuilding 1.9 GB to move one leaf feature.

`_classify()` is shared by 1 and 2 — both APIs hand back the same 21 normalised landmarks in
the same order, so the decision logic with the safety property in it exists once. The sidecar
runs this same file, so it shares that logic too, by being it.

The Tasks path needs a model file, `models/hand_landmarker.task` (7.8 MB). It is NOT fetched
automatically at approval time — a security prompt is the last place to start a download. It
is gitignored and re-downloadable, exactly like the whisper models; `--fetch-model` gets it.

## Pi budget — measured, and it is 2.2 seconds

One approval, end to end from the assistant's venv, median of 10 trials on the Pi:

    interpreter start          22 ms
    import mediapipe        1,009 ms   <-- paid per approval, because of the child process
    build HandLandmarker       55 ms
    open camera               204 ms
    4 warmup frames           602 ms   <-- 150 ms each; the webcam gives ~6.6fps, not 15
    inference                  47 ms
    ------------------------------------
    TOTAL                   2,217 ms   (min 2,197, max 2,271 — very tight)

**Only 102 ms of that is detection.** The rest is a whole Python interpreter and a mediapipe
import, thrown away and rebuilt every time, because the work cannot happen in this process.
`media/charts/gesture-approval-latency.svg` plots it; the CSVs are beside it.

The obvious improvement is a **persistent worker** — pay the 1.0 s import once and keep a pipe
open — which would bring an approval to roughly 850 ms. Not done: it turns a subprocess call
into a lifecycle to manage, and 2.2 s at a prompt that already stops to ask a question is
tolerable. Tracked in `tasks/todo.md`.

`WARMUP_FRAMES` is deliberately NOT tuned down to save the 602 ms. The first frames off a
freshly opened camera are auto-exposure garbage and a black frame reliably detects no hand —
so cutting it is a trade of reliability for latency, and there is no measurement of detection
rate versus warmup count to make that trade on. Guessing here would be the exact mistake D14
is about.

The camera is NOT held open between calls. An approval happens a few times an hour and a held
`VideoCapture` is a device nobody else can use.

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
import sys
from pathlib import Path

LOG = logging.getLogger("oddball.gesture")

__all__ = ["GestureRecognizer", "get_gesture", "gesture_approves",
           "approve_by_gesture_or_keyboard", "MODEL_PATH", "MODEL_URL", "fetch_model",
           "sidecar_python"]

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

# A second interpreter that has a working mediapipe, for when THIS one does not (see the
# header). Named by env, or found at the conventional path below, which is where
# `tools/install_gesture_venv.sh` puts it.
_SIDECAR_DEFAULT = REPO_ROOT / ".venv-gesture" / ("Scripts/python.exe" if os.name == "nt"
                                                  else "bin/python")
SIDECAR_PYTHON = os.environ.get("ODDBALL_GESTURE_PYTHON", "").strip()

# Set in the child's environment by `_ask_sidecar`, so the sidecar can never shell out again.
# Without it a misconfigured ODDBALL_GESTURE_PYTHON pointing at this same interpreter would
# fork forever, at a security prompt, which is the worst possible place for it.
_IS_SIDECAR = os.environ.get("ODDBALL_GESTURE_SIDECAR", "") == "1"

# How long the sidecar gets. It opens a camera and runs one inference: ~0.5s of work, and the
# budget is generous because a Pi under load is slow, not broken. It is still a hard ceiling —
# an approval prompt that hangs on a wedged subprocess is worse than one that falls to the
# keyboard.
SIDECAR_TIMEOUT_S = 20.0

_VALID = ("THUMBS_UP", "OPEN_PALM", "NONE", "NO_CAMERA")


def sidecar_python() -> str:
    """Which interpreter should actually open the camera. Never "" in the parent.

    `ODDBALL_GESTURE_PYTHON` wins, then the conventional `.venv-gesture` beside the repo, then
    **this interpreter** — because the work happens in a child process either way (see
    `_ask_sidecar`), and "no sidecar configured" must not mean "do it here".

    Returns "" only in the child, where it means: stop, do the work.
    """
    if _IS_SIDECAR:
        return ""
    if SIDECAR_PYTHON:
        return SIDECAR_PYTHON
    if _SIDECAR_DEFAULT.exists():
        return str(_SIDECAR_DEFAULT)
    return sys.executable


def _ask_sidecar(python: str) -> str:
    """Run one gesture read in `python` and return its answer.

    ## This is crash isolation, and on this Pi it is not optional

    mediapipe 1.x on Python 3.13 does not raise when its vision task comes up — **it SIGKILLs
    the process** (D15). No `try`/`except` can catch that. Constructing the detector in the
    assistant's own interpreter therefore risks killing the voice loop *at a security prompt*,
    which is precisely the worst place in the program for it to happen.

    So the camera is opened in a **child process, always**, even when no separate sidecar
    interpreter is configured and the child is a second copy of this one. A child that dies is
    a returncode, not a corpse where the assistant used to be. The cost is one process spawn
    per approval — a few times an hour, against a call that already opens a camera.

    The child runs THIS FILE with `--once`, so the classifier — the part with the safety
    property in it — is shared by being the same code, not by being copied.

    Any failure at all is `NO_CAMERA`: a non-zero exit, a timeout, an unparseable answer, a
    missing interpreter. A subprocess that misbehaves must never produce an approval.
    """
    import subprocess

    env = dict(os.environ, ODDBALL_GESTURE_SIDECAR="1")
    try:
        done = subprocess.run(
            [python, str(Path(__file__).resolve()), "--once"],
            capture_output=True, text=True, timeout=SIDECAR_TIMEOUT_S,
            cwd=str(REPO_ROOT), env=env, check=False)
    except subprocess.TimeoutExpired:
        LOG.warning("gesture read timed out after %.0fs", SIDECAR_TIMEOUT_S)
        return "NO_CAMERA"
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("gesture worker failed to run (%s: %s)", type(exc).__name__, exc)
        return "NO_CAMERA"

    if done.returncode != 0:
        # -9/137 is the documented mediapipe-1.x-on-this-Pi failure. Naming it here is what
        # stops the next person rediscovering it from a silent keyboard fallback — and it is
        # the whole reason this runs in a child at all.
        killed = done.returncode in (137, -9)
        LOG.warning("gesture worker exited %d%s", done.returncode,
                    " (SIGKILL — mediapipe cannot run in that interpreter; see D15)"
                    if killed else "")
        return "NO_CAMERA"

    answer = (done.stdout or "").strip().splitlines()
    token = answer[-1].strip() if answer else ""
    if token not in _VALID:
        LOG.warning("gesture worker said %r, which is not a gesture", token[:40])
        return "NO_CAMERA"
    return token


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
    """What the camera sees right now. **Never raises, never dies, never blocks forever.**

    Always out-of-process in the parent — see `_ask_sidecar` for why that is a requirement and
    not an optimisation. In the child (`--once`), this is the in-process read.
    """
    if _DISABLED:
        return "NO_CAMERA"

    python = sidecar_python()
    if python:
        return _ask_sidecar(python)

    # The child. This is the only place mediapipe is ever constructed, and if it takes the
    # process down with it, the parent sees a returncode.
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
    ap.add_argument("--once", action="store_true",
                    help="print exactly one gesture token on stdout and exit. This is the "
                         "sidecar protocol — the parent reads the last stdout line, so nothing "
                         "else may go there.")
    ap.add_argument("--watch", action="store_true", help="keep reading until ctrl-C")
    ap.add_argument("--interval", type=float, default=1.0, metavar="S",
                    help="seconds between reads under --watch (default 1.0)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.fetch_model:
        path = fetch_model()
        print(f"  {path}  ({path.stat().st_size / 1e6:.2f} MB)")
        return 0

    if args.once:
        # stdout carries the token and nothing else; mediapipe is noisy, but on stderr.
        print(get_gesture())
        return 0

    worker = sidecar_python()

    if args.backend:
        print(f"  this interpreter: {sys.executable}")
        print(f"  model:            {MODEL_PATH} "
              f"{'present' if MODEL_PATH.exists() else 'MISSING'}")
        print(f"  disabled:         {_DISABLED} (ODDBALL_GESTURE)")
        if worker:
            # Deliberately NOT constructing the detector here. On a box where mediapipe
            # cannot run, doing so would kill this very process — which is exactly the
            # failure `--backend` exists to diagnose, and a diagnostic that dies of the fault
            # it is reporting on is useless.
            own = Path(worker).resolve() == Path(sys.executable).resolve()
            print(f"  camera worker:    {worker}{'  (same interpreter)' if own else ''}")
            print(f"  worker says:      {_ask_sidecar(worker)}")
            print()
            print("  --- as reported from inside the worker ---")
            import subprocess
            probe = subprocess.run(
                [worker, str(Path(__file__).resolve()), "--backend"],
                capture_output=True, text=True, check=False,
                cwd=str(REPO_ROOT), env=dict(os.environ, ODDBALL_GESTURE_SIDECAR="1"))
            for line in (probe.stdout or "").splitlines():
                print(f"  {line}")
            if probe.returncode != 0:
                print(f"  worker --backend exited {probe.returncode}"
                      f"{'  (SIGKILL — mediapipe cannot run there; see D15)' if probe.returncode in (137, -9) else ''}")
            return 0

        rec = _recognizer()
        print(f"  backend:          {rec.backend or '(none)'}")
        print(f"  available:        {rec.available}")
        if rec.why:
            print(f"  why not:          {rec.why}")
        return 0 if rec.available else 1

    if _DISABLED:
        print("  ODDBALL_GESTURE=0 is set, so get_gesture() will report NO_CAMERA.")

    if not args.watch:
        print(f"  {get_gesture()}")
        return 0

    print(f"  watching via {worker or 'this interpreter'} — thumbs up, open palm, "
          f"ctrl-C to stop")
    try:
        while True:
            print(f"  {time.strftime('%H:%M:%S')}  {get_gesture()}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
