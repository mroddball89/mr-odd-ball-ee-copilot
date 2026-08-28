#!/usr/bin/env python3
"""
Module:  verify_notes.py
Purpose: Prove the notebook writes, adds to, reads back and deletes — and that a QUESTION is
         not mistaken for any of those.
Author:  LB
Date:    2026-08-28

    python tools/verify_notes.py
    python tools/verify_notes.py --probe

No audio, no model, no key. Every part of the notebook is a pure function of a string or of the
filesystem, which is the whole reason it was built that way: the feature LB reaches for when he
wants something written down is the one that must not depend on a network or a quota.

## Section 2 is the one that bites

`orchestrator/note_intent.py` earns its keep on the refusals. Every verb it matches on also
opens an ordinary sentence, and a matcher without anchors does not merely add a bad feature —
**it steals three working ones**:

    "read my screen"          SCREEN, which takes a screenshot and describes it
    "delete the temp files"   OS, which asks before running anything
    "open notepad"            the free launch path, which costs nothing today

Plus the two neighbours that write to the same vault: `agents/persona_agent.py` already routes
*"remember that I'm using the 2N3904"* to `save_to_vault`, and `tools/corrections.py` catches
*"always use absolute paths instead"* as a standing rule. A greedy note matcher eats both.

`--probe` removes both anchors — the opening verb and the required word "note" — and prints how
many of section 2 get taken.

## Sections 3 to 6 write to a TEMPORARY vault

`vault/` is LB's real notebook and this harness must not touch it. `ODDBALL_VAULT_DIR` is set
**before anything under `tools/` is imported**, which is the one ordering that works — the
vault path is resolved at import time. The check that the rebinding actually took is itself a
check, because a harness that quietly wrote to the real vault would pass while corrupting the
thing it tests. That is L22, and `tools/knowledge_vault.py` only started honouring the variable
when this file was written.
"""

from __future__ import annotations

import argparse
import os
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

# BEFORE any `tools.` import. `knowledge_vault.VAULT_DIR` is read at import time, so setting
# this afterwards would rebind nothing and every write below would land in LB's real vault.
_TMP = Path(tempfile.mkdtemp(prefix="oddball-notes-"))
os.environ["ODDBALL_VAULT_DIR"] = str(_TMP)

from orchestrator import note_intent                                 # noqa: E402
from orchestrator.instant import Query, normalise                    # noqa: E402
from orchestrator.note_intent import (APPEND, DELETE, LIST, NEW,     # noqa: E402
                                      READ, look_up)
from tools import knowledge_vault as kv                              # noqa: E402

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


def ask(text: str):
    """Run the matcher the way `engine/core.py` does."""
    return look_up(Query(raw=text, text=normalise(text)))


# =========================================================================================
section("1. every phrasing LB uses is recognised, with the right parts pulled out of it")
# =========================================================================================

# (utterance, op, target, folder, name, content) — "" means "must be empty".
POSITIVES: list[tuple[str, str, str, str, str, str]] = [
    # --- new -----------------------------------------------------------------------------
    ("take a note that the reg is an LM317 not a 7805",
     NEW, "", "", "", "the reg is an LM317 not a 7805"),
    ("take a note", NEW, "", "", "", ""),
    ("make a note", NEW, "", "", "", ""),
    ("start a new note", NEW, "", "", "", ""),
    ("hey can you jot this down for me: the tab on the regulator is live",
     NEW, "", "", "", "the tab on the regulator is live"),
    ("write this down in my ECE350 folder: the midterm is week 9",
     NEW, "", "ECE350", "", "the midterm is week 9"),
    ("make a note in a new folder called amp board that the input cap is 10 microfarads",
     NEW, "", "amp board", "", "the input cap is 10 microfarads"),
    ("note that the /home/lb/kicad path is where the boards live",
     NEW, "", "", "", "the /home/lb/kicad path is where the boards live"),
    ("jot down that I need two more 10k trimmers, and call it parts to order",
     NEW, "", "", "parts to order", "I need two more 10k trimmers"),
    ("log this in my lab notes folder: the scope probe is 10x",
     NEW, "", "lab notes", "", "the scope probe is 10x"),
    # --- new, starting from the folder ---------------------------------------------------
    ("make a new folder called amp board and save this note there",
     NEW, "", "amp board", "", ""),
    ("make a new folder called amp board and put a note in it that the reg is an LM317",
     NEW, "", "amp board", "", "the reg is an LM317"),
    ("create a folder called ECE350 for my notes", NEW, "", "ECE350", "", ""),
    ("new folder called scratch notes", NEW, "", "scratch notes", "", ""),
    # --- append --------------------------------------------------------------------------
    ("add to my regulator note that it needs a heatsink",
     APPEND, "regulator", "", "", "it needs a heatsink"),
    ("add to my parts to order note that I need a 10k trimmer",
     APPEND, "parts to order", "", "", "I need a 10k trimmer"),
    ("add more to my amp board note", APPEND, "amp board", "", "", ""),
    # --- read ----------------------------------------------------------------------------
    ("read me my regulator note", READ, "regulator", "", "", ""),
    ("read back my parts to order note", READ, "parts to order", "", "", ""),
    ("what does my amp board note say", READ, "amp board", "", "", ""),
    ("whats in my ECE350 notes", READ, "ece350", "", "", ""),
    # --- list ----------------------------------------------------------------------------
    ("what notes do I have", LIST, "", "", "", ""),
    ("list my notes", LIST, "", "", "", ""),
    ("show me my notes", LIST, "", "", "", ""),
    ("read me my notes", LIST, "", "", "", ""),
    ("what notes do I have in amp board", LIST, "", "amp board", "", ""),
    # --- delete --------------------------------------------------------------------------
    ("delete my scratch note", DELETE, "scratch", "", "", ""),
    ("get rid of my scratch note", DELETE, "scratch", "", "", ""),
    ("throw away my amp board note", DELETE, "amp board", "", "", ""),
]

