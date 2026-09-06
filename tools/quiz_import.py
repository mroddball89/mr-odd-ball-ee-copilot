#!/usr/bin/env python3
"""
Module:  quiz_import.py
Purpose: Turn a practice exam or a Q&A sheet into questions LB can be asked. No model involved.
Author:  LB
Date:    2026-09-02

    python tools/quiz_import.py data/inbox/calc2_practice_final.pdf
    python tools/quiz_import.py paper.pdf --subject "Calculus II"
    python tools/quiz_import.py paper.pdf --dry-run        # parse and print, write nothing
    python tools/quiz_import.py --scan data/quiz_pdfs      # every PDF in a folder

## Why a parser and not a model

The obvious way to turn a PDF of questions into structured questions is to hand it to Gemini
and ask for JSON. LB's instruction rules that out, and the instruction is right for a reason
beyond preference: a 40-question practice exam is more than one request's worth of context, the
free tier is counted in REQUESTS at 20 a day (D3), and the result would be a bank whose
contents he cannot audit against the paper it came from.

A parser is also simply the correct tool. **A practice exam is already structured** — that is
what makes it a practice exam. It is numbered, its options are lettered, and its answer key is
a list of letters. Reading that structure is regex work, and regex work is free, offline,
deterministic and reviewable.

## The four layouts, which is what the paper actually looks like

    1. numbered + separate key   "1. What is ...   A) ... B) ..."  ... "Answer Key: 1. B 2. D"
    2. numbered + inline answer  "1. What is ...   A) ...  Answer: B"
    3. Q/A pairs                 "Q: What is ...   A: ..."
    4. term sheets               "Question: ...   Answer: ..."

Layout 1 is by far the most common in a professor's review packet, and it is the one that needs
the two-pass structure: the questions are on pages 1-6 and the thing that answers them is on
page 7. So the whole document is read first, the key is found and lifted out, and only then are
the questions matched to it.

## What is refused, and why refusing matters more than parsing

A PDF of lecture slides has numbered bullets. So does a syllabus. Both parse into "questions"
if the parser is eager, and a bank contaminated with slide bullets is worse than an empty one —
LB would be asked "3. Course Objectives" and marked wrong for not knowing what that means.

So an item is only kept when it has BOTH a question that ends like a question or carries
options, AND an answer from a key, an inline marker or an A: line. **A question with no answer
is dropped, not stored with a blank.** `_report` says how many were dropped and why, because a
paper that yields 3 of 40 is a paper whose layout this parser does not handle, and LB needs to
be told that rather than left with three questions and an impression that it worked.

## Scanned papers

Half of what a professor hands out is a photocopy. `pypdf` returns "" for those, so the pages
that come back empty are sent to `tools/pdf_ocr.ocr_pdf`, which already exists for the
image-only datasheets and caches per file. OCR text is messier — that is what `_tidy` is for —
and an OCR'd paper typically yields fewer questions than a digital one. Also stated in the
report rather than hidden.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

# Guarded, and this is the bug `tools/file_manager.py` documents at its own import block —
# reproduced here by making the same mistake and then running the CLI. `python
# tools/quiz_import.py` puts `tools/` on `sys.path` and NOT the repo root, so `tools.quiz_bank`
# is unimportable; the `sys.path.insert` at the bottom of this file is far too late, because
# module-level imports have already run by the time `__main__` is reached.
#
# Guarded rather than an unconditional insert, so importing this module from an agent has no
# side effect on the interpreter's search path.
try:
    from tools.quiz_bank import QuizItem, infer_kind
except ModuleNotFoundError:                                           # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.quiz_bank import QuizItem, infer_kind

LOG = logging.getLogger("oddball.quiz")

__all__ = ["import_pdf", "import_text", "parse_questions", "guess_subject", "ImportReport"]

# A question shorter than this is a fragment — a heading, a page number, a stray line. Twelve
# characters is "What is pi?" with room to spare.
MIN_QUESTION_CHARS = 12

# And one longer than this is a passage, not a question. A parser that swallows a whole page
# into one "question" produces an item nobody can answer and nothing can mark.
MAX_QUESTION_CHARS = 700

# Options a multiple-choice question may have. E is common; F is almost always a false match on
# a line that happens to start with "F".
_OPTION_LETTERS = "ABCDE"


class ImportReport:
    """What one import found, in a form that can be said out loud.

    Args:
        source:  the file's name.
        items:   the questions that survived.
        dropped: how many candidate questions had no answer and were discarded.
        pages:   how many pages were read.
        ocr:     how many of those needed OCR.
        layout:  which parser produced the items, for the log.
    """

    def __init__(self, source: str = "", items: list | None = None, dropped: int = 0,
                 pages: int = 0, ocr: int = 0, layout: str = "", note: str = "") -> None:
        self.source = source
        self.items = items or []
        self.dropped = dropped
        self.pages = pages
        self.ocr = ocr
        self.layout = layout
        self.note = note

    def __len__(self) -> int:
        return len(self.items)

    def sentence(self) -> str:
        """One or two sentences for LB. Honest about a poor yield rather than quiet about it."""
        if not self.items:
            reason = self.note or (
                "I could not find a question-and-answer layout in it. I read numbered "
                "questions with an answer key, questions with 'Answer:' on them, and 'Q:'/'A:' "
                "pairs — if it is a different shape I need it in one of those.")
            return f"I read {self.source} and got no questions out of it. {reason}"

        subjects = sorted({i.subject for i in self.items})
        head = (f"Read {len(self.items)} question(s) out of {self.source} "
                f"into {', '.join(subjects)}.")
        if self.ocr:
            head += f" {self.ocr} of its {self.pages} pages had to be read with OCR."
        if self.dropped:
            head += (f" {self.dropped} more looked like questions but had no answer anywhere "
                     f"in the file, so I left them out rather than store a blank.")
        return head


# ---------------------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------------------

# Page furniture. Stripped before parsing, because a footer sitting between a question and its
# options splits one item into two — and "Page 4 of 12" parses as a numbered question.
_FURNITURE = re.compile(
    r"^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+|-\s*\d+\s*-|\f)\s*$", re.I)


def _tidy(text: str) -> str:
    """Clean one page of extracted text. Cheap fixes only, each for an observed defect.

    OCR and PDF extraction both produce specific, boring damage: hyphens at line ends where a
    word was broken across lines, runs of dots from a table of contents, non-breaking spaces,
    and the page furniture above. None of this is interesting and all of it breaks the regexes
    below, so it is removed before they run rather than worked around inside each of them.
    """
    text = (text or "").replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[•·▪]", " ", text)
    text = re.sub(r"\.{4,}", " ", text)                      # dot leaders
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)             # word broken across a line
    lines = [ln.rstrip() for ln in text.split("\n") if not _FURNITURE.match(ln)]
    return "\n".join(lines)


def read_pdf_text(path: Path) -> tuple[str, int, int]:
    """Every page of `path` as one string. Returns (text, pages read, pages that needed OCR).

    Pages are separated by a form feed so `_page_of` can map an offset back to a page number
    for the item's `page` field — "this one came from page 7 of the review packet" is worth
    the one character it costs.

    Never raises. An unreadable PDF returns ("", 0, 0) and the caller says so.
    """
    try:
        from pypdf import PdfReader                                  # noqa: PLC0415
    except Exception as exc:                                         # noqa: BLE001
        LOG.warning("pypdf is not available (%s)", exc)
        return "", 0, 0

    try:
        reader = PdfReader(str(path))
        pages = [(_tidy(p.extract_text() or "")) for p in reader.pages]
    except Exception as exc:                                         # noqa: BLE001
        LOG.warning("could not read %s: %s: %s", path.name, type(exc).__name__, exc)
        return "", 0, 0

    # The photocopied paper. `ocr_pdf` is asked ONLY for the pages that came back empty, which
    # is the same bargain `vector_db.fill_blanks_with_ocr` strikes: a mostly-digital paper with
    # one scanned page costs one page of OCR rather than all of it.
    blanks = [i for i, text in enumerate(pages) if len(text.strip()) < 40]
    rescued = 0
    if blanks:
        try:
            from tools.pdf_ocr import ocr_pdf                        # noqa: PLC0415

            for index, text in ocr_pdf(path, pages=blanks).items():
                if 0 <= index < len(pages) and text.strip():
                    pages[index] = _tidy(text)
                    rescued += 1
        except Exception:                                            # noqa: BLE001
            LOG.warning("OCR pass failed on %s", path.name, exc_info=True)

    # Joined with the form feed on a LINE OF ITS OWN. A bare "\f" between pages puts the next
    # page's first line after a character that `^` in MULTILINE mode does not follow — so a
    # question sitting at the top of a page was invisible to `_QUESTION_START` and silently
    # dropped. Measured on a two-page fixture: question 3, the only one on page 2, was lost.
    return "\n\f\n".join(pages), len(pages), rescued


def _page_of(text: str, offset: int) -> int:
    """1-based page number for a character offset in the joined text."""
    return text.count("\f", 0, max(0, offset)) + 1


# ---------------------------------------------------------------------------------------
# The answer key
# ---------------------------------------------------------------------------------------

# Where the key starts. "Answer Key", "Answers", "Solutions", "Key", "Answer Sheet" — on its own
# line, which is what stops "answers vary" inside a question from being read as the heading.
_KEY_HEADING = re.compile(
    r"^\s*(?:answer\s*key|answers?|solutions?|key|answer\s*sheet|marking\s*scheme)\s*:?\s*$",
    re.I | re.M)

# One entry in a key: "1. B", "1) B", "12 - D", "1: B". The letter must be alone — "1. Because
# the limit" is a worked solution, not a key entry, and matching it would set every answer to B.
_KEY_ENTRY = re.compile(r"(?:^|\s)(\d{1,3})\s*[.):\-]\s*([A-Ea-e])(?![A-Za-z0-9])")

# A key that spells the answer out rather than lettering it: "1. The derivative is 2x".
_KEY_TEXT_ENTRY = re.compile(r"^\s*(\d{1,3})\s*[.):\-]\s+(\S.{2,300})$", re.M)

# How many entries must sit under a heading before it is believed to be an answer key.
#
# **Two, not three.** Three was the first guess and it silently discarded the key of every short
# paper: a two-question history review parsed to ZERO questions, because its "1. B / 2. C" was
# one entry short of being believed and both questions were then dropped for having no answer.
# The false positive this guards against is a worked example under a heading, and one entry is
# where that lives — "Answers" followed by a single "1. B" is far more likely to be prose. Two
# numbered single-letter lines under an explicit heading is a key.
MIN_KEY_ENTRIES = 2


def _find_answer_key(text: str) -> tuple[dict, str]:
    """Pull the answer key out. Returns ({number: answer}, the text with the key removed).

    Removed, and that is the point of returning it: left in place, the key's own "1. B" lines
    are parsed by `_parse_numbered` as question 1 with the body "B", overwriting the real
    question 1. The key is lifted out of the document before the questions are read.

    The LAST heading wins. A review packet often says "answers are at the end" in its preamble;
    the real key is the one nearest the bottom of the file.
    """
    headings = list(_KEY_HEADING.finditer(text))
    if not headings:
        return {}, text

    for heading in reversed(headings):
        tail = text[heading.end():]
        lettered = dict(_KEY_ENTRY.findall(tail))
        if len(lettered) >= MIN_KEY_ENTRIES:
            return ({int(n): letter.upper() for n, letter in lettered.items()},
                    text[:heading.start()])

        spelled = {int(n): body.strip() for n, body in _KEY_TEXT_ENTRY.findall(tail)}
        if len(spelled) >= MIN_KEY_ENTRIES:
            return spelled, text[:heading.start()]

    # A heading with fewer entries under it than that is a heading that meant something else.
    return {}, text


# ---------------------------------------------------------------------------------------
# Numbered questions
# ---------------------------------------------------------------------------------------

# The start of a numbered question. Anchored to the line start, because "worth 1. point" is not
# question one. Allows "1.", "1)", "Q1.", "Question 4:".
_QUESTION_START = re.compile(
    r"^[ \t]*(?:(?:q(?:uestion)?\s*)?(\d{1,3})[.):]|\((\d{1,3})\))[ \t]*(?=\S)", re.I | re.M)

# An option, at a line start or run together on one line: "A) ...", "(B) ...", "C. ...".
_OPTION = re.compile(r"(?:^|\s)\(?([A-Ea-e])[).:]\s+(?=\S)", re.M)

# An inline answer marker. "Answer: B", "Ans. 2x", "Correct answer: C", "ANS - D".
#
# **Anchored to the line start, and the delimiter is required.** The first version allowed the
# marker anywhere with an optional delimiter, and the harness caught what that does: the
# question "A question with no answer anywhere?" matched on its own word "answer" and stored
# "anywhere?" as the answer. Every question whose TEXT contains the word would have been given
# a nonsense answer — and worse, would have counted as successfully parsed.
_INLINE_ANSWER = re.compile(
    r"^[ \t]*(?:correct\s+)?ans(?:wer)?\s*[.:\-]\s*(?:is\s+)?(\S.*?)[ \t]*$", re.I | re.M)

# A worked solution attached to the question. Everything after it, to the end of the block, is
# the explanation — which is what lets "explain that" be answered with no API call at all.
#
# Same line anchor, and for the same reason. "because", "why" and "reason" were in this list
# and are now out: an answer that BEGINS "Because every action is causally determined" is an
# answer, and treating the word as a section heading would move the answer into the explanation
# and leave the question with none.
_EXPLANATION = re.compile(
    r"^[ \t]*(?:explanation|solution|rationale|working|answer\s+explained)\s*[.:\-]\s*(.+)",
    re.I | re.S | re.M)


def _split_options(block: str) -> tuple[str, dict]:
    """Separate a question's stem from its lettered options. Returns (stem, {letter: text}).

    The letters must run in ORDER from A with no gaps. That single rule is what keeps this from
    firing on prose: a sentence containing "... a) the first case ..." has an A and no B, and a
    paragraph mentioning "(c) 2024" has a C with no A or B before it. Requiring A, then B, then
    C in sequence means the pattern only matches something that really is a list of options.
    """
    matches = list(_OPTION.finditer(block))
    if len(matches) < 2:
        return block.strip(), {}

    wanted, kept = 0, []
    for match in matches:
        letter = match.group(1).upper()
        if wanted < len(_OPTION_LETTERS) and letter == _OPTION_LETTERS[wanted]:
            kept.append((letter, match))
            wanted += 1
    if len(kept) < 2:
        return block.strip(), {}

    stem = block[:kept[0][1].start()].strip()
    choices: dict[str, str] = {}
    for index, (letter, match) in enumerate(kept):
        end = kept[index + 1][1].start() if index + 1 < len(kept) else len(block)
        body = re.sub(r"\s+", " ", block[match.end():end]).strip(" .;,")
        if body:
            choices[letter] = body
    return stem, (choices if len(choices) >= 2 else {})


def _clean_stem(text: str) -> str:
    """A question's text, on one line, without the marks-available noise."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    # "(5 marks)", "[10 points]", "(2 pts)" — grading metadata, not part of the question.
    flat = re.sub(r"[\(\[]\s*\d+\s*(?:marks?|points?|pts?)\s*[\)\]]", "", flat, flags=re.I)
    return flat.strip(" .;:-")


