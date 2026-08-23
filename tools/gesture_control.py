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

    python tools/live_test_gestures.py               # the camera window, for tuning these

Returns exactly one of `PINCH`, `CLAP`, `CLAW`, `FLICK`, `THUMBS_UP`, `OPEN_PALM`, `NONE` or
`NO_CAMERA`. Wired into the terminal security prompts in `agents/os_agent.py` and
`agents/web_agent.py`: **a thumbs up is a yes, and every other token falls through to the
keyboard** — which is why the six-gesture vocabulary below costs the gate nothing.

`FLICK` needs motion, so it needs consecutive frames. `get_gesture()` reads ONE frame from a
camera it then closes, in a child process that then exits (see `_ask_sidecar`), so there is no
previous frame for it to have moved from: **the one-shot path never returns `FLICK`.** It is
produced by `classify_stream()`, the continuous path, which `tools/live_test_gestures.py` and
any future always-on loop use. Naming it in `_VALID` anyway is deliberate — the token is part
of the vocabulary, and a whitelist that omits it would silently turn a future streaming
sidecar's honest answer into `NO_CAMERA`.

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

**That table is the 2026-08-21 measurement and WARMUP_FRAMES is now 5**, so expect ~2,367 ms
until it is re-measured. The row is left at 4 rather than quietly edited to 5: the numbers
beside it were measured together, and a table with one value swapped by hand is a table that
no longer describes any single run.

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

It went the other way on 2026-08-22: **4 -> 5**, with `MIN_DETECTION_CONFIDENCE` 0.6 -> 0.5,
after LB reported thumbs-up going undetected. Both changes point at the same suspected cause —
an underexposed frame — and both are still guesses, for the reason above: there is no
detection-rate measurement to tune against. What makes them acceptable is direction. A missed
thumbs-up costs one retry and falls back to the keyboard; neither change can turn a non-approval
into an approval, because that decision is `_classify()`, which is pure geometry.

**The honest next step is a measurement, not another nudge** — detection rate against warmup
count and confidence, over a set of saved frames. Tracked in `tasks/todo.md`.

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

## Four more gestures, and the reason three of them are tested BEFORE the thumbs up

The vocabulary grew on 2026-08-23 to the set in `jaredrhod/barehands` — `PINCH`, `CLAP`,
`CLAW`, `FLICK` — so the assistant can be driven by hand and not only *approved* by hand.
Adding them to a module whose output is wired into a security gate has one non-obvious
consequence, and it is the most important thing on this page.

**A claw and a pinch both pass the old thumbs-up test.** That test is "thumb above the index
knuckle, above the wrist, and no finger *extended*", where extended means `tip.y < pip.y`. A
claw holds every fingertip below its PIP — so it is not extended. A pinch curls the index down
to meet the thumb — so it is not extended either. Both therefore satisfy `not any(extended)`,
and both put the thumb high. Under the pre-2026-08-23 classifier, **a claw at the camera was a
`THUMBS_UP`**, and on `agents/os_agent.py`'s path a `THUMBS_UP` runs a shell command.

That was survivable while nobody made claws at the camera on purpose. It stops being
survivable the moment "the claw" is a gesture LB actually performs, which is what this change
makes it. So the collision is closed, by ordering:

    CLAP  ->  OPEN_PALM  ->  PINCH  ->  CLAW  ->  THUMBS_UP  ->  NONE

and by picking a discriminator that separates a fist from a claw cleanly rather than by
threshold luck. **A real fist tucks the fingertips BELOW the knuckles; a claw holds them
ABOVE.** So:

    thumbs up   tip.y > mcp.y     tips below the MCP line   (curled into the palm)
    claw        tip.y < mcp.y     tips above the MCP line   (strained, half-open)
                pip.y < tip.y     ...but still below the PIP  (not extended)

Those two cases cannot both be true, so a claw can never reach the `THUMBS_UP` branch and a
genuine thumbs up is never eaten by the claw branch. Mutually exclusive **by construction** —
the same property `OPEN_PALM` has had since 2026-08-19, and for the same reason.

Note what this did NOT do: **the thumbs-up test itself is unchanged.** Not one threshold in it
moved. LB has reported missed thumbs-ups before (see `WARMUP_FRAMES` above) and tightening the
approval geometry in the same commit that adds four gestures would have made the next missed
approval impossible to attribute. The new gestures are *guards placed in front of* the gate,
not edits to it. The net effect on the gate is strictly fewer false approvals and exactly the
same true ones.

## Distances are measured in palm-lengths, not in frame-widths

