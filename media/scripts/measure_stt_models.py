#!/usr/bin/env python3
"""
Module:  measure_stt_models.py
Purpose: tiny.en against base.en on LB's OWN recordings, on the workstation that runs them now.
Author:  LB
Date:    2026-08-28

    python media/scripts/measure_stt_models.py
    python media/scripts/measure_stt_models.py --models tiny.en base.en small.en --reps 3

## The claim being re-tested

`config/oddball.toml` picks `tiny.en` and says why:

    tiny.en   1.05-1.40s   7/9 exact
    base.en   2.03-2.49s   8/9 exact

    tiny.en is the default because it is the ONLY one that fits PLAN.md's under-2s turn once
    ~0.4s of Piper synthesis is added on.

**Both halves of that are Pi-era.** The timings are a Cortex-A76 at 4 threads; this runs on a
Ryzen 7 5700X. And "nine synthesised commands" is not LB — the config says so itself:
*"Measured on SYNTHESISED speech, not on LB's voice. If he is misheard in practice, switch to
base.en and accept a slower turn; it is one word."*

He is misheard in practice. `captures/` holds *"sync my schedule"* transcribed as **"sink my
schedule"** four times, **"sick mass schedule"** once and **"i think my schedule"** twice —
seven attempts, zero correct. That is the sentence `orchestrator/route_hint.py` matches to send
a turn to ACADEMIC for free, and none of those seven spellings match it.

So this script answers the question the config invited: **on this hardware, on his voice, what
does base.en cost and what does it buy?**

## Scored on where the transcript ROUTES, not on how it reads

Word-error rate needs a ground truth, and the only record of what LB said is the transcript
under test. Writing down what I think he meant and scoring against that measures my guess.

What can be measured without a ground truth is the thing that actually matters: **does the
transcript reach a handler, for free?** `route_hint.look_up` and the free tier
(`instant.Router` with the launch and note planners) are pure functions of a string, so every
transcript can be put through the real ones. "sync my schedule" reaches ACADEMIC with no API
call; "sink my schedule" reaches nothing and costs a routing call to be told so.

That is an outcome, it is objective, and it is what LB experiences.

`EXPECTED` below carries a destination for the clips where the intent is not in doubt — the
seven schedule attempts and the two note attempts. It is hand-written, it is marked as such,
and accuracy is reported over those rows ONLY, with the count stated. Everything else is
reported as a transcript pair and nothing more.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

CAPTURES = REPO / "captures"
OUT_CSV = REPO / "media" / "data" / "2026-08-28-stt-tiny-vs-base.csv"

# Production parity, from config/oddball.toml [stt]. NOT tuned up for this box on purpose:
# the question is what the shipping configuration does, and `cpu_threads = 4` is what ships.
# This machine has more cores, so both arms are equally under-served and the comparison holds.
COMPUTE_TYPE = "int8"
CPU_THREADS = 4

# Where a transcript SHOULD land, for the clips whose intent is not in doubt. Hand-written,
# and deliberately small — a guess added here becomes a number reported as accuracy.
#
# The key is the capture's filename stem, which is `tiny.en`'s own transcription of it. That is
# only an identifier here, never a ground truth.
EXPECTED: dict[str, str] = {
    # "sync my schedule" — route_hint._SYNC sends this to ACADEMIC for free. Six attempts.
    "162328_sink-my-schedule": "academic",
    "165448_i-think-my-schedule-": "academic",
    "165508_i-think-my-schedule-": "academic",
    "165531_sick-mass-schedule-": "academic",
    "165645_sink-my-schedule": "academic",
    "170833_sink-my-schedule": "academic",
    # The two note attempts, 2026-08-28 08:49:50 and 08:50:47.
    "084950_can-you-save-a-note-for-me-in-the-vault-": "note",
    "085047_i-need-to-add-a-note-to-the-vault-": "note",
    # Dismissals and free lookups — unambiguous, and they are what keeps this honest. A model
    # that wins on the schedule clips and loses these has not obviously won.
    "162405_go-to-sleep-": "sleep",
    "165727_go-to-sleep-": "sleep",
    "170901_go-to-sleep-": "sleep",
    "165551_what-s-20-plus-20-": "calc",
    "165557_that-s-20-times-20-": "calc",
    "162355_thank-you-": "thanks",
}

# Clips that must reach NOTHING free. "So." is LB clearing his throat mid-conversation, and a
# transcript that routes it anywhere is a false positive — the expensive kind, because
# `sleep` ends the conversation. Scored separately from EXPECTED, and against the same bar
# `orchestrator/note_intent.py` is held to: the danger is the rule that matches too much.
MUST_NOT_ROUTE: tuple[str, ...] = (
    "165632_so-",
    "165705_cool-",
    "162259_available-",
    "084910_elbow-",
    "165715_to-meet-them-",
    "170750_i-ll-see-you-in-the-next-video-",
    "181033_we-ll-see-you-in-the-next-one-",
)

FIELDS = ["measured_at", "clip", "model", "transcript", "seconds", "audio_s", "rtf",
          "free_destination", "expected", "correct"]


def free_destination(text: str) -> str:
    """Where this transcript lands without spending an API call, using the real matchers.

    Returns one of the free destinations, or "" when nothing free matches and the turn would
    have to pay the router to find out where it belongs.
    """
    from orchestrator import launch_intent, note_intent, route_hint
    from orchestrator.instant import Router

    if not (text or "").strip():
        return ""

    hint = route_hint.look_up(text)
    if hint:
        return hint

    reply = Router(planners={"note": note_intent.look_up,
                             "launch": launch_intent.look_up}).route(text)
    action = reply.action
    if isinstance(action, note_intent.NoteRequest):
        return "note"
    if isinstance(action, launch_intent.LaunchRequest):
        return "launch"
    return reply.intent if reply.handled else ""


def transcribe_all(name: str, clips: list[Path], reps: int) -> list[dict]:
    """Every clip through one model. Returns one row per clip."""
    from audio.stt import Transcriber, build_model, wav_audio

    print(f"\n  loading {name} ({COMPUTE_TYPE}, {CPU_THREADS} threads)…", flush=True)
    t0 = time.perf_counter()
    scribe = Transcriber(build_model(name, COMPUTE_TYPE, CPU_THREADS))
    print(f"  loaded in {time.perf_counter() - t0:.1f}s")

    rows = []
    for clip in clips:
        audio = wav_audio(clip)
        audio_s = len(audio) / 16_000
        samples, text = [], ""
        for _ in range(reps):
            t0 = time.perf_counter()
            heard = scribe.transcribe(audio)
            samples.append(time.perf_counter() - t0)
            text = heard.text.strip()

        seconds = statistics.median(samples)
        where = free_destination(text)
        want = EXPECTED.get(clip.stem, "")
        rows.append({
            "clip": clip.stem, "model": name, "transcript": text,
            "seconds": round(seconds, 3), "audio_s": round(audio_s, 2),
            "rtf": round(seconds / audio_s, 3) if audio_s else "",
            "free_destination": where, "expected": want,
            "correct": ("yes" if where == want else "no") if want else "",
        })
        print(f"    {clip.stem[:38]:40} {seconds:5.2f}s  "
              f"{where or '(pays the router)':<18} {text[:44]!r}")
    return rows


def report(rows: list[dict], models: list[str]) -> None:
    print("\n" + "=" * 86)
    print("  RESULT")
    print("=" * 86)

    for name in models:
        mine = [r for r in rows if r["model"] == name]
        judged = [r for r in mine if r["expected"]]
        right = sum(r["correct"] == "yes" for r in judged)
        times = [r["seconds"] for r in mine]
        # The other half of the score. A model that gains on the clips LB cares about and
        # starts routing his throat-clearing to `sleep` has bought one with the other.
        noise = [r for r in mine if r["clip"] in MUST_NOT_ROUTE]
        false_hits = [r for r in noise if r["free_destination"]]
        print(f"\n  {name}")
        print(f"    median {statistics.median(times):.2f}s   "
              f"slowest {max(times):.2f}s   "
              f"median RTF {statistics.median([r['rtf'] for r in mine if r['rtf']]):.2f}")
        print(f"    routes correctly on {right}/{len(judged)} clips whose intent is known")
        print(f"    FALSE routes on {len(false_hits)}/{len(noise)} clips that meant nothing"
              + (":" if false_hits else ""))
        for r in false_hits:
            print(f"        {r['clip'][:34]:36} -> {r['free_destination']:<9} "
                  f"{r['transcript'][:34]!r}")

    if len(models) == 2:
        a, b = models
        ta = statistics.median([r["seconds"] for r in rows if r["model"] == a])
        tb = statistics.median([r["seconds"] for r in rows if r["model"] == b])
        ja = [r for r in rows if r["model"] == a and r["expected"]]
        jb = [r for r in rows if r["model"] == b and r["expected"]]
        ra = sum(r["correct"] == "yes" for r in ja)
        rb = sum(r["correct"] == "yes" for r in jb)
        print(f"\n  {b} costs {tb - ta:+.2f}s per utterance and gets {rb - ra:+d} more "
              f"of {len(ja)} right.")

        print("\n  where they disagree:")
        by_clip: dict[str, dict[str, str]] = {}
        for r in rows:
            by_clip.setdefault(r["clip"], {})[r["model"]] = r["transcript"]
        shown = 0
        for clip, texts in by_clip.items():
            if texts.get(a, "").lower() != texts.get(b, "").lower():
                shown += 1
                print(f"    {clip[:40]}")
                print(f"       {a:>8}: {texts.get(a, '')!r}")
                print(f"       {b:>8}: {texts.get(b, '')!r}")
        if not shown:
            print("    (none — the models agreed on every clip)")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="tiny.en vs base.en on LB's own recordings")
    ap.add_argument("--models", nargs="+", default=["tiny.en", "base.en"])
    ap.add_argument("--reps", type=int, default=3, help="timing reps per clip (median taken)")
    ap.add_argument("--out", default=str(OUT_CSV))
    args = ap.parse_args(argv)

    clips = sorted(p for p in CAPTURES.glob("*.wav") if "empty" not in p.name)
    if not clips:
        sys.exit(f"  no recordings in {CAPTURES} — nothing to measure")
    print(f"  {len(clips)} recordings in {CAPTURES.name}/ "
          f"({len(EXPECTED)} with a known intended destination)")

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[dict] = []
    for name in args.models:
        for row in transcribe_all(name, clips, args.reps):
            row["measured_at"] = stamp
            rows.append(row)

    report(rows, args.models)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  wrote {out.relative_to(REPO).as_posix()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
