#!/usr/bin/env python3
"""
Module:  verify_wake.py
Purpose: Prove the wake pipeline works without a microphone, a Pi, or a trained model.
Author:  LB
Date:    2026-08-10

    venv\\Scripts\\python.exe tools/verify_wake.py [--wav clip.wav]

Companion to tools/verify-rig.mjs, same contract: exit 0 = all passed, non-zero = something
is wrong, and every claim is measured rather than asserted.

Neither the Pi nor the Blue mic was attached when this was written, so it had to be possible
to verify the logic with neither. The threshold and refractory rules are driven through a stub
model, and the real network is exercised only where it genuinely has to be.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from audio.wake import (  # noqa: E402
    FRAME_SAMPLES,
    SAMPLE_RATE_HZ,
    Detection,
    WakeDetector,
    build_model,
    wav_frames,
)
from orchestrator.hud_bridge import HudBridge  # noqa: E402
from orchestrator.settings import load_config  # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


class StubModel:
    """A ScoringModel that returns whatever the test tells it to.

    The threshold and refractory rules are ordinary logic and deserve ordinary tests — the
    neural network has nothing to do with whether a refractory window is respected.
    """

    def __init__(self, scores: list[float], name: str = "stub") -> None:
        self._scores = list(scores)
        self._name = name
        self.resets = 0

    def predict(self, x: np.ndarray) -> dict:
        return {self._name: self._scores.pop(0) if self._scores else 0.0}

    def reset(self) -> None:
        self.resets += 1


def silence(n: int = 1) -> np.ndarray:
    return np.zeros(FRAME_SAMPLES * n, dtype=np.int16)


def noise(seconds: float, rng: np.random.Generator, amplitude: int = 6000) -> np.ndarray:
    n = int(SAMPLE_RATE_HZ * seconds)
    return (rng.standard_normal(n) * amplitude).astype(np.int16)


def frames_of(samples: np.ndarray):
    for i in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        yield samples[i : i + FRAME_SAMPLES]


# ============================================================ 1. configuration

section("config")

try:
    cfg = load_config()
    check(True, "config/oddball.toml loads and validates",
          f"model={cfg['wake']['model']!r} threshold={cfg['wake']['threshold']} "
          f"port={cfg['hud']['port']}")
except Exception as exc:  # noqa: BLE001
    check(False, "config/oddball.toml loads and validates", f"{type(exc).__name__}: {exc}")
    cfg = None

if cfg:
    # The framework default in openWakeWord is tflite, which has no Windows wheels. Getting
    # this wrong fails at load with an unhelpful message, so assert it is stated explicitly.
    check(cfg["wake"]["framework"] in ("onnx", "tflite"),
          "inference framework is stated explicitly, not left to openWakeWord's default",
          f"framework={cfg['wake']['framework']!r}")

    # Take the REAL config and break exactly one value. A hand-written stub would be missing
    # every other key and fail on the first of those instead — which is what the first version
    # of this check did: it passed while reporting "missing wake.model", proving nothing about
    # range validation at all.
    bad = REPO_ROOT / "config" / "_bad.toml"
    try:
        original = (REPO_ROOT / "config" / "oddball.toml").read_text(encoding="utf-8")
        # Derive the line to break from the CURRENT value rather than hardcoding
        # "threshold = 0.5". Hardcoding it meant that tuning the threshold silently turned
        # this check into a no-op: the replacement matched nothing, a perfectly valid config
        # was loaded, and it reported "it was accepted" as a failure of the validator rather
        # than of itself. Assert the anchor exists so it can never quietly test nothing.
        anchor = f"threshold = {cfg['wake']['threshold']}"
        assert anchor in original, f"cannot find {anchor!r} in the config to break"
        bad.write_text(original.replace(anchor, "threshold = 5.0"), encoding="utf-8")
        try:
            load_config(bad)
            check(False, "an out-of-range threshold is rejected at load", "it was accepted")
        except ValueError as exc:
            ok = "threshold" in str(exc)
            check(ok, "an out-of-range threshold is rejected at load",
                  str(exc).split(": ", 1)[-1] if ok else f"rejected, but for another reason: {exc}")
        except KeyError as exc:
            check(False, "an out-of-range threshold is rejected at load",
                  f"failed on a missing key instead, so the range rule went untested: {exc}")
    except AssertionError as exc:
        check(False, "an out-of-range threshold is rejected at load", str(exc))
    finally:
        bad.unlink(missing_ok=True)

# ============================================================ 2. detector logic

section("detector")

# --- frame contract. openWakeWord accepts a wrongly-sized array silently and just scores it
# worse, so a block-size mistake upstream would present as "the wake word got unreliable".
det = WakeDetector(StubModel([0.0]), threshold=0.5)
try:
    det.feed(np.zeros(999, dtype=np.int16))
    check(False, "a wrong-sized frame is refused", "it was accepted")
except ValueError as exc:
    check(True, "a wrong-sized frame is refused", str(exc))

try:
    det.feed(np.zeros(FRAME_SAMPLES, dtype=np.float32))
    check(False, "a wrong dtype is refused", "it was accepted")
except TypeError as exc:
    check(True, "a wrong dtype is refused", str(exc))

try:
    WakeDetector(StubModel([]), threshold=5.0)
    check(False, "a nonsense threshold is refused at construction", "it was accepted")
except ValueError:
    check(True, "a nonsense threshold is refused at construction", "threshold=5.0 rejected")

# --- threshold: fires above, silent below, at a known score
d = WakeDetector(StubModel([0.49, 0.51]), threshold=0.50, clock=lambda: 0.0)
below = d.feed(silence())
d2 = WakeDetector(StubModel([0.51]), threshold=0.50, clock=lambda: 0.0)
above = d2.feed(silence())
check(below is None and isinstance(above, Detection),
      "fires above the threshold and stays quiet below it",
      f"0.49 -> {'fire' if below else 'quiet'}, 0.51 -> {'fire' if above else 'quiet'}")

# --- refractory. A spoken phrase spans several 80ms frames, so without this one utterance
# fires repeatedly. Driven on a fake clock so the test needs no sleeping.
now = {"t": 0.0}
stub = StubModel([1.0] * 10)
d = WakeDetector(stub, threshold=0.5, refractory_s=2.0, clock=lambda: now["t"])
fires = []
for i in range(5):                       # 5 consecutive hot frames, 80ms apart
    now["t"] = i * 0.08
    if d.feed(silence()):
        fires.append(now["t"])
now["t"] = 5.0                           # well past the refractory window
if d.feed(silence()):
    fires.append(now["t"])
check(len(fires) == 2 and fires[0] == 0.0,
      "one utterance fires once, and a later one fires again",
      f"fired at {fires} from 6 hot frames (refractory 2.0s)")

check(stub.resets == 2,
      "the model buffer is cleared on each fire",
      f"{stub.resets} resets — without this the tail of the phrase re-fires immediately")

# ============================================================ 3. the real model

section("model")

model = None
if cfg:
    # Checked before the wake model, and separately, because it is a different failure with
    # a different fix. These are the shared feature extractor every wake model runs on top
    # of; when they are missing, onnxruntime raises NO_SUCHFILE naming a path inside
    # site-packages, which reads like a corrupt install rather than a missing download. A
    # fresh Pi configured straight to the custom model hit exactly that.
    try:
        from audio.wake import ensure_feature_models

        resources = ensure_feature_models(cfg["wake"]["framework"])
        ext = "onnx" if cfg["wake"]["framework"] == "onnx" else "tflite"
        missing = [n for n in ("melspectrogram", "embedding_model")
                   if not (resources / f"{n}.{ext}").exists()]
        check(not missing,
              "openWakeWord's feature models are present (downloaded on demand if not)",
              f"{resources}" if not missing else f"still missing: {missing}")
    except Exception as exc:  # noqa: BLE001
        check(False, "openWakeWord's feature models are present (downloaded on demand if not)",
              f"{type(exc).__name__}: {exc}")

    try:
        model = build_model(cfg["wake"]["model"], cfg["wake"]["framework"])
        check(True, "the configured wake model loads",
              f"{cfg['wake']['model']!r} via {cfg['wake']['framework']}")
    except Exception as exc:  # noqa: BLE001
        check(False, "the configured wake model loads", f"{type(exc).__name__}: {exc}")

if model is not None:
    thr = cfg["wake"]["threshold"]

    # --- silence must never wake him. This is the cheapest possible false-accept test and
    # it also proves the frontend is actually being fed in the shape it expects.
    d = WakeDetector(model, threshold=thr)
    peak, hits = 0.0, 0
    for frame in frames_of(silence(60)):        # ~4.8 s
        if d.feed(frame):
            hits += 1
        peak = max(peak, d.last_score)
    check(hits == 0, "4.8s of silence does not wake him",
          f"{hits} detections, peak score {peak:.4f} against threshold {thr}")

    # --- broadband noise at a realistic room level must not wake him either. Seeded, so a
    # regression here is reproducible rather than a coin toss.
    rng = np.random.default_rng(20260810)
    model.reset()
    d = WakeDetector(model, threshold=thr)
    peak, hits = 0.0, 0
    for frame in frames_of(noise(6.0, rng)):
        if d.feed(frame):
            hits += 1
        peak = max(peak, d.last_score)
    check(hits == 0, "6s of broadband noise does not wake him",
          f"{hits} detections, peak score {peak:.4f} against threshold {thr}")

# ============================================================ 4. the hud bridge

section("bridge")


async def bridge_roundtrip() -> tuple[bool, str]:
    """Start the real server, connect a real client, and confirm what arrives."""
    from websockets.asyncio.client import connect

    bridge = HudBridge("127.0.0.1", 8799, "sleeping")
    server = await bridge.start()
    try:
        async with connect("ws://127.0.0.1:8799") as ws:
            # A rig opened mid-session must be told the current state, not left on its default.
            # Before anything has happened that state is the RESTING one — he must not be
            # described as awake to a client that connects before the first real event.
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            if first != {"type": "state", "value": "sleeping"}:
                return False, f"new client got {first}, expected the resting state"

            await bridge.broadcast({"type": "state", "value": "listening"})
            await bridge.broadcast({"type": "gesture", "value": "startle"})
            got = [
                json.loads(await asyncio.wait_for(ws.recv(), timeout=2)),
                json.loads(await asyncio.wait_for(ws.recv(), timeout=2)),
            ]
            want = [
                {"type": "state", "value": "listening"},
                {"type": "gesture", "value": "startle"},
            ]
            if got != want:
                return False, f"got {got}, expected {want}"

            # And a client connecting later must see the updated state, not the stale default.
            async with connect("ws://127.0.0.1:8799") as ws2:
                late = json.loads(await asyncio.wait_for(ws2.recv(), timeout=2))
                if late != {"type": "state", "value": "listening"}:
                    return False, f"late client got {late}, expected the updated state"

        return True, "state replay, broadcast order, and late-join all correct"
    finally:
        server.close()


try:
    ok, detail = asyncio.run(bridge_roundtrip())
    check(ok, "the rig bridge serves, broadcasts, and replays current state", detail)
except Exception as exc:  # noqa: BLE001
    check(False, "the rig bridge serves, broadcasts, and replays current state",
          f"{type(exc).__name__}: {exc}")


def rig_understands() -> tuple[bool, str]:
    """The names broadcast must exist in the rig, or the wire is talking to nobody."""
    rig = (REPO_ROOT / "hud" / "face-preview.html").read_text(encoding="utf-8")
    states = set(__import__("re").findall(r'data-s="(\w+)"', rig))
    gestures = set(__import__("re").findall(r'data-g="(\w+)"', rig))
    sent_states = {"idle", "listening", "sleeping"}
    sent_gestures = {"startle"}
    missing = (sent_states - states) | (sent_gestures - gestures)
    if missing:
        return False, f"the rig has no {sorted(missing)}"
    return True, f"states {sorted(sent_states)} and gesture 'startle' all exist in the rig"


def rig_boots_asleep() -> tuple[bool, str]:
    """He must be ASLEEP the moment the rig is on screen, before any server speaks to it.

    Checked statically because the failure is invisible in review and obvious only on the
    couch: a rig that boots awake has already spent the change the wake word is supposed to
    cause. Three things have to agree, and they live in three different places, which is
    exactly why this is a check rather than a comment:

      - the boot constant itself is `sleeping`
      - `state`, `T` and the eased current values are all seeded from it, not from a second
        hardcoded set of numbers that used to describe `idle`
      - the pressed button in the static markup is the sleeping one, so the panel is not
        wrong for the frames before the script runs
    """
    import re

    rig = (REPO_ROOT / "hud" / "face-preview.html").read_text(encoding="utf-8")

    m = re.search(r'const\s+BOOT\s*=\s*"(\w+)"', rig)
    if not m:
        return False, "no `const BOOT` in the rig — the boot state is hardcoded somewhere"
    if m.group(1) != "sleeping":
        return False, f"the rig boots into {m.group(1)!r}, not 'sleeping'"

    for decl in (r'let\s+state\s*=\s*BOOT', r'let\s+T\s*=\s*STATES\[BOOT\]',
                 r'snapTo\(T\)', r'setState\(BOOT\)'):
        if not re.search(decl, rig):
            return False, f"the rig never does `{decl}` — boot state and drawn state can drift"

    # The one that would silently undo all of the above: seeding the eased values with the old
    # awake literals would make him animate from awake into sleep on every page load.
    if re.search(r'const\s+C\s*=\s*\{[^}]*eye\s*:\s*1\b', rig):
        return False, "the eased values are still hardcoded awake — he slumps into sleep on load"

    pressed = re.findall(r'data-s="(\w+)"[^>]*aria-pressed="true"', rig)
    if pressed != ["sleeping"]:
        return False, f"the markup marks {pressed or 'nothing'} as pressed, expected ['sleeping']"

    return True, "boots asleep: BOOT='sleeping', state/T/C all seeded from it, markup agrees"


try:
    ok, detail = rig_boots_asleep()
    check(ok, "he boots asleep, so the wake word has something to change", detail)
except Exception as exc:  # noqa: BLE001
    check(False, "he boots asleep, so the wake word has something to change",
          f"{type(exc).__name__}: {exc}")


try:
    ok, detail = rig_understands()
    check(ok, "every name this pipeline sends exists in the rig", detail)
except Exception as exc:  # noqa: BLE001
    check(False, "every name this pipeline sends exists in the rig", f"{type(exc).__name__}: {exc}")

# ============================================================ 5. optional real audio

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "wake"


def score_clip(path: Path, threshold: float) -> tuple[int, float, float]:
    """Run one recording two ways. Returns (detections, fired_at, true_peak).

    The model is reset first: openWakeWord's frontend holds a rolling buffer, so without it
    the tail of the previous clip leaks into this one and scores stop being per-clip facts.

    ## Why TWO numbers, and why using one for both jobs was a bug

    `WakeDetector.feed()` calls `reset()` the instant it fires, so the highest `last_score`
    ever seen through the detector is the score **at the firing frame** — after which the
    rolling buffer is empty and the clip cannot climb any higher. That number is therefore a
    function of the threshold, and using it to CHOOSE a threshold is circular.

    Measured on `positive/normal-03.wav`, 2026-08-15:

        true peak (raw model)      0.9946
        through the detector @ 0.30, 0.50, 0.60, 0.76   0.8140
        through the detector @ 0.90                     0.9902

    One frame carries it from below 0.30 to 0.8140, it fires there, and everything after is
    thrown away. So the harness reported the quietest positive as 0.8140 when the clip really
    reaches 0.9946 — **understating every positive, and making the band look tighter than it
    is.** That is why this file failed 0.76 for "only +7% margin" while `tune_threshold.py`,
    which reads the raw model, passed the same threshold at +29%. The tuner was right.

    So: `fired_at` answers *"did production fire, and where?"* — the pass/fail question, and it
    SHOULD go through the real detector. `true_peak` answers *"how high does this clip go?"* —
    a property of the audio alone, and the only honest input to a threshold decision.
    """
    model.reset()
    d = WakeDetector(model, threshold=threshold)
    fired_at, hits = 0.0, 0
    for frame in wav_frames(path):
        if d.feed(frame):
            hits += 1
        fired_at = max(fired_at, d.last_score)

    # Second pass, raw model, never interrupted — the threshold-independent number.
    model.reset()
    true_peak = 0.0
    for frame in wav_frames(path):
        scores = model.predict(frame)
        if scores:
            true_peak = max(true_peak, float(max(scores.values())))
    model.reset()
    return hits, fired_at, true_peak


def check_fixtures(root: Path, threshold: float, required: bool) -> None:
    """Score every recording under root/positive and root/negative.

    This is the acceptance test for a trained model, and the reason it is worth more than the
    synthetic checks above: silence and seeded noise prove the plumbing, but only a real voice
    on the real input path proves the model answers to his name.
    """
    section("fixtures")

    positives = sorted((root / "positive").glob("*.wav"))
    negatives = sorted((root / "negative").glob("*.wav"))
    # Clips whose behaviour is a measured, accepted limitation of the current model (D27).
    # They are scored and printed every run, but do not fail the build — a harness that is
    # permanently red is one nobody reads, which is the failure mode this project already
    # learned from prose checklists. A change here still shows up, in the numbers.
    limits = sorted((root / "known-limits").glob("*.wav"))

    # A harness that verified nothing has already shipped twice in this project. An empty
    # fixture directory is a failure when fixtures were asked for, never a quiet pass.
    if not positives and not negatives:
        if required:
            check(False, "fixture recordings exist",
                  f"no .wav under {root} — record them with tools/record_fixture.py --plan")
        return
    check(bool(positives), f"positive fixtures exist in {root.name}/positive",
          f"{len(positives)} clip(s)")

    pos_peaks: list[float] = []
    for path in positives:
        hits, fired_at, true_peak = score_clip(path, threshold)
        pos_peaks.append(true_peak)
        check(hits > 0, f"positive/{path.name} wakes him",
              f"{hits} detection(s), fired at {fired_at:.4f}, clip peaks {true_peak:.4f}, "
              f"threshold {threshold}")

    neg_peaks: list[float] = []
    for path in negatives:
        hits, fired_at, true_peak = score_clip(path, threshold)
        neg_peaks.append(true_peak)
        check(hits == 0, f"negative/{path.name} does NOT wake him",
              f"{hits} detection(s), clip peaks {true_peak:.4f} vs threshold {threshold}")

    if limits:
        scored = [(p.name, *score_clip(p, threshold)) for p in limits]
        detail = "; ".join(f"{n} {tp:.4f}{' FIRES' if h else ''}" for n, h, _fa, tp in scored)
        check(True, f"known limits, reported not enforced ({len(limits)} clips)", detail)

    # The number that actually justifies a threshold, and it is the TRUE peak of each clip,
    # not the score it happened to fire at — see score_clip. Step 3 earned its 0.5 by measuring
    # 0.580-0.987 spoken against a 0.000-0.084 floor and putting it in the empty band between;
    # this makes that a repeatable check rather than a one-off measurement in a document.
    if pos_peaks and neg_peaks:
        worst_pos, best_neg = min(pos_peaks), max(neg_peaks)
        band = worst_pos - best_neg
        midpoint = (worst_pos + best_neg) / 2
        check(band > 0, "positives and negatives are separable",
              f"quietest positive {worst_pos:.4f}, loudest negative {best_neg:.4f}, "
              f"band {band:+.4f} — a threshold of {midpoint:.2f} sits in the middle")
        check(threshold > best_neg and threshold < worst_pos,
              "the configured threshold sits inside that band",
              f"{threshold} against [{best_neg:.4f}, {worst_pos:.4f}]")

        # INSIDE THE BAND IS NOT ENOUGH, and 2026-08-14 is what proved it.
        #
        # 0.30 passed the check above for two days. It also sat at the very BOTTOM of a
        # 0.53-wide band, and every false wake landed in the empty space it left open: LB
        # reported "he keeps waking up as if i am calling him", and one 127-minute session
        # logged 57 wakes at a median score of 0.420 — a region where no fixture of any kind
        # lives. Evidence: media/data/2026-08-14-false-wakes.csv.
        #
        # A band has two edges and a threshold has to respect both. Too close to the negatives
        # and the room wakes him; too close to the positives and a quiet call is ignored. So
        # the check is now a POSITION within the band, not mere membership.
        # The bounds are IMPORTED from audio.autotune, not typed here. `tune_threshold.py`
        # recommends against the same constants, and a recommendation that failed the check
        # meant to accept it would be the worst kind of drift — silent, and only visible when
        # somebody follows the tool's advice and the harness rejects it.
        from audio.autotune import MARGIN_MIN, POSITION_MAX, POSITION_MIN   # noqa: E402

        position = (threshold - best_neg) / band
        check(POSITION_MIN <= position <= POSITION_MAX,
              "the threshold sits in the UPPER-MIDDLE of the band, not against either edge",
              f"{threshold} is {position:.0%} of the way from {best_neg:.4f} to "
              f"{worst_pos:.4f} — under {POSITION_MIN:.0%} is what let the room wake him on "
              f"2026-08-14, over {POSITION_MAX:.0%} would drop a quiet call")

        # And state the margin the way a person would ask for it, so a future change has to
        # argue with a number rather than a feeling.
        margin = (worst_pos - threshold) / threshold
        check(margin >= MARGIN_MIN,
              "a quiet call still clears the threshold with margin to spare",
              f"quietest recorded positive {worst_pos:.4f} is {margin:+.0%} above "
              f"{threshold} (floor is {MARGIN_MIN:+.0%})")
    elif pos_peaks:
        check(True, "positives scored, no negatives to compare against",
              f"peaks {min(pos_peaks):.4f}-{max(pos_peaks):.4f} — record negatives too")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the wake pipeline offline")
    ap.add_argument("--wav", help="a 16kHz mono WAV of the wake phrase; asserts it fires")
    ap.add_argument("--fixtures", nargs="?", const=str(FIXTURE_ROOT), default=None,
                    metavar="DIR",
                    help=f"score every clip under DIR/positive and DIR/negative "
                         f"(default {FIXTURE_ROOT.relative_to(REPO_ROOT)}). Positives must "
                         f"fire, negatives must not. Runs automatically when that directory "
                         f"exists.")
    args = ap.parse_args()

    # The fixtures are the regression test, so they run whether or not anyone remembers the
    # flag. Passing it explicitly additionally makes their absence an error.
    fixture_dir = Path(args.fixtures) if args.fixtures else FIXTURE_ROOT
    if model is not None and (args.fixtures or fixture_dir.is_dir()):
        check_fixtures(fixture_dir, cfg["wake"]["threshold"], required=bool(args.fixtures))
    elif args.fixtures:
        section("fixtures")
        check(False, "fixtures were requested but the model failed to load", "")

    if args.wav and model is not None:
        section("recording")
        hits, _fired_at, peak = score_clip(Path(args.wav), cfg["wake"]["threshold"])
        check(hits > 0, f"{Path(args.wav).name} wakes him",
              f"{hits} detection(s), peak score {peak:.4f}")
    elif args.wav:
        section("recording")
        check(False, "a wav was given but the model failed to load", "")

    # ------------------------------------------------------------------ report
    failed = [r for r in RESULTS if not r[0]]
    last = ""
    print()
    for ok, sec, msg, detail in RESULTS:
        if sec != last:
            print(f"  {sec}")
            last = sec
        print(f"   {'PASS' if ok else 'FAIL'}  {msg}")
        if detail:
            print(f"           {detail}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed\n")
    sys.exit(1 if failed else 0)