`PINCH` is "landmark 4 touches landmark 8", and the obvious implementation compares their
Euclidean distance to a constant. That constant is wrong at every camera distance but one:
landmarks are normalised to the FRAME, so a hand at arm's length is half the size of the same
hand up close, and a fixed 0.05 that works at 40 cm reads every relaxed hand as a pinch at 80 cm.

So every distance here is divided by `_hand_scale()` — the wrist-to-middle-knuckle span, which
is the one length on a hand that does not change when the fingers move. A pinch is then
"the gap is under 0.40 **palm-lengths**", which is true at any distance from the camera and on
any size of hand. Same trick for the `CLAP` gap.

The thresholds below are starting values, chosen from geometry rather than measured, and they
are exactly what `tools/live_test_gestures.py` exists to tune: it prints the live ratio next to
the gesture so a threshold can be moved against something observed instead of guessed. That is
the same discipline `WARMUP_FRAMES` is still waiting on.

## What this is not

It is not a second authority. The blocklist in `tools/os_controller.py` runs regardless of how
approval arrived, and the exact command is still printed before the question is asked. A
gesture replaces the keystroke, not the review.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import deque
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

# 2 since 2026-08-23, because CLAP is a two-hand gesture and a detector capped at one hand can
# never see it. It is not free — mediapipe runs the landmark model once PER HAND, so a frame
# with two hands in it costs about twice the 47 ms inference measured in the budget above.
# That is ~2.26 s instead of ~2.22 s for an approval, which is inside the noise of that table.
#
# It does not weaken the gate. Hands are classified INDIVIDUALLY and then resolved by
# `_PRIORITY`, so a second hand can only ever add a gesture of its own — it cannot combine
# with the first to manufacture a THUMBS_UP that neither hand is making.
MAX_HANDS = 2

# The first frame off a freshly opened camera is auto-exposure garbage: on the Pi's module it
# is usually near-black, and a black frame reliably detects no hand. Pull and discard a few.
#
# 5 as of 2026-08-22, up from 4. The measured cost is ~150 ms per frame on this webcam (it
# gives ~6.6 fps, not the 15 requested), so this buys a little more auto-exposure settle time
# for 150 ms of the 2.2 s budget. Raised alongside the confidence drop below, because both
# failures LB reported — "it didn't see my thumb" — are consistent with an underexposed frame.
WARMUP_FRAMES = 5

# How sure mediapipe must be that it is looking at a hand at all.
#
# 0.5 as of 2026-08-22, down from 0.6, because LB reported thumbs-up going undetected. It is
# ONE constant now rather than a literal repeated in each API branch — two numbers that must
# agree is one number somebody edits.
#
# Lowering this is safe in a way that is worth being explicit about, because "make approval
# more forgiving" sounds like the opposite: this threshold governs *is there a hand in frame*,
# NOT *is it a thumbs up*. The gesture decision is `_classify()`, which is pure geometry with
# no confidence in it, and it still demands the other four fingers be curled — an open palm
# is not an approval at any detection confidence. So this makes the hand easier to FIND and
# does not make approval easier to GET.
MIN_DETECTION_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5

# Hand landmark indices, named. `landmarks[8]` in a boolean expression is how the open-palm
# and thumbs-up tests come to look identical to a reviewer. Identical in both mediapipe APIs.
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9      # the far end of `_hand_scale()`; see the palm-lengths section above
# (mcp, pip, tip) for each of the four fingers that must curl for a thumbs up.
FINGERS = (
    (5, 6, 8),      # index
    (9, 10, 12),    # middle
    (13, 14, 16),   # ring
    (17, 18, 20),   # pinky
)
# The knuckle row, wrist included: the five points that move together as one rigid palm no
# matter what the fingers are doing. Their mean is the palm centroid, which is what `FLICK`
# tracks — a fingertip would add finger motion to hand motion and read a wave as a flick.
PALM = (WRIST, 5, 9, 13, 17)

# --- thresholds for the four gestures added 2026-08-23 ----------------------------------
#
# Every one of these is a RATIO against `_hand_scale()`, not a raw normalised distance, for
# the reason in the palm-lengths section above. All are geometry-derived starting values,
# NOT measurements — `tools/live_test_gestures.py` prints the live ratio so they can be moved
# against something observed. Do not nudge them from memory of one bad frame.

# Thumb tip to index tip, in palm-lengths. Touching is ~0.15; an index pointing away with the
# thumb out is ~1.3. 0.40 sits in the empty middle with room on both sides.
PINCH_MAX_RATIO = 0.40

