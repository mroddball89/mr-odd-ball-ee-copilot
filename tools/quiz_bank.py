#!/usr/bin/env python3
"""
Module:  quiz_bank.py
Purpose: Every question LB can be asked, filed by subject, on his disk and nowhere else.
Author:  LB
Date:    2026-09-02

    python tools/quiz_bank.py --list                     # what subjects exist, and how big
    python tools/quiz_bank.py --show calculus            # the first questions in one deck
    python tools/quiz_bank.py --show calculus --all
    python tools/quiz_bank.py --drop calculus            # delete one deck (asks first)

## What replaced what

`quiz_data.json` was a flat list of three electrical-engineering questions with no way to add
a fourth. LB takes calculus and philosophy and will take more, and a bank with one unnamed pool
in it cannot answer "quiz me on calculus" — it can only answer "quiz me".

So a deck per subject, `data/quiz/<subject>.json`, and the subject is a first-class thing that
`pick()` filters on.

## The legacy file is ADOPTED, not migrated

`quiz_data.json` is still read, as a deck called `electronics`, and it is not moved, rewritten
or deleted. Two reasons and the second is the real one:

1. `tools/verify_engine.py` stubs `tools.quiz_manager.get_random_question`, and `README.md`
   documents the file by name. A migration that deletes it makes both of those lies at once.
2. **It is LB's file.** He put three questions in it by hand. A tool that silently relocates a
   file the user wrote is a tool he stops trusting with the next one. It is read where it lies,
   and if he ever wants it merged that is a sentence he can say.

Adoption is read-only in both directions: `add_items` never writes back into it, so an import
that happens to produce an electronics deck writes `data/quiz/electronics.json` alongside, and
`load_deck` merges the two at read time. Nothing is ever lost by a write that went to the wrong
one of the two files, because there is only one file that is ever written.

## Ids are content hashes, and that is what makes re-importing safe

The same practice exam gets uploaded twice — a second copy, a re-download, a rebuild. If ids
were sequential, the second import would double every question in the deck and LB would be
asked the same thing twice in a row for the rest of the semester.

The id is `sha1(normalised question)[:12]`, so the second import of the same paper adds nothing
and the deck is idempotent under re-upload. Normalised, not raw, because the OCR pass that read
the scan is not byte-identical run to run — "What is lim x->0" and "What is lim x-> 0" are the
same question and must hash the same.

## What is NOT here

No scheduling, no spaced repetition, no mastery model. `pick()` excludes what the session has
already asked and is otherwise random. That is the whole policy, and it is written down here so
that adding a fifth field to the item schema is a decision somebody makes on purpose rather than
something that accretes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("oddball.quiz")

__all__ = ["QUIZ_DIR", "LEGACY_FILE", "QuizItem", "subjects", "load_deck", "load_all",
           "add_items", "pick", "deck_sizes", "subject_slug", "resolve_subject",
           "item_id", "save_deck", "drop_subject"]

REPO_ROOT = Path(__file__).resolve().parents[1]

# Anchored to the repo, not the working directory, for the same reason as `HUD_DIR` in
# orchestrator/hud_bridge.py: a systemd unit or a Task Scheduler entry starts somewhere else
# and `Path("data")` would put the decks there.
QUIZ_DIR = REPO_ROOT / "data" / "quiz"

# The three hand-written EE questions. Read, never written. See the module docstring.
LEGACY_FILE = REPO_ROOT / "quiz_data.json"
LEGACY_SUBJECT = "electronics"

# What a subject name may contain once it is a filename. Everything else collapses to a single
# underscore, so "Calculus II (MATH 251)" and "calculus ii math 251" are the same deck and
# neither can walk out of `data/quiz/`. Same rule as `file_manager._safe_segment`.
_UNSAFE = re.compile(r"[^a-z0-9]+")

# Kinds an item can be. `quiz_grade` switches on this, and an unknown kind grades as "short",
# so a deck hand-edited with a typo in it still works.
KINDS = ("mcq", "numeric", "short", "prose")


def item_id(question: str) -> str:
    """A stable id for a question. Same question, same id, forever.

    Normalised before hashing — case, punctuation and runs of whitespace are dropped — because
    the OCR that read the scanned copy of a paper does not produce byte-identical text run to
    run, and two ids for one question is the same deck asking it twice.
    """
    flat = re.sub(r"[^a-z0-9]+", " ", (question or "").lower()).strip()
    return hashlib.sha1(flat.encode("utf-8")).hexdigest()[:12]


def subject_slug(subject: str) -> str:
    """One safe filename stem for a subject. Never empty, never a path."""
    flat = _UNSAFE.sub("_", (subject or "").lower()).strip("_")
    return flat or "general"


@dataclass
class QuizItem:
    """One question, with everything needed to ask it and mark it WITHOUT a model.

    Args:
        question:    what LB is asked. Never empty.
        answer:      the official answer. For an `mcq` this is the LETTER ("B"), and the text of
                     that option lives in `choices` — `quiz_grade` accepts either from him.
        choices:     {"A": "...", "B": "..."} for an mcq, empty otherwise.
        explanation: the worked solution, when the PDF carried one. **This is the field that
                     keeps the external model out of the loop**: an "explain that" answered from
                     here costs nothing. Empty is normal and not a defect.
        subject:     which deck it belongs to.
        kind:        one of KINDS. Chosen at import; decides how `quiz_grade` marks it.
        source:      the filename it was imported from, for "where did this come from".
        page:        1-based page in that file, or 0 when unknown.
        origin:      "" when a parser read this question off the page, "model" when
                     `tools/quiz_generate.py` WROTE it from reference material that contained no
                     questions. **The one field here that is about trust rather than content.**
                     A parsed question can be checked against the paper it came from; a
                     generated one is the model's claim about the material and can be wrong in a
                     way no regex can be. Stored per item, not per deck, because one subject
                     accumulates both — a professor's practice exam and a generated set off a
                     datasheet land in the same `electronics.json`.
    """

    question: str
    answer: str
    choices: dict = field(default_factory=dict)
    explanation: str = ""
    subject: str = "general"
    kind: str = "short"
    source: str = ""
    page: int = 0
    origin: str = ""

    @property
    def id(self) -> str:
        return item_id(self.question)

    def to_dict(self) -> dict:
        d = {"id": self.id, "question": self.question, "answer": self.answer,
             "subject": self.subject, "kind": self.kind}
        # Absent rather than empty, so a deck LB opens in an editor is readable. A short-answer
        # question with `"choices": {}, "explanation": "", "source": "", "page": 0` on it is
        # four lines of noise per question in a file he may well want to hand-edit.
        if self.choices:
            d["choices"] = dict(self.choices)
        if self.explanation:
            d["explanation"] = self.explanation
        if self.source:
            d["source"] = self.source
        if self.page:
            d["page"] = self.page
        if self.origin:
            d["origin"] = self.origin
        return d

    @classmethod
    def from_dict(cls, raw: dict, subject: str = "") -> "QuizItem | None":
        """Build an item from a deck entry, or None when the entry is unusable.

        Tolerant on purpose. These files are meant to be hand-editable — LB adding a question
        to `calculus.json` in a text editor is a feature, not a hazard — so a missing `kind` is
        inferred and a missing `subject` is taken from the filename. Only the two fields that
        cannot be guessed, a question and an answer, are required.
        """
        if not isinstance(raw, dict):
            return None
        question = str(raw.get("question", "")).strip()
        answer = str(raw.get("answer", "")).strip()
        if not question or not answer:
            return None

        choices = raw.get("choices") or {}
        if not isinstance(choices, dict):
            choices = {}
        choices = {str(k).strip().upper(): str(v).strip() for k, v in choices.items()
                   if str(v).strip()}

        kind = str(raw.get("kind", "")).strip().lower()
        if kind not in KINDS:
            kind = infer_kind(answer, choices)

        return cls(question=question, answer=answer, choices=choices,
                   explanation=str(raw.get("explanation", "")).strip(),
                   subject=str(raw.get("subject", "")).strip() or subject or "general",
                   kind=kind, source=str(raw.get("source", "")).strip(),
                   page=int(raw.get("page", 0) or 0),
                   origin=str(raw.get("origin", "")).strip().lower())


# What may follow a number with NO space between, and the exclusions are the whole point.
# `x`, `y`, `a`, `t` and `r` are variable names, so `2x` is algebra; `V`, `A` and `Ω` are units,
# so `2V` is two volts; `k`, `m` and `M` are scale prefixes, so `4.7k` is a resistor.
#
# Without this distinction `infer_kind("2x")` returned "numeric", and the numeric grader then
# marked a bare "2" CORRECT against an answer of "2x" — measured, in the harness.
_SCALE_PREFIX = "numµμkKMG"
_UNIT_LETTERS = "VAWFHCJNKsgLΩ°%"
_UNIT_BODY = r"[A-Za-zΩμµ°%/·^\d\s-]{0,17}"

# A number, possibly signed, decimal, exponential or a fraction, possibly carrying a scale and
# a unit. `4`, `-3.2`, `6.02e23`, `1/2`, `4.7k`, `4.7k ohms`, `1.8V`, `9.8 m/s^2`.
#
# Used only to decide an item's KIND at import; the comparison itself lives in `quiz_grade` and
# is stricter still. Four ways the tail may look, and a bare variable letter is none of them.
_LOOKS_NUMERIC = re.compile(
    rf"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:\s*/\s*[+-]?\d+(?:\.\d*)?)?"
    rf"(?:"
    rf"|\s+[A-Za-zΩμµ°%]{_UNIT_BODY}"      # a space, then anything wordlike: "9.8 m/s^2"
    rf"|[{_SCALE_PREFIX}]{_UNIT_BODY}"     # a scale prefix, then an optional unit: "4.7k ohms"
    rf"|[{_UNIT_LETTERS}]{_UNIT_BODY}"     # a known unit letter: "1.8V"
    rf"|[A-Za-zΩμµ°%]{{2}}{_UNIT_BODY}"    # two or more letters is a real unit, not a variable
    rf")$")

# "Around 1.8V to 2.0V", "about 9.8", "approximately 3 ohms". The qualifier is stripped before
# the numeric test, because a hedged number is still a number — and this exact string is the
# LED forward-drop answer already sitting in LB's `quiz_data.json`.
_NUMERIC_QUALIFIER = re.compile(
    r"^(?:around|about|approximately|approx|roughly|nearly|circa|~|close\s+to)\s+", re.I)

# A range: "1.8V to 2.0V", "1.8-2.0", "between 1 and 3". Recognised as NUMERIC so the grader
# accepts anything inside it rather than demanding the string back word for word.
_LOOKS_RANGE = re.compile(
    r"^(?:between\s+)?[+-]?\d+\.?\d*\s*[A-Za-zΩμµ°%]{0,4}\s*(?:to|-|–|and)\s*"
    r"[+-]?\d+\.?\d*\s*[A-Za-zΩμµ°%/·^\d]{0,6}$", re.I)


def infer_kind(answer: str, choices: dict | None = None) -> str:
    """Which grader an answer wants, worked out from its shape.

    The order is the whole content of this function:

    - **choices present -> mcq.** Nothing else matters; an mcq whose correct option happens to
      be the number 4 is still marked by letter.
    - **a bare number -> numeric.** `quiz_grade` then compares magnitudes with a tolerance,
      which is the only way "0.333" and "1/3" both mark correct.
    - **long -> prose.** Over eight words is a sentence — a definition or an argument — and it
      is graded on content-word coverage rather than on matching a string nobody would retype.
      Eight rather than twelve because "the sum of currents entering a node equals the sum
      leaving it" is twelve words and is plainly prose; anyone answering it out loud will use
      different connectives, and the strict short-answer threshold would mark that wrong.
    - **otherwise short.** A term, a name, a formula. Graded by normalised match, then sympy.
    """
    if choices:
        return "mcq"
    answer = (answer or "").strip()
    bare = _NUMERIC_QUALIFIER.sub("", answer).strip()
    if _LOOKS_NUMERIC.match(bare) or _LOOKS_RANGE.match(bare):
        return "numeric"
    if len(answer.split()) > 8:
        return "prose"
    return "short"


# ---------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------

def _read_json(path: Path):
    """Parse a JSON file, or return None. A broken deck is a warning, never an exception.

    These are read from inside an agent turn. A deck LB hand-edited into invalid JSON must cost
    him that deck and a sentence about it, not the turn — and certainly not every OTHER deck,
    which is what a raise from inside `load_all`'s loop would cost.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOG.warning("could not read %s: %s: %s", path.name, type(exc).__name__, exc)
        return None