for utterance, op, target, folder, name, content in POSITIVES:
    found = ask(utterance)
    if found is None:
        check(False, f"{utterance!r}", "not recognised at all")
        continue
    got = (found.op, found.target, found.folder, found.name, found.content)
    check(got == (op, target, folder, name, content), f"{utterance!r} -> {op}",
          "" if got == (op, target, folder, name, content)
          else f"wanted {(op, target, folder, name, content)}\n           got    {got}")

# The verbatim guarantee, stated as its own check rather than left implied by the row above.
# This is the reason extraction runs on the raw text and matching on the normalised one.
_VERBATIM = "the reg is an LM317, not a 7805 — see /home/lb/kicad/amp"
_v = ask(f"take a note that {_VERBATIM}")
check(_v is not None and _v.content == _VERBATIM,
      "the note is a verbatim slice of the raw text — punctuation, slashes and part numbers",
      "" if _v and _v.content == _VERBATIM else f"got {_v.content if _v else None!r}")

# =========================================================================================
section("2. and NOTHING else is — the section that bites")
# =========================================================================================

# Every one of these is either an ordinary question or a working feature of this repo. None of
# them may be read as a request about the notebook.
NOT_NOTES: list[tuple[str, str]] = [
    ("how do I take notes in Python", "a question that opens with 'how'"),
    ("whats the best way to take notes in a lab", "a question, not an order"),
    ("what did I note about the TL072", "a question about a past note, not a new one"),
    ("why did you note that down", "a question about something he did"),
    ("should I write this down myself", "a question, not an instruction"),
    ("read my screen", "SCREEN — no note word, so it cannot be a read"),
    ("read the screen and tell me whats wrong", "SCREEN"),
    ("read that dialog to me", "SCREEN"),
    ("what am I looking at", "SCREEN"),
    ("delete the temp files", "OS — and it is gated there, not here"),
    ("delete everything in downloads", "OS"),
    ("remove the old build directory", "OS"),
    ("open notepad", "the free launch path"),
    ("start firefox", "the free launch path — 'start' is a LAUNCH_VERB"),
    ("add this to the bill of materials", "HARDWARE — no note word"),
    ("add a 10k resistor to the schematic", "HARDWARE"),
    ("remember that I'm using the 2N3904", "persona's save_to_vault — deliberately absent"),
    ("do you remember what I said about the TL072", "a question about recall"),
    ("always use absolute paths instead", "tools/corrections.py — a standing rule"),
    ("that was wrong", "tools/corrections.py — a rebuke"),
    ("whats the trace width for 5 amps", "HARDWARE"),
    ("what time is it", "the free utility tier"),
    ("whats due tomorrow", "ACADEMIC"),
    ("cpu temp", "OS"),
    ("quiz me on filters", "QUIZ"),
    ("make a new folder called builds", "OS — a folder on disk, with no note in it"),
    ("create a folder called project files on my desktop", "OS"),
    ("make a new folder", "names nothing, and mentions no note"),
    ("note", "a bare word is not a command"),
    ("notes", "a bare word is not a command"),
    ("delete my note", "names no note — never guess which"),
    ("delete my notes", "names no note — never guess which"),
]