# Two palms, centroid to centroid, in palm-lengths, for a clap. Hands actually touching are
# ~1.0 apart centroid-wise (a palm-width); 1.8 allows the approach and the rebound without
# reaching across the frame for an unrelated second hand.
CLAP_MAX_GAP_RATIO = 1.8

# How far above the wrist a fingertip must sit to count as "pointing up" for a clap, again in
# palm-lengths. A hand held flat edge-on to the camera collapses toward 0.
CLAP_MIN_FINGER_RISE = 0.6

# FLICK: an open palm crossing the frame. Speed is in frame-widths per second, measured over
# a short window of recent frames.
#
# The travel floor is what stops jitter being a flick: at 6.6 fps a single noisy landmark can
# look fast over one 150 ms gap, but it cannot also have MOVED a fifth of the frame.
FLICK_WINDOW_S = 0.45          # how far back to look
FLICK_MIN_SPEED = 1.1          # frame-widths per second
FLICK_MIN_TRAVEL = 0.18        # normalised x, floor on total displacement
FLICK_HISTORY = 12             # frames kept; ~1.8 s at this webcam's 6.6 fps

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

# The sidecar protocol whitelist. The parent accepts a child's stdout token ONLY if it is in
# here — anything else becomes NO_CAMERA (see `_ask_sidecar`), so a token added to `_classify`
# and forgotten here would be silently downgraded to "the camera is broken".
_VALID = ("PINCH", "CLAP", "CLAW", "FLICK", "THUMBS_UP", "OPEN_PALM", "NONE", "NO_CAMERA")

# Which gesture wins when the two hands in frame disagree. Most deliberate first; the two
# permissive resting poses last. `CLAP` is absent because it is a property of the PAIR of
# hands, not of either one, so it is decided before this list is consulted.
#
# `THUMBS_UP` outranking `OPEN_PALM` is the one entry worth defending: a hand held open while
# the other gives a deliberate thumbs up is the approval, and reading it as OPEN_PALM would
# just send LB to the keyboard. It cannot manufacture an approval — a hand only reaches this
# list already classified, and no non-thumbs-up pose classifies as THUMBS_UP.
_PRIORITY = ("PINCH", "CLAW", "THUMBS_UP", "OPEN_PALM", "NONE")


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


def _dist(a, b) -> float:
    """Plain 2-D Euclidean distance between two landmarks, in normalised frame units.

    z is deliberately ignored. mediapipe's z is a relative depth estimate with no metric
    meaning and far more noise than x and y, and every test here is about a shape on the
    screen — including the pinch, where the fingers touch in the image plane too.
    """
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def _hand_scale(landmarks) -> float:
    """The palm length: wrist to middle knuckle, in normalised frame units.

    This is the yardstick every other distance in this module is divided by, so that a
    threshold means the same thing at 40 cm from the camera and at 80 cm. It is measured
    across the PALM rather than along a finger because the palm is rigid — it is the same
    length whether the hand is open, clawed or in a fist, which is precisely what a yardstick
    has to be.

    Never returns 0: a degenerate hand (every landmark stacked on one point, which happens on
    a garbage frame) would otherwise divide by zero at a security prompt.
    """
    return max(_dist(landmarks[WRIST], landmarks[MIDDLE_MCP]), 1e-6)


def _palm_centroid(landmarks) -> tuple[float, float]:
    """Mean of the wrist and the four knuckles — where the hand *is*, ignoring the fingers."""
    xs = [landmarks[i].x for i in PALM]
    ys = [landmarks[i].y for i in PALM]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _is_pinch(landmarks) -> bool:
    """Thumb tip touching index tip — landmarks 4 and 8, within 0.40 palm-lengths."""
    return (_dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / _hand_scale(landmarks)
            < PINCH_MAX_RATIO)


def _is_claw(landmarks) -> bool:
    """The strained claw: every fingertip below its PIP but still above its MCP.

    The lower bound (below PIP) is what makes it not an open palm. The upper bound (above MCP)
    is what makes it not a fist, and therefore what keeps a claw from reaching the THUMBS_UP
    branch — see the header. All four fingers must agree, because three-of-four admits most of
    the ways a hand can be half-closed, and the point of this gesture is that it is deliberate.
    """
    return all(landmarks[pip].y < landmarks[tip].y < landmarks[mcp].y
               for mcp, pip, tip in FINGERS)


