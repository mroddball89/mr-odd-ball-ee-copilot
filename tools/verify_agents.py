#!/usr/bin/env python3
"""
Module:  verify_agents.py
Purpose: Prove every route reaches a real agent, and that each agent's tools actually work.
Author:  LB
Date:    2026-08-19

    python tools/verify_agents.py            # no API calls, no cost
    python tools/verify_agents.py --live     # one real question per route (spends quota)

## Why this exists

`engine/core.py:_dispatch` imports agents **lazily**, inside the branch that needs them. That
is right — it keeps startup fast and lets a box without the RAG extras still run — and it means
a broken agent is invisible until the router happens to pick that route. On a headless Pi the
first sign is a spoken sentence and a traceback in a log nobody is tailing.

That is not hypothetical. On 2026-08-19 the Pi answered a filter question with *"the calculation
resulted in a ModuleNotFoundError because there is no module named 'sympy'"* — the MATH agent's
REPL sandbox was live, reachable and correctly routed, and had no maths library to import.
Everything was "connected"; the tool was empty.

So this checks three separate things, because they fail separately:

    1. every AgentRoute reaches an importable dispatch target
    2. every agent module imports, with its tools
    3. the tools can do their job — the REPL can import what the prompt tells it to use

Section 3 is the one that caught the real bug, and it is the one that would not exist if this
file only checked imports.
"""

from __future__ import annotations

import argparse
import importlib
import ast
import re
import sys
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

# Load the REAL key first, THEN fall back to a dummy.
#
# The order matters and the first version had it backwards. `load_dotenv` does not override a
# variable that is already set, so a `setdefault` before it wins permanently — the offline
# checks passed (they never call out) while `--live` failed all six routes with
# API_KEY_INVALID on a box whose key was perfectly good. The harness was testing its own
# placeholder.
from dotenv import load_dotenv                                       # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ...and if what arrived is not usable, substitute a dummy that satisfies the startup guard.
# Sections 1-4 never call out, so they must stay runnable on a box whose key is missing or
# still a placeholder — refusing to run the offline checks because the key is wrong is
# refusing to answer the question that was asked. `--live` is where a real key matters, and
# that reports the failure itself.
_k = os.environ.get("GOOGLE_API_KEY", "").strip()
if len(_k) < 20 or any(p in _k.lower() for p in ("paste", "here", "your-key", "xxx")):
    os.environ["GOOGLE_API_KEY"] = "harness-not-a-real-key-but-long-enough-to-pass"
    print("  (no usable key in .env — offline checks only; --live will fail)")

from router import AgentRoute                                        # noqa: E402

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


# Every route, and the module + callable `engine/core.py:_dispatch` reaches for it. Kept as
# DATA here so the harness fails when a route is added to the enum and not to the switchboard,
# rather than silently testing eight of nine.
ROUTE_TARGETS = {
    AgentRoute.FIRMWARE: ("agents.firmware_agent", "run_firmware_agent_response"),
    AgentRoute.HARDWARE: ("agents.hardware_agent", "run_hardware_agent"),
    AgentRoute.MATH:     ("agents.math_agent",     "run_math_agent"),
    AgentRoute.OS:       ("agents.os_agent",       "propose_os_action"),
    AgentRoute.WEB:      ("agents.web_agent",      "propose_web_search"),
    AgentRoute.QUIZ:     ("tools.quiz_manager",    "get_random_question"),
    AgentRoute.PERSONA:  ("agents.persona_agent",  "run_persona_agent"),
    AgentRoute.UTILITY:  ("orchestrator.instant",  "Router"),
    AgentRoute.ACADEMIC: ("agents.academic_agent", "run_academic_agent_response"),
    AgentRoute.SCREEN:   ("agents.screen_agent",   "propose_screen_look"),
    AgentRoute.GENERAL:  ("agents.persona_agent",  "run_persona_agent"),
}

# What the MATH agent's prompt invites the sandbox to use. The REPL runs in THIS interpreter,
# so "available to the agent" and "installed in the venv" are the same statement — and the
# prompt promising a library the venv lacks is how you get a spoken ModuleNotFoundError.
SANDBOX_LIBS = ("math", "cmath", "statistics", "numpy", "scipy", "sympy")


