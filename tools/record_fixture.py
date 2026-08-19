#!/usr/bin/env python3
"""
Module:  record_fixture.py
Purpose: Record the wake-word regression fixtures — the clips that decide whether a trained
         model actually works.
Author:  LB
Date:    2026-08-11

    python tools/record_fixture.py --list-devices
    python tools/record_fixture.py --plan                  # the whole standard set, guided
    python tools/record_fixture.py positive normal --takes 6 --seconds 3
    python tools/record_fixture.py negative ambient --takes 1 --seconds 30 --no-level-check

Records **directly at 16 kHz mono int16**, which is exactly what `wav_frames()` in
`audio/wake.py` demands. There is deliberately no conversion step: resampling after the fact
is how the upstream training notebook ends up emitting `Clip does not have the correct sample
rate!`, and a fixture that silently disagrees with the detector's frame format would make
every score meaningless.

Countdown-driven and **reads no stdin**, so it behaves identically at a console and over SSH,
where prompt-and-wait is fragile. Levels are judged at record time, because a clipped or
inaudible take is worth knowing about now rather than during validation.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from audio.wake import SAMPLE_RATE_HZ, list_devices  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "wake"

# Peak-level gates, dBFS. A take outside these is re-recorded rather than kept.
CLIP_DBFS = -1.0     # above this the waveform is against the ceiling and syllables are lost
QUIET_DBFS = -40.0   # below this the phrase is barely above the noise floor
MAX_ATTEMPTS = 4

# Placement gates. Level alone is not enough: the first fixture set had six of ten positives
# with the phrase jammed against an edge of the window, and every one of them scored ~0.001
# against a model that scored 0.86-0.92 on the well-placed ones. A truncated phrase is not a
# quiet phrase — it is a different phrase, and it reads as a broken model rather than a
# broken recording.
EDGE_GUARD_S = 0.35  # speech touching this much of either end means it was cut off

# How far above the take's OWN noise floor a 100ms block has to be to count as speech.
#
# This used to be an absolute `SPEECH_DBFS = -30.0`, and on 2026-08-14 that made the recorder
# unusable: LB waited for GO, spoke, and every take came back **"speech starts at 0.0s — you
# began before GO"**. He had not. The C270's hiss peaks around **-16 dBFS**, which is 14 dB
# ABOVE the old absolute gate, so block zero of a silent room already counted as speech and
# the edge guard rejected every take before he opened his mouth.
#
# An absolute level cannot describe "louder than the room" — only the room can. So the gate is
# now relative to the quiet quarter of the take itself, which is the same lesson D46 took from
# PiSugar's `voice-detect.ts` and, unlike the wake threshold, here it applies directly:
# this really is a level comparison on raw audio.
SPEECH_MARGIN_DB = 8.0    # at least this far above the room
SPEECH_RANGE_FRAC = 0.40  # ...or this fraction up the take's own dynamic range, whichever is more
MIN_DYNAMIC_DB = 6.0      # below this the take is all room and holds no phrase at all
NOISE_PERCENTILE = 25     # "the room" is the quiet quarter of the blocks


@dataclass(frozen=True)
class Item:
    """One group of takes in the standard plan."""

    kind: str           # "positive" | "negative" | "command"
    label: str          # becomes the filename stem
    takes: int
    seconds: float
    say: str            # what to do, printed before the countdown
    level_check: bool = True
    # Where the takes land. Wake fixtures are scored by the wake MODEL and live under
    # tests/fixtures/wake/; the sleep phrase is not a wake word at all — it is transcribed by
    # whisper and matched by `router._SLEEP_PHRASES` — so its recordings are command fixtures
    # and belong beside the others in tests/fixtures/commands/.
    #
    # Same session, two destinations, which is the point: LB asked to record the wake and
    # sleep phrases at the same time, and they are graded by completely different machinery.
    root: str = "wake"


# The standard fixture set. Positives must fire; negatives must not. The negatives matter as
# much as the positives — a model that wakes at the television is not usable, and nothing in
# the harness could notice that without recordings of the television.
#
# Windows are 5s, not 3s. The first fixture set used 3s and the phrase takes ~1.2s to say:
# after a normal beat of reaction time it ran off the end, and six of ten positives were
# unusable. 5s costs nothing and leaves room for the edge guards to mean something.
PLAN: list[Item] = [
    Item("positive", "normal", 6, 5.0,
         'say "Hey Mr Odd Ball" at normal volume, desk distance'),
    Item("positive", "quiet", 2, 5.0,
         'say "Hey Mr Odd Ball" quietly, like the house is asleep'),
    # KNOWN LIMIT, not a positive — reported by the harness, never enforced.
    #
    # Filed as `positive` until 2026-08-14, which made the harness demand that far-field wake
    # WORK. It does not and D27 says so with numbers: far-02 scores 0.0023 and no threshold
    # recovers it, because "this is a desk-range assistant" is a measured property of a
    # near-field cardioid microphone, not a bug. The shipped fixture set had these under
    # `known-limits/` and the PLAN disagreed with it; re-recording with `--fresh` is what
    # finally surfaced the disagreement, as three red checks for behaviour nobody expects.
    Item("known-limits", "far", 2, 5.0,
         'say "Hey Mr Odd Ball" from across the room — expected NOT to fire'),

    Item("negative", "ambient", 1, 30.0,
         "say NOTHING — let the room be the room", level_check=False),
    Item("negative", "speech", 1, 60.0,
         "talk normally, or leave the TV on. Anything EXCEPT the wake phrase",
         level_check=False),
    Item("negative", "hey-jarvis", 2, 5.0,
         'say "Hey Jarvis" — the stand-in model, which the new one must not inherit'),
    # Prefixes of the wake phrase are the measured weak spot: "Hey Mister Odd" scored 0.7173
    # against a trained model whose quietest true positive was 0.7681. These probe that edge
    # deliberately rather than hoping it does not exist.
    Item("negative", "hey-mister-odd", 2, 5.0, 'say "Hey Mister Odd" — then STOP'),
    Item("negative", "hey-mr-odd", 2, 5.0, 'say "Hey Mr Odd" — then STOP'),
    Item("negative", "hey-mister", 1, 5.0, 'say "Hey Mister"'),
    Item("negative", "odd-ball", 1, 5.0, 'say "Odd Ball" — no "hey"'),
    # ALSO a known limit. D27 measured "Mister Odd Ball" without the "hey" at 0.9912 and
    # records that **LB chose not to retrain for it** — he does not mind it firing. A clip the
    # owner has decided is acceptable cannot be filed as a negative the harness enforces; that
    # turns a documented decision into a permanent red check.
    Item("known-limits", "mister-odd-ball", 1, 5.0,
         'say "Mister Odd Ball" — no "hey". Expected to fire; that is allowed'),
    Item("negative", "hey-mr-on-call", 1, 5.0, 'say "Hey Mr On Call"'),

    # --- the SLEEP phrase, added 2026-08-14 with conversation mode -----------------------
    #
    # These are graded by a different machine from everything above. A wake fixture is scored
    # by the ONNX wake model; a sleep phrase is transcribed by whisper and matched as text by
    # `router._SLEEP_PHRASES`. So the pass/fail question is "did tiny.en hear the words", not
    # "did the model score above a threshold", and `tools/verify_stt.py` is what reads them.
    #
    # Recording them in the same sitting is deliberate and is what LB asked for: same
    # microphone, same position, same room, same voice, same afternoon. Fixtures recorded in
    # two different sessions are two different measurements, and D44 is this project's lesson
    # about exactly that.
    #
    # NO level check on these: they are ordinary speech at conversational volume, not a phrase
    # that has to sit cleanly inside a 5s window for a sliding scorer.
    Item("command", "sleep-go-to-sleep", 2, 4.0,
         'say "Go to sleep"', level_check=False, root="commands"),
    Item("command", "sleep-goodnight", 2, 4.0,
         'say "Goodnight"', level_check=False, root="commands"),
    Item("command", "sleep-thats-all", 2, 4.0,
         'say "That\'s all"', level_check=False, root="commands"),
    Item("command", "sleep-im-done", 1, 4.0,
         'say "I\'m done"', level_check=False, root="commands"),
    # The mirror, and it earns its place for the same reason the wake negatives do: a
    # dismissal that fires on an ordinary sentence would end conversations LB is still having.
    # The label's FIRST segment is the intent `verify_stt.py --real` expects
    # (`path.stem.split("-")[0]`), so this has to be "unknown" rather than "not-sleep" — a
    # sentence that merely mentions sleep must fall through to the model, which is intent
    # `unknown`. Naming it "not-sleep" would have made the harness look for a "not" intent
    # and fail on a correctly-behaving recording.
    Item("command", "unknown-sleepmention", 2, 5.0,
         'say "How much sleep did I get last night" — it mentions sleep '
         'and must NOT dismiss him',
         level_check=False, root="commands"),
]


def peak_dbfs(samples: np.ndarray) -> float:
    """Peak level in dBFS. -inf for digital silence."""
    peak = int(np.abs(samples).max()) if samples.size else 0
    if peak == 0:
        return float("-inf")
    return 20.0 * float(np.log10(peak / 32768.0))


def block_levels(samples: np.ndarray) -> np.ndarray:
    """Peak dBFS of each 100ms block. Empty if the take is shorter than one block."""
    blk = SAMPLE_RATE_HZ // 10
    if samples.size < blk:
        return np.empty(0)
    usable = samples[: (samples.size // blk) * blk].reshape(-1, blk)
    peaks = np.abs(usable).max(axis=1).astype(np.float64)
    return 20.0 * np.log10(np.maximum(peaks, 1.0) / 32768.0)


def speech_span(samples: np.ndarray) -> tuple[float, float] | None:
    """Seconds of the first and last 100ms block containing speech, or None if there is none.

    **Relative to the take's own noise floor, not to an absolute level.** See
    `SPEECH_MARGIN_DB` — an absolute gate made this reject every take in a room whose hiss sat
    above it, and reject them with a message blaming LB's timing.

    Returns None when nothing in the take stands out from the room by `MIN_DYNAMIC_DB`, which
    is the honest answer for a take with no phrase in it and is what `level_verdict` turns
    into "no speech found" rather than a bogus span.
    """
    blocks = block_levels(samples)
    if blocks.size == 0:
        return None

    noise = float(np.percentile(blocks, NOISE_PERCENTILE))
    peak = float(blocks.max())
    if peak - noise < MIN_DYNAMIC_DB:
        return None

    threshold = noise + max(SPEECH_MARGIN_DB, (peak - noise) * SPEECH_RANGE_FRAC)
    loud = np.nonzero(blocks > threshold)[0]
    if loud.size == 0:
        return None

    blk = SAMPLE_RATE_HZ // 10
    return float(loud[0] * blk) / SAMPLE_RATE_HZ, float((loud[-1] + 1) * blk) / SAMPLE_RATE_HZ


def level_verdict(samples: np.ndarray) -> tuple[bool, str]:
    """Judge a take by peak level *and* where the phrase sits in the window.

    Returns (acceptable, human-readable reason). The placement half matters as much as the
    level: openWakeWord scores an 80ms sliding window and needs the whole phrase, with a
    little air either side, inside the recording.
    """
    db = peak_dbfs(samples)
    if db == float("-inf"):
        return False, "digital silence — is the right device selected?"
    if db > CLIP_DBFS:
        return False, f"clipping at {db:+.1f} dBFS — move back or lower the input gain"
    if db < QUIET_DBFS:
        return False, f"only {db:+.1f} dBFS — too quiet to be a fair test"

    # Reported on every verdict, because a rejection has to be diagnosable from the message
    # alone. The absolute-gate bug told LB he had spoken too early, four times in a row,
    # while showing him nothing that would have revealed the room was the problem.
    blocks = block_levels(samples)
    room = float(np.percentile(blocks, NOISE_PERCENTILE)) if blocks.size else float("-inf")
    levels = f"peak {db:+.1f} dBFS, room {room:+.1f} dBFS"

    span = speech_span(samples)
    if span is None:
        return False, (f"{levels} — nothing stands out from the room by {MIN_DYNAMIC_DB:.0f} dB. "
                       f"Say it louder, or move closer to the microphone")
    start, end = span
    duration = len(samples) / SAMPLE_RATE_HZ
    if start < EDGE_GUARD_S:
        return False, (f"speech starts at {start:.1f}s ({levels}) — the start is cut off. "
                       f"Wait for GO, then say it")
    if end > duration - EDGE_GUARD_S:
        return False, (f"speech runs to {end:.1f}s of {duration:.0f}s ({levels}) — the end is "
                       f"cut off. Say it a little sooner after GO")
    return True, f"{levels}, phrase at {start:.1f}-{end:.1f}s"


def record(seconds: float, device: str | int | None) -> np.ndarray:
    """Capture `seconds` of 16 kHz mono int16 audio. Blocks until done."""
    import sounddevice as sd

    frames = int(round(seconds * SAMPLE_RATE_HZ))
    buf = sd.rec(
        frames,
        samplerate=SAMPLE_RATE_HZ,
        channels=1,
        dtype="int16",
        device=device if device not in ("", None) else None,
    )
    sd.wait()
    return buf[:, 0].copy()


def write_wav(path: Path, samples: np.ndarray) -> None:
    """Write mono 16-bit PCM at SAMPLE_RATE_HZ — the exact format wav_frames() expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE_HZ)
        w.writeframes(samples.tobytes())


