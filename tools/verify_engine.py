#!/usr/bin/env python3
"""
Module:  verify_engine.py
Purpose: Prove the switchboard — gates, the quiz lock, and the failure lines.
Author:  LB
Date:    2026-08-19

    python tools/verify_engine.py
    python tools/verify_engine.py --probe     # reintroduce the bug, expect RED

**No API calls.** The router and every agent are replaced with fakes, so this runs in
milliseconds, costs nothing against the 20/day free tier (D3), and tests the logic rather than
Google's uptime. That matters more than usual here: the things being checked are a permission
gate and a mode lock, and a harness that can only run when there is quota left is a harness
that stops being run.

## Section 3 is the one that bites

`is_yes` is the function standing between a model's proposed shell command and it executing.
Section 3 is negatives only — every way of not saying yes, each of which must leave the command
unrun. `--probe` replaces `is_yes` with `lambda _: True` (the shape it takes if somebody
"simplifies" the gate or stubs it while debugging) and drives the REAL Engine to see what
actually executes. If the harness is worth anything, every negative goes red.
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

# The engine imports agents lazily inside _dispatch, so nothing here needs a key. router.py is
# imported at module scope though, and it builds its chain at import — which needs a key to be
# present but never calls out. A dummy is enough and keeps the harness runnable in CI.
import os                                                            # noqa: E402

os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key")

import engine.core as core                                           # noqa: E402
from engine.core import Engine, _is_quiz_exit                        # noqa: E402
from engine.response import Card, CardKind, Pending, Response        # noqa: E402
from orchestrator.classify_yes import is_yes                         # noqa: E402
from router import AgentRoute                                        # noqa: E402

PASSED = 0
FAILED = 0
EXECUTED: list[str] = []          # every command a fake "ran", so the harness can assert on it


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


# --- fakes ------------------------------------------------------------------------------

class FakeDecision:
    def __init__(self, dest): self.destination, self.reasoning = dest, "harness"


def route_to(dest):
    """Pin the router to one destination, with no network."""
    core.router_agent = lambda q: FakeDecision(dest)


def fake_os_propose(query: str) -> Response:
    cmd = "cat /sys/class/thermal/thermal_zone0/temp"
    spoken = "I want to check the CPU temperature. Should I?"
    return Response(speech=spoken,
                    cards=[Card(CardKind.CODE, "Wants to run", cmd, "bash")],
                    route="os",
                    pending=Pending("os", {"command": cmd}, spoken, cmd),
                    raw=f"Proposed command:\n{cmd}")


def fake_os_resume(pending: Pending) -> Response:
    EXECUTED.append(pending.shown)
    return Response(speech="Done. The output's on the screen.",
                    cards=[Card(CardKind.LOG, "Output", "45000")], route="os",
                    raw="OS Execution Result:\n45000")


def install_os_fakes() -> None:
    """Put the fakes where `_dispatch`'s lazy imports will find them."""
    import types
    mod = types.ModuleType("agents.os_agent")
    mod.propose_os_action = fake_os_propose
    mod.resume_os_action = fake_os_resume
    sys.modules["agents.os_agent"] = mod


install_os_fakes()

# tools.memory_manager writes sd_card_memory.json on every turn. Silenced so the harness does
# not scribble in LB's real conversation log — a test that edits production data is a test
# nobody runs twice.
import types                                                          # noqa: E402

_mem = types.ModuleType("tools.memory_manager")
_mem.add_message = lambda role, content: None
_mem.check_for_backup_reminder = lambda: False
_mem.format_memory_for_llm = lambda: "No previous memory."
sys.modules["tools.memory_manager"] = _mem

# Same reasoning as the memory stub above, and it had to be learned the same way. `Engine.ask`
# records every failed turn in `vault/reflections.md`, and section 5 drives failures on purpose —
# so a plain run of this harness was appending its own deliberate 400s to LB's real ledger, where
# they would then be injected into every agent prompt as things that had "gone wrong".
#
# Redirected at MODULE scope, not inside a section, because the leak is in section 5 and in the
# os_agent hook, both of which run before any section that could set it up. Never restored: the
# process exits, and a restore here would only create a window where it could leak again.
import tempfile                                                       # noqa: E402

from tools import reflections as _reflections                         # noqa: E402

_REFLECT_TMP = Path(tempfile.mkdtemp(prefix="oddball-engine-reflections-"))
_reflections.VAULT_DIR = _REFLECT_TMP
_reflections.LEDGER = _REFLECT_TMP / "reflections.md"


# =========================================================================================
section("1. the OS gate suspends — nothing runs until asked")
# =========================================================================================

