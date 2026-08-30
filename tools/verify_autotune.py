#!/usr/bin/env python3
"""
Module:  verify_autotune.py
Purpose: Prove the threshold tuner cannot tune him deaf, and cannot report what it did not measure.
Author:  LB
Date:    2026-08-29

    python tools/verify_autotune.py
    python tools/verify_autotune.py --probe

No audio, no model, no microphone. `AdaptiveThreshold` is a pure function of the events it is
given, which is the property that makes this file possible and is stated as check 5 in its own
docstring.

## This file is late, and two bugs lived in the gap

`audio/autotune.py`'s docstring names `tools/verify_autotune.py` as the thing that checks it.
So does the comment over `POSITION_MIN`. It did not exist until now, and in its absence:

1. **`--replay` parsed nothing and said so quietly.** Its pattern wanted `oddball\\.run wake:`,
   the FileHandler format; `data/oddball.log` is the console stream, which carries no logger
   name. It printed "replayed 0 wakes" and then "settled at 0.300" — the hard-coded default,
   formatted exactly like a result.

2. **Silence after a wake was read as proof of a false wake.** That inference holds only if LB
   always speaks after waking him, and on 2026-08-29 the log caught the counter-example twice
   over, in the same second, from two processes on one microphone:

       11:22:17  rig    wake: hey_mr_odd_ball (0.767)
       11:22:17  meter  near miss: peaked 0.424

   He was calibrating. The rig heard the phrase, fired correctly, was never spoken to, and
   recorded a false positive that had not happened. 35 of those against 4 real wakes, and
   `--replay` recommended RAISING the threshold on a machine whose owner could not wake it.

Section 4 is the regression for the first and section 2 for the second.

## What "cannot tune him deaf" means here

The tuner's value is safety, not accuracy: a threshold that drifts up until he stops answering
fails silently and looks exactly like a broken microphone. Section 3 drives it with adversarial
event streams — all-false, all-real, alternating — and asserts the bounds hold at every step,
not merely at the end.
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from audio import autotune                                           # noqa: E402
from audio.autotune import (MARGIN_MIN, POSITION_MAX,                # noqa: E402
                            POSITION_MIN, POSITION_TARGET,
                            AdaptiveThreshold, Outcome, classify_wake)

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


def run() -> None:
    # =====================================================================================
    section("1. classify_wake — three answers, and the middle one is the point")
    # =====================================================================================
    check(classify_wake(3.44) == Outcome.REAL, "3.44s of voice is a person")
    check(classify_wake(0.08) == Outcome.FALSE, "0.08s is the room")
    check(classify_wake(0.45) == Outcome.UNKNOWN,
          "0.45s is evidence for NEITHER — the gap exists so an ambiguous wake costs nothing",
          "a single cutoff would have to call 'I want to go.' (0.16s) wrong in one direction")

    # The old signature must keep its old meaning exactly, or every existing caller silently
    # changes behaviour on upgrade.
    check(classify_wake(0.08, score=None) == Outcome.FALSE,
          "score=None restores the pre-2026-08-29 reading, unchanged")

    # =====================================================================================
    section("2. a wake the room cannot produce is never called false")
    # =====================================================================================
    #
    # THE regression. These are the real numbers from 2026-08-29 11:22:17.
    check(classify_wake(0.16, score=0.767) == Outcome.UNKNOWN,
          "the 11:22:17 wake at 0.767 with 0.16s after it is UNKNOWN, not FALSE",
          "LB was running --meter; the rig heard the phrase and was never spoken to")
    check(classify_wake(0.00, score=0.871) == Outcome.UNKNOWN,
          "and so is a 0.871 wake followed by total silence")

    # A score the room CAN produce, with nothing said, is still a false wake. Removing that
    # would make the tuner blind in the direction it exists to protect.
    check(classify_wake(0.00, score=0.05) == Outcome.FALSE,
          "but a 0.05 wake with nothing after it IS a false wake",
          "0.05 is under the measured room ceiling, so the room is a live explanation")
    check(classify_wake(0.00, score=autotune.ROOM_CEILING) == Outcome.FALSE,
          "the ceiling itself counts as reachable by the room")

    # Speech always wins, whatever the score. A loud wake followed by a real sentence is real.
    check(classify_wake(3.00, score=0.02) == Outcome.REAL,
          "and speech settles it regardless of score — a quiet wake he answered is REAL")

    # The ceiling is a measurement, and both measurements are recorded.
    check(autotune.ROOM_CEILING == 0.0885,
          f"the room ceiling is the WORSE of the two measurements ({autotune.ROOM_CEILING})",
          "2026-08-14 measured 0.0885; 2026-08-29 measured 0.005 over 60s. Fitting a safety "
          "bound to the quieter sample would be fitting it to the quietest minute on record")

    # =====================================================================================
    section("3. it cannot tune him deaf, and cannot tune itself into the noise")
    # =====================================================================================
    for name, events in (
            ("every wake false", [(0.95, Outcome.FALSE)] * 60),
            ("every wake real", [(0.62, Outcome.REAL)] * 60),
            ("alternating", [(0.9, Outcome.FALSE), (0.6, Outcome.REAL)] * 30),
            ("all unknown", [(0.8, Outcome.UNKNOWN)] * 60)):
        tuner = AdaptiveThreshold(configured=0.50, floor=0.30, ceiling=0.80)
        breached = []
        for score, outcome in events:
            tuner.observe(score, outcome)
            if not 0.30 <= tuner.value <= 0.80:
                breached.append(tuner.value)
        check(not breached, f"{name}: stays inside [0.30, 0.80] at EVERY step",
              "" if not breached else f"escaped to {breached[:3]}")

    # min_events, checked on the VALUE rather than on the counter — a counter that increments
    # while the value moves would pass a check on the counter alone.
    tuner = AdaptiveThreshold(configured=0.50, floor=0.30, ceiling=0.80, min_events=6)
    moved_early = []
    for i in range(5):
        tuner.observe(0.95, Outcome.FALSE)
        if tuner.value != 0.50:
            moved_early.append((i + 1, tuner.value))
    check(not moved_early,
          "five observations move it nowhere — one odd capture is not data",
          "" if not moved_early else f"moved at {moved_early}")
    tuner.observe(0.95, Outcome.FALSE)
    check(tuner.value != 0.50, "the sixth is when it is allowed to move",
          f"now {tuner.value:.3f}")

    # An EMA, so no single event can move it far.
    tuner = AdaptiveThreshold(configured=0.50, floor=0.30, ceiling=0.80, min_events=1)
    before = tuner.value
    tuner.observe(0.99, Outcome.FALSE)
    check(abs(tuner.value - before) < 0.20,
          f"one event moves it {abs(tuner.value - before):.3f}, not to the target",
          "a misclassification must cost a nudge, not a jump")

    # =====================================================================================
    section("4. --replay reads the log LB actually has, and admits an empty read")
    # =====================================================================================
    tmp = Path(tempfile.mkdtemp(prefix="oddball-autotune-"))

    console = tmp / "console.log"       # what start_oddball.bat redirects
    console.write_text(
        "07:06:06 INFO    wake: hey_mr_odd_ball (0.980)\n"
        "07:06:08 INFO    capture spoke: 2.48s audio, 3.10s voiced (peak vad 0.9)\n"
        "07:16:52 INFO    wake: hey_mr_odd_ball (0.847)\n"
        "07:17:02 INFO    capture spoke: 11.04s audio, 5.68s voiced (peak vad 0.9)\n",
        encoding="utf-8")

    handler = tmp / "handler.log"       # what the FileHandler writes, with the logger name
    handler.write_text(
        "07:06:06 INFO    oddball.run wake: hey_mr_odd_ball (0.980)\n"
        "07:06:08 INFO    oddball.listen capture spoke: 2.48s audio, 3.10s voiced\n",
        encoding="utf-8")

    for path, why in ((console, "the console format, which has no logger name"),
                      (handler, "the FileHandler format, which does")):
        out = io.StringIO()
        real = sys.stdout
        sys.stdout = out
        try:
            code = autotune._replay(str(path))
        finally:
            sys.stdout = real
        text = out.getvalue()
        parsed = "replayed 0 wakes" not in text
        check(parsed and code == 0, f"parses {why}",
              "" if parsed else f"read nothing from:\n           {path.read_text()[:60]!r}")

    empty = tmp / "nothing.log"
    empty.write_text("07:00:00 INFO    listening for the wake word\n", encoding="utf-8")
    out = io.StringIO()
    real = sys.stdout
    sys.stdout = out
    try:
        code = autotune._replay(str(empty))
    finally:
        sys.stdout = real
    text = out.getvalue()
    check(code != 0, "a log with no wakes in it exits NON-ZERO",
          "" if code != 0 else "it returned 0, so a script cannot tell it measured nothing")
    check("NOTHING PARSED" in text, "and says so in words")
    check("settled at" not in text,
          "and prints NO settled value — that is how this hid for a fortnight",
          f"printed: {text.strip()[-70:]!r}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    # =====================================================================================
    section("5. one definition of the position rule, three consumers")
    # =====================================================================================
    #
    # D37: the same number typed in three places drifts. verify_wake.py imports these rather
    # than restating them, and this asserts the shape they all rely on.
    check(0.0 < POSITION_MIN < POSITION_MAX < 1.0,
          f"the position band is ordered and inside (0, 1): {POSITION_MIN}-{POSITION_MAX}")
    check(POSITION_MIN <= POSITION_TARGET <= POSITION_MAX,
          f"the target {POSITION_TARGET} sits inside its own band")
    check(0.0 < MARGIN_MIN < 1.0, f"the margin is a fraction: {MARGIN_MIN}")

    # The worked example from autotune's own docstring, checked rather than asserted in prose.
    loudest_negative, quietest_positive = 0.0885, 0.6227
    recommended = loudest_negative + POSITION_TARGET * (quietest_positive - loudest_negative)
    check(abs(recommended - 0.49) < 0.01,
          f"the documented example still computes: {recommended:.3f} ~ 0.49",
          "loudest negative 0.0885, quietest positive 0.6227, target 0.75 of the band")

    # **The live threshold is NOT checked here, and that is deliberate.** `tools/verify_wake.py`
    # already enforces the position and margin rules against the fixture WAVs, which is the only
    # place the band can actually be MEASURED. Restating it here meant hardcoding a competing
    # "quietest positive" — 0.6227, copied out of a config comment — while verify_wake measures
    # 0.9771 from the files themselves. Two numbers for one quantity is the drift D37 exists to
    # stop, and it had already started: the two disagreed by 0.35 and would have certified
    # different thresholds.
    #
    # This file owns the tuner's LOGIC; verify_wake owns the band. One measurement, one place.
    check(True, "the band itself is verify_wake's to enforce — it has the fixtures",
          "checked there against measured WAVs, not against a number copied from a comment")


def probe() -> int:
    """Restore the old reading and show the mislabel come back."""
    print("\n  --probe: classify_wake without the score, on the 2026-08-29 wakes\n")
    rows = [(0.16, 0.767, "11:22:17, LB was running --meter"),
            (0.00, 0.871, "07:48:35"), (0.00, 0.807, "08:15:44"),
            (0.00, 0.862, "08:19:56"), (0.00, 0.878, "08:38:16")]
    wrong = 0
    for voiced, score, when in rows:
        old = classify_wake(voiced)                       # no score: the old behaviour
        new = classify_wake(voiced, score=score)
        wrong += old == Outcome.FALSE and new != Outcome.FALSE
        print(f"   score {score:.3f}  {old:>7} -> {new:>7}   {when}")
    print(f"\n  {wrong}/{len(rows)} would be counted as false wakes by the old reading")
    if wrong == len(rows):
        print("\n  The harness BITES.\n")
        return 0
    print("\n  PARTIAL: the old reading did not mislabel these, so section 2 proves little.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the adaptive wake threshold")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    run()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