def _parse_numbered(text: str, key: dict, source: str, subject: str,
                    full: str) -> tuple[list, int]:
    """Layouts 1 and 2. Returns (items, how many were dropped for having no answer)."""
    starts = list(_QUESTION_START.finditer(text))
    if len(starts) < 2:
        return [], 0

    items, dropped = [], 0
    for index, match in enumerate(starts):
        number = int(match.group(1) or match.group(2))
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.end():end]

        # A block carrying its own worked solution: split it off before anything else, so the
        # explanation's prose is not parsed as the question's options.
        explanation = ""
        found = _EXPLANATION.search(block)
        if found and len(found.group(1).strip()) > 15:
            explanation = re.sub(r"\s+", " ", found.group(1)).strip()[:600]
            block = block[:found.start()]

        answer = ""
        inline = _INLINE_ANSWER.search(block)
        if inline:
            answer = inline.group(1).strip(" .;:")
            block = block[:inline.start()]

        stem, choices = _split_options(block)
        stem = _clean_stem(stem)

        if not answer:
            answer = str(key.get(number, "")).strip()

        if len(stem) < MIN_QUESTION_CHARS or len(stem) > MAX_QUESTION_CHARS:
            continue
        if not answer:
            dropped += 1
            continue

        # A lettered answer is only meaningful with options to index into. Without them, "B" is
        # an answer LB can neither give nor be told — so a key entry with no question body to
        # attach it to is dropped rather than stored as a one-letter answer.
        if len(answer) == 1 and answer.upper() in _OPTION_LETTERS and not choices:
            dropped += 1
            continue

        items.append(QuizItem(
            question=stem, answer=answer, choices=choices, explanation=explanation,
            subject=subject, kind=infer_kind(answer, choices), source=source,
            page=_page_of(full, match.start())))
    return items, dropped