def _deck_path(subject: str) -> Path:
    return QUIZ_DIR / f"{subject_slug(subject)}.json"


def subjects() -> list[str]:
    """Every subject with at least one question, alphabetically.

    The names are the ones stored INSIDE the decks, not the filenames — "Calculus II" reads
    better back to LB than "calculus_ii", and the filename is only ever an addressing detail.
    """
    found: dict[str, str] = {}
    for name, items in _all_decks().items():
        if items:
            found[name] = name
    return sorted(found, key=str.lower)


def _display_name(items: list[QuizItem], fallback: str) -> str:
    """What to call a deck. The subject its questions carry, else the filename stem."""
    for item in items:
        if item.subject:
            return item.subject
    return fallback


def _all_decks() -> dict[str, list[QuizItem]]:
    """{display subject: items}, every deck on disk plus the adopted legacy file.

    The legacy file merges INTO `electronics` rather than sitting beside it, so a question
    imported into `data/quiz/electronics.json` and a question hand-written in `quiz_data.json`
    are one deck to everything above this line. Deduplicated by id, imports winning — if the
    same question exists in both, the imported copy carries the source and page.
    """
    decks: dict[str, list[QuizItem]] = {}

    if QUIZ_DIR.exists():
        for path in sorted(QUIZ_DIR.glob("*.json")):
            raw = _read_json(path)
            if not isinstance(raw, list):
                if raw is not None:
                    LOG.warning("%s is not a list of questions — skipped", path.name)
                continue
            items = [i for i in (QuizItem.from_dict(r, path.stem) for r in raw) if i]
            if items:
                decks[_display_name(items, path.stem)] = items

    legacy = _read_json(LEGACY_FILE)
    if isinstance(legacy, list):
        adopted = [i for i in (QuizItem.from_dict(r, LEGACY_SUBJECT) for r in legacy) if i]
        if adopted:
            # Find the deck the legacy questions belong with, by slug rather than by display
            # name, so `data/quiz/electronics.json` calling itself "Electronics" still absorbs
            # them instead of producing two decks one capital letter apart.
            target = next((name for name in decks
                           if subject_slug(name) == LEGACY_SUBJECT), LEGACY_SUBJECT)
            existing = decks.get(target, [])
            have = {i.id for i in existing}
            decks[target] = existing + [i for i in adopted if i.id not in have]

    return decks