def _fingers_point_up(landmarks) -> bool:
    """All four fingers extended AND the hand held upright — for the clap.

    `_extended` alone only says a tip is above its own middle joint, which stays true for a
    hand lying on its side. A clap is two upright palms, so the tips must also rise clear of
    the wrist by a real fraction of a palm length.
    """
    if not all(_extended(landmarks, pip, tip) for _mcp, pip, tip in FINGERS):
        return False
    rise = min(landmarks[WRIST].y - landmarks[tip].y for _mcp, _pip, tip in FINGERS)
    return rise / _hand_scale(landmarks) > CLAP_MIN_FINGER_RISE


def _is_clap(hands) -> bool:
    """Two upright open palms, held close together.

    ## What this does not attempt

    The full description is "palms *facing each other*", and this does not test that. Palm
    orientation from 21 landmarks means recovering the palm normal and comparing two of them,
    which is doable and is not robust at this camera's resolution and frame rate — the
    wrist/index/pinky triangle is nearly degenerate exactly when the palms face each other
    edge-on to the lens, which is the pose it would have to be measured in.

    So the test is the observable part: **two hands, both upright and open, close together.**
    Stating that plainly is better than a normal-vector calculation that looks rigorous and
    fires at random, and it is why this returns a clean bool rather than a confidence.

    The cost of the simplification is that two open palms held side by side an inch apart, not
    facing, also read as CLAP. Nothing in the security gate is reachable from CLAP, so the
    cost is a wrong app command, not a wrong approval.
    """
    if len(hands) != 2:
        return False
    first, second = hands[0], hands[1]
    if not (_fingers_point_up(first) and _fingers_point_up(second)):
        return False

    ax, ay = _palm_centroid(first)
    bx, by = _palm_centroid(second)
    gap = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
    scale = (_hand_scale(first) + _hand_scale(second)) / 2
    return gap / scale < CLAP_MAX_GAP_RATIO


def _classify(landmarks) -> str:
    """One hand's 21 landmarks -> 'PINCH', 'CLAW', 'THUMBS_UP', 'OPEN_PALM' or 'NONE'.

    Pure, so it is testable without a camera, without a model file and without mediapipe
    installed at all: hand it any sequence of 21 objects with `.x` and `.y`. Shared by both
    backends, which is the reason the fork above costs almost nothing.

    `CLAP` is not here — it is a property of two hands, so it lives in `_classify_frame`.
    `FLICK` is not here either — it is a property of two *moments*, so it lives in
    `classify_stream`. What is left is what one hand looks like right now.

    **The order of these branches is a safety property, not a style choice.** See the header:
    PINCH and CLAW both satisfy the thumbs-up test's "no finger extended" clause, so both must
    be resolved before it, or a claw at the camera approves a shell command.
    """
    extended = [_extended(landmarks, pip, tip) for _mcp, pip, tip in FINGERS]

    # Open palm FIRST. It is the permissive gesture and it overlaps the naive thumbs-up test,
    # so checking it first is what stops a wave being read as a yes.
    if all(extended):
        return "OPEN_PALM"

    # Then the two gestures that would otherwise fall through into THUMBS_UP.
    if _is_pinch(landmarks):
        return "PINCH"
    if _is_claw(landmarks):
        return "CLAW"

    # Thumbs up: thumb above the index knuckle AND above the wrist, with every other finger
    # curled. The curl requirement is the whole difference between this and a raised hand.
    #
    # Unchanged since 2026-08-19. Everything above is a guard in front of it; nothing above
    # can make it fire, and the two poses that used to reach it wrongly no longer arrive.
    thumb_up = (landmarks[THUMB_TIP].y < landmarks[FINGERS[0][0]].y
                and landmarks[THUMB_TIP].y < landmarks[WRIST].y)
    if thumb_up and not any(extended):
        return "THUMBS_UP"

    return "NONE"