# ---------------------------------------------------------------------------------------
# Q/A pairs
# ---------------------------------------------------------------------------------------

# "Q: ...", "Q1.", "Question 3:" then "A: ...", "Ans.", "Answer -". The two halves are found
# together so a stray "A:" with no question above it cannot invent an item.
_QA_PAIR = re.compile(
    r"(?:^|\n)[ \t]*(?:q(?:uestion)?\s*\d{0,3})\s*[.):\-]\s*(?P<q>.+?)"
    r"\n?[ \t]*(?:a(?:ns(?:wer)?)?\s*\d{0,3})\s*[.):\-]\s*(?P<a>.+?)"
    r"(?=\n[ \t]*(?:q(?:uestion)?\s*\d{0,3})\s*[.):\-]|\Z)",
    re.I | re.S)


def _parse_qa_pairs(text: str, source: str, subject: str, full: str) -> list:
    """Layouts 3 and 4. A flashcard sheet, or a study guide written as questions."""
    items = []
    for match in _QA_PAIR.finditer(text):
        stem = _clean_stem(match.group("q"))
        body = match.group("a")

        explanation = ""
        found = _EXPLANATION.search(body)
        if found and len(found.group(1).strip()) > 15:
            explanation = re.sub(r"\s+", " ", found.group(1)).strip()[:600]
            body = body[:found.start()]

        answer = re.sub(r"\s+", " ", body).strip(" .;:")
        if len(stem) < MIN_QUESTION_CHARS or len(stem) > MAX_QUESTION_CHARS or not answer:
            continue
        # An "answer" as long as an essay is a parse that ran past the next question. Kept, but
        # truncated at the paragraph, because half an answer is still gradeable by coverage.
        answer = answer[:600]

        items.append(QuizItem(question=stem, answer=answer, subject=subject,
                              kind=infer_kind(answer), source=source, explanation=explanation,
                              page=_page_of(full, match.start())))
    return items


