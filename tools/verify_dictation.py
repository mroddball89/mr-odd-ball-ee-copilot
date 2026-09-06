#!/usr/bin/env python3
"""
Module:  verify_dictation.py
Purpose: Prove a dictated paragraph survives the recording cap. It did not, on 2026-09-02.
Author:  LB
Date:    2026-09-03

    python tools/verify_dictation.py
    python tools/verify_dictation.py --probe     # put the silent truncation back, expect RED

## The recording this exists for

`data/oddball.log`, 2026-09-02 17:10:37, LB dictating his English thesis into a vault note:

    WARNING utterance hit the 15s cap — keeping what we have
    heard 'My thesis is driven by the inherent profit maximization principles of capitalism,
           treating essential medicines as market commodities rather than public goods,
           incentivizes'
    turn: route 0ms -> note | note appended          <- extras: "hit max_s, note appended"

Three failures, in order:

1. The cap fired mid-clause.
2. **The fragment was written and announced as "Added to your note."** `engine/turn.py` had
   recorded `hit max_s` in the turn extras since the cap existed, and passed it to nobody.
3. The remainder, spoken ninety seconds later, was no longer note content. It routed to
   GENERAL, was answered as chit-chat, and was thrown away.

`vault/notes/english research question.md` still ends on the word "incentivizes".

## What is checked

    1. the cap follows the ACTIVITY   15s to ask, 90s to dictate, and put back afterwards
    2. nothing is written half-done   a capped draft is HELD, not committed
    3. the continuation joins         one sentence in the vault, not two blocks and a rule
    4. the exits still work           "never mind", a dismissal, and silence all close it
    5. section 1 does not leak        a stub recorder, and a draft awaiting a NAME, get 15s

Section 4 is the one that keeps this safe. Holding a draft open across a turn is the exact
shape of a bug `engine/core.ask()` documents at its permission gate — *"a held question that
stays held eats the next thing LB says about anything at all"* — and the only thing that makes
it acceptable here is that every way out still works and he is told it is open.

`--probe` restores the 2026-09-02 behaviour: commit the fragment, say "Added to your note.",
close the draft. Sections 2, 3 and 4 go red.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from audio.listen import UtteranceRecorder                           # noqa: E402
from engine.core import Engine, NoteDraft                            # noqa: E402
from engine.turn import Turn                                         # noqa: E402
from tools import knowledge_vault                                    # noqa: E402

PASSED = 0
FAILED = 0

# The two halves of the sentence, exactly as they were spoken and transcribed.
FRONT = ("My thesis is driven by the inherent profit maximization principles of capitalism, "
         "treating essential medicines as market commodities rather than public goods, "
         "incentivizes")
REST = ("things such as predatory pricing, regulatory capture, and a systematic "
        "prioritization of ongoing treatments over permanent cures.")


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


class _StubVAD:
    """Enough of the VAD protocol to build a recorder. Never asked for a score."""

    def predict(self, x, frame_size=512):                            # noqa: ARG002
        return 0.0


class _NoMaxRecorder:
    """A recorder stub with NO `max_s`, like the one in `verify_deafness.py`.

    Section 5 uses it because reading `max_s` unconditionally is how the first version of this
    change killed the microphone thread — an AttributeError out of `_capture`, on a rig where
    the recorder happened to be a test double.
    """

    def reset(self) -> None:
        pass

    def feed(self, frame):                                            # noqa: ARG002
        return None


def make_turn(engine, recorder, dictation_max_s: float = 90.0) -> Turn:
    """A Turn wired to nothing but the recorder and the engine. No audio, no network."""
    return Turn(recorder=recorder, transcriber=None, engine=engine, speaker=None,
                bridge=None, gate=None, frames=lambda: None, greeting=["hi"],
                gate_tail_s=0.0, dictation_max_s=dictation_max_s)


def run(probe: bool = False) -> int:
    print("=" * 78)
    print("  verify_dictation.py — a dictated paragraph, and the cap that cut one in half")
    print("=" * 78)

    workspace = Path(tempfile.mkdtemp(prefix="oddball-dictation-"))
    real_vault = knowledge_vault.VAULT_DIR
    knowledge_vault.VAULT_DIR = workspace
    (workspace / "notes").mkdir(parents=True, exist_ok=True)

    try:
        _sections(workspace, probe)
    finally:
        knowledge_vault.VAULT_DIR = real_vault
        shutil.rmtree(workspace, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if probe:
        if FAILED:
            print(f"\n  The harness BITES: {FAILED} check(s) went red.\n")
            return 0
        print("\n  PROBE DID NOT BITE — this harness is not testing what it claims.\n")
        return 1
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        return 1
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    return 0


def _new_note(workspace: Path) -> Path:
    note = workspace / "notes" / "english research question.md"
    note.write_text("Research paper question for English: to what extent can capitalism "
                    "negatively affect the price of medicine?\n", encoding="utf-8")
    return note


def _sections(workspace: Path, probe: bool) -> None:
    if probe:
        _reintroduce_the_bug()

    # =====================================================================================
    section("1. the cap follows the ACTIVITY, and is put back afterwards")
    # =====================================================================================
    recorder = UtteranceRecorder(_StubVAD(), threshold=0.5, wait_s=1.0, hangover_s=0.5,
                                 max_s=15.0)
    engine = Engine()
    turn = make_turn(engine, recorder)

    check(recorder.max_s == 15.0, "a question is capped at the configured 15s")
    check(not turn._is_dictating(), "with no draft open, he is not dictating")

    engine.note_draft = NoteDraft(op="append", awaiting="content", path=_new_note(workspace))
    check(turn._is_dictating(), "a draft awaiting CONTENT means he is dictating")

    turn._capture()                        # frames() returns None -> returns immediately
    check(recorder.max_s == 15.0,
          "...and the raised cap is PUT BACK after the capture, in a finally",
          f"left at {recorder.max_s}")

    engine.note_draft = NoteDraft(op="new", awaiting="name", content="something")
    check(not turn._is_dictating(),
          "a draft awaiting a NAME is not dictation — that answer is three words long")

    # The stub with no `max_s` at all. This killed the microphone thread on the first attempt.
    engine.note_draft = NoteDraft(op="append", awaiting="content", path=_new_note(workspace))
    try:
        make_turn(engine, _NoMaxRecorder())._capture()
        check(True, "a recorder with no `max_s` does not crash the capture — it just is "
                    "not raised")
    except Exception as exc:                                          # noqa: BLE001
        check(False, "a recorder with no `max_s` does not crash the capture",
              f"{type(exc).__name__}: {exc}")

    # =====================================================================================
    section("2. a capped recording is HELD, not committed half-done")
    # =====================================================================================
    note = _new_note(workspace)
    before = note.read_text(encoding="utf-8")
    engine = Engine()
    engine.note_draft = NoteDraft(op="append", awaiting="content", path=note)

    reply = engine.ask(FRONT, truncated=True)
    check(engine.note_draft is not None, "the draft is still open after a capped recording")
    # `getattr` rather than a bare attribute, so `--probe` — which closes the draft — reports
    # every red in this section instead of dying on the first one. A harness that crashes
    # under its own probe cannot show what the probe proved.
    check(getattr(engine.note_draft, "truncated", False),
          "...and marked truncated, so the next utterance CONTINUES rather than replaces")
    check(note.read_text(encoding="utf-8") == before,
          "nothing was written to the note yet — a half sentence is not a note",
          note.read_text(encoding="utf-8")[-90:])
    check("recording time" in reply.speech.lower() or "ran out" in reply.speech.lower(),
          "he SAYS the recording was cut off", reply.speech)
    check("keep going" in reply.speech.lower() or "carry on" in reply.speech.lower(),
          "...and says what to do about it", reply.speech)
    check(any("Cut off" in c.title for c in reply.cards),
          "...and it is on screen as well as in the air, because speech is heard once")
    check("TRUNCATED" in reply.raw, "...and in the log line")

    # =====================================================================================
    section("3. the continuation lands in the note, as ONE sentence")
    # =====================================================================================
    reply = engine.ask(REST)
    written = note.read_text(encoding="utf-8")
    check(engine.note_draft is None, "the draft closes once the sentence is finished")
    check(FRONT in written, "the first half is in the note")
    check(REST in written, "the second half is in the note — the half that used to be "
                           "answered as chit-chat and discarded")
    check(f"{FRONT} {REST}" in written,
          "...and they are ONE block, not two separated by a horizontal rule",
          repr(written[-120:]))
    check(written.count("---") == 1,
          "exactly one separator — the one that divides this entry from the previous one",
          f"{written.count('---')} rules")
    check("Added" in reply.speech, "and he confirms the save normally", reply.speech)

    # =====================================================================================
    section("4. every way OUT of a held draft still works")
    # =====================================================================================
    # This is what makes holding a draft across a turn acceptable at all. `engine/core.ask()`
    # documents the bug it risks: "a held question that stays held eats the next thing LB says
    # about anything at all."
    for closing, label in (("never mind", "an explicit cancel"),
                           ("that's all", "a dismissal"),
                           ("goodnight", "a goodnight")):
        note = _new_note(workspace)
        engine = Engine()
        engine.note_draft = NoteDraft(op="append", awaiting="content", path=note)
        engine.ask(FRONT, truncated=True)
        check(engine.note_draft is not None, f"[{label}] the draft is open to begin with")
        engine.ask(closing)
        check(engine.note_draft is None, f"{closing!r} closes a held draft — {label}")

    note = _new_note(workspace)
    engine = Engine()
    engine.note_draft = NoteDraft(op="append", awaiting="content", path=note)
    engine.ask(FRONT, truncated=True)
    engine.ask("")
    check(engine.note_draft is None,
          "silence closes it too — `ask()` drops a draft on an empty transcript")

    # =====================================================================================
    section("5. an UNCAPPED turn is unaffected — this must not fire on every note")
    # =====================================================================================
    note = _new_note(workspace)
    engine = Engine()
    engine.note_draft = NoteDraft(op="append", awaiting="content", path=note)
    reply = engine.ask("a short note that finished on its own")     # truncated defaults False
    check(engine.note_draft is None, "an ordinary dictation closes the draft as it always did")
    check("Added" in reply.speech, "...and says 'Added to your note.'", reply.speech)
    check("a short note that finished on its own" in note.read_text(encoding="utf-8"),
          "...and writes it")

    # And the typed path, which has no microphone and therefore no cap.
    note = _new_note(workspace)
    engine = Engine()
    engine.note_draft = NoteDraft(op="append", awaiting="content", path=note)
    engine.ask("typed straight into the panel")
    check(engine.note_draft is None,
          "a typed note is never truncated — `ask` defaults `truncated` to False")


def _reintroduce_the_bug() -> None:
    """Restore the 2026-09-02 behaviour: write the fragment, announce it, close the draft."""
    print("\n  [--probe] restoring the pre-2026-09-03 note path: a capped recording is "
          "committed and announced as a clean save\n")

    import engine.core as core                                        # noqa: PLC0415
    from router import AgentRoute                                     # noqa: PLC0415

    original = core.Engine._resolve_note

    def blind_resolve(self, text, t, truncated=False):                # noqa: ARG001
        # The bug: the truncation flag is accepted and dropped on the floor, exactly as
        # `engine/turn.py` used to drop it after writing "hit max_s" into the turn extras.
        return original(self, text, t, truncated=False)

    core.Engine._resolve_note = blind_resolve

    # The router is pinned as well, and NOT as a convenience. With the draft wrongly closed,
    # the continuation is no longer note content — so it goes to the router, over the network,
    # exactly as it did on 2026-09-02 when "things such as predatory pricing..." was answered
    # as chit-chat and thrown away. Faithful, and it made the probe hang for two minutes on a
    # live API call. Pinned to PERSONA because that is where it actually went.
    class _Decision:
        destination = AgentRoute.PERSONA
        reasoning = "probe"

    core.router_agent = lambda q: _Decision()
    core.Engine._dispatch = lambda self, route, text, t: core.Response(
        speech="Interesting point about pharmaceutical pricing.", route=route.value,
        raw="(the persona answering a sentence that should have been a note)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prove a dictated paragraph survives the cap")
    ap.add_argument("--probe", action="store_true",
                    help="restore the silent truncation, expect RED")
    args = ap.parse_args(argv)
    return run(probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
