#!/usr/bin/env python3
"""
Module:  quiz_generate.py
Purpose: Write questions FROM reference material that contains none. One API call per document.
Author:  LB
Date:    2026-09-09

    python tools/quiz_generate.py data/quiz_pdfs/resistorcharts.pdf
    python tools/quiz_generate.py chart.pdf --subject "Electronics" --want 15
    python tools/quiz_generate.py chart.pdf --dry-run      # generate and print, write nothing

## Why this exists, when `tools/quiz_import.py` says a model is the wrong tool

It is the wrong tool for the job that module does, and this is a different job.

`quiz_import` EXTRACTS: a practice exam already contains questions and answers, so reading them
is regex work — free, offline, deterministic, and checkable line by line against the paper. That
argument is sound and this module does not weaken it. The parser still runs first, always, and
whatever it finds is what gets stored.

This module handles the case the parser cannot: **material with no questions in it at all.**
LB's `resistorcharts.pdf` is a resistor colour-code table — "Gold Black Brown Red ... 1st Band
2nd Band 3rd Band ... 0 1 2 3" — and it is superb quiz material that contains not one question.
No parser can extract what is not written down. Measured 2026-09-09: the parser found 35
candidate blocks in it and dropped all 35, twice, over two separate filings, and told nobody.

## The budget, which is the reason the shape is what it is

D3: the free tier is counted in REQUESTS, twenty per model per day. So the rule here is **one
call per document, at FILING time** — not one per question, and never one at quiz time.

That is the trade LB chose on 2026-09-09, in his words: spend one request up front, write the
questions to disk, and keep the asking and the marking completely local and free forever after.
A deck generated once from a chart he uploads in September is still being asked in December at
no cost, because `tools/quiz_grade.py` never needed a model to mark an answer and still does
not.

The failure mode this avoids is the one the original instruction was written against: a bank
that costs a request every time it is opened, and dies at question twenty-one.

## Everything generated is MARKED, and that is not decoration

`QuizItem.origin` is set to "model" on every item this module produces, and it survives into
the deck JSON. A parsed question can be audited against the page it came from; a generated one
is the model's claim about the material, and models get colour codes subtly wrong in exactly the
way that costs marks. So:

    - `--dry-run` prints them without writing, which is the reviewing workflow
    - the deck file names them, so LB can delete a bad one in a text editor
    - `_report` says how many were generated rather than read

**A generated question LB has not looked at is a question he should not trust.** Saying so is
this module's job; refusing to generate is not.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

# The same shadowed-import guard `quiz_import.py` carries, and for the same measured reason —
# a stray `tools/` package in site-packages poisons `sys.modules` before the repo root is
# consulted. See that file's import block for the full account.
try:
    from tools.quiz_bank import QuizItem, infer_kind
except ModuleNotFoundError:                                           # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for _shadowed in [n for n in sys.modules if n == "tools" or n.startswith("tools.")]:
        del sys.modules[_shadowed]
    from tools.quiz_bank import QuizItem, infer_kind

LOG = logging.getLogger("oddball.quiz")

__all__ = ["generate_questions", "generate_from_pdf", "GenerateReport"]

# How many questions to ask for. Twelve is enough to make a deck worth entering and short enough
# that one response carries them all — a request that returns forty questions is a request whose
# tail gets truncated, and a truncated JSON array is a parse failure that costs the whole call.
DEFAULT_WANT = 12

# How much of the document to send. Roughly 12k characters is a comfortable single request and
# covers a chart, a formula sheet or a lecture handout in full. A textbook chapter gets its first
# pages, which is stated in the report rather than hidden — a deck built from page 1 of 40 is a
# deck about page 1.
MAX_CHARS = 12000

# How long to wait for the questions. Deliberately not `CLOUD_TIMEOUT_S` — see the note at the
# call site. Ninety seconds is generous for a dozen questions and is time nobody is standing
# around for, because this runs on the indexer's thread while LB carries on talking.
GENERATE_TIMEOUT_S = float(os.environ.get("ODDBALL_GENERATE_TIMEOUT_S", "90"))

# The instruction. Written as a contract about the OUTPUT SHAPE rather than as a persona, because
# the only thing that matters here is that the reply parses.
#
# "Do not invent" is load-bearing and is the one line aimed at the failure that actually happens:
# asked for twelve questions off a chart with eight facts on it, a model pads. Four invented
# resistor tolerances in a bank LB revises from is worse than eight real ones.
PROMPT = """You are building a revision deck from a student's own reference material.

Below is the text extracted from {source}. Write up to {want} exam-style questions that test
what this material actually says.

RULES:
- Use ONLY facts present in the text below. Do not invent, extrapolate or add outside knowledge.
- If the material only supports five good questions, write five. Fewer real questions beats
  padding, and padding is the failure mode being guarded against here.
- Each question must be answerable out loud in a sentence or less. This is a SPOKEN quiz.
- No question may refer to "the chart", "the table", "the diagram" or "the document" — the
  student hears the question with the material in front of him, so it must stand alone.