# ---------------------------------------------------------------------------------------
# Which subject
# ---------------------------------------------------------------------------------------

# Subject fingerprints, checked against the filename first and the text second. Longest and
# most specific first: "linear algebra" must be seen before "algebra", and "organic chemistry"
# before "chemistry", or every deck collapses into its parent field.
#
# LB's words were "from calculus to philosophy and all other classes I will take", so this is
# deliberately wider than the engineering degree — the humanities rows are the point, not
# padding.
_SUBJECT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Linear Algebra", ("linear algebra", "eigenvalue", "eigenvector", "matrix", "matrices",
                        "determinant", "row echelon")),
    ("Differential Equations", ("differential equation", "diffeq", "ordinary differential",
                                "laplace transform", "wronskian")),
    # "calc2" with no space is how LB names a file, and "calc 2" is how a professor titles a
    # paper. Both spellings, because the filename is checked before the content and a miss here
    # sends a calculus paper into a deck named after its own filename.
    ("Calculus", ("calculus", "calc1", "calc2", "calc3", "calc 1", "calc 2", "calc 3",
                  "precalc", "derivative", "integral", "limit as x", "chain rule", "riemann",
                  "taylor series")),
    ("Statistics", ("statistics", "probability", "standard deviation", "null hypothesis",
                    "p-value", "regression", "binomial")),
    ("Organic Chemistry", ("organic chemistry", "alkane", "alkene", "benzene", "stereochem")),
    ("Chemistry", ("chemistry", "stoichiometry", "molarity", "periodic table", "covalent")),
    ("Physics", ("physics", "kinematic", "newton's", "momentum", "electric field",
                 "magnetic field", "thermodynamic")),
    ("Circuits", ("circuit", "kirchhoff", "thevenin", "norton", "impedance", "capacitor",
                  "inductor", "ohm's law")),
    ("Digital Logic", ("digital logic", "karnaugh", "boolean algebra", "flip-flop",
                       "truth table", "logic gate")),
    ("Electronics", ("electronic", "transistor", "mosfet", "op-amp", "operational amplifier",
                     "diode", "datasheet")),
    ("Signals and Systems", ("signals and systems", "fourier", "convolution", "z-transform",
                             "nyquist", "sampling theorem")),
    ("Philosophy", ("philosophy", "epistemolog", "metaphysic", "kant", "aristotle", "plato",
                    "socrates", "utilitarian", "categorical imperative", "free will",
                    "ethics", "moral")),
    ("Political Science", ("political science", "posc", "constitution", "federalism",
                           "electoral", "legislature", "judiciary")),
    ("History", ("history", "century", "treaty", "revolution", "dynasty", "world war")),
    ("Psychology", ("psychology", "cognitive", "behaviorism", "freud", "conditioning")),
    ("Biology", ("biology", "mitosis", "enzyme", "photosynthesis", "dna", "cell membrane")),
    ("Economics", ("economics", "supply and demand", "elasticity", "gdp", "marginal cost")),
    ("English", ("english", "literature", "thesis statement", "rhetoric", "sonnet",
                 "protagonist")),
    ("Programming", ("programming", "python", "algorithm", "big-o", "compile", "pointer")),
)