# =========================================================================================
section("1. every route reaches a real, importable target")
# =========================================================================================

check(set(ROUTE_TARGETS) == set(AgentRoute),
      "every AgentRoute is covered by this harness",
      f"missing: {sorted(r.value for r in set(AgentRoute) - set(ROUTE_TARGETS))}")

for route, (mod_name, attr) in ROUTE_TARGETS.items():
    try:
        mod = importlib.import_module(mod_name)
        ok = callable(getattr(mod, attr, None))
        check(ok, f"{route.value:9} -> {mod_name}.{attr}",
              "" if ok else f"{attr} missing or not callable")
    except Exception as exc:                                          # noqa: BLE001
        check(False, f"{route.value:9} -> {mod_name}.{attr}",
              f"{type(exc).__name__}: {exc}")

# And the switchboard really does name them — a target this harness imports fine but that
# _dispatch never calls is a route that answers nothing.
dispatch_src = (Path(__file__).resolve().parents[1] / "engine" / "core.py").read_text(
    encoding="utf-8")
for route, (mod_name, attr) in ROUTE_TARGETS.items():
    if route in (AgentRoute.GENERAL,):        # falls through to persona by design
        continue
    check(attr in dispatch_src or mod_name in dispatch_src,
          f"engine/core.py dispatches {route.value}", f"expected {attr}")

# =========================================================================================
section("2. each agent's own tools import")
# =========================================================================================

TOOLS = {
    "tools.trace_calculator": "calculate_ipc2221_trace_width",
    "tools.kicad_parser":     "extract_kicad_bom",
    "tools.math_sandbox":     "math_repl_tool",
    "tools.os_controller":    "execute_terminal_command",
    "tools.web_search":       "perform_web_search",
    "tools.memory_manager":   "add_message",
    "tools.quiz_manager":     "get_random_question",
    "tools.vector_db":        "get_retriever",
    "tools.academic_calendar": "get_upcoming_deadlines",
}
for mod_name, attr in TOOLS.items():
    try:
        mod = importlib.import_module(mod_name)
        check(hasattr(mod, attr), f"{mod_name}.{attr}")
    except Exception as exc:                                          # noqa: BLE001
        check(False, f"{mod_name}.{attr}", f"{type(exc).__name__}: {exc}")

# ...and that requirements.txt names everything the code imports.
#
# **This replaced a check about `stage_install.sh` on 2026-08-26, when that file was deleted
# with the rest of the Pi tooling.** The old check proved that every package in
# requirements.txt appeared in one of the installer's hand-written stages — the Pi needed the
# install split into groups so a pip resolver backtrack stayed isolated to one group, and the
# cost of hand-grouping was drift. It caught `sympy` and `kiutils` missing from every stage in
# 2026-08-21, which on a fresh Pi meant every derivative question answered
# "ModuleNotFoundError".
#
# Windows needs no staging: `pip install -r requirements.txt` resolves in one pass on a box
# with 32 GB. So that check has nothing left to check, and deleting it outright would quietly
# drop the class of bug it was FOR — a dependency that is used and not declared.
#
# This is that class of bug, caught from the other end. The old check asked "is everything
# declared also installed?"; this asks "is everything IMPORTED also declared?", which is the
# half that actually bites now: `pypdf` was relied on transitively through langchain-community
# for months before anything named it, and it would have vanished the day that package changed
# its own dependencies.
#
# Third-party imports only — the standard library and this repo's own packages are not
# dependencies. The list of local packages is read from the filesystem rather than hardcoded,
# so a new top-level package does not silently become a "missing dependency".
_root = Path(__file__).resolve().parents[1]

# BOTH files. `requirements-rag.txt` is the optional vector-store extra, and reading only the
# main file reported `langchain_chroma` — declared there, used by tools/vector_db.py — as an
# undeclared dependency. A check that cries wolf about a package that IS declared gets muted,
# and a muted check is the one that misses the real one next to it.
_reqs = "\n".join(
    (_root / name).read_text(encoding="utf-8")
    for name in ("requirements.txt", "requirements-rag.txt")
    if (_root / name).exists())

