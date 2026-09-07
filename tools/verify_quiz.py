#!/usr/bin/env python3
"""
Module:  verify_quiz.py
Purpose: Prove the quiz marks locally, imports a real PDF, and never phones out uninvited.
Author:  LB
Date:    2026-09-02

    python tools/verify_quiz.py
    python tools/verify_quiz.py --probe     # reintroduce the bug section 5 exists to catch

No network, and that is not merely true — it is **enforced**. Section 5 monkeypatches
`socket.socket` to raise, then runs a whole quiz session through the engine. If any part of
marking a question reaches out, the checks in that section go red rather than quietly passing
on a cached response or an offline error handler.

## What each section is for

    1. the bank      subject decks, stable ids, re-import does not duplicate
    2. the grader    every kind: mcq, numeric, symbolic, prose — including the negation trap
    3. the importer  a REAL two-page PDF, built here, in all four layouts
    4. the session   no repeats, score, skip, exhaustion, explain-from-the-deck
    5. no network    the whole thing again with sockets disabled

## Section 5 is the one that matters, and --probe is what proves it bites

Sections 1-4 would all stay green if `_quiz_turn` still called Gemini on every answer — the
marking would come back, the score would be right, and nothing in them would notice the twenty
requests it had spent. That is the exact regression this rewrite exists to prevent, so section
5 tests the property directly rather than by implication.

`--probe` puts the old behaviour back — a grader that calls out on every answer — and shows
section 5 going red. A claim that a harness bites is worth what the last check of it was worth.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.harness_lib import bootstrap, check, counts as _tally, section  # noqa: E402

bootstrap()


from tools import quiz_bank                                          # noqa: E402
from tools.quiz_bank import QuizItem, infer_kind, item_id            # noqa: E402
from tools.quiz_grade import Grade, explain_locally, grade           # noqa: E402
from tools.quiz_import import guess_subject, import_pdf, parse_questions  # noqa: E402


def marked(official: str, given: str, kind: str = "", choices: dict | None = None) -> Grade:
    """Grade one answer through the real grader."""
    item = QuizItem(question="q", answer=official, choices=choices or {},
                    kind=kind or infer_kind(official, choices))
    return grade(item, given)


# ---------------------------------------------------------------------------------------
# A real PDF with a real text layer, written here.
#
# Built rather than checked in, for the reason `verify_ocr.py` gives about its image fixture:
# a harness that depends on a file staying in `data/` is a harness that breaks when LB tidies
# up. This writer is about sixty lines of PDF syntax and produces a file `pypdf` reads exactly
# as it reads a professor's export.
# ---------------------------------------------------------------------------------------

def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_text_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Write a PDF with a genuine text layer. One argument per page, one string per line."""
    count = len(pages)
    page_ids = [4 + 2 * i for i in range(count)]
    content_ids = [5 + 2 * i for i in range(count)]

    objects: list[tuple[int, str | None]] = [
        (1, "<< /Type /Catalog /Pages 2 0 R >>"),
        (2, f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
            f"/Count {count} >>"),
        (3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    ]
    streams: dict[int, str] = {}
    for index, lines in enumerate(pages):
        objects.append((page_ids[index],
                        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        f"/Contents {content_ids[index]} 0 R "
                        f"/Resources << /Font << /F1 3 0 R >> >> >>"))
        body = "BT\n/F1 11 Tf\n1 0 0 1 50 740 Tm\n14 TL\n"
        body += "".join(f"({_escape(line)}) Tj T*\n" for line in lines)
        streams[content_ids[index]] = body + "ET"
        objects.append((content_ids[index], None))

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number, payload in sorted(objects):
        offsets[number] = len(out)
        if payload is None:
            data = streams[number].encode("latin-1")
            out += f"{number} 0 obj\n<< /Length {len(data)} >>\nstream\n".encode()
            out += data + b"\nendstream\nendobj\n"
        else:
            out += f"{number} 0 obj\n{payload}\nendobj\n".encode()

    xref_at = len(out)
    top = max(offsets) + 1
    out += f"xref\n0 {top}\n0000000000 65535 f \n".encode()
    for number in range(1, top):
        out += f"{offsets.get(number, 0):010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {top} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


MCQ_PAPER = [
    ["MATH 251 - Practice Final", "Page 1 of 2", "",
     "1. What is the derivative of f(x) = x^2?",
     "   A) x", "   B) 2x", "   C) x^2/2", "   D) 2", "",
     "2. Evaluate the limit as x approaches 0 of sin(x)/x.",
     "   A) 0", "   B) 1", "   C) infinity", "   D) undefined"],
    ["3. The integral of 2x dx is:",
     "   A) x^2 + C", "   B) 2", "   C) x^2", "   D) 2x^2 + C", "",
     "Answer Key", "1. B", "2. B", "3. A"],
]

PROSE_PAPER = [
    ["PHIL 101 Review Questions", "",
     "1. According to Kant, what is the categorical imperative?",
     "Answer: A universal moral law that one should act only on maxims that could be willed",
     "as universal laws.",
     "Explanation: Kant contrasts it with hypothetical imperatives, which are conditional on",
     "what one happens to want.", "",
     "2. What is the central claim of utilitarianism?",
     "Answer: That the right action is the one producing the greatest happiness for the",
     "greatest number."],
]

QA_PAPER = [
    ["Circuits study guide", "",
     "Q: What does Kirchhoff's Current Law state?",
     "A: The sum of currents entering a node equals the sum leaving it.", "",
     "Q: State Ohm's Law.",
     "A: V = I * R", "",
     "Q: What is the impedance of an ideal capacitor at DC?",
     "A: Infinite - it acts as an open circuit."],
]

# The negative fixture. Numbered lines that are NOT questions — a parser that yields anything
# from this would fill LB's bank with slide bullets he can never answer.
SLIDES_PAPER = [
    ["EEGR 105 - Lecture 1", "", "Course Objectives",
     "1. Understand the fundamentals of circuit analysis",
     "2. Apply Kirchhoff's laws to practical networks",
     "3. Design and analyse simple passive filters", "",
     "Grading", "1. Homework 20%", "2. Midterm 30%", "3. Final 50%"],
]


def run(probe: bool = False, probe_pacing: bool = False) -> int:
    print("=" * 78)
    print("  verify_quiz.py — the question bank, the local grader, and the silence")
    print("=" * 78)

    workspace = Path(tempfile.mkdtemp(prefix="oddball-quiz-"))
    real_dir, real_legacy = quiz_bank.QUIZ_DIR, quiz_bank.LEGACY_FILE
    quiz_bank.QUIZ_DIR = workspace / "decks"
    quiz_bank.LEGACY_FILE = workspace / "quiz_data.json"

    try:
        _sections(workspace, probe, probe_pacing)
    finally:
        quiz_bank.QUIZ_DIR, quiz_bank.LEGACY_FILE = real_dir, real_legacy
        shutil.rmtree(workspace, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"  {_tally.passed + _tally.failed} checks, {_tally.passed} passed, {_tally.failed} failed")
    print("=" * 78)
    if probe or probe_pacing:
        which = "5" if probe else "6"
        if _tally.failed:
            print(f"\n  The harness BITES: {_tally.failed} check(s) went red.\n")
            return 0
        print(f"\n  PROBE DID NOT BITE — section {which} is not testing what it "
              f"claims.\n")
        return 1
    if _tally.failed:
        print(f"\n  {_tally.failed} RED\n")
        return 1
    print(f"\n  {_tally.passed}/{_tally.passed} checks passed — all green\n")
    return 0


def _sections(workspace: Path, probe: bool, probe_pacing: bool = False) -> None:
    # =====================================================================================
    section("1. the bank — decks by subject, stable ids, no duplicates on re-import")
    # =====================================================================================
    check(item_id("What is 2+2?") == item_id("what is 2 + 2 ?"),
          "the id is the NORMALISED question — OCR whitespace must not fork a question",
          f"{item_id('What is 2+2?')} vs {item_id('what is 2 + 2 ?')}")
    check(item_id("What is 2+2?") != item_id("What is 3+3?"), "...and two questions differ")

    calc = [QuizItem(question="What is the derivative of x^2?", answer="2x", subject="Calculus"),
            QuizItem(question="What is the integral of 2x?", answer="x^2 + C",
                     subject="Calculus")]
    phil = [QuizItem(question="Who wrote the Critique of Pure Reason?", answer="Immanuel Kant",
                     subject="Philosophy")]
    added = quiz_bank.add_items(calc + phil)
    check(added.get("Calculus") == 2 and added.get("Philosophy") == 1,
          "one import writes two decks, split by the subject on each item", str(added))
    check(sorted(quiz_bank.subjects()) == ["Calculus", "Philosophy"],
          "...and both subjects are listed", str(quiz_bank.subjects()))

    again = quiz_bank.add_items(calc)
    check(sum(again.values()) == 0, "re-importing the same paper adds NOTHING",
          f"it added {sum(again.values())}")
    check(len(quiz_bank.load_deck("Calculus")) == 2,
          "...and the deck did not grow", str(len(quiz_bank.load_deck("Calculus"))))

    check(quiz_bank.resolve_subject("calculus") == "Calculus", "a subject resolves case-blind")
    check(quiz_bank.resolve_subject("phil") == "Philosophy", "...and on a prefix")
    check(quiz_bank.resolve_subject("basket weaving") == "",
          "...and an unknown subject resolves to nothing rather than to a guess")

    # The legacy file is ADOPTED, not migrated. Written after the decks exist, so the merge is
    # the thing under test rather than the file being the only source.
    quiz_bank.LEGACY_FILE.write_text(
        json.dumps([{"question": "What does I2C stand for?",
                     "answer": "Inter-Integrated Circuit"}]), encoding="utf-8")
    check("electronics" in [s.lower() for s in quiz_bank.subjects()],
          "quiz_data.json is adopted as a deck", str(quiz_bank.subjects()))
    check(quiz_bank.LEGACY_FILE.exists(),
          "...and is NOT moved or deleted — it is LB's file, read where it lies")

    exclude = {i.id for i in quiz_bank.load_deck("Calculus")}
    check(quiz_bank.pick("Calculus", exclude=exclude) is not None,
          "an exhausted deck WRAPS rather than dropping him out of the mode")
    picked = [quiz_bank.pick("Philosophy") for _ in range(5)]
    check(all(p is not None and p.subject == "Philosophy" for p in picked),
          "a subject-scoped pick never leaves the subject")

    # =====================================================================================
    section("2. the grader — every kind, locally, with no key set")
    # =====================================================================================
    check(marked("V = I * R", "V equals I times R").correct,
          "a SPOKEN formula is marked right — Whisper never writes '*'")
    check(marked("V = I * R", "voltage equals current times resistance").correct,
          "...and so is one stated in words rather than symbols")
    check(marked("V = I * R", "R = V / I").correct,
          "a rearrangement of the same law is right — that is knowing it better, not worse")
    check(not marked("V = I * R", "P equals I squared R").correct,
          "...but a DIFFERENT law is wrong")
    check(not marked("V = I * R", "V = 0").correct,
          "and sympy's 'no' is not overruled by characters two strings happen to share")

    check(marked("2*x", "x + x").correct, "algebraically equal answers are equal")
    check(marked("x**2/2 + C", "x squared over two").verdict == "partial",
          "a dropped constant of integration is a PARTIAL with a name, not a flat wrong",
          marked("x**2/2 + C", "x squared over two").why)

    check(marked("Around 1.8V to 2.0V", "about 1.9 volts").correct,
          "a range answer accepts anything inside it — this is a real row in his bank")
    check(not marked("Around 1.8V to 2.0V", "3.3 volts").correct, "...and rejects outside it")
    check(marked("4.7k", "4700").correct, "'4.7k' and '4700' are the same resistor")
    check(marked("5 mA", "0.005 A").correct, "...and so are 5 mA and 0.005 A")
    check(marked("4.7k ohms", "4.7 ohms").verdict == "partial",
          "right digits, wrong scale is called out AS a scale error",
          marked("4.7k ohms", "4.7 ohms").why)

    mcq = {"A": "x", "B": "2x", "C": "x^2/2", "D": "2"}
    check(marked("B", "B", "mcq", mcq).correct, "a multiple choice answered by letter")
    check(marked("B", "the answer is B", "mcq", mcq).correct, "...or by a sentence naming it")
    check(marked("B", "two x", "mcq", mcq).correct,
          "...or by READING THE OPTION OUT, which is what he will actually do out loud")
    check(not marked("B", "x squared over two", "mcq", mcq).correct,
          "...and reading out the WRONG option is wrong")
    check("C" in marked("B", "x squared over two", "mcq", mcq).why,
          "...and he is told which one he picked", marked("B", "x squared over two", "mcq", mcq).why)

    check(marked("Inter-Integrated Circuit", "interintegrated circut").correct,
          "a misspelling is not the thing being tested")
    check(not marked("Inter-Integrated Circuit", "inter process communication").correct,
          "...but a different expansion is still wrong")

    # The trap. Both answers carry identical content words; the only difference is the word a
    # stopword list throws away.
    determinism = "Free will does not exist because every action is causally determined"
    check(marked(determinism, "free will exists because we choose our actions").verdict
          != "correct",
          "stating the OPPOSITE is never marked correct — the negation check",
          marked(determinism, "free will exists because we choose our actions").why)
    check(marked(determinism,
                 "it does not exist, every action is causally determined").correct,
          "...while the same claim in his own words is")

    check(marked("V = I * R", "").verdict == "incorrect", "silence is not a pass")
    check(marked("V = I * R", "I have no idea").why.startswith("No problem"),
          "'I don't know' is answered kindly and still marked",
          marked("V = I * R", "I have no idea").why)

    # =====================================================================================
    section("3. the importer — a real PDF, in the four layouts a paper actually comes in")
    # =====================================================================================
    mcq_pdf = write_text_pdf(workspace / "math251_practice_final.pdf", MCQ_PAPER)
    report = import_pdf(mcq_pdf, write=False)
    check(len(report) == 3, "all three MCQs come out, key on a later page than the questions",
          report.sentence())
    check(all(len(i.choices) == 4 for i in report.items), "...each with its four options")
    check([i.answer for i in report.items] == ["B", "B", "A"],
          "...matched to the right answer-key letters", str([i.answer for i in report.items]))
    check(report.items[2].page == 2,
          "...and a question on page 2 is FOUND and recorded as page 2",
          f"page {report.items[2].page}")
    check(not any("Answer Key" in i.question for i in report.items),
          "the key itself is lifted out and never parsed as a question")
    check(report.items[0].subject == "Calculus",
          "the subject is read off the paper", report.items[0].subject)

    prose_pdf = write_text_pdf(workspace / "phil101_review.pdf", PROSE_PAPER)
    report = import_pdf(prose_pdf, write=False)
    check(len(report) == 2, "inline 'Answer:' questions parse", report.sentence())
    check(report.items[0].subject == "Philosophy", "...into the right subject",
          report.items[0].subject)
    check("Kant contrasts it" in report.items[0].explanation,
          "...and the paper's own EXPLANATION is captured — this is what makes 'explain that' "
          "free", report.items[0].explanation[:60])
    check(report.items[0].kind == "prose", "...and a long answer is graded as prose")

    qa_pdf = write_text_pdf(workspace / "circuits_study_guide.pdf", QA_PAPER)
    report = import_pdf(qa_pdf, write=False)
    check(len(report) == 3, "Q:/A: flashcards parse", report.sentence())
    check(any(i.answer.replace(" ", "") == "V=I*R" for i in report.items),
          "...including a formula answer", str([i.answer for i in report.items]))

    slides_pdf = write_text_pdf(workspace / "eegr105_lecture_1.pdf", SLIDES_PAPER)
    report = import_pdf(slides_pdf, write=False)
    check(len(report) == 0,
          "LECTURE SLIDES yield nothing — numbered bullets are not questions",
          f"got {len(report)}: {[i.question[:40] for i in report.items]}")
    check("no questions" in report.sentence().lower(),
          "...and he is TOLD it found nothing rather than left thinking it worked",
          report.sentence()[:80])

    check(guess_subject(Path("calc2_practice_final.pdf")) == "Calculus",
          "the filename names the subject when it can")
    check(guess_subject(Path("kant_and_mill.pdf"), "utilitarian ethics and the categorical "
                                                   "imperative in Kant") == "Philosophy",
          "...and the CONTENT names it when the filename cannot")

    # The course code, translated. LB names files by course — his vault already holds
    # EEGR105.md and POSC201.md — and a deck called "Hist110" is one he cannot ask for aloud.
    # The underscore is the point: `\b` does not match between "0" and "_", so every one of
    # these fell through to the filename until the lookahead replaced it.
    for filename, expected in (("hist110_midterm_review.pdf", "History"),
                               ("eegr105_practice.pdf", "Electrical Engineering"),
                               ("chem101_problem_set.pdf", "Chemistry"),
                               ("PHYS 205 exam.pdf", "Physics")):
        check(guess_subject(Path(filename)) == expected,
              f"{filename} is filed under {expected}", guess_subject(Path(filename)))

    # A SHORT paper's answer key. Three entries were required at first, so a two-question
    # review parsed to zero questions: its key was one line short of being believed, and both
    # questions were then dropped for having no answer.
    two = ("1. In what year did the Treaty of Westphalia end the war?\n"
           "   A) 1618\n   B) 1648\n   C) 1701\n   D) 1789\n\n"
           "2. Who was the first Consul of the French Republic?\n"
           "   A) Robespierre\n   B) Danton\n   C) Napoleon Bonaparte\n   D) Louis XVI\n\n"
           "Answer Key\n1. B\n2. C\n")
    items, _, _ = parse_questions(two, "hist110_review.pdf", "History")
    check(len(items) == 2, "a TWO-question paper's answer key is believed",
          f"got {len(items)}")
    check([i.answer for i in items] == ["B", "C"], "...and matched correctly",
          str([i.answer for i in items]))

    dropped = parse_questions("1. A question with no answer anywhere?\n"
                              "2. Another one with no answer either?\n", "x.pdf", "Test")
    check(len(dropped[0]) == 0 and dropped[1] == 2,
          "a question with NO answer is dropped, not stored blank", str(dropped[1]))

    # =====================================================================================
    section("4. the session — no repeats, a score, skipping, and the deck running out")
    # =====================================================================================
    from engine.core import Engine, QuizSession, quiz_subject_of      # noqa: PLC0415

    check(quiz_subject_of("quiz me on calculus") == "calculus", "the subject is heard")
    check(quiz_subject_of("test me on some of my philosophy material") == "philosophy",
          "...through the words that are part of the asking",
          quiz_subject_of("test me on some of my philosophy material"))
    check(quiz_subject_of("quiz me") == "", "...and 'quiz me' names none, which means all")

    import engine.core as core                                        # noqa: PLC0415
    from engine.core import AgentRoute                                # noqa: PLC0415

    # The router is pinned rather than called. `RouteDecision.destination` is the field the
    # engine reads — a stub with a `route` attribute instead sails through construction and
    # fails inside the turn, which is how the first version of this section died.
    class _Decision:
        destination = AgentRoute.QUIZ
        reasoning = "harness"

    core.router_agent = lambda q: _Decision()
    engine = Engine()

    reply = engine.ask("quiz me on calculus")
    check(engine.mode == "quiz", "asking enters quiz mode")
    check(engine.quiz.subject == "Calculus", "...scoped to the deck he named",
          str(engine.quiz.subject))
    check("Calculus" in reply.speech, "...and he is told which subject", reply.speech[:60])

    first = engine.quiz.item
    engine.ask("skip")
    check(engine.quiz.item is not None and engine.quiz.item.id != first.id,
          "'skip' moves on WITHOUT marking")
    check(engine.quiz.answered == 0, "...and a skip does not count against his score")

    # Answer whatever is ACTUALLY on the table. `pick` is random, and an earlier version of
    # this section typed one fixed answer and passed or failed depending on which of the two
    # calculus questions came up — a harness that is right half the time is worse than none.
    on_the_table = engine.quiz.item.answer
    engine.ask(on_the_table)
    check(engine.quiz.answered == 1, "an answer is marked")
    check(engine.quiz.score == 1.0, "...and the official answer, given back, scores full marks",
          f"{engine.quiz.score} for {on_the_table!r}")

    reply = engine.ask("how am I doing")
    check("1 out of 1" in reply.speech, "the score is available mid-quiz", reply.speech)
    check(engine.quiz.answered == 1, "...and asking for it is not marked as an answer")

    check("Correct" not in reply.speech or reply.speech.count("Correct") <= 1,
          "the verdict is said ONCE, not stuttered", reply.speech)

    # Round the deck. Two calculus questions, both already used, so the next answer must wrap —
    # and must SAY it is wrapping. Silently re-asking is the bug the old `random.choice` had.
    reply = engine.ask(engine.quiz.item.answer)
    check("going round again" in reply.speech.lower(),
          "running out of questions WRAPS and says so, rather than repeating in silence",
          reply.speech[:120])
    check(engine.mode == "quiz", "...and does not drop him out of the mode")

    reply = engine.ask("exit quiz")
    check(engine.mode == "normal", "the exit releases the lock")
    check("2 out of 2" in reply.speech, "...and reports the score on the way out", reply.speech)

    # The deck LB asked for does not exist. Answered, not silently widened.
    engine = Engine()
    reply = engine.ask("quiz me on underwater basket weaving")
    check(engine.mode != "quiz", "an unknown subject does NOT start a quiz on something else")
    check("basket weaving" in reply.speech.lower(), "...and says which subject it lacks",
          reply.speech[:80])
    check("Calculus" in reply.raw, "...and lists what it does have", reply.raw[:120])

    # =====================================================================================
    section("5. NO NETWORK — the whole thing again with sockets disabled")
    # =====================================================================================
    item = QuizItem(question="What is the derivative of x^2?", answer="2x", subject="Calculus",
                    explanation="Bring the exponent down and reduce it by one.")

    if probe:
        # The bug, put back: a grader that calls out on every answer. This is what the code
        # did before 2026-09-02, and section 5 exists to make sure it can never come back.
        import tools.quiz_grade as grader_module                      # noqa: PLC0415

        def _phone_home(item, given):                                 # noqa: ARG001
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return Grade("correct", 1.0, "Correct!", "remote")

        grader_module.grade = _phone_home
        core.__dict__.setdefault("_probe", True)

    real_socket = socket.socket

    class _Forbidden(socket.socket):
        def __init__(self, *args, **kwargs):                          # noqa: ARG002
            raise AssertionError("the quiz opened a socket")

    socket.socket = _Forbidden
    try:
        from tools.quiz_grade import grade as live_grade              # noqa: PLC0415

        if probe:
            import tools.quiz_grade as grader_module                  # noqa: PLC0415
            live_grade = grader_module.grade

        for official, given, label in (
                ("2x", "two x", "a symbolic answer"),
                ("B", "2x", "a multiple choice"),
                ("9.81", "9.8", "a number"),
                ("A universal moral law", "a universal moral law", "a prose answer")):
            try:
                verdict = live_grade(QuizItem(question="q", answer=official,
                                              choices={"A": "x", "B": "2x"} if official == "B"
                                              else {}), given)
                check(verdict.verdict in ("correct", "partial", "incorrect"),
                      f"marking {label} opens no socket", f"{verdict.verdict}")
            except AssertionError as exc:
                check(False, f"marking {label} opens no socket", str(exc))

        # And the explanation, which is the ONLY thing allowed to call out — served from the
        # deck's own stored working, so even that costs nothing when the paper had one.
        try:
            local = explain_locally(item, marked("2x", "x"))
            check("exponent" in local,
                  "'explain that' is answered from the PAPER's own solution, no call made",
                  local[:70])
        except AssertionError as exc:
            check(False, "'explain that' is answered locally", str(exc))

        bare = QuizItem(question="q", answer="2x")
        check(explain_locally(bare, None) == "",
              "...and a deck with NO stored working returns nothing, which is what routes him "
              "to the model — the one case LB carved out")
    finally:
        socket.socket = real_socket


    # =====================================================================================
    section("6. pacing — the quiz waits for him, and gives the microphone back")
    # 2026-09-06. LB: *"i need him to take on a different persona so he can wait for my answers
    # especially if its math i need time to answer the question."*
    #
    # Two independent mechanisms, and this section covers both:
    #   the BUDGET  — engine/turn.py raises `wait_s` while a question is on the table
    #   the COMMIT  — engine/core.py marks nothing until LB says he is ready
    #
    # The checks that matter most are the ones about giving the microphone BACK. A raised wait
    # left raised is spent on every ordinary turn that hears nothing, for the rest of the day.

    from engine.core import Engine, QuizSession                       # noqa: PLC0415
    from engine.turn import Turn                                      # noqa: PLC0415
    from agents.quiz_agent import QUIZ_PERSONA                        # noqa: PLC0415
    from agents.persona_agent import PERSONA                          # noqa: PLC0415

    item_short = QuizItem(question="What is the derivative of x squared?", answer="2x",
                          subject="Calculus", kind="short")

    class _Rec:
        """A recorder that can be told to wait, like the real one since 2026-09-06."""

        def __init__(self):
            self.wait_s, self.max_s, self.ignore_start_s = 1.5, 15.0, 0.0
            self.seen = {}

        def reset(self):
            pass

        def feed(self, _frame):
            self.seen = {"wait_s": self.wait_s, "max_s": self.max_s}
            return "captured"

    class _OldRec:
        """A stub written BEFORE `wait_s` was a property. Three harnesses contain one."""

        __slots__ = ("max_s",)

        def __init__(self):
            self.max_s = 15.0

        def reset(self):
            pass

        def feed(self, _frame):
            return "captured"

    def _turn(recorder, engine, greet=None):
        return Turn(recorder=recorder, transcriber=None, engine=engine, speaker=None,
                    bridge=None, gate=None, frames=lambda: b"f", greeting=["What's up LB?"],
                    gate_tail_s=0.0,
                    # --probe-pacing puts the old behaviour back: no budget at all.
                    quiz_wait_s=0.0 if probe_pacing else 30.0,
                    quiz_max_s=0.0 if probe_pacing else 45.0,
                    should_greet=greet)

    def _quizzing(item=item_short):
        eng = Engine(quiz_commit=True, quiz_idle_turns=4, quiz_nudge_after=3)
        eng.mode = "quiz"
        eng.quiz = QuizSession(subject="Calculus", item=item, asked=set())
        return eng

    def _boom(_frame):
        raise RuntimeError("mid-capture")

    # ---- the budget -------------------------------------------------------------------
    rec = _Rec()
    _turn(rec, _quizzing())._capture()
    check(rec.seen.get("wait_s") == 30.0,
          "a quiz question on the table raises wait_s for the capture",
          f"waited {rec.seen.get('wait_s')}s, not 1.5s")
    check(rec.seen.get("max_s") == 45.0,
          "...and raises the cap, so he can work an answer out loud",
          f"cap {rec.seen.get('max_s')}s")
    check((rec.wait_s, rec.max_s) == (1.5, 15.0),
          "BOTH are given back when the capture ends",
          f"wait_s={rec.wait_s} max_s={rec.max_s}")

    # The one that would be a silent bug: an exception out of the recorder must still restore.
    # A raised wait left raised is a microphone that sits in silence for thirty seconds after
    # every false wake for the rest of the session.
    rec = _Rec()
    rec.feed = _boom
    try:
        _turn(rec, _quizzing())._capture()
    except RuntimeError:
        pass
    check((rec.wait_s, rec.max_s) == (1.5, 15.0),
          "...and given back even when the capture RAISES — the `finally` is the point",
          f"wait_s={rec.wait_s} max_s={rec.max_s}")

    # The `verify_deafness` lesson, and the one that has actually bitten: a stub recorder
    # predating the property must not take the microphone thread down with it.
    try:
        got = _turn(_OldRec(), _quizzing())._capture()
        check(got == "captured",
              "a recorder with no settable wait_s does not crash, it just does not wait longer",
              "no AttributeError")
    except AttributeError as exc:
        check(False, "a recorder with no settable wait_s does not crash", f"raised {exc}")

    # A recorder with `wait_s` but no `max_s`. Written as ONE try block, the raise on max_s
    # lands in the same `except` that clears `was_wait_s`, so the `finally` restores nothing
    # and the wait stays at 30s for the rest of the session — the exact leak the guard exists
    # to prevent, reintroduced by the guard. Found by writing this check.
    class _WaitOnly:
        def __init__(self):
            self.wait_s = 1.5

        def reset(self):
            pass

        def feed(self, _frame):
            return "captured"

        @property
        def max_s(self):
            raise AttributeError("this stub has no max_s")

        @max_s.setter
        def max_s(self, _v):
            raise AttributeError("this stub has no max_s")

    partial = _WaitOnly()
    _turn(partial, _quizzing())._capture()
    check(partial.wait_s == 1.5,
          "a recorder that raises on max_s still gets its WAIT back — two guards, not one",
          f"wait_s={partial.wait_s}")

    rec = _Rec()
    _turn(rec, Engine())._capture()
    check(rec.seen.get("wait_s") == 1.5,
          "an ordinary turn is untouched — the budget is scoped to the quiz",
          f"waited {rec.seen.get('wait_s')}s")

    rec = _Rec()
    _turn(rec, _quizzing(item=None))._capture()
    check(rec.seen.get("wait_s") == 1.5,
          "quiz mode with an EMPTY bank gets no long wait — there is nothing to work out",
          f"waited {rec.seen.get('wait_s')}s")

    # ---- the greeting that must not fire ----------------------------------------------
    plain = Engine()
    check(_turn(_Rec(), plain, greet=lambda: True)._silence_line(1) == "What's up LB?",
          "a wake that scored well is still greeted when nothing follows it")
    check(_turn(_Rec(), plain, greet=lambda: False)._silence_line(1) is None,
          "a wake nobody meant is answered with SILENCE — None also skips the second capture",
          "the whole audible cost of a false wake")
    check(_turn(_Rec(), plain)._silence_line(1) == "What's up LB?",
          "...and a Turn built without should_greet behaves exactly as it always did")

    # ---- the commit phrase ------------------------------------------------------------
    eng = _quizzing()
    reply = eng.ask("okay so I bring the power down")
    check(not (reply.speech or "").strip(),
          "thinking out loud is answered with SILENCE — speaking would gate the mic shut",
          "empty speech")
    check(eng.quiz.answered == 0,
          "...and is not marked, which is the whole point of commit_required",
          f"answered={eng.quiz.answered}")

    eng = _quizzing()
    eng.ask("my answer is 2x")
    check(eng.quiz.answered == 1 and eng.quiz.score == 1.0,
          "'my answer is 2x' marks 2x, and marks it right", eng.quiz.tally())

    eng = _quizzing()
    eng.ask("the answer is V = I times R")
    check(eng.quiz.last_answer == "V = I times R",
          "the payload is sliced from the ORIGINAL text, so an equals sign survives",
          f"graded {eng.quiz.last_answer!r}")

    eng = _quizzing()
    said = eng.ask("ready")
    check(eng.quiz.awaiting == "answer" and eng.quiz.answered == 0,
          "a bare 'ready' marks nothing and opens the door", f"said {said.speech!r}")
    eng.ask("2x")
    check(eng.quiz.answered == 1 and eng.quiz.awaiting == "commit",
          "...and the NEXT utterance is the answer, whole", eng.quiz.tally())

    eng = _quizzing()
    eng.ask("I have no idea")
    check(eng.quiz.answered == 1,
          "'I don't know' is an ANSWER and is marked as one, commit phrase or not",
          "not left waiting for a phrase he should not have to say")

    for utterance, label in (("exit quiz", "exit"), ("how am i doing", "score"),
                             ("skip this one", "skip")):
        eng = _quizzing()
        eng.ask(utterance)
        check("thinking aloud" not in " ".join(eng.last.extras),
              f"'{utterance}' still wins over the commit gate", label)

    eng = _quizzing()
    for _ in range(2):
        eng.ask("hmm let me see")
    check("reminded" not in " ".join(eng.last.extras),
          "he does NOT interrupt after one or two mutters", "silence held")
    third = eng.ask("something like that")
    check("ready" in (third.speech or ""),
          "...but after the third he says the phrase once, so a mis-heard commit is escapable",
          (third.speech or "")[:60])

    # ---- silence, and the bound on it -------------------------------------------------
    eng = _quizzing()
    first = eng.quiz_silence_line(1)
    check(first is not None and len(first.split()) <= 4,
          "a nudge into a pause is at most four words — speaking holds the mic gate shut",
          repr(first))

    mcq = QuizItem(question="Who wrote the Categorical Imperative?", answer="B",
                   choices={"A": "Plato", "B": "Kant", "C": "Hume", "D": "Mill"},
                   subject="Philosophy", kind="mcq")
    eng = _quizzing(item=mcq)
    reread = eng.quiz_silence_line(2)
    check(bool(reread) and "Categorical" in reread,
          "the second pause re-reads the question", (reread or "")[:60])
    check(bool(reread) and not any(o in reread for o in ("Plato", "Kant", "Hume", "Mill")),
          "...stem ONLY — re-reading four options is twenty seconds he does not need",
          "no option text, and no answer, leaked")

    eng = _quizzing()
    kept = [eng.quiz_heard_nothing() for _ in range(4)]
    check(kept == [True, True, True, False],
          "four silent turns end the session — a man thinking and an empty room look alike",
          f"{kept}")
    check(eng.mode == "normal" and eng.quiz is None,
          "...and it ends through leave_quiz, the escape hatch nothing called until today",
          f"mode={eng.mode}")

    eng = _quizzing()
    eng.ask("still working on it")
    eng.quiz_heard_nothing()
    eng.ask("nearly there")
    check(eng.quiz is not None and eng.quiz.silences == 0,
          "any utterance resets the count — a man muttering is a man still there", "silences=0")

    # ---- the character ----------------------------------------------------------------
    identity = PERSONA.split("You are cheerful")[0].strip()
    check(bool(identity) and identity in QUIZ_PERSONA,
          "QUIZ_PERSONA still quotes PERSONA's identity verbatim — one ball, one description",
          "if this goes red, PERSONA changed and QUIZ_PERSONA must follow it")
    check("patient" in QUIZ_PERSONA and "Never fill it" in QUIZ_PERSONA,
          "...and adds the examiner's hat: patient, unhurried, does not fill the silence")

    # The regression this section exists to prevent as much as any bug. Prefixing a verdict on
    # top of `Grade.why` produced "Correct. Correct - B, act only on maxims..." out loud on
    # 2026-08-19. The persona frames the quiz; it never touches the marking.
    check(marked("2x", "2x").why.startswith("Correct"),
          "the marking sentence is still the grader's own, unwrapped by any persona",
          marked("2x", "2x").why[:50])
    check(marked("2x", "seventeen bananas").why.startswith("Not quite"),
          "...for a wrong answer too", marked("2x", "seventeen bananas").why[:50])
    check(marked("2x", "x").why.startswith("Part of it"),
          "...and for a partial, which is its own sentence and its own half mark",
          marked("2x", "x").why[:50])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prove the quiz marks locally and stays offline")
    ap.add_argument("--probe", action="store_true",
                    help="reintroduce the per-answer network call section 5 exists to catch")
    ap.add_argument("--probe-pacing", action="store_true",
                    help="take the quiz listen budget away again — the 1.5s wait that cut LB "
                         "off mid-calculation, which section 6 exists to catch")
    args = ap.parse_args(argv)
    return run(probe=args.probe, probe_pacing=args.probe_pacing)


if __name__ == "__main__":
    raise SystemExit(main())