def load_deck(subject: str) -> list[QuizItem]:
    """Every question in one subject. Empty list when there is no such deck."""
    return _all_decks().get(resolve_subject(subject) or subject, [])


def load_all() -> list[QuizItem]:
    """Every question in every subject, deduplicated by id."""
    out: list[QuizItem] = []
    seen: set[str] = set()
    for items in _all_decks().values():
        for item in items:
            if item.id not in seen:
                seen.add(item.id)
                out.append(item)
    return out


def deck_sizes() -> dict[str, int]:
    """{subject: how many questions}. What `list_quiz_subjects` reads back to LB."""
    return {name: len(items) for name, items in sorted(_all_decks().items(),
                                                       key=lambda kv: kv[0].lower()) if items}


def resolve_subject(spoken: str) -> str:
    """The deck LB means, or "" when nothing matches.

    He is voice-first, so this has to survive Whisper. "calc two", "calculus 2" and
    "Calculus II" are the same deck; so are "philosophy" and "phil". Matched in three passes,
    loosest last, and a tie is NOT guessed between — an ambiguous subject returns "" and the
    caller asks him which, for the same reason `file_manager._find_in_inbox` refuses to pick
    between two files.
    """
    want = subject_slug(spoken)
    if not want or want == "general":
        return ""

    names = list(_all_decks())
    if not names:
        return ""

    for name in names:                                   # exact, on the slug
        if subject_slug(name) == want:
            return name

    # Roman numerals, because a course is "Calculus II" on the syllabus and "calculus 2" out
    # loud, and Whisper will produce either. Only the four that turn up in a course number.
    romanised = want
    for arabic, roman in (("_4", "_iv"), ("_3", "_iii"), ("_2", "_ii"), ("_1", "_i")):
        romanised = romanised.replace(arabic, roman)
    for name in names:
        if subject_slug(name) == romanised:
            return name

    hits = [n for n in names if want in subject_slug(n) or subject_slug(n) in want]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return ""

    # Loosest pass: one WORD of what he said, inside a deck name.
    #
    # A deck named from a filename is named badly — `quiz_import.guess_subject` produced
    # "Resistorcharts" from `resistorcharts.pdf`, which is nothing anybody says out loud. Every
    # pass above compares the whole phrase, so "resistors", "resistor charts" and "resistor band
    # colors" all missed a deck that was sitting right there with twelve questions in it.
    #
    # Measured 2026-09-09, and it is the question LB actually asked: "Quiz me on resistor band
    # colors" -> "No deck for 'resistor band colors'".
    #
    # Trailing "s" is dropped because he pluralises where a filename does not. Five characters
    # minimum, and stopwords excluded, so "the", "my" and "some" cannot reach a deck — and a
    # word matching TWO decks still returns "" and asks him, exactly as the pass above does.
    # It runs in BOTH directions, because the abbreviation can be on either side. "resistors"
    # is longer than the deck word it should find inside `resistorcharts`; "trigonometry" is
    # longer than the deck named `Trig Limits` that answers it — and Whisper produces both,
    # having heard LB say "trigonometry" and written "trig? Anometry".
    stop = {"quiz", "test", "questions", "question", "about", "some", "band", "colour", "color"}
    spoken_words = {w for w in re.findall(r"[a-z]{5,}", want.replace("_", " "))} - stop

    for token in sorted(spoken_words, key=len, reverse=True):
        singular = token[:-1] if token.endswith("s") else token
        found = [n for n in names
                 if singular in subject_slug(n)
                 # ...or a word of the DECK's name opens the word he said. Four characters, so
                 # "calc" reaches "calculus" and no two-letter fragment reaches anything.
                 or any(len(w) >= 4 and singular.startswith(w)
                        for w in re.findall(r"[a-z]+", subject_slug(n).replace("_", " ")))]
        if len(found) == 1:
            return found[0]
    return ""


