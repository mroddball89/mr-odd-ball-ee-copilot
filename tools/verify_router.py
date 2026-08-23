#!/usr/bin/env python3
"""
Module:  verify_router.py
Purpose: Prove the zero-token route hints name the right agent, and never claim an ambiguous one.
Author:  LB
Date:    2026-08-23

    python tools/verify_router.py
    python tools/verify_router.py --probe

Closes the Stage 8 item that had been open since the merge: *"all 9 routes reachable,
PERSONA/UTILITY don't swallow EE questions"*. There are ten routes now, and the second half of
that sentence turned out to be the whole job.

Keyless and network-free, per D7. `router_agent` is never called — where a check needs to prove
that, it rebinds the name to something that raises.

## The section that matters more than the others

Section 4. A hint that fails to match costs one router call, which is what every turn cost
yesterday. A hint that matches too much **removes an agent from the answer path** — "what's the
current time" answered by HARDWARE, "quiz me on filters" answered by ACADEMIC — and the user
never finds out, because a confident wrong answer looks exactly like a right one.

That asymmetry is why the negative corpus is longer than the positive one, and why two of the
four mutations below reinstate designs that were *specified and refused* rather than bugs that
were shipped: the bare-keyword dictionary (M3) and the naive course-code regex (M4). If those
mutations do not bite, this file is not evidence that the refusals were right.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Before importing anything that reaches engine/models.py. D7: Windows has no .env by design,
# and a harness that needs one is a harness LB cannot run where he writes the code.
os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key-but-long-enough-to-pass")

import orchestrator.instant as instant_mod                            # noqa: E402
import orchestrator.route_hint as hint_mod                            # noqa: E402
import router                                                         # noqa: E402
from engine.response import Response                                  # noqa: E402
from orchestrator.instant import Router as InstantRouter              # noqa: E402
from router import AgentRoute                                         # noqa: E402

PASSED = 0
FAILED = 0
QUIET = False           # probes re-run the checks counting reds instead of printing them


def check(ok: bool, what: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        if not QUIET:
            print(f"   PASS  {what}")
    else:
        FAILED += 1
        if not QUIET:
            print(f"   FAIL  {what}")
    if detail and not QUIET:
        print(f"           {detail}")
    return ok


def section(name: str) -> None:
    if not QUIET:
        print(f"\n  {name}")


# =========================================================================================
# 1. the hint can only ever name a real route
# =========================================================================================

def s1_enum() -> None:
    section("1. every string route_hint can return is a real AgentRoute")

    returnable = (hint_mod.ACADEMIC, hint_mod.OS)
    values = {r.value for r in AgentRoute}

    for name in returnable:
        check(name in values, f"{name!r} is an AgentRoute value",
              f"the enum holds {sorted(values)}")
        # The conversion engine/core.py actually performs. A typo here is a ValueError on a
        # live turn, which is the one failure mode a string-typed return introduces.
        try:
            AgentRoute(name)
            check(True, f"AgentRoute({name!r}) constructs")
        except ValueError as e:
            check(False, f"AgentRoute({name!r}) constructs", str(e))

    # The module must not grow a third route without this file being told about it.
    exported = {v for k, v in vars(hint_mod).items()
                if k.isupper() and isinstance(v, str) and not k.startswith("_")}
    check(exported == set(returnable),
          "the module exports exactly the routes this section checks",
          f"found {sorted(exported)}")

    # And the hint must never name a route that has no agent behind it.
    check(AgentRoute.ACADEMIC.value in values and AgentRoute.OS.value in values,
          "ACADEMIC and OS both still exist as dispatch targets")


# =========================================================================================
# 2. ACADEMIC — the turns that have exactly one destination
# =========================================================================================

ACADEMIC_YES = (
    # sync — ROUTER_PROMPT names these because they read like OS commands and are not
    "sync canvas", "sync my calendar", "update my schedule", "refresh my deadlines",
    "can you sync canvas for me", "update my calendar please",
    # policy
    "what does the syllabus say about late work",
    "whats the late policy", "when are his office hours",
    "whats the grading policy", "how is the grade weighted",
    "whats the attendance policy", "is there an extra credit policy",
    # deadlines
    "whats due tomorrow", "is anything due this week", "when is the midterm",
    "whats due", "what is due today", "when is the final",
    "whats on my schedule", "what are my upcoming deadlines",
)


def s2_academic() -> None:
    section("2. coursework turns reach ACADEMIC with no API call")
    for utterance in ACADEMIC_YES:
        got = hint_mod.look_up(utterance)
        check(got == hint_mod.ACADEMIC, f"{utterance!r} -> academic", f"got {got!r}")
    check(len(ACADEMIC_YES) >= 20, "the academic corpus is substantial",
          f"{len(ACADEMIC_YES)} utterances")


# =========================================================================================
# 3. OS — machine stats, and only about THIS machine
# =========================================================================================

OS_YES = (
    "whats the cpu temp", "what is the cpu temperature", "how hot is the pi",
    "hows the cpu usage", "whats the cpu load",
    "how much ram is free", "whats the memory usage", "how much memory do i have left",
    "how much disk space is left", "how much free space is there", "is the sd card full",
    "whats the uptime", "how long have you been up",
)


def s3_os() -> None:
    section("3. machine stats reach OS with no API call")
    for utterance in OS_YES:
        got = hint_mod.look_up(utterance)
        check(got == hint_mod.OS, f"{utterance!r} -> os", f"got {got!r}")


# =========================================================================================
# 4. THE NEGATIVES. The section this file exists for.
# =========================================================================================

# Every one of these was a rule in the specified keyword dictionary, or is a question that rule
# would have claimed. None of them has a destination a phrase list can know. D38.
MUST_REFUSE = (
    # --- the collisions inside the specified dictionary itself ---------------------------
    "whats the current time",              # `current` -> HARDWARE, and it is the clock
    "whats ohms law",                      # already FREE in the formula table; a rule makes it paid
    "whats the time constant of an rc circuit",
    "quiz me on filters",                  # QUIZ exists and is not ACADEMIC
    "test me on ohms law",                 # ditto — "test" was an ACADEMIC keyword
    "give me a quiz on capacitors",
    "reboot the raspberry pi",             # OS, not FIRMWARE
    "whats the pinout of the esp32",       # 3-4 letters + a number is not a course code
    "how do i configure spi on the stm32",
    "whats the i2c address of the bme280",
    # --- bare EE keywords that were proposed as routes -----------------------------------
    "what resistor do i need for an led",
    "how do i pick a capacitor for this filter",
    "whats the trace width for 5 amps",
    "what voltage should i run this at",
    "how much current can this transistor take",
    "can you read my kicad schematic",
    "whats on the amp board",
    # --- vault words, which name a TOOL and not a route ----------------------------------
    "remember that i used a 10k resistor on the amp board",
    "save this to my parts list",
    "whats in my inventory",
    "recall what i said about the load cell",
    # --- stats phrasing that is not about this machine -----------------------------------
    "how hot does this resistor get",
    "how hot does the esp32 get",
    "whats the cpu temp on the stm32",
    "how much memory does the atmega have",
    # --- "due" and "schedule" in their ordinary English senses ---------------------------
    "the reading was off due to the resistor tolerance",
    "is the capacitor due for replacement",
    # --- launches, which belong to launch_intent and are already free --------------------
    "how do i open a file in python",
    "is firefox installed",
    # --- ordinary conversation --------------------------------------------------------
    "tell me a joke", "how are you doing", "what do you think of capacitors",
    "whats the weather in baltimore today",
)


def s4_negatives() -> None:
    section("4. nothing ambiguous is claimed — the section that matters")
    for utterance in MUST_REFUSE:
        got = hint_mod.look_up(utterance)
        check(got is None, f"{utterance!r} is NOT claimed", f"claimed {got!r}")

    check(len(MUST_REFUSE) >= 30, "the refusal corpus is substantial",
          f"{len(MUST_REFUSE)} utterances")

    # Stated as its own check so a regression names itself rather than showing up as thirty
    # unrelated failures.
    check(hint_mod.look_up("whats the current time") is None
          and hint_mod.look_up("whats the cpu temp") == hint_mod.OS,
          "a stat PHRASE matches where a bare keyword would over-match "
          "(guards the corpus above from passing vacuously)")


# =========================================================================================
# 5. the upload guard — a new upload is ALWAYS GENERAL
# =========================================================================================

UPLOADS = (
    "i just uploaded ece350_syllabus.pdf",
    "i just uploaded my syllabus",
    "heres the syllabus for this semester",
    "i attached the syllabus",
    "just added the syllabus pdf",
    "i sent you the course syllabus",
    "uploading my syllabus now",
)


def s5_upload() -> None:
    section("5. an upload is GENERAL even when it names a syllabus and a course")
    for utterance in UPLOADS:
        got = hint_mod.look_up(utterance)
        check(got is None, f"{utterance!r} falls through to the router", f"claimed {got!r}")

    # The guard has to beat the strongest possible academic match, or it is decoration.
    check(hint_mod.look_up("whats the late policy in the syllabus") == hint_mod.ACADEMIC,
          "...while the same words WITHOUT an upload still reach academic")


# =========================================================================================
# 6. course codes come from the vault, not from a regex over the alphabet
# =========================================================================================

def s6_courses() -> None:
    section("6. course codes are read from vault/courses, so esp32 cannot be one")

    with tempfile.TemporaryDirectory() as tmp:
        courses = Path(tmp) / "courses"
        courses.mkdir()
        (courses / "ECE350.md").write_text("# ECE 350\n", encoding="utf-8")
        (courses / "POSC_100_syllabus.md").write_text("# POSC 100\n", encoding="utf-8")
        (courses / "notes.md").write_text("not a course\n", encoding="utf-8")

        real_dir, real_cache = hint_mod._COURSE_DIR, hint_mod._cache
        hint_mod._COURSE_DIR, hint_mod._cache = courses, None
        try:
            codes = hint_mod.known_courses()
            check(set(codes) == {"ece350", "ece 350", "posc100", "posc 100"},
                  "both spellings of each real course, and nothing else", str(codes))

            for utterance in ("what am i doing in ece350", "whats ece 350 about",
                              "what does posc100 cover"):
                check(hint_mod.look_up(utterance) == hint_mod.ACADEMIC,
                      f"{utterance!r} -> academic")

            # THE point of reading the filesystem instead of matching [a-z]{3,4}\\d+.
            for utterance in ("whats the pinout of the esp32", "how do i flash the stm32",
                              "whats the msp430 clock speed", "pic16 datasheet"):
                check(hint_mod.look_up(utterance) is None,
                      f"{utterance!r} is NOT a course code", str(hint_mod.look_up(utterance)))
        finally:
            hint_mod._COURSE_DIR, hint_mod._cache = real_dir, real_cache

    # Absent vault (the normal state on Windows) must disable the rule, never raise.
    real_dir, real_cache = hint_mod._COURSE_DIR, hint_mod._cache
    hint_mod._COURSE_DIR, hint_mod._cache = Path("/definitely/not/here"), None
    try:
        check(hint_mod.known_courses() == (), "no vault -> no course rules, no exception")
        check(hint_mod.look_up("whats due tomorrow") == hint_mod.ACADEMIC,
              "...and the phrase rules keep working without it")
    finally:
        hint_mod._COURSE_DIR, hint_mod._cache = real_dir, real_cache


# =========================================================================================
# 7. the social three, end-anchored before they were let in front of the router
# =========================================================================================

SOCIAL_YES = (
    ("hello", "hello"), ("hi", "hello"), ("hey", "hello"), ("hi there", "hello"),
    ("good morning", "hello"), ("hey mr odd ball", "hello"),
    ("thanks", "thanks"), ("thank you", "thanks"), ("thanks a lot", "thanks"),
    ("cheers", "thanks"), ("thanks man", "thanks"),
    ("who are you", "identity"), ("what are you", "identity"),
    ("whats your name", "identity"), ("tell me about yourself", "identity"),
)

# A greeting WORD inside a question for an agent. Before the anchor, every one of these was
# answered "Hey LB." — and once promoted past the router, that answer is the whole turn.
SOCIAL_NO = (
    "hey whats the trace width for 5 amps",
    "hi can you check the esp32 pinout",
    "thanks now whats the late policy",
    "hey whats due tomorrow",
    "good morning whats on my schedule",
    "who would you recommend for a resistor supplier",
    "what are you going to do about the noise on this rail",
    "hey can you open firefox",
    "thanks for nothing what is ohms law",
)


def s7_social() -> None:
    section("7. greetings are free, and a greeting word inside a question is not a greeting")
    r = InstantRouter()

    for utterance, want in SOCIAL_YES:
        got = r.route(utterance).intent
        check(got == want, f"{utterance!r} -> {want}", f"got {got!r}")

    for utterance in SOCIAL_NO:
        got = r.route(utterance).intent
        check(got not in {"hello", "thanks", "identity"},
              f"{utterance!r} is NOT a social intent", f"claimed {got!r}")

    # The promotion itself: these must be answerable without the router.
    from engine.core import Engine
    for name in ("hello", "thanks", "identity"):
        check(name in Engine.FREE_INTENTS, f"{name!r} is in FREE_INTENTS")
        check(name in Engine.SOCIAL_INTENTS, f"{name!r} is labelled social")
    check("formula" not in Engine.FREE_INTENTS,
          "`formula` is STILL behind the router — it claims questions belonging to MATH")


# =========================================================================================
# 8. end to end: the router is not called, and the right agent is
# =========================================================================================

def _explode(_q):
    raise AssertionError("the router was called on a turn that should have been free")


def s8_engine() -> None:
    section("8. through the real Engine: hinted turns never reach router_agent")

    import engine.core as core
    import tools.memory_manager as mem

    real_router = router.router_agent
    real_core_router = core.router_agent
    real_dispatch = core.Engine._dispatch
    real_backup = core.Engine._with_backup_reminder
    real_deadline = core.Engine._with_deadline_reminder
    real_add = mem.add_message

    seen: list[tuple[str, str]] = []

    def fake_dispatch(self, route, text, t):
        """Record where the turn went. No agent runs, so no API call can happen."""
        seen.append((route.value, text))
        return Response(speech="(agent)", route=route.value, raw="(agent)")

    core.Engine._dispatch = fake_dispatch
    core.Engine._with_backup_reminder = lambda self, r, t: r
    core.Engine._with_deadline_reminder = lambda self, r, t: r
    mem.add_message = lambda *a, **k: None

    try:
        # --- A. hinted turns: the router must not be reachable ---------------------------
        router.router_agent = _explode
        core.router_agent = _explode
        eng = core.Engine()

        for utterance, want in (("sync canvas", "academic"),
                                ("whats due tomorrow", "academic"),
                                ("whats the late policy", "academic"),
                                ("whats the cpu temp", "os"),
                                ("how much ram is free", "os")):
            seen.clear()
            r = eng.ask(utterance)
            check(r.route == want, f"{utterance!r} -> {want} with no router call",
                  f"route={r.route}")
            check(seen == [(want, utterance)], f"...and {want} is the agent that ran",
                  f"dispatched {seen}")
            check(eng.last.route_s == 0.0,
                  "...and the log shows a 0ms route leg", f"route_s={eng.last.route_s}")
            check(any(x.startswith("free route:") for x in eng.last.extras),
                  "...and the Turnlog says so", str(eng.last.extras))

        # --- B. the social three: free, and no agent runs either -------------------------
        for utterance in ("hello", "thanks", "who are you"):
            seen.clear()
            r = eng.ask(utterance)
            check(r.route == "persona", f"{utterance!r} -> persona, free",
                  f"route={r.route}")
            check(seen == [], "...and NO agent ran at all", f"dispatched {seen}")
            check(bool(r.speech), "...and he actually said something", repr(r.speech))

        # --- C. ambiguous turns still reach the paid router ------------------------------
        # The other direction, and it is not optional: a hint layer that quietly swallowed
        # everything would pass every check above.
        calls: list[str] = []

        class FakeDecision:
            def __init__(self, dest):
                self.destination, self.reasoning = dest, "harness"

        def counting_router(q):
            calls.append(q)
            return FakeDecision(AgentRoute.HARDWARE)

        router.router_agent = counting_router
        core.router_agent = counting_router

        for utterance in ("whats the trace width for 5 amps",
                          "whats the current time",
                          "quiz me on filters",
                          "remember that i used a 10k resistor",
                          "i just uploaded ece350_syllabus.pdf"):
            calls.clear()
            seen.clear()
            eng.ask(utterance)
            check(calls == [utterance], f"{utterance!r} DOES reach the paid router",
                  f"calls={calls}")
            check(eng.last.route_s > 0.0 or seen,
                  "...and is dispatched from that decision", f"dispatched {seen}")
    finally:
        router.router_agent = real_router
        core.router_agent = real_core_router
        core.Engine._dispatch = real_dispatch
        core.Engine._with_backup_reminder = real_backup
        core.Engine._with_deadline_reminder = real_deadline
        mem.add_message = real_add


def build() -> None:
    s1_enum(); s2_academic(); s3_os(); s4_negatives()
    s5_upload(); s6_courses(); s7_social(); s8_engine()


# =========================================================================================


def _rerun(groups) -> tuple[int, int]:
    """Re-run check groups with output suppressed. Returns (passed, failed)."""
    global PASSED, FAILED, QUIET
    p0, f0, QUIET = PASSED, FAILED, True
    try:
        for g in groups:
            try:
                g()
            except Exception as e:                                     # noqa: BLE001
                print(f"   (group {g.__name__} raised {type(e).__name__}: {e})")
    finally:
        QUIET = False
    passed, failed = PASSED - p0, FAILED - f0
    PASSED, FAILED = p0, f0
    return passed, failed


def probe() -> int:
    """Break each guard in turn and confirm the checks that cover it go red.

    M3 and M4 are not bugs that were shipped — they are the designs that were *specified and
    refused*. Reinstating them here is what makes the refusal evidence instead of opinion.
    """
    print("\n  PROBE: four mutations, each restoring a rejected design\n")
    results = []

    # --- M1: drop the upload guard -------------------------------------------------------
    real_markers = hint_mod._UPLOAD_MARKERS
    hint_mod._UPLOAD_MARKERS = ()
    _, failed = _rerun([s5_upload])
    hint_mod._UPLOAD_MARKERS = real_markers
    results.append(("M1 upload guard removed", failed))
    print(f"   M1  upload guard removed       -> {failed} check(s) red "
          f"({'an uploaded syllabus is never filed' if failed else 'NOT COVERED'})")

    # --- M2: put the bare social matchers back -------------------------------------------
    real_bare = instant_mod._is_bare
    instant_mod._is_bare = lambda text, phrases, filler: any(
        instant_mod._has(text, p) for p in phrases)
    _, failed = _rerun([s7_social])
    instant_mod._is_bare = real_bare
    results.append(("M2 social end-anchor removed", failed))
    print(f"   M2  social end-anchor removed  -> {failed} check(s) red "
          f"({'\"hey whats the trace width\" answers Hey LB.' if failed else 'NOT COVERED'})")

    # --- M3: THE specified keyword dictionary, exactly as proposed -----------------------
    real_look = hint_mod.look_up
    ACADEMIC_KW = ("canvas", "calendar", "schedule", "due", "deadline", "homework",
                   "assignment", "exam", "quiz", "test", "syllabus", "grading")
    HARDWARE_KW = ("resistor", "capacitor", "transistor", "led", "kicad", "schematic",
                   "pcb", "voltage", "current", "ohms law")

    def bare_keywords(text: str):
        flat = instant_mod.normalise(text)
        if any(instant_mod._has(flat, k) for k in ACADEMIC_KW):
            return hint_mod.ACADEMIC
        if any(instant_mod._has(flat, k) for k in HARDWARE_KW):
            return "hardware"
        return real_look(text)

    hint_mod.look_up = bare_keywords
    _, failed = _rerun([s4_negatives, s5_upload])
    hint_mod.look_up = real_look
    results.append(("M3 bare-keyword dictionary", failed))
    print(f"   M3  bare-keyword dictionary    -> {failed} check(s) red "
          f"({'\"the current time\" is HARDWARE, \"quiz me\" is ACADEMIC' if failed else 'NOT COVERED'})")

    # --- M4: the naive course-code regex -------------------------------------------------
    import re as _re
    _naive = _re.compile(r"(?<![a-z0-9])([a-z]{3,4})\s?(\d{2,4})(?![a-z0-9])")

    def regex_courses(text: str):
        flat = instant_mod.normalise(text)
        if _naive.search(flat):
            return hint_mod.ACADEMIC
        return real_look(text)

    hint_mod.look_up = regex_courses
    _, failed = _rerun([s4_negatives, s6_courses])
    hint_mod.look_up = real_look
    results.append(("M4 3-4 letters + a number", failed))
    print(f"   M4  naive course-code regex    -> {failed} check(s) red "
          f"({'esp32 and stm32 route to ACADEMIC' if failed else 'NOT COVERED'})")

    bitten = sum(1 for _, f in results if f > 0)
    print(f"\n  {bitten}/{len(results)} mutations bite")
    if bitten == len(results):
        print("\n  The harness BITES.\n")
        return 0
    for name, f in results:
        if f == 0:
            print(f"    NOT COVERED: {name}")
    print()
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the zero-token route hints")
    ap.add_argument("--probe", action="store_true",
                    help="restore each rejected design and confirm the checks go red")
    args = ap.parse_args()

    if args.probe:
        build()                          # populate, then discard the counts
        PASSED = FAILED = 0
        raise SystemExit(probe())

    build()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} FAILED\n")
    else:
        print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(1 if FAILED else 0)