# A course code in a filename — "MATH251", "PHIL 101", "EEGR105". Used as the subject when no
# fingerprint matches, because a course code is a real name for a deck and a filename is not.
#
# **The tail is a negative lookahead, not `\b`.** An underscore is a word character, so `\b`
# after the digits does not match in `HIST110_MIDTERM_REVIEW` — which is precisely how LB names
# files. Measured: `posc201_quiz.pdf` resolved correctly only because "posc" is also a keyword
# fingerprint, and every other course code in the test set fell through to the filename.
_COURSE_CODE = re.compile(r"\b([A-Z]{2,5})[ _-]?(\d{3}[A-Z]?)(?![0-9A-Z])")

# What a course-code prefix means. **This is how LB actually names things** — his vault already
# holds `EEGR105.md` and `POSC201.md` — so a paper called `hist110_midterm_review.pdf` should
# land in "History" and not in a deck called "Hist110" that he can never ask for by name.
#
# Checked after the content fingerprints and before the bare-code fallback: a paper whose TEXT
# is plainly calculus goes to Calculus even if the file is called MATH251, because the content
# is the stronger evidence and the code is the fallback.
_COURSE_PREFIXES: dict[str, str] = {
    "MATH": "Mathematics", "MTH": "Mathematics", "CALC": "Calculus", "STAT": "Statistics",
    "PHYS": "Physics", "PHY": "Physics", "CHEM": "Chemistry", "BIOL": "Biology",
    "BIO": "Biology", "PHIL": "Philosophy", "HIST": "History", "HIS": "History",
    "PSYC": "Psychology", "PSY": "Psychology", "ECON": "Economics", "POSC": "Political Science",
    "POLS": "Political Science", "ENGL": "English", "ENG": "English", "SOC": "Sociology",
    "EEGR": "Electrical Engineering", "ECE": "Electrical Engineering",
    "EE": "Electrical Engineering", "CS": "Computer Science", "CSC": "Computer Science",
    "COSC": "Computer Science", "MENG": "Mechanical Engineering", "ME": "Mechanical Engineering",
}