for utterance, why in NOT_NOTES:
    found = ask(utterance)
    check(found is None, f"{utterance!r} is left alone",
          "" if found is None else f"was read as {found.op} ({why})")

# =========================================================================================
section("3. the temp vault is real, and it is NOT LB's")
# =========================================================================================

_real = Path(__file__).resolve().parents[1] / "vault"
check(kv.VAULT_DIR.resolve() == _TMP.resolve(),
      f"the vault is rebound to a temp directory ({_TMP.name})",
      f"VAULT_DIR is {kv.VAULT_DIR}")
check(kv.VAULT_DIR.resolve() != _real.resolve(),
      "and it is not the real vault — nothing here can touch LB's notes")
check(kv.TRASH_DIR.resolve().is_relative_to(kv.VAULT_DIR.resolve()),
      "the trash lives inside the vault it belongs to")

# =========================================================================================
section("4. the state machine: bare command -> content -> name -> a file on disk")
# =========================================================================================

os.environ.setdefault("GOOGLE_API_KEY", "not-a-real-key-for-the-harness")
os.environ["ODDBALL_SELF_CONTEXT"] = "0"          # keep the ledgers out of this

from engine.core import Engine                                       # noqa: E402


def turn(engine: Engine, text: str):
    """One turn, with the deadline and backup reminders ignored — they are another file's job."""
    return engine.ask(text)


eng = Engine(confirm_gates=True)

r1 = turn(eng, "take a note")
check(eng.note_draft is not None and eng.note_draft.awaiting == "content",
      "a bare 'take a note' holds the turn open and asks what to write",
      f"said: {r1.speech!r}")

r2 = turn(eng, "the TL072 has output on pin 1")
check(eng.note_draft is not None and eng.note_draft.awaiting == "name",
      "the next utterance becomes the content, and he asks what to call it",
      f"said: {r2.speech!r}")

r3 = turn(eng, "op amp pinouts")
check(eng.note_draft is None, "the draft is closed once it is written")
written = kv.find_notes("op amp pinouts")
check(len(written) == 1, "the note is on disk", f"found {[p.name for p in written]}")
if written:
    body = written[0].read_text(encoding="utf-8").strip()
    check(body == "the TL072 has output on pin 1",
          "and it holds exactly what LB said, with no name or command text in it",
          f"got {body!r}")
    check(written[0].parent.name == "notes",
          "filed in the default folder when none was named",
          f"in {written[0].parent.name!r}")

# A folder LB invents is created on demand — the half of the request that needed no new code.
turn(eng, "take a note in a new folder called amp board that the reg is an LM317")
turn(eng, "regulator choice")
made = kv.find_notes("regulator choice")
check(len(made) == 1 and made[0].parent.name == "amp board",
      "a folder named in the same breath is created and used",
      f"landed in {made[0].parent.name!r}" if made else "no note written")

# A name given inline skips the question entirely.
eng2 = Engine(confirm_gates=True)
turn(eng2, "jot down that I need two more 10k trimmers, and call it parts to order")
check(eng2.note_draft is None, "a name volunteered inline means he does not ask for one")
check(len(kv.find_notes("parts to order")) == 1, "and the note is written in one turn")

# =========================================================================================
section("5. the escapes — a held question can never eat an unrelated utterance")
# =========================================================================================

esc = Engine(confirm_gates=True)
for phrase in ("never mind", "forget it", "cancel", "stop", "goodnight"):
    turn(esc, "take a note")
    held = esc.note_draft is not None
    r = turn(esc, phrase)
    check(held and esc.note_draft is None, f"{phrase!r} cancels the draft",
          f"said: {r.speech!r}")
check(not kv.find_notes("never mind") and not kv.find_notes("forget it"),
      "and none of them was written down as a note")

# Silence, checked on the SPEECH as well as the state. Asserting only that the draft is gone
# passes when there was no draft to begin with, which is exactly how the cancel bug above hid
# for two checks after the one it actually broke.
turn(esc, "take a note")
r = turn(esc, "")
check(esc.note_draft is None and "nothing written down" in r.speech.lower(),
      "silence cancels the draft, and he says so", f"said: {r.speech!r}")

turn(esc, "take a note")
before = len(kv.notes())
turn(esc, "forget it")
check(len(kv.notes()) == before, "a cancelled draft writes no file at all")

