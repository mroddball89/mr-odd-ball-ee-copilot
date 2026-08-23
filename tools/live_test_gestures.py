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
    "THUMBS_UP":   (80, 220, 80),      # the approval. Its own colour, deliberately.
    "THUMBS_DOWN": (80, 80, 240),      # the decline
    "OPEN_PALM":   (230, 200, 90),
    "FIST":        (170, 170, 170),
    "PINCH":       (200, 140, 255),
    "CLAW":        (90, 170, 255),
    "POINT":       (255, 190, 120),
    "TAP":         (255, 255, 140),
    "DRAG":        (220, 160, 255),
    "FLICK":       (120, 255, 255),
    "NONE":        (150, 150, 150),
    "NO_CAMERA":   (80, 80, 240),
}

# The events. They last one frame by construction — a tap is a pinch that already ended — so
# they are HELD on screen for a beat or they cannot be read at ~9.7 fps.
EVENTS = ("TAP", "FLICK")


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


def ensure_qt_platform() -> str:
    """Point Qt at a platform plugin that OpenCV actually ships. Returns a one-line report.

    ## The failure this exists to prevent

    Measured on the Pi, 2026-08-23. The window did not open and the program exited cleanly
    saying "camera released", with one line of Qt noise above it:

        qt.qpa.plugin: Could not find the Qt platform plugin "wayland" in
          ".../.venv-gesture/lib/python3.12/site-packages/cv2/qt/plugins"

    Both halves of that are true and neither is a bug in this repo:

    * This Pi runs **labwc**, so `XDG_SESSION_TYPE=wayland`, so Qt auto-selects its `wayland`
      platform plugin.
    * The `opencv-python` wheel ships exactly one platform plugin — `libqxcb.so`. There is no
      `libqwayland*.so` in it and there never was.

    So Qt asks for a plugin that is not in the wheel, `cv2.imshow` creates nothing, and the
    loop's own "did the user close the window?" check sees no window on frame 1 and stops —
    which is correct behaviour reaching an incorrect conclusion, and the most confusing kind
    of exit there is.

    **Xwayland is already running** on this box (labwc starts it, socket at `/tmp/.X11-unix/X0`),
    so the xcb plugin the wheel *does* ship works fine — the window is presented through
    XWayland onto the Wayland desktop. That is what this selects.

    An explicit `QT_QPA_PLATFORM` from the environment is never overridden: someone who set it
    by hand is debugging exactly this, and a tool that silently disagrees is no help.
    """
    import importlib.util

    if os.environ.get("QT_QPA_PLATFORM"):
        return f"QT_QPA_PLATFORM={os.environ['QT_QPA_PLATFORM']} (from the environment, kept)"

    wayland = (os.environ.get("XDG_SESSION_TYPE") == "wayland"
               or bool(os.environ.get("WAYLAND_DISPLAY")))
    if not wayland:
        return ""                              # X11 or Windows: Qt's own default is right

    spec = importlib.util.find_spec("cv2")     # located WITHOUT importing it
    plugins = (Path(spec.submodule_search_locations[0]) / "qt" / "plugins" / "platforms"
               if spec and spec.submodule_search_locations else None)
    if plugins and list(plugins.glob("libqwayland*.so")):
        return ""                              # a wheel that can do wayland; leave it alone

    os.environ["QT_QPA_PLATFORM"] = "xcb"
    note = "QT_QPA_PLATFORM=xcb (wayland session, but this opencv only ships libqxcb.so)"

    # xcb needs a DISPLAY. Under labwc, Xwayland is already up and owns :0 — but a shell that
    # never had DISPLAY exported (a plain `ssh`, or a terminal started oddly) will not have it.
    if not os.environ.get("DISPLAY"):
        for sock in sorted(Path("/tmp/.X11-unix").glob("X*")) if Path("/tmp/.X11-unix").is_dir() else []:
            os.environ["DISPLAY"] = ":" + sock.name[1:]
            note += f", DISPLAY={os.environ['DISPLAY']} (Xwayland)"
            break
        else:
            note += ", and DISPLAY is unset with no X socket to guess — the window WILL fail"
    return note


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
        gap = gc._pinch_ratio(h)
        aspect = gc._aspect(h)
        frontal = aspect < gc.ASPECT_FRONTAL
        ceiling = gc.PINCH_MAX_RATIO if frontal else gc.PINCH_MAX_RATIO_PROFILE

        lines.append(f"hand {n}   pose {gc._classify(h)}"
                     f"{'   GARBAGE (aspect)' if aspect > gc.ASPECT_GARBAGE else ''}")
        lines.append(f"  span {scale:.3f}   aspect {aspect:.2f} "
                     f"({'frontal' if frontal else 'profile'})")

        # curl: the rotation-invariant fold. +1 straight, negative hooked.
        curls = " ".join(f"{c:+.2f}" for c in gc._curls(h))
        lines.append(f"  curl IMRP    {curls}"
                     f"   (folded < {gc.CURL_FOLDED}, straight > {gc.CURL_STRAIGHT})")

        # reach: the rotation-invariant extension. This is what says a finger is OUT.
        reaches = [gc._reach(h, tip, mcp) for mcp, _p, _d, tip in gc.CHAINS]
        out = "".join(c if r > gc.EXTEND_REACH else "."
                      for c, r in zip("IMRP", reaches))
        lines.append(f"  reach IMRP   " + " ".join(f"{r:.2f}" for r in reaches)
                     + f"   out:{out or '....'}  (> {gc.EXTEND_REACH})")

        back = (reaches[1] + reaches[2] + reaches[3]) / 3
        lines.append(f"  pinch gap    {gap:.2f} < {ceiling}   "
                     f"contrast {back - reaches[0]:+.2f} > {gc.PINCH_CONTRAST}   "
                     f"{'YES' if gc._is_pinch(h) else 'no'}")
        lines.append(f"  claw mouth   {gap:.2f} in "
                     f"{gc.CLAW_MOUTH_MIN}-{gc.CLAW_MOUTH_MAX}   "
                     f"{'YES' if gc._is_claw(h) else 'no'}")

        rise = (h[gc.WRIST].y - h[gc.THUMB_TIP].y) / scale
        lines.append(f"  thumb rise   {rise:+.2f}   "
                     f"(up > {gc.THUMB_RISE}, down < -{gc.THUMB_DROP})   "
                     f"{gc._thumb_direction(h) or '-'}")
    return lines