def _norm_dist(name: str) -> str:
    """pip is case-insensitive and treats `-` and `_` as the same character."""
    return name.strip().strip("\"'").lower().replace("-", "_")


_declared = set()
for _line in _reqs.splitlines():
    _bare = _line.split("#")[0].strip()
    if _bare:
        _name = re.split(r"[<>=!~\[;]", _bare)[0].strip()
        if _name:
            _declared.add(_norm_dist(_name))
        continue

    # A COMMENTED `pip install` line is still a declaration — it is this repo's convention for
    # a package that must be installed by hand, and the convention is load-bearing rather than
    # sloppy. Two packages depend on it:
    #
    #   openwakeword   installed with `--no-deps`, because it declares tflite-runtime as a
    #                  hard dependency. `--no-deps` cannot be expressed in a requirements line
    #                  at all, so the instruction is the only place it can live.
    #   openai         deliberately NOT installed: it is only for the GLM benchmark, and
    #                  requirements.txt says so in as many words. That module reports
    #                  UNAVAILABLE rather than crashing when it is absent.
    #
    # Reading these keeps the check honest in BOTH directions. Ignoring them made it red about
    # two packages whose absence is a documented decision, and a check that is red for a
    # correct state is a check that gets muted — taking the real findings next to it with it.
    _m = re.search(r"pip install\s+(?:--[\w-]+\s+)*[\"']?([A-Za-z0-9._-]+)", _line)
    if _m:
        _declared.add(_norm_dist(re.split(r"[<>=!~\[;]", _m.group(1))[0]))


# Import name != distribution name for a handful of packages. This map is the ONLY hardcoded
# part, and it is small on purpose: an entry here is a claim that two names mean one package,
# which is exactly the kind of thing that goes stale, so each one is a package this repo
# actually imports.
_IMPORT_TO_DIST = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv_python",
    "dotenv": "python_dotenv",
    "fitz": "pymupdf",
    "serial": "pyserial",
    "sklearn": "scikit_learn",
    "dateutil": "python_dateutil",
    "piper": "piper_tts",
}
# Entries are only for packages whose IMPORT name genuinely differs from their DISTRIBUTION
# name. Anything that matches after lowercasing and folding `-` to `_` must NOT be listed:
# `langchain_core` was mapped to `langchain` here and that was simply wrong — it made the
# check look for a package this repo does not use, and go red about one that was correctly
# declared two lines above it. A synonym table is a second copy of the truth; keep it minimal
# and let the normal rule do the work.