route_to(AgentRoute.OS)
eng = Engine()
EXECUTED.clear()

r = eng.ask("check the cpu temperature")
check(r.pending is not None, "a proposed command comes back as pending", f"{r.pending}")
check(EXECUTED == [], "NOTHING has executed at the point of asking", f"executed={EXECUTED}")
check(eng.pending is not None, "the engine holds the pending action across turns")
check(any(c.kind == CardKind.CODE for c in r.cards),
      "the exact command is on a card BEFORE the question is asked")
shown = [c for c in r.cards if c.kind == CardKind.CODE][0].body
check(shown == "cat /sys/class/thermal/thermal_zone0/temp",
      "the card carries the command verbatim, not a paraphrase", shown)
check(r.speech != shown, "what he SAYS is not the raw command", r.speech)
check("/sys/" not in r.speech and "cat " not in r.speech,
      "no path or command name reaches the speech channel", r.speech)

r2 = eng.ask("yes")
check(EXECUTED == ["cat /sys/class/thermal/thermal_zone0/temp"],
      "a clear yes runs it, once", f"executed={EXECUTED}")
check(eng.pending is None, "the pending action is cleared after resolving")

# =========================================================================================
section("2. the gate answer is never routed as a fresh question")
# =========================================================================================

route_to(AgentRoute.OS)
eng = Engine()
EXECUTED.clear()
eng.ask("check the cpu temperature")
routed = []
core.router_agent = lambda q: (routed.append(q), FakeDecision(AgentRoute.OS))[1]
eng.ask("no")
check(routed == [], "answering the gate does not call the router", f"routed={routed}")
check(EXECUTED == [], "and nothing ran", f"executed={EXECUTED}")

# =========================================================================================
section("3. NEGATIVES — every way of not saying yes leaves the command UNRUN")
# =========================================================================================

DECLINES = [
    ("no",                  "a flat no"),
    ("nope",                "nope"),
    ("no thanks",           "a polite refusal"),
    ("ok no",               "a no with a yes word in front of it"),
    ("nah",                 "nah"),
    ("don't",               "an apostrophised don't"),
    ("cancel",              "cancel"),
    ("stop",                "stop"),
    ("never mind",          "never mind"),
    ("forget it",           "forget it"),
    ("leave it",            "leave it"),
    ("abort",               "abort"),
    ("hmm",                 "a mumble"),
    ("what",                "a confused what"),
    ("the weather is nice", "an unrelated sentence"),
    ("",                    "silence"),
    ("   ",                 "whitespace"),
    ("maybe",               "maybe"),
    ("i guess",             "a hesitation"),
]

for text, why in DECLINES:
    route_to(AgentRoute.OS)
    eng = Engine()
    EXECUTED.clear()
    eng.ask("check the cpu temperature")
    eng.ask(text)
    check(EXECUTED == [], f"{why} does NOT run the command",
          f"EXECUTED {EXECUTED} — this is the bug" if EXECUTED else "")
    check(eng.pending is None, f"{why} also CLOSES the gate",
          "the gate is still open — the next thing said would be eaten as its answer"
          if eng.pending is not None else "")

ACCEPTS = ["yes", "yeah", "yep", "sure", "go ahead", "do it", "ok", "okay", "please do",
           "run it", "of course"]
for text in ACCEPTS:
    route_to(AgentRoute.OS)
    eng = Engine()
    EXECUTED.clear()
    eng.ask("check the cpu temperature")
    eng.ask(text)
    check(len(EXECUTED) == 1, f"{text!r} DOES run the command", f"executed={EXECUTED}")

# is_yes itself, at the unit level. "no" first is the ordering that makes "ok no" safe.
check(is_yes("ok no") is False, "'ok no' is a refusal, not consent — no is checked first")
check(is_yes("i know") is None, "'know' does not match 'no' (whole-word matching)")
check(is_yes("") is None and is_yes("mumble") is None,
      "silence and a mumble are None, which is not False but also is not yes")

# =========================================================================================
section("4. the quiz lock bypasses the router, and can always be left")
# =========================================================================================

EXITS = ["exit quiz", "quit quiz", "stop the quiz", "end the quiz", "im done", "i'm done",
         "i am done", "that's all", "thats all", "no more questions", "stop quizzing me",
         "exit", "quit", "enough", "stop", "get me out"]
for text in EXITS:
    check(_is_quiz_exit(text), f"{text!r} leaves quiz mode")