def save_sample(cv2, gc, root: Path, label: str | None, verdict: str,
                shown, raw, hands) -> int:
    """Write one tuning sample: the annotated frame, the clean frame, and the NUMBERS.

    ## The numbers are the point; the pictures are for the vlog

    A threshold cannot be fitted from a screenshot. The JSON beside it carries the 21 raw
    landmarks and every derived metric the classifier read, so a pose can be re-judged offline
    against a changed constant without going back to the camera — and so a disagreement
    between what LB meant and what the classifier said is a row of numbers rather than a
    recollection.

    `intended` is what LB was actually doing, from `--label`. It is the whole value of the
    file: fitting a threshold needs the truth, and the classifier's own verdict is precisely
    the thing under suspicion. Without it the sample is only evidence of what already happens.

    Returns 1 so the caller can count.
    """
    import json

    out = root / "media" / "captures"
    (out / "data").mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"gesture-{(label or verdict).lower()}-{stamp}"

    cv2.imwrite(str(out / f"{name}.png"), shown)
    cv2.imwrite(str(out / f"{name}-raw.png"), raw)

    sample = {
        "when": stamp,
        "intended": label,                 # None when the session was not labelled
        "classified": verdict,
        "agrees": None if label is None else label.upper() == verdict,
        "backend": getattr(gc, "_LAST_BACKEND", None),
        "thresholds": {k: getattr(gc, k) for k in (
            "EXTEND_REACH", "CURL_FOLDED", "CURL_STRAIGHT", "PINCH_MAX_RATIO",
            "PINCH_MAX_RATIO_PROFILE", "PINCH_CONTRAST", "PINCH_BACK_ARCH",
            "CLAW_MOUTH_MIN", "CLAW_MOUTH_MAX", "CLAW_INDEX_CURL", "CLAW_MIDDLE_CURL",
            "CLAW_MIN_REACH", "THUMB_RISE", "THUMB_DROP", "ASPECT_FRONTAL")},
        "hands": [],
    }
    for h in hands:
        sample["hands"].append({
            "pose": gc._classify(h),
            "span": gc._hand_scale(h),
            "aspect": gc._aspect(h),
            "pinch_gap": gc._pinch_ratio(h),
            "thumb_rise": (h[gc.WRIST].y - h[gc.THUMB_TIP].y) / gc._hand_scale(h),
            "curl": gc._curls(h),
            "reach": [gc._reach(h, tip, mcp) for mcp, _p, _d, tip in gc.CHAINS],
            # The raw landmarks, so any future metric can be computed from these files
            # without needing the hand back in front of the camera.
            "landmarks": [[h[i].x, h[i].y, getattr(h[i], "z", 0.0)] for i in range(21)],
        })

    with open(out / "data" / f"{name}.json", "w", encoding="utf-8") as fh:
        json.dump(sample, fh, indent=2)

    verdict_note = "" if label is None else (
        "  OK" if label.upper() == verdict else f"  <-- said {verdict}, you meant {label.upper()}")
    print(f"  saved {name}  ({len(hands)} hand){verdict_note}")
    return 1


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
    ap.add_argument("--claw-mouth", type=float, metavar="R",
                    help="override CLAW_MOUTH_MIN for this run")
    ap.add_argument("--extend", type=float, metavar="R",
                    help="override EXTEND_REACH (what counts as a finger being out)")
    ap.add_argument("--flick-speed", type=float, metavar="V",
                    help="override FLICK_MIN_SPEED, in palm spans per second")
    ap.add_argument("--label", metavar="POSE",
                    help="tag saved samples with the pose you INTENDED (e.g. --label claw). "
                         "Run one session per pose; press s several times. Without this, "
                         "samples are tagged with whatever the classifier said, which is "
                         "useless for fitting a threshold that is currently wrong.")
    ap.add_argument("--no-reexec", action="store_true",
                    help="do not jump into .venv-gesture even if it exists")
    args = ap.parse_args(argv)

    if not args.no_reexec:
        reexec_into_sidecar()               # never returns if it fires

    import tools.gesture_control as gc

    # Overrides land on the module the classifier reads, so the REAL decision changes — the
    # numbers panel and the gesture name can never disagree about which threshold was used.
    for flag, const in (("pinch", "PINCH_MAX_RATIO"), ("claw_mouth", "CLAW_MOUTH_MIN"),
                        ("extend", "EXTEND_REACH"), ("flick_speed", "FLICK_MIN_SPEED")):
        value = getattr(args, flag)
        if value is not None:
            print(f"  {const} = {value}  (was {getattr(gc, const)}, for this run only)")
            setattr(gc, const, value)

    # BEFORE anything imports cv2: Qt reads this when it loads its platform plugin, and
    # GestureRecognizer's constructor is the first thing here that imports cv2.
    qt_note = ensure_qt_platform()
    if qt_note:
        print(f"  {qt_note}")

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

    # ASCII ONLY, and it is load-bearing. Measured on the Pi 2026-08-23: this same title with
    # an em dash in it made `cv2.imshow` produce a window whose WND_PROP_VISIBLE was 0.0 and
    # which never appeared, while the identical call with an ASCII title reported 1.0 and drew
    # normally. OpenCV's Qt backend does not handle a non-ASCII window name on this build.
    #
    # It cost a whole debugging pass because it stacks with the Wayland plugin problem above:
    # both fail as "the window did not open", and fixing only one changes nothing visible.
    # This repo writes em dashes everywhere in prose, so the habit walked straight into a
    # string that is an identifier rather than prose.
    window = "MR ODD BALL - gesture tuning"
    assert window.isascii(), "the cv2 window title must be ASCII; see the note above"
    show_numbers = True
    fps, last = 0.0, time.monotonic()
    held, held_until, held_note = "NONE", 0.0, ""
    held_colour = (200, 200, 200)
    saved = frames = 0

    if args.label:
        print(f"  labelling samples as {args.label.upper()} — hold that pose and press s")
    else:
        print("  (no --label: samples are tagged with the classifier's own verdict, which is "
              "no use for fitting)")
    print("\n  q or ESC to quit, s to save a sample, h for the numbers panel\n")
    try:
        while True:
            frames += 1
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

            # Kept BEFORE the skeleton goes on. Saving only the annotated frame was a mistake
            # the first capture session paid for: re-running the detector over those PNGs to
            # recover the landmarks fails, because it re-detects its own bright overlay and
            # either misses the hand entirely or returns distorted joints. Three of LB's seven
            # 2026-08-23 captures came back "no hand" for exactly that reason.
            raw = frame.copy()

            for landmarks in hands:
                draw(frame, landmarks)

            now = time.monotonic()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last, 1e-6))
            last = now

            # FLICK is one frame wide by construction (it clears its own history so a swipe
            # fires once). One frame at 6.6 fps is 150 ms — too short to read. So it is HELD
            # on screen for a beat. The held banner is the only thing here that is not the
            # instantaneous truth, which is why it says so.
            if gesture in EVENTS:
                held, held_colour = gesture, COLOUR.get(gesture, (200, 200, 200))
                held_until, held_note = now + 0.8, rec.last_release
            banner = held if now < held_until else gesture
            key_colour = (held_colour if now < held_until
                          else COLOUR.get(gesture, (200, 200, 200)))

            h, w = frame.shape[:2]
            label(cv2, frame, banner, (16, 58), scale=1.6, colour=key_colour, thick=4)
            if now < held_until:
                label(cv2, frame, f"{held_note}   (held 0.8 s so it can be read)", (18, 84),
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
                saved += save_sample(cv2, gc, REPO_ROOT, args.label, gesture, frame, raw, hands)

            # `getWindowProperty` returns < 1 for BOTH "the user closed it" and "it was never
            # created", and those need opposite responses. On frame 1 it can only be the
            # second — no one closes a window inside 150 ms — so that case is diagnosed loudly
            # instead of exiting like a normal quit. That ambiguity is exactly what made the
            # 2026-08-23 Wayland failure look like a clean exit; see `ensure_qt_platform`.
            visible = cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE)
            if frames == 1 and visible < 1:
                print("\n  The window never opened — cv2.imshow created nothing.")
                print(f"    QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '(unset)')}   "
                      f"DISPLAY={os.environ.get('DISPLAY', '(unset)')}   "
                      f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '(unset)')}")
                print("    Look for a 'qt.qpa.plugin' line above this. If it names a plugin,")
                print("    that plugin is missing from the opencv wheel — force one it has:")
                print("      QT_QPA_PLATFORM=xcb DISPLAY=:0 python tools/live_test_gestures.py")
                print("    Over SSH with no desktop, there is nowhere to put a window at all;")
                print("    run it from the Pi's own terminal, or use ssh -X.\n")
                return 2
            if visible < 1:
                break                          # the window manager's close button
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
        print(f"  {saved} sample{'s' if saved != 1 else ''} in media/captures/ "
              f"(numbers in media/captures/data/)")
    print("  camera released\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