# ---------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------

def save_deck(subject: str, items: list[QuizItem]) -> Path:
    """Write one deck, atomically. Returns where it went.

    Temp file plus `os.replace`, which is the pattern this repo owes several of its ledgers
    (see the open item in tasks/todo.md): a power cut halfway through `json.dump` on the live
    file leaves LB with a truncated deck and no way to know which questions it lost. Written
    beside the target, because `os.replace` is only atomic within a filesystem.
    """
    import os                                                        # noqa: PLC0415

    QUIZ_DIR.mkdir(parents=True, exist_ok=True)
    path = _deck_path(subject)
    payload = json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False)

    temp = path.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)
    return path


def add_items(items: list[QuizItem]) -> dict[str, int]:
    """Merge questions into their decks. Returns {subject: how many were NEW}.

    Grouped by the subject on each item, so one import of a mixed PDF can write two decks.

    **Dedup is by id and the EXISTING copy wins.** Re-importing a paper LB has already imported
    must not overwrite a deck he has since hand-edited — if he fixed a mangled OCR answer in
    `calculus.json`, a second upload of the same scan would otherwise mangle it again. New
    questions are appended; questions already there are left exactly as they are.
    """
    by_subject: dict[str, list[QuizItem]] = {}
    for item in items:
        if item.question.strip() and item.answer.strip():
            by_subject.setdefault(item.subject or "general", []).append(item)

    added: dict[str, int] = {}
    for subject, new in by_subject.items():
        target = resolve_subject(subject) or subject
        # Straight off the deck FILE, not `load_deck` — that merges the adopted legacy file in,
        # and writing the merge back would copy `quiz_data.json`'s questions into
        # `data/quiz/electronics.json` as a side effect of importing something unrelated.
        raw = _read_json(_deck_path(target))
        existing = [i for i in (QuizItem.from_dict(r, target) for r in (raw or [])) if i] \
            if isinstance(raw, list) else []

        have = {i.id for i in existing}
        fresh = []
        for item in new:
            if item.id in have:
                continue
            have.add(item.id)
            fresh.append(item)

        if fresh:
            save_deck(target, existing + fresh)
        added[target] = len(fresh)
    return added