# The draft must not survive its own turn under ANY branch — that is the bug `ask()` documents
# the permission gate having had.
turn(esc, "take a note")
turn(esc, "the scope probe is 10x")
turn(esc, "scope settings")
check(esc.note_draft is None, "the draft is cleared unconditionally once the name arrives")

# =========================================================================================
section("6. add to, read back, list")
# =========================================================================================

app = Engine(confirm_gates=True)
turn(app, "add to my scope settings note that the bandwidth limit is on")
note = kv.find_notes("scope settings")[0]
text = note.read_text(encoding="utf-8")
check("the scope probe is 10x" in text and "the bandwidth limit is on" in text,
      "an append keeps the original entry and adds the new one")
check("---" in text, "separated by a rule, so a later read can tell where one stops")

view = kv.read_note(note)
check(len(view.entries) == 2, f"the note reads back as 2 entries", f"got {len(view.entries)}")
check("---" not in view.spoken,
      "the rule is NOT read out loud — he would say 'dash dash dash'",
      f"spoken: {view.spoken!r}")
check(len(view.spoken.split()) <= kv.MAX_WORDS,
      f"and the spoken form stays inside the {kv.MAX_WORDS}-word ceiling")

# A long note is clipped, and says so rather than trailing off.
kv.write_note("long one", " ".join(f"word{i}" for i in range(200)), "notes")
long_view = kv.read_note(kv.find_notes("long one")[0])
check(len(long_view.spoken.split()) <= kv.MAX_WORDS,
      "a 200-word note is not read out in full")
check("screen" in long_view.spoken.lower(),
      "and he says where the rest of it is rather than stopping mid-sentence",
      f"spoken: {long_view.spoken!r}")

r = turn(app, "what notes do I have")
check(r.cards and str(len(kv.notes())) in r.speech,
      "the listing names how many there are and puts the names on a card",
      f"said: {r.speech!r}")

r = turn(app, "read me my scope settings note")
check("10x" in r.speech and r.cards, "reading a note back says it and shows it",
      f"said: {r.speech!r}")

# An ambiguous name is a question, never a guess. Two notes whose STEMS both contain the name,
# because that is what `find_notes` matches on — a note that merely lives in a folder called
# "amp board" is not a second candidate for the name "amp board", and assuming it was made this
# check pass for the wrong reason the first time it was written.
kv.write_note("amp board bring up", "first power on", "notes")
kv.write_note("amp board layout", "ground pour on the bottom", "notes")
amb = Engine(confirm_gates=True)
r = turn(amb, "read me my amp board note")
check(len(kv.find_notes("amp board")) > 1, "'amp board' now matches more than one note",
      f"matches {[p.name for p in kv.find_notes('amp board')]}")
check("which" in r.speech.lower(), "so he asks which one instead of picking",
      f"said: {r.speech!r}")

r = turn(amb, "read me my nonexistent note")
check("don't have" in r.speech.lower() or "dont have" in r.speech.lower(),
      "and a name that matches nothing is said plainly, not invented",
      f"said: {r.speech!r}")

# =========================================================================================
section("7. delete asks first, and a delete is a move")
# =========================================================================================

dele = Engine(confirm_gates=True)
target = kv.find_notes("op amp pinouts")[0]
r = turn(dele, "delete my op amp pinouts note")
check(dele.pending is not None, "a delete opens the permission gate rather than acting",
      f"said: {r.speech!r}")
check(dele.pending.kind == "note" and dele.pending.tool == "trash_note",
      "through the same Pending an OS command uses")
check(str(target.resolve()) == dele.pending.tool_args["path"],
      "carrying the RESOLVED path, so what is approved is what is deleted")
check(any(target.name in c.body for c in r.cards),
      "and the path is on a card BEFORE the question is asked")
check(target.exists(), "nothing has been deleted yet")

r = turn(dele, "no")
check(dele.pending is None and target.exists(),
      "a declined gate leaves the note exactly where it was", f"said: {r.speech!r}")

turn(dele, "delete my op amp pinouts note")
r = turn(dele, "yes")
check(not target.exists(), "an approved delete removes it from the vault",
      f"said: {r.speech!r}")
trashed = list(kv.TRASH_DIR.glob("*.md")) if kv.TRASH_DIR.exists() else []
check(any("op amp pinouts" in p.name for p in trashed),
      "but the file itself is still there, in the trash",
      f"trash holds {[p.name for p in trashed]}")
check(not kv.find_notes("op amp pinouts"),
      "and a trashed note stops resolving — it cannot be read, appended to or deleted again")