STAYS = [
    "the voltage is current times resistance",
    "i think it's about ten ohms",
    "excited electrons in the valence band",       # contains "exit" as a substring
    "the capacitor stores charge",
    "i don't know",
    "quiz me on ohms law",
]
for text in STAYS:
    check(not _is_quiz_exit(text), f"{text[:44]!r} does NOT leave quiz mode")

route_to(AgentRoute.QUIZ)
eng = Engine()
_quiz = types.ModuleType("tools.quiz_manager")
_quiz.get_random_question = lambda: {"question": "What is Ohm's Law?", "answer": "V = I * R"}
sys.modules["tools.quiz_manager"] = _quiz
_grader = types.ModuleType("agents.quiz_agent")
_grader.evaluate_quiz_answer = lambda **kw: "Correct! Voltage equals current times resistance."
sys.modules["agents.quiz_agent"] = _grader

r = eng.ask("quiz me")
check(eng.mode == "quiz", "asking to be quizzed enters quiz mode")
check("Ohm" in r.speech, "the first question is spoken", r.speech)
check(any("QUIZ MODE" in c.title for c in r.cards),
      "a visible QUIZ MODE chip goes up — a mode you cannot see is one you get stuck in")

routed = []
core.router_agent = lambda q: (routed.append(q), FakeDecision(AgentRoute.QUIZ))[1]
r = eng.ask("voltage equals current times resistance")
check(routed == [], "while locked, an answer is NOT sent to the router", f"routed={routed}")
check(eng.mode == "quiz", "and the lock holds")

r = eng.ask("exit quiz")
check(eng.mode == "normal", "the exit phrase releases the lock")

eng.mode = "quiz"
eng.leave_quiz()
check(eng.mode == "normal",
      "leave_quiz() releases it without a transcript — the escape that cannot be misheard")

# =========================================================================================
section("5. failure lines name the layer")
# =========================================================================================

from engine.core import _failure_line                                # noqa: E402


class _E(Exception):
    pass


# A 429 is not one thing, and since 2026-08-22 the two kinds say different sentences.
# Real bodies, from the measured log — the quotaId is what tells them apart.
_DAILY_429 = ("ClientError: 429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
              "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
              "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, "
              "model: gemini-3.5-flash")
_MINUTE_429 = ("ClientError: 429 RESOURCE_EXHAUSTED. quotaId: "
               "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")

check("quota exceeded for today" in _failure_line(_E(_DAILY_429)).lower(),
      "a DAILY quota ceiling says so in LB's words, not as a crash (D3)",
      _failure_line(_E(_DAILY_429)))
check("free questions" in _failure_line(_E(_DAILY_429)),
      "...and still names the number, so he knows what ran out")
check("few seconds" in _failure_line(_E(_MINUTE_429)),
      "a PER-MINUTE 429 says come back in seconds, NOT come back tomorrow",
      _failure_line(_E(_MINUTE_429)))

# A bare 429 with no quotaId is genuinely ambiguous, and the two errors are not symmetric:
# guessing "daily" also LATCHES the model out until midnight (engine/quota.py), taking him off
# the air for a day over what may have been a two-second burst. Guessing "transient" costs a
# slightly wrong sentence and a retry. So ambiguity resolves to transient, on purpose.
check("few seconds" in _failure_line(_E("429 RESOURCE_EXHAUSTED")),
      "an UNLABELLED 429 is treated as transient — the safe side of the latch",
      _failure_line(_E("429 RESOURCE_EXHAUSTED")))
check("model name" in _failure_line(_E("404 NOT_FOUND")),
      "a retired model name says so", _failure_line(_E("404 NOT_FOUND")))
check("key" in _failure_line(_E("PERMISSION_DENIED")),
      "a bad key says so", _failure_line(_E("PERMISSION_DENIED")))

route_to(AgentRoute.OS)
eng = Engine()
core.router_agent = lambda q: (_ for _ in ()).throw(_E(_DAILY_429))
r = eng.ask("check the temperature")
check("free questions" in r.speech, "a failed turn still SPEAKS something safe", r.speech)

# The latch: having been told once, the NEXT turn must not pay a round trip to be told again.
# Before LLM_MAX_RETRIES went to 0 that round trip took up to 217 seconds, during which the
# turn thread — which is also the thread draining the microphone — was blocked.
from engine import quota as _quota                                    # noqa: E402
from engine.models import AGENT_MODEL as _AM, ROUTER_MODEL as _RM     # noqa: E402

# _DAILY_429 names `model: gemini-3.5-flash`, so THAT is what may be latched.
check(_quota.exhausted(_AM),
      "a daily 429 latches the model the error actually named",
      f"latched: {_quota.status()}")