def drop_subject(subject: str) -> bool:
    """Delete one deck file. True when something was deleted.

    Never touches `quiz_data.json` — the adopted file is LB's, and `--drop electronics` removing
    a file this module has always described as read-only would be the surprise this whole design
    is written against.
    """
    name = resolve_subject(subject) or subject
    path = _deck_path(name)
    try:
        path.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------------------
# Choosing the next question
# ---------------------------------------------------------------------------------------

def pick(subject: str = "", exclude: set | None = None) -> QuizItem | None:
    """The next question to ask. None when the bank is empty.

    Args:
        subject: a deck to stay inside, or "" for the whole bank.
        exclude: ids already asked this session.

    `exclude` is the fix for the bug that made the old quiz feel broken with three questions in
    it: `random.choice` over a list of three will hand back the question just answered about a
    third of the time. Exhausting the pool WRAPS rather than stopping — the session has run out
    of new questions, and asking a repeat is better than dropping LB out of a mode he asked to
    be in. The caller is what tells him he has been round once.
    """
    pool = load_deck(subject) if subject else load_all()
    if not pool:
        return None
    unseen = [i for i in pool if i.id not in (exclude or set())]
    return random.choice(unseen or pool)


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

    ap = argparse.ArgumentParser(description="the question bank on disk")
    ap.add_argument("--list", action="store_true", help="every subject and its size")
    ap.add_argument("--show", metavar="SUBJECT", default="", help="questions in one deck")
    ap.add_argument("--all", action="store_true", help="with --show, do not truncate")
    ap.add_argument("--drop", metavar="SUBJECT", default="", help="delete one deck")
    args = ap.parse_args(argv)

    if args.drop:
        name = resolve_subject(args.drop) or args.drop
        count = len(load_deck(name))
        reply = input(f"Delete the {name!r} deck and its {count} question(s)? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Left alone.")
            return 0
        print(f"Deleted {name}." if drop_subject(name) else f"No deck file for {name}.")
        return 0

    if args.show:
        name = resolve_subject(args.show) or args.show
        items = load_deck(name)
        if not items:
            print(f"No questions under {name!r}. Subjects: "
                  + (", ".join(subjects()) or "(none yet)"))
            return 1
        print(f"\n  {name} — {len(items)} question(s)\n")
        for item in (items if args.all else items[:10]):
            print(f"  [{item.kind}] {item.question}")
            for letter, text in sorted(item.choices.items()):
                print(f"       {letter}) {text}")
            print(f"    -> {item.answer}"
                  + (f"   ({item.source} p{item.page})" if item.source else ""))
            if item.explanation:
                print(f"       why: {item.explanation[:120]}")
            print()
        if not args.all and len(items) > 10:
            print(f"  ... and {len(items) - 10} more. --all to see them.\n")
        return 0

    sizes = deck_sizes()
    if not sizes:
        print("\n  The question bank is empty. Upload a practice quiz or a Q&A PDF and file it "
              "as 'quiz'.\n")
        return 0
    total = sum(sizes.values())
    print(f"\n  {total} question(s) across {len(sizes)} subject(s):\n")
    for name, count in sizes.items():
        print(f"    {name:<28} {count:>4}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
