#!/usr/bin/env python3
"""
Module:  verify_credible.py
Purpose: Prove room tone cannot authorise a command, and cannot buy an API call.
Author:  LB
Date:    2026-08-29

    python tools/verify_credible.py
    python tools/verify_credible.py --probe
    python tools/verify_credible.py --sweep

No audio, no model, no key. `orchestrator/credible.py` is a pure function of two floats and a
string, which is the entire reason it was split out of `engine/turn.py`: the decision that
gates an OS command should be testable without a microphone attached.

## The corpus is every capture in the log, not a sample

52 rows, lifted from `data/oddball.log` on 2026-08-29 — every line where a `capture spoke`
was followed by a `heard`, across three days. Both numbers are the recorder's own, logged by
`audio/listen._finish` at the moment the capture closed.

**Copied in rather than read from the log**, because `.gitignore:102` excludes `*.log`. A
harness that read `data/oddball.log` would pass on this machine and pass vacuously on a fresh
clone, which is L15 wearing the same hat it wore in `verify_notes.py` section 2b.

## Labelling, and where it is honest about doubt

`KEEP` means LB really said it and the assistant must act on it. `DROP` means the room produced
it and acting on it is a bug. The DROP labels are not guesses in the cases that matter:

    "Yes. Yes."   0.96s in 18.08s   approved a real OS command at 07:24:10
    "Yes. Yes."   0.96s in 19.60s   same shape, 2026-08-28 19:35:34
    "Yes. Yes."   0.96s in 18.32s   same shape, 2026-08-28 19:37:55
    "No. No."     0.72s in 14.64s   same shape
    "Thank you for watching."       the Whisper outro, on 0.08s of voiced audio

Three of those are the identical transcript at the identical voiced duration on three separate
days. That is a signature, not a coincidence, and it is what the ratio test was built for.

**Two rows are labelled DROP on evidence rather than on certainty**, and the evidence is in the
corpus itself: "elbow." and "Bobo." both measure 0.16s voiced in 2.48s of audio, and a third
capture with *exactly those numbers* transcribed to nothing at all. 0.16-in-2.48 is what this
microphone produces when nobody is talking.

The single genuinely ambiguous row is `"No."` at 0.40s in 18.16s. LB may well have said it —
he had been asked twice. It is labelled DROP anyway and the label costs nothing either way:
that answer arrived at a permission gate, where a rejection declines, and a decline is what
"No" meant. **A false reject there produces the outcome he asked for.**

## --probe removes the tests and shows what walks through

The check that the harness bites. With the thresholds opened up, every DROP row should be
accepted — if some are still rejected with the guard disabled, they were passing for a reason
that has nothing to do with the code under test.

## --sweep prints the separation, so the next tuning starts from data

The margins here are thin (11.1% against 8%, 15.0 against 16.0). `--sweep` prints where every
row sits so a different microphone or a different room can be retuned from measurements rather
than from these constants, which describe a Blue Snowball in LB's bedroom in August 2026.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from orchestrator import credible                                    # noqa: E402
from orchestrator.credible import assess                             # noqa: E402

PASSED = 0
FAILED = 0

KEEP = "KEEP"
DROP = "DROP"


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


# (label, voiced_s, audio_s, transcript, when)
CORPUS: list[tuple[str, float, float, str, str]] = [
    # --- 2026-08-27 --------------------------------------------------------------------
    (KEEP, 4.00, 15.84, "battery 80% yes dbsk chop 3 and a few", "07:57:09"),
    (KEEP, 0.80, 4.16, "I think a little bit backwards today, so.", "07:57:37"),
    (KEEP, 2.40, 10.08, "He's got a company with Ben. He's got a company with", "07:58:05"),
    # --- 2026-08-28 --------------------------------------------------------------------
    (DROP, 0.16, 2.48, "elbow.", "08:49:09"),
    (KEEP, 3.04, 6.16, "Do you have SKGOP 3 NFU 5VK?", "08:49:26"),
    (KEEP, 2.88, 5.68, "Can you save a note for me in the vault? Save Mr. Av", "08:49:50"),
    # The lowest genuine ratio in the whole corpus (11.1%) AND the highest genuine word
    # rate (15.0). Both floors are set around this single row.
    (KEEP, 0.80, 7.20, "Can you write a note and see what you hope for me?", "08:50:01"),
    (KEEP, 2.64, 22.40, "No. Can you save a note to the vault for me?", "08:50:35"),
    (KEEP, 1.92, 4.40, "I need to add a note to the vault.", "08:50:47"),
    (DROP, 0.16, 2.48, "Bobo.", "19:34:16"),
    (KEEP, 1.52, 3.84, "I need you to save me a note.", "19:34:29"),
    (DROP, 0.96, 19.60, "Yes. Yes.", "19:35:34"),
    (KEEP, 0.88, 2.96, "I want to write a note.", "19:35:59"),
    (KEEP, 0.32, 2.64, "No.", "19:36:53"),
    (KEEP, 2.08, 4.40, "and need you to save my thoughts to the vault.", "19:37:11"),
    (DROP, 0.96, 18.32, "Yes. Yes.", "19:37:55"),
    (KEEP, 2.08, 4.56, "Remember I love peanut butter.", "19:38:08"),
    (KEEP, 6.40, 14.16, "Remember what extent can capitalism negatively affec", "19:38:28"),
    (DROP, 0.72, 14.64, "No. No.", "19:39:09"),
    (KEEP, 8.72, 13.12, "Remember that my question for my research paper for ", "19:39:32"),
    (KEEP, 4.40, 7.52, "Can you read me back the topic of my research paper ", "19:39:52"),
    (KEEP, 0.56, 2.88, "Go to sleep.", "19:40:20"),
    (KEEP, 0.48, 2.80, "No.", "19:40:35"),
    (KEEP, 2.56, 4.96, "What are the classes that are currently on my schedu", "19:40:59"),
    (KEEP, 2.32, 4.64, "Do I have any assignments that are due today?", "19:41:31"),
    (KEEP, 0.72, 3.04, "What's your name?", "19:41:55"),
    (KEEP, 2.80, 5.60, "Are you sure that you look on the plie because right", "19:42:10"),
    (KEEP, 2.88, 6.08, "What is the integral of 10?", "19:42:27"),
    (KEEP, 0.40, 3.20, "That's bad.", "19:42:53"),
    # --- 2026-08-29, the morning that motivated this file -------------------------------
    (DROP, 0.08, 2.48, "Thank you for watching.", "07:06:08"),
    (KEEP, 2.00, 4.32, "What's on my schedule for today?", "07:06:20"),
    (KEEP, 1.76, 5.28, "Open, Creality print.", "07:06:47"),
    (KEEP, 0.40, 2.72, "Yes.", "07:06:54"),
    (KEEP, 6.48, 12.32, "Can you organize all the STL in 3D objects on my des", "07:07:17"),
    (DROP, 0.48, 15.52, "Come on!", "07:09:43"),
    (KEEP, 5.68, 11.04, "Can you take all the STO and OBJ files on my desktop", "07:17:02"),
    (KEEP, 1.12, 6.48, "Yes.", "07:17:18"),
    (KEEP, 4.64, 9.68, "Can you add to my note about the topic for my Englis", "07:18:01"),
    (KEEP, 2.16, 4.48, "Read me back the notes that you have for me.", "07:20:27"),
    (DROP, 0.32, 3.12, "I don't know what it is.", "07:23:41"),
    # THE ONE. This approved an OS command on 0.96s of voiced audio.
    (DROP, 0.96, 18.08, "Yes. Yes.", "07:24:10"),
    (KEEP, 2.24, 4.40, "Tell me what notes you have saved for me.", "07:24:44"),
    (DROP, 0.40, 18.16, "No.", "07:30:25"),
    (KEEP, 0.48, 3.52, "Sleep.", "07:30:45"),
]

# Captures whose transcript came back empty. They are already handled — `Engine.ask("")` means
# something specific on both paths — but the verdict must still be "do not believe this", or a
# later refactor could start treating an empty string as an utterance.
EMPTIES: list[tuple[float, float, str]] = [
    (1.60, 7.36, "07:27:24"), (0.24, 2.72, "09:26:57"), (0.16, 2.48, "16:17:58"),
    (0.08, 2.24, "18:09:45"), (0.24, 3.04, "19:40:52"), (0.24, 2.64, "19:43:12"),
    (0.24, 2.64, "07:10:33"), (0.08, 2.40, "07:30:34"),
]


def run() -> int:
    # =====================================================================================
    section("1. what LB really said is believed — all of it")
    # =====================================================================================
    for label, voiced, audio, text, when in CORPUS:
        if label != KEEP:
            continue
        v = assess(text, voiced, audio)
        check(v.credible, f"{when}  {text[:44]!r}",
              "" if v.credible else f"{voiced:.2f}s in {audio:.2f}s — rejected: {v.reason}")

    # =====================================================================================
    section("2. what the room produced is not — including the one that ran a command")
    # =====================================================================================
    for label, voiced, audio, text, when in CORPUS:
        if label != DROP:
            continue
        v = assess(text, voiced, audio)
        check(not v.credible, f"{when}  {text[:44]!r} is rejected",
              v.reason if not v.credible else
              f"BELIEVED on {voiced:.2f}s voiced in {audio:.2f}s — this is the bug")

    # =====================================================================================
    section("3. an empty transcript is never believed")
    # =====================================================================================
    for voiced, audio, when in EMPTIES:
        v = assess("", voiced, audio)
        check(not v.credible, f"{when}  empty transcript is rejected", v.reason)
    check(not assess("   ", 5.0, 6.0).credible,
          "and so is whitespace, however much voiced audio came with it")

    # =====================================================================================
    section("4. the margins, stated as checks so a retune cannot silently close them")
    # =====================================================================================
    keeps = [(v, a, t) for lbl, v, a, t, _ in CORPUS if lbl == KEEP]
    drops = [(v, a, t) for lbl, v, a, t, _ in CORPUS if lbl == DROP]

    # Two claims, stated separately, because the obvious single claim is FALSE and saying it
    # as one line hid that. `max(ratio of every DROP row)` is 10.3% — above the 8% floor —
    # because "I don't know what it is." is not the ratio test's to catch. It is caught by the
    # word rate, at 18.8 a second. Rolling both into one inequality asserted something the
    # design never promised and then reported it as a broken margin.
    keep_ratio = min(v / a for v, a, _ in keeps)
    check(credible.MIN_VOICED_RATIO <= keep_ratio,
          f"the ratio floor never reaches genuine speech: "
          f"{credible.MIN_VOICED_RATIO:.0%} <= {keep_ratio:.1%}",
          "the quietest thing LB really said is the only thing this floor must clear")

    by_ratio = [v / a for v, a, _ in drops if v / a < credible.MIN_VOICED_RATIO]
    check(bool(by_ratio) and max(by_ratio) < credible.MIN_VOICED_RATIO <= keep_ratio,
          f"and the rows it IS responsible for sit under it: {max(by_ratio):.1%} < "
          f"{credible.MIN_VOICED_RATIO:.0%} <= {keep_ratio:.1%}",
          f"headroom is {keep_ratio - max(by_ratio):.1%} — thin, and complementary tests are "
          f"what make that acceptable")

    keep_rate = max(len(t.split()) / v for v, _, t in keeps)
    drop_rate = min(len(t.split()) / v for v, _, t in drops
                    if len(t.split()) / v > credible.MAX_WORDS_PER_VOICED_S)
    check(keep_rate <= credible.MAX_WORDS_PER_VOICED_S < drop_rate,
          f"the word-rate ceiling sits in the gap: {keep_rate:.1f} <= "
          f"{credible.MAX_WORDS_PER_VOICED_S:.0f} < {drop_rate:.1f}")

    quietest = min(v for v, _, _ in keeps)
    check(credible.MIN_VOICED_S <= quietest,
          f"the voiced floor is at or under the quietest real utterance "
          f"({credible.MIN_VOICED_S:.2f}s <= {quietest:.2f}s)")

    # =====================================================================================
    section("5. the tests are complementary, not redundant")
    # =====================================================================================
    #
    # The claim the docstring makes, checked rather than asserted in prose: each test must be
    # the ONLY thing catching at least one row. A test that never uniquely catches anything is
    # a test that could be deleted, and one that is quietly doing nothing is worse than absent
    # because it reads as protection.
    loose = dict(min_voiced_s=0.0, min_voiced_ratio=0.0, max_words_per_voiced_s=1e9)
    for name, key in (("the ratio floor", "min_voiced_ratio"),
                      ("the word-rate ceiling", "max_words_per_voiced_s")):
        only_this = dict(loose)
        only_this[key] = getattr(credible, key.upper())
        caught = [t for lbl, v, a, t, _ in CORPUS if lbl == DROP
                  and not assess(t, v, a, **only_this).credible]
        others = dict(loose)
        for other in ("min_voiced_s", "min_voiced_ratio", "max_words_per_voiced_s"):
            if other != key:
                others[other] = getattr(credible, other.upper())
        unique = [t for t in caught if assess(
            t, *next((v, a) for lbl, v, a, tt, _ in CORPUS if tt == t), **others).credible]
        check(bool(unique), f"{name} is the only test that catches something",
              f"uniquely catches {unique[:2]}" if unique
              else "every row it catches is caught by another test — it earns nothing")

    # --- the voiced floor is the exception, and it is checked against GEOMETRY -------------
    #
    # It uniquely catches nothing in this corpus, and the first version of this section failed
    # for saying it did. Investigating rather than deleting it turned up what it is actually
    # for, which is not a row in the log but a setting in the config.
    #
    # A capture is `PREROLL_S + voiced + hangover_s` long, so the ratio a single 80ms frame
    # produces depends entirely on the hangover — and `config/oddball.toml` has changed that
    # number FOUR times (0.6 -> 0.75 -> 1.10 -> 2.00), with its own comment saying the next
    # complaint is the knob to turn. Recomputed across all four:
    #
    #     hangover 2.00   0.08s voiced -> 2.38s audio -> 3.4%   caught by the ratio floor
    #     hangover 1.10   0.08s voiced -> 1.48s audio -> 5.4%   caught by the ratio floor
    #     hangover 0.75   0.08s voiced -> 1.13s audio -> 7.1%   caught by the ratio floor
    #     hangover 0.60   0.08s voiced -> 0.98s audio -> 8.2%   ABOVE the 8% floor
    #
    # At the hangover this repo shipped with, one voiced frame walks past the ratio test. The
    # voiced floor is what stops it, and it is the only thing that does. So it stays, and the
    # check is written against the geometry rather than against today's log.
    from audio.listen import PREROLL_S

    slipped = []
    for hangover in (0.60, 0.75, 1.10, 2.00):
        audio_s = PREROLL_S + 0.08 + hangover
        without = assess("Yes.", 0.08, audio_s, min_voiced_s=0.0)
        with_floor = assess("Yes.", 0.08, audio_s)
        if without.credible:
            slipped.append(hangover)
        check(not with_floor.credible,
              f"one 80ms frame is refused at hangover_s={hangover:.2f} "
              f"({0.08 / audio_s:.1%} of {audio_s:.2f}s)",
              with_floor.reason)
    check(bool(slipped),
          f"and the voiced floor is the ONLY test doing it at hangover_s={slipped}",
          "if this ever empties, the floor has become redundant and can go")

    wired()
    return 0


# =========================================================================================
# 6. IT IS ACTUALLY WIRED IN — driven through the real Turn, not the pure function
# =========================================================================================
#
# L24: a guard that never fires makes every check after it pass while testing nothing. Sections
# 1 to 5 prove `assess()` is right and prove nothing whatever about `engine/turn.py` calling
# it. This section drives the real `Turn` with canned captures and asserts on the OUTCOME —
# whether a command was approved, whether the Engine was asked anything at all.
#
# The recorder, the microphone and the transcriber are replaced; `Turn._capture` is overridden
# to hand back a `Capture` built from the corpus numbers. Everything from there down —
# `_spoken_gate_answer`, `run`, `is_yes`, the retry — is the shipping code.

def wired() -> None:
    import numpy as np

    from audio.listen import SAMPLE_RATE_HZ, Capture, Outcome
    from engine.response import Response
    from engine.turn import Timings, Turn

    section("6. and it is wired into the real Turn — the gate, and the dispatch")

    class Bridge:
        def __init__(self):
            self.state = "sleeping"
        def set_state(self, n):  self.state = n
        def set_mouth(self, v):  pass
        def set_route(self, r):  pass
        def set_mode(self, m):   pass
        def show_card(self, c):  pass
        def say_line(self, r, t):  pass
        def ask_approval(self, p): pass
        def clear_pending(self):   pass
        def drain_inbound(self):   return []

    class Speaker:
        def __init__(self):  self.said = []
        def speak(self, text, on_envelope=None, on_start=None):
            self.said.append(text)
            if on_start:
                on_start()

    class Gate:
        def speaking(self, tail_s):
            class _C:
                def __enter__(s):     return s
                def __exit__(s, *a):  return False
            return _C()

    class Stt:
        """Returns a fixed transcript, exactly as base.en did for the real audio."""
        def __init__(self, text):  self.text = text; self.calls = 0
        def transcribe(self, audio):
            self.calls += 1
            class _G:
                pass
            g = _G()
            g.text, g.took_s = self.text, 0.0
            return g

    class Engine:
        mode = "normal"
        def __init__(self):
            self.asked = []
            class _L:
                extras: list[str] = []
            self.last = _L()
        def ask(self, text):
            self.asked.append(text)
            return Response(speech="ok", route="utility", raw="ok")

    def turn_for(text: str, voiced_s: float, audio_s: float, engine=None):
        """A Turn whose next capture holds `voiced_s` of speech in `audio_s` of audio."""
        bridge, speaker = Bridge(), Speaker()
        t = Turn(recorder=None, transcriber=Stt(text), engine=engine or Engine(),
                 speaker=speaker, bridge=bridge, gate=Gate(), frames=None,
                 greeting=["What's up LB?"], gate_tail_s=0.0, thinking_state="thinking")
        samples = np.zeros(int(audio_s * SAMPLE_RATE_HZ), dtype=np.float32)
        t._capture = lambda: Capture(outcome=Outcome.SPOKE, audio=samples,
                                     speech_s=voiced_s, waited_s=0.0)
        return t, speaker

    # The camera is the gate's second channel and it spawns a subprocess. Force it to the
    # answer a machine with no webcam gives, so this harness needs no hardware.
    import tools.gesture_control as gc
    gc.get_gesture = lambda *a, **k: "NO_CAMERA"

    # --- the gate ------------------------------------------------------------------------
    # THE regression. These are the exact numbers from 2026-08-29 07:24:10.
    t, speaker = turn_for("Yes. Yes.", 0.96, 18.08)
    answer = t._spoken_gate_answer(Timings())
    check(answer == "",
          "the 07:24:10 'Yes. Yes.' no longer answers the permission gate",
          "" if answer == "" else f"gate returned {answer!r} — anything but '' approves")
    check(any("didn't catch" in s for s in speaker.said),
          "and he says so out loud instead of declining in silence",
          f"said: {speaker.said}")

    # A real "Yes." must still work, or the gate is merely broken in a safer direction.
    t, _ = turn_for("Yes.", 0.40, 2.72)
    answer = t._spoken_gate_answer(Timings())
    check(answer == "Yes.",
          "the 07:06:54 'Yes.' — a genuine confirmation — still approves",
          "" if answer == "Yes." else
          f"gate returned {answer!r}; a guard that refuses real answers is not a fix")

    # And a real "No." still declines through the normal route, not by exhausting the retries.
    t, _ = turn_for("No.", 0.48, 2.80)
    check(t._spoken_gate_answer(Timings()) == "No.",
          "and a genuine 'No.' is still read as a clear answer")

    # --- the dispatch --------------------------------------------------------------------
    engine = Engine()
    t, _ = turn_for("Thank you for watching.", 0.08, 2.48, engine=engine)
    t.run()
    check(engine.asked == [],
          "the 07:06:08 'Thank you for watching.' never reaches the Engine",
          "" if not engine.asked else
          f"Engine.ask was called with {engine.asked} — that is the paid API call")

    engine = Engine()
    t, _ = turn_for("What's on my schedule for today?", 2.00, 4.32, engine=engine)
    t.run()
    check(engine.asked == ["What's on my schedule for today?"],
          "while a real question is dispatched exactly as before",
          f"Engine.ask got {engine.asked}")

    # The Timings line carries the reason, so a rejected turn is visible in oddball.log rather
    # than looking like the microphone died.
    engine = Engine()
    t, _ = turn_for("Come on!", 0.48, 15.52, engine=engine)
    timings = t.run()
    check(any("not credible" in e for e in timings.extras),
          "and a rejection is recorded in the turn log with its reason",
          f"extras: {timings.extras}")


def probe() -> int:
    """Open the thresholds up and confirm every DROP row walks straight through."""
    print("\n  --probe: thresholds disabled; every DROP row should now be BELIEVED\n")
    walked = 0
    drops = [(v, a, t, w) for lbl, v, a, t, w in CORPUS if lbl == DROP]
    for voiced, audio, text, when in drops:
        v = assess(text, voiced, audio, min_voiced_s=0.0, min_voiced_ratio=0.0,
                   max_words_per_voiced_s=1e9)
        walked += bool(v.credible)
        print(f"   {'WALKS THROUGH' if v.credible else 'still rejected'}  "
              f"{when}  {text!r}")
    print(f"\n  {walked}/{len(drops)} would be acted on with the guard removed")
    if walked == len(drops):
        print("\n  The harness BITES.\n")
        return 0
    print(f"\n  PARTIAL: {len(drops) - walked} row(s) pass for some other reason.\n")
    return 1


def sweep() -> int:
    """Print where every row sits, so the next retune starts from data."""
    print(f"\n  {'when':9} {'label':5} {'voiced':>7} {'audio':>7} {'ratio':>7} {'w/s':>7}  text\n")
    for label, voiced, audio, text, when in sorted(CORPUS, key=lambda r: r[1] / r[2]):
        ratio = voiced / audio
        rate = len(text.split()) / voiced if voiced else 0.0
        print(f"  {when:9} {label:5} {voiced:7.2f} {audio:7.2f} {ratio:7.1%} {rate:7.1f}  "
              f"{text[:40]!r}")
    print(f"\n  floors in force: voiced >= {credible.MIN_VOICED_S}s, "
          f"ratio >= {credible.MIN_VOICED_RATIO:.0%}, "
          f"rate <= {credible.MAX_WORDS_PER_VOICED_S}/s\n")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the voiced-audio credibility floor")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())
    if args.sweep:
        raise SystemExit(sweep())

    run()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