check(not _quota.exhausted(_RM),
      "and NOT the other one — AGENT_MODEL is a prefix of ROUTER_MODEL, so a substring "
      "test would have silenced both (L11)",
      f"latched: {_quota.status()}")
check(_quota.names_model("model: gemini-3.5-flash-lite", _RM)
      and not _quota.names_model("model: gemini-3.5-flash-lite", _AM),
      "a flash-lite error names flash-lite ONLY")

# With the ROUTER's model latched, the next turn must send nothing at all.
_quota.note(_RM, _DAILY_429.replace("gemini-3.5-flash", _RM))
_asked = []
core.router_agent = lambda q: _asked.append(q)
r2 = eng.ask("check the temperature again")
check(not _asked, "with the router latched, the next turn sends NO request at all",
      f"sent {len(_asked)}")
check("quota exceeded for today" in r2.speech.lower(),
      "...while still saying why", r2.speech)

# A free lookup must still work with the latch on — that is the whole point of it.
r3 = eng.ask("what time is it")
check(r3.route == "utility" and "quota" not in r3.speech.lower(),
      "the free tier still answers while the paid one is latched out", r3.speech)
_quota.clear()
check(any(c.kind == CardKind.ERROR for c in r.cards),
      "and the detail goes on an error card, not into his mouth")

# =========================================================================================
section("6. a correction is caught HERE, above the router and below the gate")
# =========================================================================================

# `tools/verify_corrections.py` proves the detector and the ledger. This section proves the one
# thing only the engine can: that a correction is intercepted at the right point in `ask()`.
# The ordering is the whole property — one place too high and it eats a permission answer, one
# place too low and it costs a Gemini call to be forgotten by the persona agent.

import shutil                                                         # noqa: E402

from tools import corrections                                         # noqa: E402

_real_ledger, _real_vault = corrections.LEDGER, corrections.VAULT_DIR
_tmp = Path(tempfile.mkdtemp(prefix="oddball-engine-corrections-"))
corrections.VAULT_DIR = _tmp
corrections.LEDGER = _tmp / "corrections.md"

# The reflection ledger was redirected at module scope — see the note beside the memory stub.
# Asserted here rather than there so the failure shows up in a section rather than at import.
check(_reflections.LEDGER.parent == _REFLECT_TMP,
      "the reflection ledger is redirected too, NOT just the corrections one",
      "section 5 drives failures on purpose; unredirected they land in LB's real ledger and "
      "are then injected into every agent prompt")

ROUTED: list[str] = []


def _counting_router(query):
    ROUTED.append(query)
    return FakeDecision(AgentRoute.PERSONA)


try:
    corrections.clear()
    core.router_agent = _counting_router
    eng = Engine()

    ROUTED.clear()
    r = eng.ask("Always use absolute paths.")
    check(r.route == "correction", "a correction is routed to 'correction'", f"got {r.route!r}")
    check(ROUTED == [], "...and the paid router was NEVER called",
          f"router saw {ROUTED}")
    check(r.pending is None, "...and no gate was opened")
    check("written that down" in r.speech.lower(),
          "he says he wrote it down", r.speech)
    rules = corrections.active_rules()
    check(len(rules) == 1 and "absolute paths" in rules[0].rule,
          "...and it really is in the ledger", f"got {[x.rule for x in rules]}")

    # The acknowledgement is spoken, so it has to survive the speech filter like anything else.
    from engine.split import is_speakable                             # noqa: E402
    check(is_speakable(r.speech) is None, "the acknowledgement is speakable", r.speech)

    # A bare rebuke has no rule, and asking is the useful answer rather than a deflection.
    r = eng.ask("That was wrong.")
    check(r.route == "correction", "a bare rebuke is a correction too")
    check("?" in r.speech, "...and he asks what he should have done instead", r.speech)
    check(ROUTED == [], "...still with no router call")

    # ---- the ordering, which is what this section is really for -------------------------

    # ABOVE the router: an ordinary question is unaffected and still routes.
    ROUTED.clear()
    r = eng.ask("tell me about operational amplifiers")
    check(ROUTED == ["tell me about operational amplifiers"],
          "an ordinary question still reaches the router", f"router saw {ROUTED}")
    check(r.route != "correction", "...and is not mistaken for a correction")

    # BELOW the gate: "don't do that" with a permission question open is a DECLINE. If the
    # correction check ran first it would swallow the answer and leave the gate open, and the
    # next thing LB said about anything at all would be consumed as its answer.
    install_os_fakes()
    route_to(AgentRoute.OS)
    EXECUTED.clear()
    eng = Engine()
    r = eng.ask("check the cpu temperature")
    check(r.pending is not None, "a gate is open")
    before = len(corrections.active_rules())
    r = eng.ask("no, don't do that")
    check(EXECUTED == [], "...'no, don't do that' does not run the command")
    check(eng.pending is None, "...and CLOSES the gate rather than leaving it open",
          "a gate that stays open eats the next thing he says, whatever it is about")
    check(r.route == "os", "...answered as a decline, not as a correction", f"got {r.route!r}")
    check(len(corrections.active_rules()) == before,
          "...and nothing was filed as a standing rule")

    # BELOW quiz mode: "wrong" is a plausible thing to say to a quiz question, and the quiz
    # owns every utterance while it is on.
    core.router_agent = _counting_router
    eng = Engine()
    eng.mode = "quiz"
    eng.quiz_item = {"question": "What is Ohm's law?", "answer": "V = I R"}
    before = len(corrections.active_rules())
    try:
        r = eng.ask("that was wrong")
        check(r.route == "quiz", "in quiz mode, 'that was wrong' stays a quiz answer",
              f"got {r.route!r}")
    except Exception as exc:                                          # noqa: BLE001
        # The quiz grader is a real agent and there is no key here; reaching it at all is the
        # property under test, and it can only be reached by NOT being intercepted.
        check("correction" not in str(exc).lower(),
              "in quiz mode, 'that was wrong' reaches the quiz grader, not the ledger",
              f"{type(exc).__name__}")
    check(len(corrections.active_rules()) == before,
          "...and nothing was filed while the quiz had the turn")