- Prefer specific, checkable answers (a number, a colour, a name, a formula) over essays.
- The answer must be short enough to say and to mark by string comparison.

Return ONLY a JSON object, no prose before or after, no markdown fence:
  {{"subject": "...", "questions": [{{"question": "...", "answer": "...", "explanation": "..."}}]}}
`subject` is what a student would CALL this topic out loud — two or three words, properly spaced
and capitalised, like "Resistor Charts", "Trig Limits" or "Op Amps". Never a filename.
`explanation` is optional and should be one short sentence, or "".

TEXT FROM {source}:
{body}
"""


class GenerateReport:
    """What one generation produced, in a form that can be said out loud.

    Args:
        source:    the file's name.
        items:     the questions written.
        truncated: True when the document was longer than `MAX_CHARS` and only its first pages
                   were read. Reported, because a deck off page 1 of 40 is not a deck off the
                   document and LB is the only one who can decide whether that matters.
        note:      why nothing came back, when nothing did.
    """

    def __init__(self, source: str = "", items: list | None = None,
                 truncated: bool = False, note: str = "") -> None:
        self.source = source
        self.items = items or []
        self.truncated = truncated
        self.note = note

    def __len__(self) -> int:
        return len(self.items)

    def sentence(self) -> str:
        """One or two sentences for LB. Names the questions as GENERATED every time."""
        if not self.items:
            return (f"I could not write any questions from {self.source}. "
                    f"{self.note or 'The model returned nothing I could read as questions.'}")

        subjects = sorted({i.subject for i in self.items})
        head = (f"There were no questions in {self.source}, so I wrote {len(self.items)} of my "
                f"own from what it says and put them in {', '.join(subjects)}.")
        if self.truncated:
            head += (" It was longer than I can read in one go, so those come from its first "
                     "few pages only.")
        return head + " They are mine rather than the paper's, so check them before you trust them."


def _strip_fence(reply: str) -> str:
    """Take the JSON out of whatever the model wrapped it in.

    Asked for bare JSON, a model returns bare JSON most of the time and a fenced block the rest,
    and the difference is not worth a retry costing a second request against a twenty-a-day
    budget. Cheaper to accept both.

    Both shapes are salvaged — an object, and a bare array from a model that ignored the wrapper
    — because the array is still twelve good questions and throwing them away over their
    packaging would spend the request for nothing.
    """
    text = (reply or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    # A model that adds "Here are the questions:" first still gave us the JSON after it.
    #
    # **Whichever bracket comes FIRST wins, and checking the object shape first is wrong.** A
    # bare `[{"question": ...}]` contains a `{`, so searching for the object bounds ahead of the
    # array's returns the first ELEMENT and silently throws away every question after it —
    # measured here, twelve questions reduced to one that then parsed cleanly and looked fine.
    candidates = [(text.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if text.find(o) != -1]
    for start, _opener, closer in sorted(candidates):
        end = text.rfind(closer)
        if end > start:
            return text[start:end + 1]
    return text


def _subject_from(payload, fallback: str) -> str:
    """The deck name the model proposed, or `fallback` when it did not give a usable one.

    **The fallback is `quiz_import.guess_subject`, and it is why this field exists at all.**
    That function reads the filename, so `resistorcharts.pdf` became a deck called
    `Resistorcharts` — nothing anybody says out loud, and a name every whole-phrase pass in
    `quiz_bank.resolve_subject` then failed to match. The model is already reading the document;
    asking it what the topic is called costs nothing on top of a request being spent anyway.

    Bounded and stripped of anything path-like, because this becomes a FILENAME. A model that
    answers "../../etc" gets ignored rather than obeyed.
    """
    if not isinstance(payload, dict):
        return fallback
    proposed = str(payload.get("subject", "")).strip()
    if not proposed or len(proposed) > 40:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", proposed).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def _items_from(payload, source: str, subject: str) -> list:
    """Turn the decoded JSON into QuizItems, skipping anything unusable.

    Tolerant in the same way `QuizItem.from_dict` is, and for a stricter reason: a model that
    returns eleven good questions and one malformed one should cost the malformed one, not the
    request. There is no second request.
    """
    out = []
    if isinstance(payload, dict):
        payload = payload.get("questions")
    if not isinstance(payload, list):
        return out
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question", "")).strip()
        answer = str(raw.get("answer", "")).strip()
        if not question or not answer:
            continue
        out.append(QuizItem(
            question=question, answer=answer,
            explanation=str(raw.get("explanation", "")).strip()[:600],
            subject=subject, kind=infer_kind(answer, {}), source=source,
            origin="model"))
    return out


def generate_questions(text: str, source: str, subject: str = "",
                       want: int = DEFAULT_WANT) -> GenerateReport:
    """Write questions from reference material. **Exactly one API call.**

    Args:
        text:    the document's extracted text.
        source:  its filename, recorded on every item and named in the prompt.
        subject: the deck to file them under. Empty asks `quiz_import.guess_subject`.
        want:    how many to aim for. The model is told fewer is better than padded.

    Returns:
        A `GenerateReport`. Never raises — a failure here must cost the questions and not the
        filing that called it, exactly as `_import_quizzes` treats the parser next door.
    """
    body = (text or "").strip()
    if not body:
        return GenerateReport(source=source, note="There was no readable text in it at all.")

    truncated = len(body) > MAX_CHARS
    if truncated:
        body = body[:MAX_CHARS]

    named = bool(subject)
    if not subject:
        from tools.quiz_import import guess_subject                   # noqa: PLC0415
        subject = guess_subject(Path(source), text)

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI     # noqa: PLC0415

        from engine.models import (AGENT_MODEL, CLOUD_TIMEOUT_S,      # noqa: PLC0415
                                   LLM_MAX_RETRIES)
        from engine.llm_text import extract_text_content              # noqa: PLC0415

        # temperature=0.0 deliberately. This is an extraction-shaped task wearing a generation
        # hat — the questions should be the material's, not the model's imagination, and every
        # degree of temperature here is a degree of invented resistor tolerance.
        #
        # **And it does NOT inherit `CLOUD_TIMEOUT_S`.** That twenty seconds is the budget for a
        # SPOKEN TURN, where the cost of waiting is LB standing in silence in front of a machine
        # that looks broken. Nothing here is on the turn path — `file_manager._INDEXER` runs this
        # on a background thread precisely so it can take its time — and the reply being waited
        # for is twelve questions rather than a sentence.
        #
        # Measured 2026-09-09: at twenty seconds this returned 504 DEADLINE_EXCEEDED against
        # LB's own resistor chart, having spent the request. A timeout that abandons a call the
        # server is still answering wastes the quota it was set to protect, which makes the
        # short limit worse for the budget rather than better.
        timeout = max(CLOUD_TIMEOUT_S, GENERATE_TIMEOUT_S)
        llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.0,
                                     max_retries=LLM_MAX_RETRIES, timeout=timeout)
        reply = extract_text_content(
            llm.invoke(PROMPT.format(source=source, want=want, body=body)).content)
    except Exception as exc:                                          # noqa: BLE001
        # No key, no quota, no network, a timeout. All of them mean the same thing to LB — the
        # questions did not get written — and none of them should take the filing down with it.
        LOG.exception("could not generate questions from %s", source)
        return GenerateReport(source=source,
                              note=f"The model could not be reached: {type(exc).__name__}.")

    try:
        payload = json.loads(_strip_fence(reply))
    except (ValueError, TypeError):
        LOG.warning("generated questions for %s did not parse as JSON: %.200s", source, reply)
        return GenerateReport(source=source,
                              note="What came back was not in a shape I could read as questions.")

    # The model's own name for the topic wins over the one derived from the filename — but only
    # when the caller did not NAME a subject. An explicit `--subject "Calculus II"` is LB making
    # a deliberate choice about which deck this joins, and nothing read out of a PDF outranks it.
    chosen = subject if named else _subject_from(payload, subject)
    items = _items_from(payload, source, chosen)
    LOG.info("generated %d question(s) from %s into %s", len(items), source, chosen)
    return GenerateReport(source=source, items=items, truncated=truncated)


def generate_from_pdf(path: Path, subject: str = "", want: int = DEFAULT_WANT,
                      write: bool = True) -> GenerateReport:
    """Read a PDF and write questions from it. Adds them to the bank unless `write` is False."""
    from tools.quiz_import import read_pdf_text                       # noqa: PLC0415

    text, _pages, _ocr = read_pdf_text(Path(path))
    report = generate_questions(text, Path(path).name, subject, want)

    if write and report.items:
        from tools.quiz_bank import add_items                         # noqa: PLC0415
        add_items(report.items)
    return report


def main(argv: "list[str] | None" = None) -> int:
    """Generate questions from one document, and say what came out."""
    import argparse

    ap = argparse.ArgumentParser(
        description="write quiz questions from reference material that contains none")
    ap.add_argument("path", type=Path, help="the PDF, .txt or .md to read")
    ap.add_argument("--subject", default="", help="the deck to file under")
    ap.add_argument("--want", type=int, default=DEFAULT_WANT, help="how many to aim for")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the questions without writing them to the bank")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.path.exists():
        print(f"  no such file: {args.path}")
        return 1

    if args.path.suffix.lower() == ".pdf":
        report = generate_from_pdf(args.path, args.subject, args.want, write=not args.dry_run)
    else:
        text = args.path.read_text(encoding="utf-8", errors="replace")
        report = generate_questions(text, args.path.name, args.subject, args.want)
        if not args.dry_run and report.items:
            from tools.quiz_bank import add_items                     # noqa: PLC0415
            add_items(report.items)

    print()
    print(f"  {report.sentence()}")
    print()
    for item in report.items:
        print(f"  Q: {item.question}")
        print(f"  A: {item.answer}")
        if item.explanation:
            print(f"     ({item.explanation})")
        print()
    if args.dry_run:
        print("  (dry run — nothing was written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