check("op amp pinouts" not in kv.read_from_vault.invoke({"search_term": "output on pin 1"}),
      "nor be found by a vault search and fed into a prompt as current")
check(target.name not in [p.name for p in kv.notes()],
      "notes() skips the trash, which is what makes all of the above true at once")

# A delete that names nothing never reaches the gate.
safe = Engine(confirm_gates=True)
turn(safe, "delete my note")
check(safe.pending is None,
      "'delete my note' names no note, so no gate is opened and nothing is proposed")

# =========================================================================================
section("8. the vault path safety still holds, with the notebook on top of it")
# =========================================================================================

kv.write_note("../../etc/pwned", "nope", "../../etc")
escaped = Path(__file__).resolve().parents[2] / "etc" / "pwned.md"
check(not escaped.exists(), "a traversal in the folder and the filename writes nothing outside")
check(all(kv.VAULT_DIR.resolve() in p.resolve().parents for p in kv.notes()),
      "every note the walk returns is inside the vault")

trash_twice = kv.trash_note(kv.find_notes("long one")[0])
check(trash_twice.startswith("Moved"), "a note can be trashed")
outside = kv.trash_note(Path(__file__).resolve())
check(not outside.startswith("Moved") and Path(__file__).exists(),
      "and a path outside the vault is refused, not moved",
      f"got {outside!r}")

# =========================================================================================
section("9. none of this costs an API call")
# =========================================================================================

import ast                                               # noqa: E402
import inspect                                           # noqa: E402

# Asserted against the module's IMPORT STATEMENTS, parsed, rather than by grepping the source
# text — and that distinction cost two false failures to learn. `note_intent.py`'s own docstring
# contains the sentences "Nothing here imports `agents/`" and "a planner returns a request", so
# a substring scan for "agents" and "requests" finds the module's promise not to do a thing and
# reports it as the thing. A textual check on a file this heavily commented reads the prose.
#
# The claim is also structural rather than behavioural: this module CANNOT reach a model, not
# merely that it did not happen to this time.
_tree = ast.parse(inspect.getsource(note_intent))
_imported: set[str] = set()
for _node in ast.walk(_tree):
    if isinstance(_node, ast.Import):
        _imported.update(alias.name.split(".")[0] for alias in _node.names)
    elif isinstance(_node, ast.ImportFrom) and _node.module:
        _imported.add(_node.module.split(".")[0])

for forbidden in ("langchain", "langchain_google_genai", "google", "genai",
                  "requests", "urllib", "httpx", "agents", "engine", "tools", "router"):
    check(forbidden not in _imported,
          f"note_intent.py does not import {forbidden}",
          "" if forbidden not in _imported else f"imports are {sorted(_imported)}")

check(_imported <= {"__future__", "logging", "re", "dataclasses", "orchestrator", "sys"},
      "and everything it DOES import is the standard library or orchestrator/",
      f"imports are {sorted(_imported)}")

# =========================================================================================
# Put the temp vault back, and prove it went.
# =========================================================================================
shutil.rmtree(_TMP, ignore_errors=True)
check(not _TMP.exists(), "the temp vault is cleaned up afterwards")
check((Path(__file__).resolve().parents[1] / "vault").exists(),
      "and LB's real vault is untouched and still there")


def probe() -> int:
    """Remove both anchors and count how many working features get eaten.

    The loosened matcher is what a first draft looks like: match a note verb anywhere in the
    line, and do not require the word "note". That is the version that answers "read my screen"
    with "I don't have a note called screen", and reads "delete the temp files" as a note it
    should offer to throw away.
    """
    print("\n  PROBE: verbs matched anywhere, and the note word not required\n")

    import re

    every_verb = (note_intent._NEW + note_intent._APPEND + note_intent._READ
                  + note_intent._DELETE + note_intent._LIST)

    def loose(text: str) -> "str | None":
        flat = normalise(text)
        for phrase in sorted(every_verb, key=len, reverse=True):
            if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", flat):
                return phrase
        return None

    caught = 0
    for text, why in NOT_NOTES:
        hit = loose(text)
        if hit:
            caught += 1
            print(f"   WOULD TAKE   {text!r:52} (on {hit!r} — really {why})")

    print(f"\n  {caught}/{len(NOT_NOTES)} would be read as notebook commands instead of "
          f"reaching the feature that owns them.")
    if caught:
        print("  The harness BITES: section 2 goes red without the anchors.\n")
        return 0
    print("  The harness is VACUOUS: loosening the rule changed nothing.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the vault notebook")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(0)
