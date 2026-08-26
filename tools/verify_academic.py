#!/usr/bin/env python3
"""
Module:  verify_academic.py
Purpose: Prove the ACADEMIC route answers from the calendar ALONE, and that the deadline
         banner is global, free, and silent.
Author:  LB
Date:    2026-08-21 (rewritten 2026-08-23 when the syllabus RAG was removed — D23)

    python tools/verify_academic.py            # no API calls, no key, no cost
    python tools/verify_academic.py --store    # also builds a throwaway Chroma store (slow)

## Why these three things and not others

D11 rests on three claims that are invisible from the outside. Each one fails silently:

1. **The agent reads the calendar and retrieves NOTHING.** This check used to prove that
   retrieval ran *before* generation; the property now is that retrieval does not run at all.
   A stray `get_retriever` reintroduced on this path would pull torch onto the answer path and
   ground a schedule answer in whatever prose happened to be nearest.

   It also checks the thing the removal COST: he can no longer answer a policy question, and a
   model that is not told so answers it from what universities usually do. That is D11's
   fabrication arriving through a different door, so the prompt has to forbid it explicitly and
   this section has to pin that it still does.

2. **The deadline banner is global and costs nothing.** It sits on the turn path, so if it ever
   started spending an API call it would blow D3's 20-per-day ceiling on the free lookups that
   were specifically made free. Checked by making the router raise: if anything reaches it, the
   turn was not free.

3. **The banner is never spoken.** A deadline read aloud in the middle of an unrelated answer
   is startling, and `engine/split.py` would not catch it — the card and the speech are built
   separately.

Section 4 is opt-in (`--store`) because it builds real embeddings, which is slow and pulls
torch. It proves a syllabus under `data/academic/` cannot be retrieved by the FIRMWARE agent —
and that matters MORE than it used to. The academic collection was the backstop; with it gone,
the path exclusion in `vector_db.load_pdfs` is the only thing left, and a firmware answer citing
a course outline as a datasheet is D30's failure with a citation attached.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# These ledgers are written from `Engine.ask` and `agents/os_agent.py`, so this harness writes
# to them the moment it drives a failure — even though it was written before they existed and
# does not mention them. Redirected to a temp directory BEFORE anything under `tools/` is
# imported, because both read their location at import time. tasks/lessons.md L22.
import os                                                             # noqa: E402
import tempfile                                                       # noqa: E402

os.environ.setdefault("ODDBALL_VAULT_DIR",
                      tempfile.mkdtemp(prefix="oddball-harness-vault-"))


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import os                                                            # noqa: E402

# Keyless by construction — D7: the box this is authored on has no key. Every check here
# replaces the LLM outright, so no call is ever made; the dummy only satisfies the startup
# guard in engine/models.py, which validates at import time.
from dotenv import load_dotenv                                       # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
_k = os.environ.get("GOOGLE_API_KEY", "").strip()
if len(_k) < 20 or any(p in _k.lower() for p in ("paste", "here", "your-key", "xxx")):
    os.environ["GOOGLE_API_KEY"] = "harness-not-a-real-key-but-long-enough-to-pass"

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


def _days_out(n: int) -> str:
    return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")


# =========================================================================================
section("1. the calendar reads, and the date maths is right")
# =========================================================================================

import tools.academic_calendar as cal                                # noqa: E402

check(isinstance(cal.load_calendar(), list),
      "an absent calendar reads as [] rather than raising",
      "this is the normal state of a fresh clone — see the module docstring")

_real_load = cal.load_calendar
cal.load_calendar = lambda: [
    {"course": "ECE 350", "title": "Lab 4",  "type": "assignment", "due_date": _days_out(2)},
    {"course": "ECE 350", "title": "Quiz 2", "type": "quiz",       "due_date": _days_out(0)},
    {"course": "ECE 350", "title": "Final",  "type": "exam",       "due_date": _days_out(30)},
    {"course": "ECE 350", "title": "Past",   "type": "assignment", "due_date": _days_out(-2)},
    {"course": "ECE 350", "title": "Broken", "type": "other",      "due_date": "not a date"},
]
try:
    up = cal.get_upcoming_deadlines(days=3)
    titles = [e["title"] for e in up]

    check(titles == ["Quiz 2", "Lab 4"],
          "a 3-day window keeps today and 2 days out, soonest first",
          f"got {titles}")
    check("Final" not in titles, "a deadline 30 days out does not fire")
    check("Past" not in titles, "a deadline that has passed does not fire")
    # An unparseable date must be SKIPPED, not crash the turn and not count as due now.
    check("Broken" not in titles, "an unparseable due_date is skipped, not guessed at")
    check(up[0]["days_away"] == 0 and up[1]["days_away"] == 2,
          "days_away is whole days, and today is 0",
          f"got {[e['days_away'] for e in up]}")

    # Day granularity, not timestamp: a deadline due today must still show at 4pm.
    rendered = cal.format_deadlines(up)
    check("due TODAY" in rendered and "due in 2 days" in rendered,
          "the card renders each deadline readably", rendered.replace("\n", " | "))

    prompt_ctx = cal.format_calendar_for_llm()
    check("Today's date is" in prompt_ctx and "Final" in prompt_ctx,
          "the LLM context carries today's date AND the full calendar",
          "without today's date the model cannot resolve 'this week'")
finally:
    cal.load_calendar = _real_load


# =========================================================================================
section("2. the agent reads the calendar, generates ONCE, and retrieves nothing")
# =========================================================================================

import agents.academic_agent as aa                                   # noqa: E402

events: list[str] = []
captured: dict = {}


class _LLM:
    def __init__(self, **kw):
        captured["model"] = kw.get("model")

    def bind_tools(self, tools):
        captured["bound"] = [t.name for t in tools]
        return self

    def invoke(self, prompt):
        events.append("generate")
        captured["prompt"] = prompt

        class R:
            content = ("Lab 4 is due Friday.\n"
                       "SPOKEN: Lab four is due Friday.")
            tool_calls: list = []
        return R()


_real_llm = aa.ChatGoogleGenerativeAI
_real_fmt = aa.format_calendar_for_llm

aa.ChatGoogleGenerativeAI = _LLM
aa.format_calendar_for_llm = lambda *a, **k: (events.append("read calendar"),
                                              "Today's date is 2026-08-23.\n\n"
                                              "KNOWN DEADLINES:\n"
                                              "- 2026-08-30  POSC201  Lab 4 (assignment)")[1]
try:
    resp = aa.run_academic_agent_response("when is lab 4 due")
    prompt = captured["prompt"]

    # THE check, inverted from what it used to be. It used to prove retrieval ran BEFORE
    # generation; the property now is that retrieval does not run AT ALL. A stray
    # `get_retriever` reintroduced here would cost a torch import on the answer path and
    # ground a schedule answer in whatever prose happened to be nearest.
    check(events == ["read calendar", "generate"],
          "the calendar is read, then the model is called — once, and in that order",
          f"call order was {events}")
    check(not hasattr(aa, "get_retriever"),
          "the module does not import get_retriever at all",
          "a retriever on this path is a torch import on the answer path")
    check("ACADEMIC_COLLECTION" not in open(aa.__file__, encoding="utf-8").read(),
          "and does not name the retired academic collection")

    check(captured["model"] == "gemini-3.5-flash",
          "it runs on the agent model, not the router's lite model",
          f"got {captured['model']!r}")
    check(captured.get("bound") == ["sync_canvas_calendar", "read_from_vault"],
          "both tools are bound: the Canvas sync writes dates, the vault reads policies",
          str(captured.get("bound")))

    check("KNOWN DEADLINES" in prompt and "Lab 4" in prompt,
          "the calendar is injected into the prompt")
    check("using ONLY the calendar below" in prompt and "say you do not know" in prompt,
          "the strict-grounding directive survived the rewrite, both halves")

    # Policy questions. He CAN answer them again (D25), but only by looking — the notes are
    # behind a tool call rather than in the prompt, so the failure mode is no longer inventing
    # an answer, it is REFUSING without searching. Both halves are pinned.
    check("CALL IT FIRST" in prompt and "Do not answer a policy question without looking"
          in prompt,
          "he is told to search the notes BEFORE answering a policy question")
    check("say plainly that you have no notes on that course yet" in prompt,
          "...and what to say when the search comes back empty")
    # Whitespace-flattened, because the prompt is wrapped at 100 columns and this phrase
    # straddles a line break. A literal substring check against wrapped prose passes or fails on
    # where the author happened to wrap, which is not the property being tested.
    flat = " ".join(prompt.split())
    check("Do NOT describe what such a policy usually says" in flat,
          "...and never to improvise one from convention")
    check("Never take a date out of a note" in prompt,
          "and dates come from the calendar even when a note contains one",
          "a note is a syllabus snapshot; Canvas may already have moved the date")

    # D11: the deadline check is global, so a second copy here would double it on exactly the
    # turns where LB is already talking about coursework.
    check(not any(c.title.startswith("Due") for c in resp.cards),
          "the agent does NOT append its own deadline card — that check is global")
    check(not any(c.title == "Sources" for c in resp.cards),
          "and no Sources card, since there is nothing to cite any more",
          f"cards were {[c.title for c in resp.cards]}")

    # An empty calendar must still produce the refusal, not a blank slot the model fills in.
    events.clear()
    aa.format_calendar_for_llm = lambda *a, **k: (events.append("read calendar"),
                                                  "No coursework deadlines are on file yet.")[1]
    aa.run_academic_agent("when is the midterm")
    check("No coursework deadlines are on file yet" in captured["prompt"],
          "with an empty calendar the prompt says so rather than leaving the slot blank")
    check("using ONLY the calendar below" in captured["prompt"],
          "...and the strict directive still stands, so he refuses rather than inventing")
finally:
    aa.ChatGoogleGenerativeAI = _real_llm
    aa.format_calendar_for_llm = _real_fmt


# =========================================================================================
section("3. the deadline banner is global, free, and never spoken")
# =========================================================================================

import router                                                        # noqa: E402

from engine.core import Engine                                       # noqa: E402

_real_router = router.router_agent
cal.load_calendar = lambda: [
    {"course": "ECE 350", "title": "Lab 4", "type": "assignment", "due_date": _days_out(1)}]


def _explode(_q):
    raise AssertionError("the router was called on a free turn")


router.router_agent = _explode
try:
    eng = Engine()
    # A UTILITY question — nothing to do with coursework, and answered from lookup tables.
    r = eng.ask("what time is it")

    check(r.route == "utility", "the free path still answers with no API call", f"route={r.route}")
    check(not any(x.startswith("error") for x in eng.last.extras),
          "...and nothing reached the router", str(eng.last.extras))
    check(any(c.title.startswith("Due") for c in r.cards),
          "the deadline banner appears on a NON-academic turn",
          f"cards were {[c.title for c in r.cards]}")
    check("Lab 4" not in r.speech,
          "...and never leaks into the spoken half", f"said: {r.speech!r}")
    check(any(c.kind == "error" for c in r.cards if c.title.startswith("Due")),
          "it is styled as an alert, so it is obvious on the HUD")

    # Nothing due: no card. A banner that is always there is a banner nobody reads.
    cal.load_calendar = lambda: [
        {"course": "ECE 350", "title": "Final", "type": "exam", "due_date": _days_out(40)}]
    r2 = eng.ask("what time is it")
    check(not any(c.title.startswith("Due") for c in r2.cards),
          "no banner when nothing is due inside the window",
          f"cards were {[c.title for c in r2.cards]}")

    # A broken calendar must cost the reminder, never the turn.
    def _boom(days=3):
        raise RuntimeError("calendar exploded")

    _real_up = cal.get_upcoming_deadlines
    cal.get_upcoming_deadlines = _boom
    try:
        r3 = eng.ask("what time is it")
        # The turn must still ANSWER — not merely return. A swallowed exception that produced
        # "something went wrong on my end" would pass a bool(speech) check while being the
        # exact failure this guards against.
        check(r3.route == "utility" and not any(c.kind == "error" for c in r3.cards),
              "a failing deadline check costs the reminder, not the turn",
              f"route={r3.route} cards={[c.title for c in r3.cards]} said: {r3.speech!r}")
        check(not any(c.title.startswith("Due") for c in r3.cards),
              "...and no half-built banner is left behind")
    finally:
        cal.get_upcoming_deadlines = _real_up
finally:
    router.router_agent = _real_router
    cal.load_calendar = _real_load


# =========================================================================================
def store_checks() -> int:
    """Real embeddings, real Chroma, a throwaway store. Slow, and pulls torch."""
    import shutil
    import tempfile

    print("\n  4. the two collections cannot see each other (real embeddings)\n")

    import tools.vector_db as vdb
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    tmp = Path(tempfile.mkdtemp(prefix="oddball-collections-"))
    data, academic = tmp / "data", tmp / "data" / "academic"
    (data / "sensors").mkdir(parents=True)
    academic.mkdir(parents=True)

    def make_pdf(path: Path, text: str) -> None:
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        with path.open("wb") as fh:
            writer.write(fh)

    make_pdf(data / "sensors" / "hx711.pdf",
             "The HX711 load cell amplifier uses a 24 bit sigma delta ADC on channel A.")
    # Named to trip a substring filter. A datasheet must not be dropped for its FILENAME.
    make_pdf(data / "sensors" / "academic_press_sensor.pdf",
             "The Academic Press pressure sensor outputs 4 to 20 milliamps.")
    make_pdf(academic / "ece350.pdf",
             "ECE 350 Signals and Systems. Late homework loses ten percent per day.")

    # An image-only PDF: a real page with NO text layer. Both of LB's Pi camera datasheets are
    # exactly this, and on 2026-08-21 they took the whole build down with
    # "ValueError: Expected Embeddings to be non-empty list" from inside Chroma — a message
    # about Chroma's internals for a problem entirely about the input file. It must be reported
    # and skipped, and the usable files beside it must still index.
    from pypdf import PdfWriter as _W
    _blank = _W()
    _blank.add_blank_page(width=612, height=792)
    with (data / "sensors" / "scanned_no_text.pdf").open("wb") as fh:
        _blank.write(fh)

    vdb.DATA_PATH, vdb.EXCLUDED_FROM_DATASHEETS = data, academic
    vdb.CHROMA_PATH, vdb._stores = tmp / "chroma_db", {}

    try:
        sheets = sorted(Path(d.metadata["source"]).name
                        for d in vdb.load_pdfs(data, exclude=academic))
        check(sheets == ["academic_press_sensor.pdf", "hx711.pdf", "scanned_no_text.pdf"],
              "the exclusion is by PATH — a datasheet named 'academic_*' survives it",
              f"got {sheets}")

        # A textless PDF must not take the build down, and must not silently vanish either.
        vdb.build_vector_database()
        sheet_r = vdb.get_retriever(k=3, collection=vdb.DATASHEET_COLLECTION)

        # THE check, and it matters MORE now than when there were two collections. The academic
        # collection used to be the backstop: a syllabus that leaked into the datasheet walk
        # still had a separate pool it belonged to. That pool is gone (D23), so the path
        # exclusion is the only thing standing between a course outline and a register-level
        # answer — and a firmware reply citing a syllabus as a datasheet is D30's failure with a
        # citation attached.
        q = "what is the late homework policy"
        from_sheets = [Path(d.metadata["source"]).name for d in sheet_r.invoke(q)]
        check("ece350.pdf" not in from_sheets,
              "a syllabus in data/academic/ is NOT retrievable by the firmware agent",
              f"got {from_sheets}")

        q2 = "how many bits is the load cell ADC"
        from_sheets2 = [Path(d.metadata["source"]).name for d in sheet_r.invoke(q2)]
        check("hx711.pdf" in from_sheets2, "a datasheet is retrievable from datasheets",
              f"got {from_sheets2}")

        # The retired collection must not come back by accident. Opening a name Chroma has
        # never seen creates it EMPTY rather than raising, so a stray reference would look like
        # a working retriever returning nothing — silent, and indistinguishable from an
        # un-ingested store.
        check(not hasattr(vdb, "ACADEMIC_COLLECTION"),
              "the academic collection constant is gone, not merely unused")

        # The textless file contributed nothing, and the text-bearing ones beside it still
        # indexed. Reaching this line at all is the regression check: before the fix,
        # build_vector_database() raised out of Chroma on the empty chunk list.
        check("scanned_no_text.pdf" not in from_sheets2 + from_sheets,
              "an image-only PDF contributes no chunks and does not poison the collection",
              f"got {sorted(set(from_sheets + from_sheets2))}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the ACADEMIC route and the deadline banner")
    ap.add_argument("--store", action="store_true",
                    help="also build a throwaway Chroma store to prove collection isolation "
                         "(slow; needs requirements-rag.txt)")
    args = ap.parse_args()

    if args.store:
        store_checks()

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    if not args.store:
        print("  (collection isolation not checked — rerun with --store)\n")