def guess_subject(path: Path, text: str = "") -> str:
    """Which deck this paper belongs in.

    Filename first, then the first 6000 characters. In that order deliberately: LB names his
    files, and `calc2_practice_final.pdf` is a stronger statement of intent than a word that
    happens to appear on page one. Falls back to a course code, then to the cleaned filename —
    never to "general", because a deck called "general" is one he can never quiz on by name.
    """
    stem = re.sub(r"[_\-]+", " ", path.stem).lower()
    head = (text or "")[:6000].lower()

    for name, needles in _SUBJECT_HINTS:
        if any(needle in stem for needle in needles):
            return name
    for name, needles in _SUBJECT_HINTS:
        if sum(head.count(needle) for needle in needles) >= 2:
            return name

    code = _COURSE_CODE.search(path.stem.upper())
    if code:
        # The prefix, translated, before the raw code. "HIST110" is a filename; "History" is a
        # subject he can ask to be quizzed on out loud.
        named = _COURSE_PREFIXES.get(code.group(1))
        return named or f"{code.group(1)}{code.group(2)}"

    cleaned = re.sub(r"\b(practice|quiz|exam|test|final|midterm|review|questions?|answers?|"
                     r"key|packet|worksheet|study\s*guide|\d+)\b", " ", stem, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title() if len(cleaned) >= 3 else path.stem[:40]


# ---------------------------------------------------------------------------------------
# The entry points
# ---------------------------------------------------------------------------------------

def parse_questions(text: str, source: str = "", subject: str = "") -> tuple[list, int, str]:
    """Parse already-extracted text. Returns (items, dropped, which layout won).

    Both parsers run and the one that found MORE wins, rather than the first one that found
    anything. A study guide can contain both shapes — a numbered section and a set of Q/A
    definitions — and picking by yield gets the bigger half of it instead of whichever the
    author happened to put first.
    """
    key, body = _find_answer_key(text)
    numbered, dropped = _parse_numbered(body, key, source, subject, text)
    pairs = _parse_qa_pairs(body, source, subject, text)

    if len(pairs) > len(numbered):
        return pairs, 0, "Q/A pairs"
    layout = ("numbered questions with an answer key" if key
              else "numbered questions with inline answers")
    return numbered, dropped, layout


def import_text(text: str, source: str, subject: str = "") -> ImportReport:
    """Parse text that did not come from a PDF — a .txt or .md sheet."""
    resolved = subject or guess_subject(Path(source), text)
    items, dropped, layout = parse_questions(text, source, resolved)
    return ImportReport(source=source, items=items, dropped=dropped, pages=1, layout=layout)


def import_pdf(path: Path, subject: str = "", write: bool = True) -> ImportReport:
    """Read one PDF into the question bank. **Never raises, never uses the network.**

    Args:
        path:    the PDF.
        subject: force a deck name, or "" to work it out from the file.
        write:   False to parse and report without touching disk (`--dry-run`).

    Returns:
        An `ImportReport`. A file that yields nothing returns an empty report with the reason
        in `note`, which `sentence()` reads back — the one thing this must never do is fail
        silently and leave LB thinking his paper is in there.
    """
    path = Path(path)
    if not path.exists():
        return ImportReport(source=path.name, note="I cannot find that file.")

    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        try:
            return import_text(path.read_text(encoding="utf-8", errors="replace"), path.name,
                               subject)
        except OSError as exc:
            return ImportReport(source=path.name, note=f"I could not read it: {exc}")

    if suffix != ".pdf":
        return ImportReport(source=path.name,
                            note=f"I read questions out of PDFs, and text or Markdown files. "
                                 f"I cannot parse a {suffix} file.")

    text, pages, ocr = read_pdf_text(path)
    if not text.strip():
        return ImportReport(source=path.name, pages=pages,
                            note="No text came out of it at all — not from its text layer and "
                                 "not from OCR. If it is a photograph of a page, a clearer "
                                 "scan would fix it.")

    resolved = subject or guess_subject(path, text)
    items, dropped, layout = parse_questions(text, path.name, resolved)
    report = ImportReport(source=path.name, items=items, dropped=dropped, pages=pages,
                          ocr=ocr, layout=layout)

    if items and write:
        from tools.quiz_bank import add_items                        # noqa: PLC0415

        added = add_items(items)
        already = len(items) - sum(added.values())
        if already:
            # Said out loud rather than logged. Re-uploading a paper is a normal thing to do,
            # and "I got 40 questions" followed by a deck that did not grow by 40 is the kind
            # of quiet disagreement that makes him stop trusting the count.
            report.note = (f"{already} of them were already in the bank from an earlier "
                           f"import, so the deck grew by {sum(added.values())}.")
    return report


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse                                                  # noqa: PLC0415
    import sys                                                       # noqa: PLC0415

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="turn a practice quiz PDF into questions")
    ap.add_argument("path", nargs="?", default="", help="the PDF, .txt or .md to read")
    ap.add_argument("--subject", default="", help="force the deck name")
    ap.add_argument("--dry-run", action="store_true", help="parse and print, write nothing")
    ap.add_argument("--scan", metavar="DIR", default="", help="every PDF in a folder")
    args = ap.parse_args(argv)

    targets: list[Path] = []
    if args.scan:
        targets = sorted(Path(args.scan).rglob("*.pdf"))
        if not targets:
            print(f"No PDFs under {args.scan}.")
            return 1
    elif args.path:
        targets = [Path(args.path)]
    else:
        ap.error("give a file, or --scan a folder")

    total = 0
    for target in targets:
        report = import_pdf(target, subject=args.subject, write=not args.dry_run)
        print(f"\n  {report.sentence()}")
        if report.items:
            print(f"  (layout: {report.layout})")
        for item in report.items[:5 if not args.dry_run else 100]:
            print(f"\n    [{item.kind}] {item.question}")
            for letter, text in sorted(item.choices.items()):
                print(f"         {letter}) {text}")
            print(f"      -> {item.answer}")
            if item.explanation:
                print(f"         why: {item.explanation[:150]}")
        if len(report.items) > 5 and not args.dry_run:
            print(f"\n    ... and {len(report.items) - 5} more.")
        total += len(report.items)

    print(f"\n  {total} question(s) in total."
          + ("  (dry run — nothing was written)" if args.dry_run else "") + "\n")
    return 0 if total else 1


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    raise SystemExit(main())
