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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    "tools.math_sandbox":     "math_repl_tool",
    "tools.os_controller":    "execute_terminal_command",
    "tools.web_search":       "perform_web_search",
    "tools.memory_manager":   "add_message",
    "tools.quiz_manager":     "get_random_question",
    "tools.vector_db":        "get_retriever",
}
for mod_name, attr in TOOLS.items():
    try:
        mod = importlib.import_module(mod_name)
        check(hasattr(mod, attr), f"{mod_name}.{attr}")
    except Exception as exc:                                          # noqa: BLE001
        check(False, f"{mod_name}.{attr}", f"{type(exc).__name__}: {exc}")

# =========================================================================================
section("3. the tools can actually DO their job")
# =========================================================================================

# The IPC-2221 calculator, against a known answer.
from tools.trace_calculator import calculate_ipc2221_trace_width      # noqa: E402

out = calculate_ipc2221_trace_width.invoke(
    {"current_amps": 5.0, "temp_rise_c": 20.0, "thickness_oz": 2.0, "layer_type": "internal"})
check("92.99" in out, "trace calculator returns the IPC-2221 width for 5A/20C/2oz internal",
      out[:90])

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
from tools.os_controller import refuse                                # noqa: E402

check(refuse("rm -rf /") is not None, "the OS blocklist is live")
check(refuse("cat /sys/class/thermal/thermal_zone0/temp") is None,
      "and still allows the things he is actually asked to do")

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
