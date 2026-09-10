#!/usr/bin/env python3
"""
Module:  verify_file_intent.py
Purpose: Prove "file those as quizzes" MOVES a file, rather than producing a sentence about it.
Author:  LB
Date:    2026-09-09

    python tools/verify_file_intent.py
    python tools/verify_file_intent.py --probe    # take the intent away and watch section 1 die

## The regression, which was not a crash

2026-09-08 20:37. LB typed "can you file the resistor chart and trig limits sheets to quizes".
The log recorded `no intent matched`, routed to the general agent, and the reply was:

    Filed resistorcharts.pdf and trig limits 2.pdf as quizzes. They're being indexed now and
    not searchable yet.

Both files were still in `data/inbox/` the following afternoon. There is no filing line in
`data/oddball.log` between the request and the reply — no move, no parse, no indexer job. The
sentence was a paraphrase of `file_manager._file_quiz`'s real return string, which was sitting
in that agent's PREVIOUS CONTEXT from a genuine filing two days earlier.

Nothing went red. Nothing could have gone red: a false confirmation is a well-formed answer, and
the only evidence against it was a directory listing nobody checked for eighteen hours.

## So this harness checks the DISK, never the sentence

Section 2 is the one that matters. It runs a real filing through `Engine._file_turn` against a
temporary inbox and asserts the file is **gone from the inbox and present in the destination**.
An implementation that returns a beautiful paragraph and moves nothing fails it.

`--probe` unbinds the planner, putting the old behaviour back, and shows section 1 going red. A
claim that a harness bites is worth what the last check of it was worth.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.harness_lib import bootstrap, check, counts as _tally, section  # noqa: E402

bootstrap()

from orchestrator import file_intent                                 # noqa: E402


class _Q:
    """The shape `orchestrator.instant.Router` hands a planner: raw text and a normalised one."""

    def __init__(self, text: str) -> None:
        self.raw = text
        self.text = text.lower()


def _match(text: str):
    return file_intent.look_up(_Q(text))


def _sections(workspace: Path, probe: bool) -> None:
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name in ("resistorcharts.pdf", "trig limits 2.pdf"):
        (inbox / name).write_bytes(b"%PDF-1.4\n")

    import tools.file_manager as fm

    real_inbox_files = fm.inbox_files
    fm.inbox_files = lambda: sorted(inbox.glob("*.pdf"))
    if probe:
        file_intent.look_up = lambda q: None                          # the old behaviour

    try:
        # =================================================================================
        section("1. the utterance that failed — it must resolve, and to BOTH files")
        # =================================================================================
        said = "can you file the resistor chart and trig limits sheets to quizes"
        request = _match(said)
        check(request is not None, f"{said!r} is recognised at all", str(request))
        if request is not None:
            check(set(request.filenames) == {"resistorcharts.pdf", "trig limits 2.pdf"},
                  "...and names BOTH documents, spoken loosely and with 'quizes' misspelt",
                  str(request.filenames))
            check(request.category == "quiz", "...as quizzes", request.category)

        declared = _match("both of the pdfs in the inbox are quizzes")
        check(declared is not None and len(declared.filenames) == 2,
              "a STATEMENT of category files them too — he answered the question he was asked "
              "twice and nothing happened", str(declared))

        one = _match("file the resistor chart as a quiz")
        check(one is not None and one.filenames == ("resistorcharts.pdf",),
              "one named document resolves to one file", str(one))

        # =================================================================================
        section("2. it MOVES the file — the check the false confirmation would have failed")
        # =================================================================================
        quiz_dir = workspace / "quiz_pdfs"
        quiz_dir.mkdir(parents=True, exist_ok=True)
        real_quiz_dir, real_data = fm.QUIZ_PDF_DIR, fm.DATA_DIR
        real_request = fm._INDEXER.request
        fm.QUIZ_PDF_DIR, fm.DATA_DIR = quiz_dir, workspace
        fm._INDEXER.request = lambda *a, **k: None       # no OCR, no model, no background work
        try:
            from engine.core import Engine, Turnlog                   # noqa: PLC0415

            moving = _match("file the resistor chart as a quiz")
            if moving is None:
                check(False, "nothing to file — section 1 already said why")
            else:
                response = Engine._file_turn(Engine.__new__(Engine), moving, Turnlog())
                check(not (inbox / "resistorcharts.pdf").exists(),
                      "the file is GONE from the inbox — the property the reply cannot fake")
                check((quiz_dir / "resistorcharts.pdf").exists(),
                      "...and is in data/quiz_pdfs/ where the parser will find it")
                check("resistorcharts.pdf" in response.speech,
                      "...and he says so, naming it", response.speech)
                check(response.route == "file",
                      "...on the free file route, having spent no API call", response.route)
        finally:
            fm.QUIZ_PDF_DIR, fm.DATA_DIR = real_quiz_dir, real_data
            fm._INDEXER.request = real_request

        # =================================================================================
        section("3. what must NOT match — a move is not a thing to guess at")
        # =================================================================================
        for text, why in (
            ("how do I file a quiz in canvas",
             "a question ABOUT filing is not an instruction to file"),
            ("what should I do with the resistor chart",
             "...nor is asking what to do with it"),
            ("is the resistor chart a quiz",
             "...nor is asking whether it is one"),
            ("quiz me on resistor band colors",
             "asking to be TESTED must never file anything"),
            ("save my note about op amps",
             "a note has a filing verb and no category — it belongs to note_intent"),
            ("file the amp board schematic",
             "a document that is not in the inbox cannot be filed, however clear the sentence"),
        ):
            check(_match(text) is None, why, repr(text))

        # The guard that removes nearly every false positive at once, and the reason the list
        # above can stay short: with nothing waiting, no sentence about filing can match.
        fm.inbox_files = lambda: []
        check(_match("file the resistor chart as a quiz") is None,
              "with an EMPTY inbox nothing matches at all — the single widest guard here")
        fm.inbox_files = lambda: sorted(inbox.glob("*.pdf"))
    finally:
        fm.inbox_files = real_inbox_files


def run(probe: bool = False) -> int:
    print("=" * 78)
    print("  verify_file_intent.py — filing is something that HAPPENS, not something said")
    print("=" * 78)

    workspace = Path(tempfile.mkdtemp(prefix="oddball-file-intent-"))
    real_look_up = file_intent.look_up
    try:
        _sections(workspace, probe)
    finally:
        file_intent.look_up = real_look_up
        shutil.rmtree(workspace, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"  {_tally.passed + _tally.failed} checks, {_tally.passed} passed, "
          f"{_tally.failed} failed")
    print("=" * 78)
    if probe:
        if _tally.failed:
            print(f"\n  The harness BITES: {_tally.failed} check(s) went red.\n")
            return 0
        print("\n  PROBE DID NOT BITE — section 1 is not testing what it claims.\n")
        return 1
    if _tally.failed:
        print(f"\n  {_tally.failed} RED\n")
        return 1
    print(f"\n  {_tally.passed}/{_tally.passed} checks passed — all green\n")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="prove a filing request moves a file rather than describing one")
    ap.add_argument("--probe", action="store_true",
                    help="unbind the planner — the state the 2026-09-08 false confirmation "
                         "happened in")
    args = ap.parse_args(argv)
    return run(probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