def countdown(seconds: int = 3) -> None:
    """Spoken-pace countdown. Printed, never prompted — this has to work over SSH."""
    for n in range(seconds, 0, -1):
        print(f"      {n}...", flush=True)
        time.sleep(1.0)


def item_dir(item: Item) -> Path:
    """Where this item's takes belong.

    Wake fixtures go under `tests/fixtures/wake/{positive,negative}/` where the wake harness
    looks for them. Command fixtures — the sleep phrase — go under `tests/fixtures/commands/`
    beside the five recordings of LB's voice from 2026-08-12, because whisper grades them and
    `tools/verify_stt.py` is what reads them.
    """
    if item.root == "wake":
        return FIXTURE_ROOT / item.kind
    return REPO_ROOT / "tests" / "fixtures" / item.root


def next_index(directory: Path, label: str) -> int:
    """Lowest unused take number, so re-running never overwrites an existing recording."""
    existing = sorted(directory.glob(f"{label}-*.wav"))
    used = set()
    for p in existing:
        try:
            used.add(int(p.stem.rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    n = 1
    while n in used:
        n += 1
    return n


def record_item(item: Item, device: str | int | None, fresh: bool) -> list[tuple[Path, float]]:
    """Record every take of one plan item. Returns [(path, peak_dbfs)]."""
    out_dir = item_dir(item)
    out_dir.mkdir(parents=True, exist_ok=True)

    if fresh:
        for stale in out_dir.glob(f"{item.label}-*.wav"):
            stale.unlink()
            print(f"   removed {stale.name}")

    if item.kind == "command":
        # Graded by whisper, not by the wake model, so "fires" is the wrong word entirely.
        fires = "must TRANSCRIBE"
    elif item.kind == "known-limits":
        fires = "reported, NOT enforced"
    else:
        fires = "must FIRE" if item.kind == "positive" else "must NOT fire"
    print(f"\n── {item.kind}/{item.label}  ({item.takes}× {item.seconds:g}s, {fires})")
    print(f"   {item.say}")

    written: list[tuple[Path, float]] = []
    for take in range(1, item.takes + 1):
        idx = next_index(out_dir, item.label)
        path = out_dir / f"{item.label}-{idx:02d}.wav"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"   take {take}/{item.takes} — starting in", flush=True)
            countdown(3)
            print(f"      GO — recording {item.seconds:g}s", flush=True)
            samples = record(item.seconds, device)

            ok, reason = level_verdict(samples)
            if ok or not item.level_check:
                note = reason if item.level_check else f"peak {peak_dbfs(samples):+.1f} dBFS"
                write_wav(path, samples)
                print(f"      saved {path.name}  ({note})")
                written.append((path, peak_dbfs(samples)))
                break
            if attempt < MAX_ATTEMPTS:
                print(f"      REJECTED: {reason} — retrying")
            else:
                write_wav(path, samples)
                print(f"      kept anyway after {MAX_ATTEMPTS} attempts: {reason}")
                print("      >>> review this one before trusting the fixture set")
                written.append((path, peak_dbfs(samples)))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Record wake-word regression fixtures at 16kHz mono",
        epilog="Then score them: venv/Scripts/python.exe tools/verify_wake.py --fixtures",
    )
    # "known-limits" and "command" belong here too: the PLAN records both, so an ad-hoc
    # re-record of a single one of them has to be expressible. Leaving them out meant a bad
    # take in either category could only be fixed by re-recording the entire plan.
    ap.add_argument("kind", nargs="?",
                    choices=["positive", "negative", "known-limits", "command"],
                    help="omit and pass --plan to record the whole standard set")
    ap.add_argument("label", nargs="?", help="filename stem, e.g. 'normal' or 'ambient'")
    ap.add_argument("--plan", action="store_true",
                    help="record the full standard set: 10 positives, 8 negatives, ~6 min")
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--say", default="say the phrase", help="prompt text for an ad-hoc group")
    ap.add_argument("--no-level-check", action="store_true",
                    help="accept any level — correct for ambient and background-speech takes")
    ap.add_argument("--device", default=None,
                    help="input device: index, or a substring of its name. Prefer the name: "
                         "indices shift when Bluetooth devices reconnect")
    ap.add_argument("--fresh", action="store_true",
                    help="delete existing takes with this label first (default is to add more)")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args(argv)

    if args.list_devices:
        print(list_devices())
        return 0

    device: str | int | None = args.device
    if device not in ("", None):
        try:
            device = int(device)
        except ValueError:
            pass

    if args.plan:
        items = PLAN
    elif args.kind and args.label:
        # `command` takes live under tests/fixtures/commands/, not under wake/ — the same
        # split the PLAN uses, because whisper grades them and the wake model does not.
        items = [Item(args.kind, args.label, args.takes, args.seconds, args.say,
                      level_check=not args.no_level_check,
                      root="commands" if args.kind == "command" else "wake")]
    else:
        ap.error("give KIND and LABEL, or --plan")

    try:
        import sounddevice  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"sounddevice unavailable: {exc}")
        return 1

    devices = list_devices()
    if devices == "no input devices found":
        print("no input devices found — nothing to record with.\n"
              "On Windows check Sound settings; on the Pi, `arecord -l`.")
        return 1
    print(devices)
    roots = sorted({item_dir(i).relative_to(REPO_ROOT).as_posix() for i in items})
    print("\nrecording into " + ", ".join(roots))
    if args.plan:
        total_s = sum(i.takes * i.seconds for i in PLAN)
        wake_takes = sum(i.takes for i in PLAN if i.root == "wake")
        cmd_takes = sum(i.takes for i in PLAN if i.root != "wake")
        print(f"full plan: {wake_takes} wake takes + {cmd_takes} command takes, "
              f"{total_s / 60:.1f} min of audio")

    written: list[tuple[Path, float]] = []
    try:
        for item in items:
            written.extend(record_item(item, device, args.fresh))
    except KeyboardInterrupt:
        print("\n\nstopped. Everything recorded so far is saved.")

    print(f"\n{len(written)} clip(s) written:")
    for path, db in written:
        print(f"   {path.relative_to(REPO_ROOT)}  {db:+.1f} dBFS")
    if written:
        # Two destinations, two graders. Naming both is what stops the sleep-phrase takes
        # being recorded and then never scored by anything.
        print("\nnow score them:")
        print("   venv/bin/python tools/verify_wake.py --fixtures     # the wake model")
        print("   venv/bin/python tools/verify_stt.py --real          # whisper + the router")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