_local = {p.name for p in _root.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
_local |= {p.stem for p in _root.glob("*.py")}

# Parsed with `ast`, NOT with a regex over the source. The regex version was written first and
# it read prose: this repo's docstrings are long and explanatory, so lines like "import a
# second copy" and "import his own module" were matched, and the check reported `a` and `his`
# as undeclared dependencies. A module name is a thing the parser knows and a pattern only
# guesses at, and in a codebase whose comments outnumber its statements the guess loses.
_undeclared: set[str] = set()
for _py in sorted(_root.glob("*.py")) + sorted(_root.glob("*/*.py")):
    if "verify_" in _py.name or _py.parent.name in ("tests", "raw_downloads", "media"):
        continue
    try:
        _tree = ast.parse(_py.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    _mods: set[str] = set()
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Import):
            _mods.update(a.name.split(".")[0] for a in _node.names)
        elif isinstance(_node, ast.ImportFrom) and _node.level == 0 and _node.module:
            _mods.add(_node.module.split(".")[0])
    for _mod in _mods:
        if _mod in _local or _mod in sys.stdlib_module_names:
            continue
        _dist = _IMPORT_TO_DIST.get(_mod, _mod).lower().replace("-", "_")
        if _dist not in _declared:
            _undeclared.add(f"{_mod} (in {_py.relative_to(_root)})")

check(not _undeclared,
      "every third-party module the code imports is declared in requirements.txt",
      "" if not _undeclared else
      f"IMPORTED BUT NOT DECLARED: {', '.join(sorted(_undeclared)[:6])} — a dependency you "
      f"rely on and do not name is one that disappears when whatever pulled it changes")

# =========================================================================================
section("3. the tools can actually DO their job")
# =========================================================================================

# The IPC-2221 calculator, against a known answer.
from tools.trace_calculator import calculate_ipc2221_trace_width      # noqa: E402

out = calculate_ipc2221_trace_width.invoke(
    {"current_amps": 5.0, "temp_rise_c": 20.0, "thickness_oz": 2.0, "layer_type": "internal"})
check("92.99" in out, "trace calculator returns the IPC-2221 width for 5A/20C/2oz internal",
      out[:90])

# The KiCad reader, against a fixture with a known answer.
#
# Here for the same reason the sympy check is: `tools.kicad_parser` imports perfectly whether or
# not `kiutils` is installed — the import is wrapped, by design, so the HARDWARE agent still
# starts on a box without it. That means "the module imports" says nothing at all about whether
# the tool can read a file, and a Pi that missed one `pip install` would answer every schematic
# question with an install instruction. tools/verify_kicad.py is the full harness; this is the
# one check that belongs in the reachability sweep.
from tools.kicad_parser import extract_kicad_bom                          # noqa: E402

_fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "kicad" / "flat.kicad_sch"
out = extract_kicad_bom.invoke({"file_path": str(_fixture)})
check("8 parts" in out and "TL074" in out,
      "the KiCad reader returns the known BOM for tests/fixtures/kicad/flat.kicad_sch",
      out.splitlines()[0] if out else "(no output)")

# The REPL sandbox — and the libraries the MATH prompt tells it to use. THIS is the check that
# caught the live bug: the agent was reachable and the sandbox ran, and `import sympy` failed.
from tools.math_sandbox import math_repl_tool                         # noqa: E402

got = math_repl_tool.invoke({"query": "print(2 + 2)"})
check("4" in str(got), "the Python sandbox executes and captures print()", str(got)[:70])

for lib in SANDBOX_LIBS:
    res = str(math_repl_tool.invoke({"query": f"import {lib}; print('{lib} ok')"}))
    ok = f"{lib} ok" in res
    check(ok, f"the sandbox can import {lib}",
          "" if ok else f"MISSING — the MATH agent will answer with a ModuleNotFoundError. "
                        f"pip install {lib}")

# The instant tables, which are the UTILITY route in full.
from orchestrator.instant import Router as InstantRouter              # noqa: E402

inst = InstantRouter()
for q, why in [("what time is it", "time"), ("what does i2c stand for", "define"),
               ("convert 3.3 volts to millivolts", "convert"),
               ("what is the speed of light", "formula")]:
    r = inst.route(q)
    check(r.handled, f"UTILITY answers {q!r} with no API call", f"intent={r.intent}")

# The OS blocklist is live in the deployed tool, not just in its own harness.
#
# Both probes follow the PLATFORM, because the table does (2026-08-26). The pair used to be
# `rm -rf /` and a thermal-zone read, hardcoded — and on Windows the first of those is not a
# refusal, so this check went red while reporting a problem it did not have: the blocklist
# was live, it simply was not the Linux one. The generic version of that mistake is the whole
# subject of `tools/os_controller.py`'s docstring, and `active_table_name()` exists so the
# question can be asked properly rather than guessed at from a string.
from tools.os_controller import active_table_name, refuse              # noqa: E402

_DANGEROUS, _ORDINARY = {
    "windows": ("del /s /q C:\\", "Get-CimInstance Win32_Processor"),
    "linux": ("rm -rf /", "cat /sys/class/thermal/thermal_zone0/temp"),
}[active_table_name()]

check(refuse(_DANGEROUS) is not None,
      f"the OS blocklist is live ({active_table_name()} table)", _DANGEROUS)
check(refuse(_ORDINARY) is None,
      "and still allows the things he is actually asked to do", _ORDINARY)

# The academic calendar. This one is on the TURN PATH — `engine/core.py` checks it on every
# routed question — so "it does not raise when the file is absent" is a property the whole
# copilot depends on, not just the ACADEMIC route. A fresh clone has no calendar at all.
from tools.academic_calendar import (                                 # noqa: E402
    format_deadlines, get_upcoming_deadlines, load_calendar)

check(isinstance(load_calendar(), list),
      "the academic calendar reads (an empty list when never built)")
check(isinstance(get_upcoming_deadlines(days=3), list),
      "the deadline check returns a list and never raises on the turn path")

# And the date maths, against a synthetic entry rather than LB's real calendar — which is
# gitignored, usually absent, and would make this check pass or fail by the calendar month.
from datetime import datetime, timedelta                              # noqa: E402
import tools.academic_calendar as _cal                                # noqa: E402

_real_load = _cal.load_calendar
_soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
_late = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
_cal.load_calendar = lambda: [
    {"course": "ECE 350", "title": "Lab 4", "type": "assignment", "due_date": _soon},
    {"course": "ECE 350", "title": "Final",  "type": "exam",       "due_date": _late},
    {"course": "ECE 350", "title": "Broken", "type": "other",      "due_date": "not a date"},
]
try:
    _up = _cal.get_upcoming_deadlines(days=3)
    check(len(_up) == 1 and _up[0]["title"] == "Lab 4",
          "a 3-day window keeps what is due soon and drops what is a month out",
          f"got {[e['title'] for e in _up]}")
    check(_up[0]["days_away"] == 2, "days_away is counted in whole days",
          f"got {_up[0].get('days_away')}")
    check("Lab 4" in format_deadlines(_up) and "due in 2 days" in format_deadlines(_up),
          "the deadline card renders a readable line", format_deadlines(_up))
finally:
    _cal.load_calendar = _real_load

# Quiz data loads (creates its defaults if absent).
from tools.quiz_manager import get_random_question                    # noqa: E402

q = get_random_question()
check(isinstance(q, dict) and "question" in q and "answer" in q,
      "the quiz bank loads a question", str(q)[:80])

# =========================================================================================
section("4. the engine wires them together")
# =========================================================================================

from engine.core import Engine                                        # noqa: E402

eng = Engine()
check(eng.mode == "normal" and eng.pending is None, "a fresh Engine starts unlocked and ungated")

r = eng.ask("")
check(r.speech and "didn" in r.speech.lower(), "an empty question is handled, not routed",
      r.speech)


def live() -> int:
    """One real question per route. Spends quota — see D3."""
    print("\n  LIVE: one real question per route\n")
    from engine.core import Engine as E

    probes = [
        ("what time is it",                                   "utility"),
        ("tell me a joke about resistors",                    "persona"),
        ("what is 12 times 8",                                "utility/math"),
        ("what is the cutoff frequency of an RC filter with "
         "R equals 10 kilohms and C equals 1 microfarad",     "math"),
        ("how wide for 3 amps on 1oz external copper",        "hardware"),
        ("how do I set GPIO 13 as an output on an ESP32",     "firmware"),
        # With no syllabi ingested the right answer is "I don't know", which is a PASS — the
        # probe is checking that the route reaches the agent and the agent refuses cleanly
        # rather than inventing a due date. See the module docstring of academic_agent.py.
        ("what does my syllabus say is due this week",        "academic"),
    ]
    bad = 0
    e = E()
    for question, expected in probes:
        got = e.ask(question)
        failed = any(x.startswith("error") for x in e.last.extras)
        bad += failed
        print(f"   {'FAIL' if failed else 'ok  '}  {e.last.route or '-':9} "
              f"{e.last.total_s:5.2f}s  (want {expected})")
        print(f"           says: {got.speech[:96]}")
        for c in got.cards:
            if c.kind == "error":
                print(f"           ERROR CARD: {c.body[:130]}")
    print(f"\n  {len(probes) - bad}/{len(probes)} routes answered without error\n")
    return 1 if bad else 0


# =========================================================================================
section("the persona provider — and a key alone no longer switches it")
# =========================================================================================
#
# PERSONA is also GENERAL, and GENERAL is the ONLY route that can file an upload. So the
# property that must hold whichever provider answers is that `bind_tools` exists.
#
# **A key alone does NOT move the persona to OpenRouter, and that is a measurement.** On
# 2026-08-29 `minimax/minimax-m2.7:free` was defaulted in on the strength of its advertised
# tool support, and measured: 3/3 tool calls on a bare prompt, **0/3 on the real 8,175-char
# persona prompt**, twice claiming "I've written that down" while calling nothing. Gemini
# passed the identical prompt 1/1. So OpenRouter is opt-in BY MODEL NAME, and a candidate
# earns it through `tools/probe_persona_tools.py`, never through its own capability list.
#
# Every case sets BOTH variables explicitly. `.env` supplies ODDBALL_PERSONA_MODEL on this
# machine, and `load_dotenv` re-reads it on reload — so a case that merely UNSETS one is not
# testing what it thinks it is. That cost a red run.

import importlib                                                      # noqa: E402
import os as _os                                                      # noqa: E402

_saved = {k: _os.environ.get(k) for k in ("OPENROUTER_API_KEY", "ODDBALL_PERSONA_MODEL")}
_FAKE = "sk-or-v1-not-a-real-key-for-the-harness"

# (key, model, expected provider, why)
_CASES = [
    (_FAKE, "nvidia/nemotron-3.5-lightning:free", "openrouter",
     "a key AND a slug is the only combination that leaves Google"),
    (_FAKE, "gemini-3.5-flash-lite", "google",
     "a Gemini name wins even with a key present"),
    (None, "nvidia/nemotron-3.5-lightning:free", "google",
     "a slug with NO key falls back rather than failing"),
    (None, "gemini-3.5-flash-lite", "google", "neither: plain Gemini"),
]

try:
    import engine.models as _m
    # The module ATTRIBUTES are set directly, not the environment. Popping
    # OPENROUTER_API_KEY and reloading does not clear it: `load_dotenv(ENV_FILE)` runs at
    # import and reads it straight back out of `.env`, which on this machine has one. The
    # "no key" cases were therefore unreachable through the environment and went red against
    # code that was behaving correctly — the test was wrong, not the module.
    _real_key, _real_model = _m.OPENROUTER_API_KEY, _m.PERSONA_MODEL
    for _key, _model, _want, _why in _CASES:
        _m.OPENROUTER_API_KEY = _key or ""
        _m.PERSONA_MODEL = _model
        _got = _m.persona_provider()
        check(_got == _want,
              f"key={'yes' if _key else 'no ':3} model={_model[:34]:36} -> {_want}",
              _why if _got == _want else f"got {_got}")
        _llm = _m.build_persona_llm()
        check(hasattr(_llm, "bind_tools"),
              f"    {type(_llm).__name__} binds tools — GENERAL can still file an upload")
        if _want == "openrouter":
            check(str(getattr(_llm, "openai_api_base", "")).startswith("https://openrouter.ai"),
                  "    and points at OpenRouter, not at OpenAI",
                  str(getattr(_llm, "openai_api_base", "")))

    _m.OPENROUTER_API_KEY, _m.PERSONA_MODEL = _real_key, _real_model

    # The default, read from the SOURCE rather than the environment — `.env` cannot be unset.
    import inspect                                                    # noqa: E402
    _src = inspect.getsource(_m)
    _default = re.search(
        r'PERSONA_MODEL = os\.environ\.get\("ODDBALL_PERSONA_MODEL", "([^"]+)"\)', _src)
    check(_default is not None and "/" not in _default.group(1),
          "and the DEFAULT is a Gemini name, so a bare key changes nothing",
          f"default is {_default.group(1) if _default else 'unparseable'!r}")
finally:
    for _k, _v in _saved.items():
        if _v is None:
            _os.environ.pop(_k, None)
        else:
            _os.environ[_k] = _v
    importlib.reload(importlib.import_module("engine.models"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify every agent is reachable and working")
    ap.add_argument("--live", action="store_true",
                    help="also ask one real question per route (spends free-tier quota)")
    args = ap.parse_args()

    rc = 0
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        rc = 1
    else:
        print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")

    if args.live:
        rc = live() or rc
    raise SystemExit(rc)
