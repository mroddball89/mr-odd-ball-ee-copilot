#!/usr/bin/env python3
"""
Module:  verify_awareness.py
Purpose: Prove he knows what machine he is on, and that every agent is actually told.
Author:  LB
Date:    2026-08-25

    python tools/verify_awareness.py
    python tools/verify_awareness.py --probe

No audio, no model, no key. Reads `/proc` and `/sys` where they exist and asserts the honest
answer where they do not, which on LB's Windows box is most of them — D7, every harness runs on
the authoring box.

## The two properties worth the harness

**1. It never invents a number.** `tools/system_state.py`'s whole value is that he stops guessing
about himself, so a reading that could not be taken must come back as `None` and be *stated* as
unreadable. Section 1 runs on Windows, where almost nothing is readable, and requires exactly
that. An assistant that confidently reports a CPU temperature it never read is worse than one
that says it cannot see the sensor — it is the same failure `os_controller`'s "confident success"
section is about.

**2. Every agent really is told.** This is the one that would silently rot. `tools/self_context.py`
composes the block, but it only reaches an agent because `memory_manager.format_memory_for_llm()`
prepends it and all seven agents call that. Section 4 asserts the seam end to end — not that the
block *can* be built, but that the function the agents actually call returns it. Every other
check here passes just as happily with the block wired to nothing.

## Section 3 is about ORDER, and the order is the argument

Corrections are instructions; machine state is background. A model weights the top of a long
prompt, so putting the CPU temperature above LB's standing rules would spend the good position
on the thing that matters least. Truncation has to respect that too — `--probe` inverts the
order and shows what gets cut when it does.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from tools import corrections, memory_manager, reflections, self_context, system_state  # noqa: E402

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


# Both ledgers are pointed at a temp directory for the whole run. LB's real corrections must not
# appear in a test's prompt block, and a test must never write to them.
_real = {
    "c_ledger": corrections.LEDGER, "c_vault": corrections.VAULT_DIR,
    "r_ledger": reflections.LEDGER, "r_vault": reflections.VAULT_DIR,
    "memory": memory_manager.MEMORY_FILE,
}
_tmp = Path(tempfile.mkdtemp(prefix="oddball-awareness-"))
corrections.VAULT_DIR = reflections.VAULT_DIR = _tmp
corrections.LEDGER = _tmp / "corrections.md"
reflections.LEDGER = _tmp / "reflections.md"
memory_manager.MEMORY_FILE = str(_tmp / "memory.json")

check(corrections.LEDGER != _real["c_ledger"] and reflections.LEDGER != _real["r_ledger"]
      and memory_manager.MEMORY_FILE != _real["memory"],
      "the harness writes to temp files, NOT to LB's real ledgers or conversation log")

try:
    # =====================================================================================
    section("1. the machine reads itself, and NEVER invents a reading")
    # =====================================================================================

    state = system_state.read_state(force=True)
    check(state.system != "", "it knows what OS it is on", f"got {state.system!r}")
    check(state.host != "", "it knows the machine's name", f"got {state.host!r}")
    check(state.cpu_count and state.cpu_count > 0, "it knows how many cores it has")
    check(state.disk_total_gb is not None, "disk space is readable on every platform")

    on_linux = os.path.exists("/proc/meminfo")
    if on_linux:
        check(state.mem_total_mb is not None, "memory is readable on Linux")
        check(state.uptime_s is not None, "uptime is readable on Linux")
    else:
        check(state.mem_total_mb is None, "memory reads as UNKNOWN off Linux, not as zero",
              "a zero would be a number, and a wrong number is worse than a blank")
        check(state.cpu_temp_c is None, "CPU temperature reads as UNKNOWN off the Pi")

    block = system_state.for_prompt()
    check(block.strip() != "", "a state block is produced")
    if state.cpu_temp_c is None:
        check("cannot read it" in block,
              "an unreadable temperature is STATED as unreadable, not omitted",
              "an absent line reads as 'normal' to a model")
        check("degrees Celsius" not in block, "...and no temperature number is invented")
    else:
        check(f"{state.cpu_temp_c:.1f}" in block, "the real temperature is in the block")

    # Every reading that IS present must be in the block; a snapshot nobody is told about is
    # the same as no snapshot.
    check(state.host in block, "the hostname reaches the prompt")
    check("Port 8765" in block and "Port 8767" in block, "both services are reported")

    # =====================================================================================
    section("2. what he claims he can do is derived from disk, not asserted")
    # =====================================================================================

    for what, module in system_state.CAPABILITIES:
        check((REPO_ROOT / module).exists(), f"the module behind {what!r} exists", module)

    check(len(state.capabilities) == len(system_state.CAPABILITIES),
          "every capability is currently installed",
          f"{len(state.capabilities)} of {len(system_state.CAPABILITIES)}")

    # The check that gives the derivation its point: remove the file, lose the claim.
    _saved = system_state.CAPABILITIES
    system_state.CAPABILITIES = _saved + (("do something impossible", "tools/not_a_file.py"),)
    lost = system_state.read_state(force=True)
    check("do something impossible" not in lost.capabilities,
          "a capability whose module is MISSING is not claimed",
          "this is what makes the list self-maintaining rather than a list that lies")
    system_state.CAPABILITIES = _saved
    system_state.read_state(force=True)

    # The ports are stated in three places and must agree. A state block confidently reporting
    # the wrong port is a debugging session that starts from a false premise.
    with (REPO_ROOT / "config" / "oddball.toml").open("rb") as fh:
        cfg = tomllib.load(fh)
    declared = {port for port, _ in system_state.SERVICES}
    check(cfg["hud"]["port"] in declared,
          f"the rig's port from oddball.toml ({cfg['hud']['port']}) is one this module reports")
    check(cfg["hud"]["upload_port"] in declared,
          f"the upload port ({cfg['hud']['upload_port']}) is one this module reports")

    # =====================================================================================
    section("3. the preamble puts LB's rules FIRST, and truncation respects that")
    # =====================================================================================

    corrections.clear()
    reflections.clear()
    check(self_context.preamble("anything") .strip() != "",
          "with empty ledgers there is still a preamble — the machine state")
    check("STANDING CORRECTIONS" not in self_context.preamble("anything"),
          "...but no corrections heading when there are none")

    corrections.record(corrections.detect("Always use absolute paths."))
    reflections.note("os/not-installed", "open `firefox`", "not on the pinned PATH",
                     "say so instead of offering")

    block = self_context.preamble("open firefox")
    check("STANDING CORRECTIONS" in block, "the corrections block is present")
    check("PAST MISTAKES" in block, "the mistakes block is present")
    check("WHAT YOU ARE RIGHT NOW" in block, "the machine state block is present")
    check(block.index("STANDING CORRECTIONS") < block.index("PAST MISTAKES")
          < block.index("WHAT YOU ARE RIGHT NOW"),
          "ORDER: corrections, then mistakes, then state",
          "a model weights the top; the instruction has to sit above the background")
    check("Always use absolute paths" in block, "LB's rule is in it, verbatim")
    check("firefox" in block, "and the relevant past mistake is too")

    # `set_question` is the seam that makes relevance work with no agent signature changed.
    self_context.set_question("open firefox")
    check("firefox" in self_context.preamble(), "set_question() steers which mistake surfaces")
    self_context.set_question("what is the capital of France")
    reflections.note("academic", "read the ECE350 syllabus", "the note was not in the vault")
    check(self_context.current_question() == "what is the capital of France",
          "...and the question it was given is what it holds")

    # Truncation must cut the machine state, never the rules.
    _max = self_context.MAX_CHARS
    self_context.MAX_CHARS = 400
    cut = self_context.preamble("open firefox")
    check("Always use absolute paths" in cut,
          "when the block is truncated, LB's RULE survives")
    check("WHAT YOU ARE RIGHT NOW" not in cut,
          "...and the machine state is what gets dropped",
          "cutting from the end is what makes the ordering hold under pressure")
    check("truncated" in cut, "...and the cut is announced rather than silent")
    self_context.MAX_CHARS = _max

    # The kill switches. A feature that changes every prompt in the system needs an off switch
    # that is not a code edit.
    os.environ["ODDBALL_SELF_CONTEXT"] = "0"
    check(self_context.preamble("open firefox") == "",
          "ODDBALL_SELF_CONTEXT=0 turns the whole preamble off")
    del os.environ["ODDBALL_SELF_CONTEXT"]

    os.environ["ODDBALL_STATE"] = "0"
    partial = self_context.preamble("open firefox")
    check("WHAT YOU ARE RIGHT NOW" not in partial, "ODDBALL_STATE=0 drops only the state block")
    check("STANDING CORRECTIONS" in partial, "...and leaves the rules in place")
    del os.environ["ODDBALL_STATE"]

    check(self_context.preamble("open firefox") != "",
          "unset means ON — the normal state on the Pi needs no configuration")

    # =====================================================================================
    section("4. THE SEAM — every agent is actually told, via the one function they all call")
    # =====================================================================================

    # This is the check the whole feature hangs on. `format_memory_for_llm()` is what all seven
    # agents in agents/ interpolate as {chat_history}; if the block is not in ITS output, it is
    # in no prompt anywhere, and every other check in this file still passes.
    served = memory_manager.format_memory_for_llm()
    check("STANDING CORRECTIONS" in served,
          "format_memory_for_llm() carries LB's standing rules",
          "this is the ONLY path the block takes into an agent prompt")
    check("Always use absolute paths" in served, "...with the rule text itself")
    check("WHAT YOU ARE RIGHT NOW" in served, "...and the machine state")

    memory_manager.add_message("user", "what is the trace width for five amps")
    memory_manager.add_message("assistant", "About two point three millimetres.")
    served = memory_manager.format_memory_for_llm()
    check("PREVIOUS CONTEXT" in served, "the conversation log is still there")
    check("two point three millimetres" in served, "...with the messages in it")
    check(served.index("STANDING CORRECTIONS") < served.index("PREVIOUS CONTEXT"),
          "the rules come BEFORE the conversation log",
          "a standing rule must not sit underneath forty lines of last week's chat")

    # Every agent must reach this function, or it is not the seam it claims to be.
    agents = sorted((REPO_ROOT / "agents").glob("*_agent.py"))
    check(len(agents) >= 7, f"{len(agents)} agents found in agents/")
    for agent in agents:
        text = agent.read_text(encoding="utf-8")
        uses = "format_memory_for_llm" in text
        check(uses, f"{agent.name} calls format_memory_for_llm()",
              "" if uses else "this agent will NOT see LB's corrections — wire it in")

    # It must degrade, never fail. This is the function every agent depends on.
    _broken = self_context.preamble
    self_context.preamble = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        degraded = memory_manager.format_memory_for_llm()
        check("PREVIOUS CONTEXT" in degraded,
              "a broken preamble costs the BLOCK, never the conversation history",
              "this function is on every agent's path; it degrades, it does not fail")
    finally:
        self_context.preamble = _broken
finally:
    corrections.LEDGER, corrections.VAULT_DIR = _real["c_ledger"], _real["c_vault"]
    reflections.LEDGER, reflections.VAULT_DIR = _real["r_ledger"], _real["r_vault"]
    memory_manager.MEMORY_FILE = _real["memory"]
    shutil.rmtree(_tmp, ignore_errors=True)

check(corrections.LEDGER == _real["c_ledger"], "the real paths were restored afterwards")

# =========================================================================================


def probe() -> int:
    """Invert the block order and show what truncation throws away.

    The ordering looks like presentation and is not: it decides what survives when the prompt
    budget runs out. With the state block first, a truncated preamble keeps the CPU temperature
    and loses the rule LB gave — which is the failure that would never be noticed, because
    nothing errors and the answers just quietly stop obeying him.
    """
    print("\n  PROBE: machine state FIRST, rules last, then truncate\n")

    tmp = Path(tempfile.mkdtemp(prefix="oddball-probe-"))
    real = (corrections.LEDGER, corrections.VAULT_DIR, reflections.LEDGER, reflections.VAULT_DIR)
    corrections.VAULT_DIR = reflections.VAULT_DIR = tmp
    corrections.LEDGER, reflections.LEDGER = tmp / "c.md", tmp / "r.md"
    try:
        corrections.clear()
        reflections.clear()
        corrections.record(corrections.detect("Always ask me before running anything."))

        inverted = (system_state.for_prompt() + reflections.for_prompt("x")
                    + corrections.for_prompt())
        correct = corrections.for_prompt() + reflections.for_prompt("x") + system_state.for_prompt()

        budget = 400
        print(f"   truncated to {budget} characters:\n")
        print(f"   RULES FIRST (as shipped)  -> rule survives: "
              f"{'Always ask me' in correct[:budget]}")
        print(f"   STATE FIRST (the probe)   -> rule survives: "
              f"{'Always ask me' in inverted[:budget]}")

        lost = ("Always ask me" in correct[:budget]) and ("Always ask me"
                                                          not in inverted[:budget])
        print()
        if lost:
            print("   With the state first, LB's standing rule is the thing that gets cut.")
            print("   The harness BITES: section 3's ordering checks go red.\n")
            return 0
        print("   The harness is VACUOUS: the order made no difference at this budget.\n")
        return 1
    finally:
        (corrections.LEDGER, corrections.VAULT_DIR,
         reflections.LEDGER, reflections.VAULT_DIR) = real
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify operational self-awareness")
    ap.add_argument("--probe", action="store_true")
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
