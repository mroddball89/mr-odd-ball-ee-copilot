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

Returns exactly one of `MOVE`, `SCALE`, `PINCH`, `FIST`, `THUMBS_UP`, `THUMBS_DOWN`,
`OPEN_PALM`, `NONE` or `NO_CAMERA`. Wired into the terminal security prompts in
`agents/os_agent.py` and `agents/web_agent.py`: **a thumbs up is a yes, a thumbs down is a no,
and every other token falls through to the keyboard.**

`MOVE` and `SCALE` are manipulations — differences between two frames, carrying NUMBERS rather
than just a name (see `Motion`). `get_gesture()` reads ONE frame from a camera it then closes,
in a child process that then exits (see `_ask_sidecar`), so there is no previous frame to have
moved from: **the one-shot path returns poses only.** Manipulation comes from `track()`, the
continuous path, which `tools/live_test_gestures.py` uses. Both names are in `_VALID` anyway,
because a whitelist that omitted them would silently turn a future streaming sidecar's honest
answer into `NO_CAMERA`.

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
## The vocabulary is jaredrhod/barehands', and the geometry is too

**2026-08-23.** The measurements here are ported from `barehands`' `stage.html`, whose comments
carry the corpus each constant was fitted against ("v3.9.32 — his final pinch sample", "fitted
from his pinch corpus, 3 correct / 3 fists"). The constants in this file before that date were
derived from geometry and honestly labelled as guesses. These are not guesses, and that is the
entire reason to port rather than reinvent.

    poses          OPEN_PALM   FIST   PINCH   THUMBS_UP   THUMBS_DOWN
    manipulation   MOVE (one pinch, travelling)   SCALE (two pinches: zoom + twist)

`CLAW`, `POINT`, `TAP`, `DRAG` and `FLICK` were built on 2026-08-23 and removed the same day,
at LB's word: *"i dont need a point or a claw right now. i just want to be pinch hold to and be
able to move things around up down around or rotate, double pinch fingers close zoom out make
smaller fingers moving away makes bigger zoom in."* They are in the history if they are ever
wanted back. Carrying five gestures nobody uses is five more ways to misread the two that
matter.

## Every finger test is measured against the HAND, not against the image

This is the change that made recognition reliable, and it is worth stating plainly because the
bug it fixed was invisible for four days.

The tests here used to be image-coordinate comparisons — `tip.y < pip.y` for "extended". That
is an angle measured against the **frame**, and it is correct only while the hand is held
upright. **A thumbs up is naturally made with the palm turned side-on**, and when it is, the
fingers curl sideways rather than downward: their tips land at roughly their PIPs' height,
`tip.y < pip.y` starts returning true for a fully curled finger, and "all four fingers curled"
fails.

LB photographed exactly that — a clean thumbs up, every landmark correctly placed, classified
`NONE`: `media/captures/gesture-none-20260823-171120.png`. Nothing was wrong with the
detection. The question being asked of it was wrong. Swept over twelve rotations of one closed
fist, the old test calls it an OPEN PALM at six of them.

Two rotation-invariant primitives replace all of it, both from `barehands`:

* **`_curl()`** — the dot product of a finger's proximal segment with its distal one. Straight
  is ~+0.9, hooked goes negative, *in any camera orientation*. Its own note on why an angle and
  not a distance: "monocular z is too weak for distances ... angles survive the foreshortening
  that lies about lengths."
* **`_reach()`** — tip-to-wrist over knuckle-to-wrist. Both measured from the same point, so
  rotating the hand cannot change the ratio.

The one test still in image coordinates is `_thumb_direction()`, and that is deliberate: up and
down are facts about gravity, not about the hand. Everything else is hand-relative precisely so
that this one can be world-relative and still mean something.

## Only a closed hand may approve, and "closed" is stricter than "not open"

`THUMBS_UP` is what `agents/os_agent.py` runs a shell command on, so the guard in front of it is
stated as an invariant: **only a hand with all four fingers shut reaches the thumb branches at
all**, and which of `THUMBS_UP` / `THUMBS_DOWN` / `FIST` it becomes is decided by the thumb
alone.

`_finger_shut()` demands a finger be folded by **both** curl and reach; `_finger_open()` accepts
**either**. They are deliberately not each other's negation — the band between them falls to
`NONE`, which declines to the keyboard.

That gap is not fussiness. With a single threshold, "not proven out" means "in", and on
2026-08-23 that turned LB's pointing hand — index `curl +0.60, reach 1.37`, just under the 1.45
bar — into an approval. `verify_gestures.py` asserts the invariant exhaustively rather than
trusting the reading.

## Distances are measured in palm-spans, not in frame-widths

`PINCH` is "landmark 4 touches landmark 8", and the obvious implementation compares their
distance to a constant. That constant is correct at exactly one camera distance: landmarks are
normalised to the FRAME, so the same hand at 80 cm is half the size it is at 40 cm.

Every distance here is divided by `_hand_scale()` — wrist to middle knuckle, `barehands`' `span`
— the one length on a hand that does not change when the fingers move. Thresholds are in
palm-spans and hold at any distance and on any size of hand. `Motion`'s translation is in the
same units, so dragging a thing across a desk does not depend on how far LB is sitting back.


## What this is not

It is not a second authority. The blocklist in `tools/os_controller.py` runs regardless of how
approval arrived, and the exact command is still printed before the question is asked. A
gesture replaces the keystroke, not the review.
"""

from __future__ import annotations

import contextlib
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

# 2 since 2026-08-23. barehands scales objects with two hands, and a detector capped at one can
# never see a second. It is not free — mediapipe runs the landmark model once PER HAND, so a
# frame with two hands costs about twice the 47 ms inference measured in the budget above.
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
# matter what the fingers are doing. Their mean is the palm centroid, which is what `MOVE`
# tracks — a fingertip would add the finger's own motion to the hand's, so pinching harder
# would read as travel.
PALM = (WRIST, 5, 9, 13, 17)

# The four finger chains as (mcp, pip, dip, tip). `FINGERS` above is the (mcp, pip, tip)
# form the y-comparison era used; this is the full chain, because the curl test needs the
# DIP joint to see which way the last segment points.
CHAINS = (
    (5, 6, 7, 8),        # index
    (9, 10, 11, 12),     # middle
    (13, 14, 15, 16),    # ring
    (17, 18, 19, 20),    # pinky
)

# --- thresholds, ported from jaredrhod/barehands 2026-08-23 ------------------------------
#
# These are NOT geometry-derived guesses any more. Every number below is the value that
# project arrived at by fitting against a corpus of real hands — its source carries the
# version history in comments ("v3.9.32 — his final pinch sample", "fitted from his pinch
# corpus, 3 correct / 3 fists"). Where a number here differs from barehands it is noted.
#
# Taking measured constants over invented ones is the whole reason to port rather than
# reinvent: the previous set of thresholds in this file were honest guesses, and they are
# what LB was fighting.

# EXTENDED: a finger counts as out when its tip is 1.45x further from the wrist than its own
# knuckle is. A ratio of distances FROM A COMMON POINT, so it does not care which way the
# hand is rotated — unlike the `tip.y < pip.y` test this replaces.
EXTEND_REACH = 1.45

# CURL: the dot product of a finger's proximal segment (mcp->pip) with its distal one
# (dip->tip), in 3D. An extended finger's segments are aligned, ~+0.9. A hooked finger's
# distal segment points back against the proximal one, going negative. barehands' note on
# why this and not distances: "monocular z is too weak for distances... angles survive the
# foreshortening that lies about lengths."
CURL_FOLDED = 0.35             # below this the finger is folded
CURL_STRAIGHT = 0.60           # above this it is straight

# PINCH: thumb-index gap over palm span.
#
# 0.66, LB's own number, chosen at the camera on 2026-08-23 and then checked against the 17
# hands he captured. What that check found is worth writing down, because it changes what this
# constant is FOR:
#
#     his genuine pinches      0.09 .. 0.32     (twelve of them)
#     nearest non-pinch hand   0.69
#
# **Every pinch he made was already inside the old 0.32 ceiling.** The gap was never what was
# rejecting them — the two bugs below were. So 0.66 is not the threshold that makes pinching
# work; it is a wide margin sitting in a canyon that runs from 0.32 to 0.69, and the CONTRAST
# LAW is what actually decides a touch. barehands reached the same arrangement from the other
# direction and parked its own ceiling "in a margin role... the contrast law is the real touch
# enforcement".
#
# The margin above 0.66 is thin — 0.03 to that 0.69 hand. 0.50 would sit mid-canyon and still
# admit every pinch in the corpus. Kept at 0.66 because it is LB's measured preference and the
# contrast law carries the duty; change this line, not the logic, if a stray hand ever grabs.
PINCH_MAX_RATIO = 0.66

# There was a second, wider ceiling here for palms turned side-on, ported from barehands along
# with an `aspect < 2.0` test to choose between them. Removed 2026-08-23: measured on LB's
# captures, his hands run aspect 1.13 to 6.30 with a median of 3.74, so that test put 13 of 17
# real hands in the "profile" lane and the split decided nothing. It was a knob that could only
# ever be wrong, inherited from a pipeline whose aspect numbers are not these.

# THE CONTRAST LAW (barehands v3.8.2). A closed fist also puts the thumb on the index tip,
# so the gap alone cannot tell a pinch from a clench. The tell is CONTRAST: in a real pinch
# the index curls IN to the thumb while middle/ring/pinky stay OUT. Measured there at >= 0.28
# for every correct sample and <= 0.07 for every impostor; cut at 0.18 with a back-arch floor
# of 1.30 as the fist wall.
PINCH_CONTRAST = 0.18
PINCH_BACK_ARCH = 1.30

# THUMB direction, for THUMBS_UP / THUMBS_DOWN. This is the ONE test that is deliberately
# still in image coordinates: up and down are facts about the world, not about the hand, so
# rotating the hand must NOT rotate the answer. Measured as a fraction of palm span so it is
# distance-independent, and required in both directions so a level thumb is neither.
THUMB_RISE = 0.55              # thumb tip this far above the wrist, in palm spans
THUMB_DROP = 0.55              # ...or this far below it

# SANITY (barehands v3.9.30). Palm span over palm width. A hand resting on a face read 7-8
# there — the knuckle row collapsed, a geometrically impossible hand — while no real hand in
# a two-day corpus exceeded 5.5. Past this the tracker is guessing, and a guess must not be
# allowed to produce a gesture at all, least of all at a security prompt.
# 9.0, up from barehands' 6.0. Its 6.0 was measured against ITS OWN landmark pipeline, and the
# number did not transfer: LB's captures include a textbook pinch — gap 0.17, contrast 0.53 —
# sitting at aspect 6.30, which 6.0 threw away as a hallucinated hand. His pinching pose turns
# the palm side-on, which foreshortens the knuckle row and drives this ratio up legitimately.
# A ported constant is only as good as the pipeline it was fitted on; this one is now fitted
# on ours.
ASPECT_GARBAGE = 9.0

# --- the manipulation layer ---------------------------------------------------------------
#
# "Every gesture is a movement, not a pose." — barehands, The Gestures.md
#
# LB's spec, 2026-08-23: *"i just want to be pinch hold to and be able to move things around up
# down around or rotate, double pinch fingers close zoom out make smaller fingers moving away
# makes bigger zoom in."*
#
# ## Why this layer returns NUMBERS and not a name
#
# A token cannot move anything. `"MOVE"` says a hand is dragging; it does not say where to, and
# whatever is being dragged needs to know. So `track()` returns a `Motion` carrying the actual
# deltas — dx, dy, a scale factor and a rotation — and the name is only a label on top of them.
#
# Everything is measured PER FRAME, as a delta rather than an absolute, so a consumer applies
# it incrementally and a dropped frame costs a little movement instead of a jump.
#
# Translation is in PALM SPANS, not frame widths: the same physical hand movement covers more
# of the frame close to the camera, and moving a thing across a desk should not depend on how
# far LB is sitting from the lens.

# A pinch has to travel this far, in palm spans, before it counts as MOVE rather than a
# stationary PINCH. Without a floor, landmark jitter creeps whatever is held.
MOVE_DEADZONE = 0.04

# Two-hand scaling. The gesture is the CHANGE in the distance between the two pinch points, so
# what matters is the ratio between this frame and the last. These clamp a single frame's
# contribution — a tracker glitch that doubles the gap in one frame must not fling the zoom.
SCALE_MIN_STEP = 0.5
SCALE_MAX_STEP = 2.0
# ...and a deadzone in the same units, so two held hands do not slowly drift the zoom.
SCALE_DEADZONE = 0.015

# Rotation comes free from the same two points: the angle of the line between them. Clamped
# per frame for the same reason, and dead-zoned against jitter.
ROTATE_MAX_STEP = 0.5           # radians in one frame
ROTATE_DEADZONE = 0.02          # radians


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
_VALID = ("MOVE", "SCALE", "PINCH", "FIST",
          "THUMBS_UP", "THUMBS_DOWN", "OPEN_PALM", "NONE", "NO_CAMERA")

# Which gesture wins when the two hands in frame disagree. Most deliberate first; the two
# permissive resting poses last.
#
# `THUMBS_UP` outranking `OPEN_PALM` is the one entry worth defending: a hand held open while
# the other gives a deliberate thumbs up is the approval, and reading it as OPEN_PALM would
# just send LB to the keyboard. It cannot manufacture an approval — a hand only reaches this
# list already classified, and no non-thumbs-up pose classifies as THUMBS_UP.
# The deliberate poses first, the permissive resting ones last. MOVE and SCALE are absent
# because `track()` decides them from two frames, before this list is consulted. THUMBS_DOWN
# sits beside THUMBS_UP; neither can be reached from the other, because the thumb cannot be
# both above and below the wrist.
_PRIORITY = ("PINCH", "THUMBS_UP", "THUMBS_DOWN", "FIST", "OPEN_PALM", "NONE")


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


# The handshake with `tools/gesture_pointer.py`, which holds the camera open to drive the
# desktop pointer. Two processes cannot both have `/dev/video0`, so the gate takes it.
POINTER_PAUSE_FILE = REPO_ROOT / "data" / "gesture_pointer.pause"

# How long to give the daemon to notice and let go. It polls every 0.15 s, so 0.5 s is three
# chances. Paid once per approval, on a prompt that already costs 2.2 s and is about to stop
# and ask a question anyway.
POINTER_YIELD_S = 0.5


@contextlib.contextmanager
def _camera_yielded():
    """Ask the pointer daemon to drop the camera for the duration of this block.

    Two things happen while this file exists, and the second is the one that matters:

    1. The daemon releases `/dev/video0`, so the approval read can open it. Without this a
       running daemon would make every approval report `NO_CAMERA` — safe, but it would
       silently delete gesture approval.
    2. **The daemon stops injecting pointer events entirely.** A security prompt is exactly
       when a synthetic mouse must not be moving things, and this is what guarantees it is
       inert. See the security model in `tools/gesture_pointer.py`.

    Best-effort by design: if the file cannot be written the approval still goes ahead, because
    a gesture read that cannot happen falls to the keyboard, which is the safe direction. It is
    removed in a `finally` so a crashed approval cannot leave the daemon paused for ever.
    """
    made = False
    try:
        POINTER_PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        POINTER_PAUSE_FILE.write_text("gesture approval in progress\n", encoding="utf-8")
        made = True
        time.sleep(POINTER_YIELD_S)
    except OSError as exc:
        LOG.debug("could not pause the pointer daemon (%s); carrying on", exc)
    try:
        yield
    finally:
        if made:
            try:
                POINTER_PAUSE_FILE.unlink(missing_ok=True)
            except OSError:
                LOG.warning("could not clear %s — the pointer daemon will stay paused",
                            POINTER_PAUSE_FILE)


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
        with _camera_yielded():
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


def _dist(a, b) -> float:
    """2-D Euclidean distance between two landmarks, in normalised frame units."""
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


class Motion:
    """What to do this frame. The output of the manipulation layer.

    Every field is a DELTA for this frame, never an absolute:

        name       "MOVE", "SCALE", or the pose seen when nothing is being manipulated
        dx, dy     translation, in PALM SPANS — so it does not change with camera distance.
                   dy is positive DOWNWARDS, matching image coordinates.
        scale      multiplicative. 1.0 is no change, >1 grows, <1 shrinks.
        rotation   radians, positive clockwise on screen (y runs down).
        hands      how many hands were in frame

    A no-op frame is `Motion("PINCH")` — scale 1.0 and every delta 0.0 — so a consumer can
    apply every frame unconditionally without checking the name first.
    """

    __slots__ = ("name", "dx", "dy", "scale", "rotation", "hands")

    def __init__(self, name: str, dx: float = 0.0, dy: float = 0.0,
                 scale: float = 1.0, rotation: float = 0.0, hands: int = 0) -> None:
        self.name, self.dx, self.dy = name, dx, dy
        self.scale, self.rotation, self.hands = scale, rotation, hands

    @property
    def moving(self) -> bool:
        """True when this frame carries an actual change to apply."""
        return (self.dx or self.dy or self.rotation) != 0.0 or self.scale != 1.0

    def __repr__(self) -> str:
        if self.name == "MOVE":
            return f"MOVE dx{self.dx:+.3f} dy{self.dy:+.3f}"
        if self.name == "SCALE":
            return f"SCALE x{self.scale:.3f} rot{self.rotation:+.3f}"
        return self.name


def _dist_xy(ax: float, ay: float, bx: float, by: float) -> float:
    """Distance between two bare (x, y) pairs. The movement layer has no landmarks."""
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _hand_scale(landmarks) -> float:
    """The palm span: wrist to middle knuckle. barehands calls this `span`.

    The yardstick every ratio here divides by, so a threshold means the same thing at 40 cm
    from the camera and at 80 cm. Measured across the palm because the palm is rigid — it is
    the same length whether the hand is open, clawed or in a fist.

    Never 0: a degenerate hand on a garbage frame would otherwise divide by zero.
    """
    return max(_dist(landmarks[WRIST], landmarks[MIDDLE_MCP]), 1e-6)


def _palm_centroid(landmarks) -> tuple[float, float]:
    """Mean of the wrist and the four knuckles — where the hand *is*, ignoring the fingers."""
    xs = [landmarks[i].x for i in PALM]
    ys = [landmarks[i].y for i in PALM]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _seg(landmarks, a: int, b: int) -> tuple[float, float, float]:
    """The unit vector from landmark a to landmark b, in 3D."""
    p, q = landmarks[a], landmarks[b]
    dx, dy = q.x - p.x, q.y - p.y
    dz = getattr(q, "z", 0.0) - getattr(p, "z", 0.0)
    length = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
    return dx / length, dy / length, dz / length


def _curl(landmarks, mcp: int, pip: int, dip: int, tip: int) -> float:
    """How hooked this finger is: +1 straight, 0 bent square, negative folded back.

    The dot product of the finger's proximal segment (mcp->pip) with its distal one
    (dip->tip). An extended finger keeps those aligned, ~+0.9. A hooked finger's last segment
    points back against the first, so the dot goes negative.

    ## Why an ANGLE and not a distance

    This is the measurement that makes the whole classifier orientation-proof, and it is
    barehands' finding, stated in its own source:

        monocular z is too weak for distances ... a hooked finger's distal segment points back
        AGAINST its proximal segment (dot < 0-ish); an extended finger's segments stay aligned
        (dot ~ +0.9) in ANY camera orientation — angles survive the foreshortening that lies
        about lengths.

    The version of this file before 2026-08-23 asked `tip.y < pip.y`, which is an angle
    measured against the IMAGE, not against the hand. It is correct only while the hand is
    held upright, and a thumbs up is naturally made with the palm turned side-on — so LB's
    thumbs up photographed at 09:11 that morning classified as NONE, with every finger
    correctly detected and every landmark in the right place. See `media/captures/`.
    """
    a = _seg(landmarks, mcp, pip)
    b = _seg(landmarks, dip, tip)
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _reach(landmarks, tip: int, mcp: int) -> float:
    """How far the tip is from the wrist, relative to its own knuckle. barehands' `fR`.

    Both distances start at the wrist, so the ratio is unchanged by rotating the hand — the
    second rotation-invariant primitive, and the one that says a finger is OUT rather than
    merely UNFOLDED.
    """
    knuckle = _dist(landmarks[WRIST], landmarks[mcp])
    return _dist(landmarks[WRIST], landmarks[tip]) / knuckle if knuckle > 0 else 9.0


def _extended(landmarks, mcp: int, tip: int) -> bool:
    """True when this finger is out: its tip reaches 1.45x past its own knuckle.

    NOTE the signature changed on 2026-08-23, from `(landmarks, pip, tip)` to
    `(landmarks, mcp, tip)`. The old one compared y coordinates and took the PIP; this one
    compares reaches and takes the MCP. Both callers were updated. It is a different test,
    not a refactor.
    """
    return _reach(landmarks, tip, mcp) > EXTEND_REACH


def _finger_open(landmarks, chain) -> bool:
    """This finger is OUT — straight, or at least reaching well past its knuckle.

    Either signal admits, because they fail in different circumstances: `_reach` under-reads a
    finger pointing towards the lens (foreshortening), and `_curl` under-reads when the tracker
    puts the DIP joint slightly wrong on a blurred frame.
    """
    mcp, pip, dip, tip = chain
    return (_curl(landmarks, mcp, pip, dip, tip) > CURL_STRAIGHT
            or _reach(landmarks, tip, mcp) > EXTEND_REACH)


def _finger_shut(landmarks, chain) -> bool:
    """This finger is IN — genuinely folded, by BOTH measures.

    ## Why this is not simply `not _finger_open`

    It is the security test, and the gap between the two is deliberate. `THUMBS_UP` runs a
    shell command on `agents/os_agent.py`'s path, and it is only reachable when all four
    fingers are shut — so "shut" has to mean *folded*, not merely *not proven to be out*.

    A hand whose fingers are neither clearly out nor clearly folded — half-curled, or badly
    tracked — satisfies neither predicate and falls through to `NONE`, which declines to the
    keyboard. That is the safe direction, and it is the whole point of leaving a band between
    them rather than splitting hand-space down the middle with one threshold.

    Measured 2026-08-23: LB's pointing hand read index `curl +0.60, reach 1.37`. Under the
    single-threshold version its index was "not extended" (1.37 < 1.45), which made the hand
    all-fingers-closed, which sent a POINT into the thumb branch and out as `THUMBS_UP` — an
    approval, from a hand that is not remotely a fist. See `media/captures/`.
    """
    mcp, pip, dip, tip = chain
    return (_curl(landmarks, mcp, pip, dip, tip) < CURL_FOLDED
            and _reach(landmarks, tip, mcp) < EXTEND_REACH)


def _aspect(landmarks) -> float:
    """Palm span over palm width. Over ASPECT_GARBAGE the tracker is hallucinating."""
    width = _dist(landmarks[FINGERS[0][0]], landmarks[FINGERS[3][0]])   # index MCP to pinky MCP
    return _hand_scale(landmarks) / width if width > 0 else 9.0


def _pinch_ratio(landmarks) -> float:
    """Thumb tip to index tip, in palm spans. barehands' `ratio`; the mouth of the claw too."""
    return _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / _hand_scale(landmarks)


def _curls(landmarks) -> list[float]:
    """The four finger curls, index to pinky. barehands' c8, c12, c16, c20."""
    return [_curl(landmarks, *chain) for chain in CHAINS]


def _is_pinch(landmarks) -> bool:
    """Thumb and index touching — and the other three NOT, which is the whole test.

    A closed fist also lays the thumb on the index tip, so gap alone cannot tell a pinch from
    a clench. barehands' CONTRAST LAW is the fix: in a real pinch the index curls IN while
    middle, ring and pinky stay OUT, and that contrast separated every correct sample from
    every impostor in its corpus where absolute finger height did not.
    """
    if _pinch_ratio(landmarks) >= PINCH_MAX_RATIO:
        return False

    index_reach = _reach(landmarks, 8, 5)
    back = (_reach(landmarks, 12, 9) + _reach(landmarks, 16, 13) + _reach(landmarks, 20, 17)) / 3
    return back - index_reach > PINCH_CONTRAST and back > PINCH_BACK_ARCH


def _thumb_direction(landmarks) -> str:
    """"UP", "DOWN" or "" — where the thumb points, in the WORLD, not in the hand.

    Deliberately still an image-y comparison, and that is not an oversight. Up and down are
    facts about gravity; rotating the hand must not rotate the answer. Everything else in this
    module is hand-relative precisely so that this one test can be world-relative and mean
    something.

    Measured in palm spans from the wrist so it stays distance-independent, and required in
    one direction or the other by a real margin, so a thumb held level is neither.
    """
    rise = (landmarks[WRIST].y - landmarks[THUMB_TIP].y) / _hand_scale(landmarks)
    if rise > THUMB_RISE:
        return "UP"
    if rise < -THUMB_DROP:
        return "DOWN"
    return ""


def _classify(landmarks) -> str:
    """One hand's 21 landmarks -> a pose.

    'PINCH', 'THUMBS_UP', 'THUMBS_DOWN', 'FIST', 'OPEN_PALM' or 'NONE'.

    Pure: hand it any sequence of 21 objects with `.x`, `.y` and optionally `.z`. No camera,
    no model file, no mediapipe. Shared by both backends.

    MOVE and SCALE are not here — they are differences between frames, and this sees one
    instant. They live in `track()`.

    **The order of these branches is a safety property.** `THUMBS_UP` is what
    `agents/os_agent.py` runs a shell command on, so every pose that could be mistaken for it
    is resolved first. See the header.
    """
    if _aspect(landmarks) > ASPECT_GARBAGE:
        return "NONE"                  # the tracker is guessing; a guess is not a gesture

    out = [_finger_open(landmarks, chain) for chain in CHAINS]
    shut = [_finger_shut(landmarks, chain) for chain in CHAINS]

    # PINCH FIRST, since 2026-08-23. It used to run after the open-palm test and that was
    # wrong: in a real pinch the index finger ARCS to meet the thumb rather than folding, so
    # its segment-alignment curl stays high — LB has a captured pinch reading index curl +0.74,
    # comfortably over the 0.6 "straight" bar. All four fingers therefore looked open,
    # OPEN_PALM matched, and the pinch below was never reached. That is the bug his "max pinch
    # about 0.66" was compensating for, and no gap threshold could have fixed it.
    #
    # Moving it in front of OPEN_PALM is safe because `_is_pinch` is not a gap test: it demands
    # the CONTRAST of an index curled in against three fingers still out. His open palm scores
    # 0.18 contrast against the 0.18 bar, and 0.78 gap against the 0.66 ceiling — it fails both.
    if _is_pinch(landmarks):
        return "PINCH"

    # Open palm, as it has been since 2026-08-19: the permissive pose, tested before the thumb
    # so that a wave can never be read as a yes.
    if all(out):
        return "OPEN_PALM"

    # A closed hand, and ONLY a properly closed one — see `_finger_shut` for why this demands
    # all four folded rather than merely not-extended. Which gesture it is then depends on the
    # thumb, and only on the thumb.
    if all(shut):
        thumb = _thumb_direction(landmarks)
        if thumb == "UP":
            return "THUMBS_UP"
        if thumb == "DOWN":
            return "THUMBS_DOWN"
        return "FIST"

    return "NONE"


def _classify_frame(hands) -> str:
    """Every hand in one frame -> a single pose token, resolved by `_PRIORITY`.

    Pure, like `_classify`.
    """
    if not hands:
        return "NONE"
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

        # The manipulation layer is the only thing here that needs the recogniser to remember
        # anything, because MOVE and SCALE are differences against the previous frame. One
        # tuple: ("one"|"two", x, y, span, gap, angle) — whichever fields that mode uses.
        #
        # `get_gesture()`, the one-shot path the security gate runs on, never touches this and
        # so carries no state between approvals at all.
        self._grip: tuple | None = None
        self.motion = Motion("NONE")   # the last thing `track()` decided, for the debugger

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

        Returns a POSE — 'PINCH', 'THUMBS_UP', 'THUMBS_DOWN', 'FIST', 'OPEN_PALM' or
        'NONE'. Optimised for Raspberry Pi camera processing: the camera is
        opened, warmed, read once and closed.

        **Never 'MOVE' or 'SCALE'.** Those are differences between frames; one frame
        carries none, and this method holds no state across calls by design — in the parent it
        is a fresh child every time. Use `detect_hands` + `track` on a live loop.

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

    def track(self, hands, now: float | None = None) -> "Motion":
        """Hands from a LIVE loop -> what to DO this frame, as numbers.

        This is the manipulation layer, and the reason it returns a `Motion` rather than a
        token is that a token cannot move anything. `"MOVE"` says a hand is dragging; it does
        not say where to, and the thing being dragged needs to know.

        ## The two gestures, and they are the two LB asked for

            ONE hand pinching   -> MOVE.  dx, dy = how far the pinch travelled this frame.
            TWO hands pinching  -> SCALE. scale = how much the gap between them grew this
                                   frame (apart = bigger, together = smaller), and rotation =
                                   how far the line between them turned.

        Scale and rotation arrive together because they come from the same two points, which is
        also how every touchscreen has worked for fifteen years: the gap is the zoom, the angle
        is the twist. There is no extra gesture to learn for rotating.

        ## Deltas, never absolutes

        Every field is *this frame's change*. A consumer applies it incrementally, so a dropped
        frame costs a little movement rather than a jump, and there is no origin to drift.

        Call once per frame, in order.

        Args:
            hands: what `detect_hands` returned for this frame.
            now:   monotonic timestamp; defaults to now. Injectable so all of this is testable
                   without a camera and without sleeping.

        Returns:
            A `Motion`. Its `.name` is `MOVE`, `SCALE`, or whatever pose was seen.
        """
        if now is None:
            now = time.monotonic()

        # Every hand that is pinching, with the palm centre as its position and the palm span
        # as its yardstick. The palm centre rather than the fingertips: a fingertip carries the
        # finger's own motion, so pinching harder would read as travel.
        grips = [(_palm_centroid(h), _hand_scale(h)) for h in hands if _classify(h) == "PINCH"]
        pose = _classify_frame(hands)

        if len(grips) >= 2:
            motion = self._two_hands(grips[0], grips[1])
        elif len(grips) == 1:
            motion = self._one_hand(grips[0])
        else:
            motion = Motion(pose, hands=len(hands))
            self._grip = None

        self.motion = motion
        return motion

    def _one_hand(self, grip) -> "Motion":
        """A single pinch: translation, in palm spans."""
        (cx, cy), scale = grip
        last, self._grip = self._grip, ("one", cx, cy, scale, 0.0, 0.0)
        if last is None or last[0] != "one":
            return Motion("PINCH", hands=1)          # the frame the grip formed

        dx = (cx - last[1]) / scale
        dy = (cy - last[2]) / scale
        if (dx * dx + dy * dy) ** 0.5 < MOVE_DEADZONE:
            # Held still. Report the grip, not a jitter-sized move — this is "pinch and hold".
            return Motion("PINCH", hands=1)
        return Motion("MOVE", dx=dx, dy=dy, hands=1)

    def _two_hands(self, a, b) -> "Motion":
        """Two pinches: scale from the gap between them, rotation from its angle.

        Both are ratios/differences against the previous frame, so neither needs an origin and
        neither accumulates error. Each is clamped and dead-zoned: a tracker glitch that
        doubles the gap in one frame must not fling the zoom, and two hands held still must not
        drift it.
        """
        import math

        (ax, ay), a_scale = a
        (bx, by), b_scale = b
        gap = _dist_xy(ax, ay, bx, by)
        angle = math.atan2(by - ay, bx - ax)
        span = (a_scale + b_scale) / 2

        last, self._grip = self._grip, ("two", 0.0, 0.0, span, gap, angle)
        if last is None or last[0] != "two" or last[4] <= 0:
            return Motion("SCALE", hands=2)          # the frame the two-hand grip formed

        # Zoom. Fingers moving apart -> the gap grows -> scale > 1 -> bigger. That is LB's
        # spec exactly: "fingers close zoom out make smaller, fingers moving away makes bigger".
        step = gap / last[4]
        step = min(max(step, SCALE_MIN_STEP), SCALE_MAX_STEP)
        if abs(step - 1.0) < SCALE_DEADZONE:
            step = 1.0

        # Twist. Wrapped into (-pi, pi] before clamping, or a hand crossing the +/-pi seam
        # would read as a full turn in one frame.
        turn = angle - last[5]
        turn = (turn + math.pi) % (2 * math.pi) - math.pi
        turn = min(max(turn, -ROTATE_MAX_STEP), ROTATE_MAX_STEP)
        if abs(turn) < ROTATE_DEADZONE:
            turn = 0.0

        return Motion("SCALE", scale=step, rotation=turn, hands=2)

    def classify_stream(self, hands, now: float | None = None) -> str:
        """`track()`, reduced to a token. For anything that only wants to display a name."""
        return self.track(hands, now).name


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
    if seen == "THUMBS_DOWN":
        # Added 2026-08-23 with the gesture itself. This is the ONE case where the camera is
        # allowed to end the question without the keyboard, and it is safe for a reason that
        # does not generalise: it returns False. A declined action does not run, and a gesture
        # that can only ever decline cannot approve anything by being misread. The asymmetry
        # is the whole design — a wrong THUMBS_UP executes a shell command, a wrong
        # THUMBS_DOWN costs LB one retry.
        print("   👎 Thumbs down — declined by gesture.")
        return False
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

    print(f"  watching via {worker or 'this interpreter'} — pinch, fist, thumbs up/down, "
          f"open palm; ctrl-C to stop")
    print("  (poses only: each read is one frame from a camera that is then closed, so MOVE "
          "and SCALE cannot appear — run tools/live_test_gestures.py for those)")
    try:
        while True:
            print(f"  {time.strftime('%H:%M:%S')}  {get_gesture()}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