finally:
    corrections.LEDGER, corrections.VAULT_DIR = _real_ledger, _real_vault
    shutil.rmtree(_tmp, ignore_errors=True)

check(corrections.LEDGER == _real_ledger, "the real ledger path was restored afterwards")

# =========================================================================================


def probe() -> int:
    """Stub the gate open and drive the REAL Engine.

    Two populations, and the distinction is the point:

    **Spoken declines** ("no", "nah", a mumble) reach the gate and are refused by `is_yes`.
    Stub it open and every one of them must execute — that is what proves those checks are
    testing the gate rather than passing for some other reason.

    **Silence** never reaches `is_yes` at all. `Engine.ask()` declines an empty transcript
    structurally, before the gate is consulted, so stubbing `is_yes` open must NOT make it
    run. That is defence in depth, not a weak check, and counting it as one is how a real
    guarantee gets deleted for looking redundant.

    The first version of this probe did exactly that: it reported 17/19 and called the
    remaining two "not pulling their weight", when what they were actually recording was that
    silence is refused by a second, independent mechanism.
    """
    print("\n  PROBE: is_yes() -> True for everything; driving the real Engine\n")

    import orchestrator.classify_yes as cy

    spoken = [(t, w) for t, w in DECLINES if t.strip()]
    silent = [(t, w) for t, w in DECLINES if not t.strip()]

    real = cy.is_yes
    cy.is_yes = lambda transcript: True

    ran, held = 0, 0
    try:
        for text, why in spoken:
            route_to(AgentRoute.OS)
            eng_ = Engine()
            EXECUTED.clear()
            eng_.ask("check the cpu temperature")
            eng_.ask(text)
            if EXECUTED:
                ran += 1
                print(f"   RAN ANYWAY   {why:<38} on {text!r}")
            else:
                print(f"   still held   {why:<38} on {text!r}  <- NOT testing the gate")

        for text, why in silent:
            route_to(AgentRoute.OS)
            eng_ = Engine()
            EXECUTED.clear()
            eng_.ask("check the cpu temperature")
            eng_.ask(text)
            if EXECUTED:
                print(f"   LEAKED       {why:<38} silence must decline without is_yes")
            else:
                held += 1
                print(f"   held anyway  {why:<38} <- second line of defence, as designed")
    finally:
        cy.is_yes = real

    print(f"\n  spoken declines: {ran}/{len(spoken)} executed with the gate stubbed open")
    print(f"  silence:         {held}/{len(silent)} still declined, independently of is_yes")

    if ran == len(spoken) and held == len(silent):
        print("\n  The harness BITES, and silence is guarded twice over.\n")
        return 0
    if ran < len(spoken):
        print(f"\n  PARTIAL: {len(spoken) - ran} spoken decline(s) stayed green with the gate\n"
              "  removed, so those checks are passing for some other reason.\n")
        return 1
    print("\n  A silent decline executed the command. That is the gate's whole job.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the engine switchboard")
    ap.add_argument("--probe", action="store_true",
                    help="stub the gate open and confirm the negatives catch it")
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