def _classify_frame(hands) -> str:
    """Every hand mediapipe found in one frame -> a single gesture token.

    Two hands can disagree, so `_PRIORITY` decides. CLAP is tested first because it is the one
    gesture that is a fact about the pair rather than about either hand.

    Pure, like `_classify`, and for the same reason: `hands` is a sequence of landmark
    sequences and nothing here touches a camera.
    """
    if not hands:
        return "NONE"
    if _is_clap(hands):
        return "CLAP"

    seen = {_classify(h) for h in hands}
    for gesture in _PRIORITY:
        if gesture in seen:
            return gesture
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

        # FLICK is the only gesture that is not a function of the current frame, so it is the
        # only one that needs the recogniser to remember anything. Samples are
        # (monotonic seconds, (x, y) of the open palm or None, pose), and only
        # `classify_stream` touches them — `get_gesture()`, the one-shot approval path that
        # the security gate runs on, never appends here and so carries no state at all.
        self._history: deque = deque(maxlen=FLICK_HISTORY)
        self.last_flick = ""          # "LEFT" or "RIGHT", for anything that wants a direction

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
                        num_hands=MAX_HANDS,
                        min_hand_detection_confidence=MIN_DETECTION_CONFIDENCE,
                        min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
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
                    max_num_hands=MAX_HANDS,
                    min_detection_confidence=MIN_DETECTION_CONFIDENCE,
                    min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
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
        """One frame from the camera -> one gesture token. The approval path.

        Returns 'PINCH', 'CLAP', 'CLAW', 'THUMBS_UP', 'OPEN_PALM' or 'NONE'. Optimised for
        Raspberry Pi camera processing: the camera is opened, warmed, read once and closed.

        **Never 'FLICK'.** One frame carries no motion, and this method holds no state across
        calls by design — in the parent process it is a fresh child every time. Use
        `detect_hands` + `classify_stream` on a live loop for that.

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
        return _classify_frame(self._detect(rgb_frame))

    def detect_hands(self, rgb):
        """One RGB frame -> a list of hands, each a sequence of 21 landmarks.

        The raw output, before any gesture decision. Split out from `classify_stream` so a
        caller that wants to DRAW the skeleton and also name the gesture runs the detector
        once for both — `tools/live_test_gestures.py` is the caller that needs this, and at
        88 ms per inference on the Pi, running it twice a frame would halve the frame rate of
        the tuning window.

        Returns [] when no detector was built, so a caller can treat "no mediapipe" and "no
        hand" the same way.
        """
        if not self.available:
            return []
        return self._detect(rgb)

    def classify_stream(self, hands, now: float | None = None) -> str:
        """Hands from a LIVE loop -> a gesture token, `FLICK` included.

        The difference from `_classify_frame` is memory: this keeps a short history of where
        the palm was and when, so an open palm that is travelling reads as `FLICK` instead of
        `OPEN_PALM`. Everything else is passed straight through.

        Call it once per frame, in order. Gaps are handled — a stale history simply fails the
        window test — but calling it on frames out of order is meaningless.

        Args:
            hands: what `detect_hands` returned for this frame.
            now:   monotonic timestamp; defaults to the current time. Injectable so the flick
                   logic is testable without a camera and without sleeping.

        Returns:
            One of the tokens in `_VALID`, except `NO_CAMERA`.
        """
        if now is None:
            now = time.monotonic()

        pose = _classify_frame(hands)

        # Track the palm that could be flicking. A flick is an open palm crossing the frame,
        # so an open hand is the only one worth remembering the position of.
        centre = None
        for hand in hands:
            if _classify(hand) == "OPEN_PALM":
                centre = _palm_centroid(hand)
                break

        self._history.append((now, centre, pose))

        if pose != "OPEN_PALM" or centre is None:
            return pose

        direction = self._flick_direction(now, centre[0])
        if not direction:
            return pose

        # Edge-triggered, not level-triggered: clearing the history means one swipe of the
        # hand produces ONE flick, not a flick on every frame for as long as the hand is
        # moving. A gesture wired to an action has to fire once per intent.
        self._history.clear()
        self.last_flick = direction
        return "FLICK"

    def _flick_direction(self, now: float, x: float) -> str:
        """"LEFT", "RIGHT" or "" — has the palm crossed the frame fast enough, and which way?

        Both a speed floor and a distance floor have to be cleared, and the distance floor is
        the one doing the real work. This webcam gives ~6.6 fps, so a single jittery landmark
        between two frames 150 ms apart can look like it is moving quickly; it cannot also
        have moved a fifth of the frame width.
        """
        for when, centre, pose in self._history:
            if centre is None or pose != "OPEN_PALM":
                continue                       # the hand was not open then; not this flick
            dt = now - when
            if dt <= 0 or dt > FLICK_WINDOW_S:
                continue                       # too old, or the same sample
            dx = x - centre[0]
            if abs(dx) < FLICK_MIN_TRAVEL or abs(dx) / dt < FLICK_MIN_SPEED:
                continue
            # x grows to the right in image space. The live window mirrors the frame so it
            # reads like a mirror, and it names the direction LB sees, not this one.
            return "RIGHT" if dx > 0 else "LEFT"
        return ""

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

    print(f"  watching via {worker or 'this interpreter'} — pinch, clap, claw, thumbs up, "
          f"open palm; ctrl-C to stop")
    print("  (no FLICK here: each read is one frame from a camera that is then closed — "
          "run tools/live_test_gestures.py for that)")
    try:
        while True:
            print(f"  {time.strftime('%H:%M:%S')}  {get_gesture()}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
