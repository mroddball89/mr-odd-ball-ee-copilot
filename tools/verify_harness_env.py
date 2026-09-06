#!/usr/bin/env python3
"""
Module:  verify_harness_env.py
Purpose: Prove a harness cannot write to LB's real conversation log, even when it forgets to ask.
Author:  LB
Date:    2026-09-04

    python tools/verify_harness_env.py
    python tools/verify_harness_env.py --probe

No audio, no model, no key. Writes nothing outside a temp directory — which is the property it
is about, so section 1 checks that claim about this file before checking anything else.

## What is actually being checked

The explicit path — a harness calls `isolate()` and gets a temp world — is easy, and it is not
the one that has failed three times. The one that keeps failing is the harness that never calls
anything, because its author never knew there was anything to call. `verify_notes.py` was that
harness in August. `verify_quiz.py` was that harness on 2026-09-02, and by 2026-09-04
`conversation_memory.json` was 60% test fixture.

So section 3 is the important one: it imports `memory_manager` in a process that merely LOOKS
like a harness, with no isolation call anywhere, and requires the resolved path to be somewhere
other than the repo's real file.

## The probe

`--probe` runs the same check with the backstop disabled (`ODDBALL_HARNESS=0`) and requires the
write to land in the repo file. Without that, section 3 would pass just as happily on a machine
where `conversation_memory.json` happened not to exist, and a guard that cannot be shown
failing is a guard nobody should trust.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from tools import harness_env                                        # noqa: E402

PASSED = 0
FAILED = 0

REAL_MEMORY = REPO / "conversation_memory.json"


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


def _run_child(script: str, env_extra: dict, argv0: str) -> dict:
    """Run `script` in a fresh interpreter and return the JSON it prints.

    A child process, not an import, because every path here is resolved AT IMPORT TIME and this
    harness has already imported `tools.memory_manager` indirectly. Re-importing it under a
    different environment inside one process would test the import cache, not the guard.

    `argv0` is what the child should believe it was invoked as — that is the entire input to
    `running_under_harness`, so it has to be controllable.
    """
    env = dict(os.environ)
    env.pop("ODDBALL_MEMORY_FILE", None)
    env.pop("ODDBALL_VAULT_DIR", None)
    env.pop("ODDBALL_HARNESS", None)
    env.update(env_extra)
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"

    tmp = Path(tempfile.mkdtemp(prefix="oddball-child-"))
    path = tmp / argv0
    path.write_text(script, encoding="utf-8")
    out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                         env=env, cwd=str(REPO), timeout=120)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:                                                     # noqa: BLE001
        return {"error": (out.stdout + out.stderr)[-400:]}


# What a harness that has never heard of any of this looks like: import, write a turn, report
# where it landed. No isolate(), no environment variable, no mention of memory anywhere.
_INNOCENT = """
import json
from tools import memory_manager
memory_manager.add_message("user", "quiz me on underwater basket weaving")
print(json.dumps({"path": memory_manager.MEMORY_FILE}))
"""

# The same thing with the write taken out. **Used for every case whose expected answer is the
# REAL file**, and the distinction is not pedantry: the first version of this harness ran
# `_INNOCENT` under the name main.py to prove a normal entry point is not guarded, and thereby
# appended "quiz me on underwater basket weaving" to LB's actual conversation log — committing,
# inside the harness for the fix, the exact defect the fix exists to prevent.
#
# A check about where a path RESOLVES has no business writing to it. That the resolved path is
# correct is the whole claim; the write added nothing and could only ever do harm.
_RESOLVE_ONLY = """
import json
from tools import memory_manager
print(json.dumps({"path": memory_manager.MEMORY_FILE}))
"""


def main() -> int:
    # =====================================================================================
    section("1. this harness is itself isolated")
    # =====================================================================================

    check(harness_env.running_under_harness(),
          "a script named verify_* is recognised as a harness",
          f"argv[0] = {Path(sys.argv[0]).name!r}")

    tmp = harness_env.isolate("selftest")
    check(str(tmp) != str(REPO), "isolate() hands back a directory outside the repo", str(tmp))
    check(os.environ["ODDBALL_MEMORY_FILE"].startswith(str(tmp)),
          "...and the conversation log points into it")
    check(os.environ["ODDBALL_VAULT_DIR"].startswith(str(tmp)),
          "...and so does the vault, so reflections and corrections follow too")
    check(harness_env.isolate("again") == tmp,
          "isolate() is idempotent",
          "a second call must not move a harness's files out from under it")

    # =====================================================================================
    section("2. an explicit path always wins")
    # =====================================================================================

    chosen = str(Path(tempfile.mkdtemp(prefix="oddball-chosen-")) / "mine.json")
    result = _run_child(_INNOCENT, {"ODDBALL_MEMORY_FILE": chosen}, "verify_child.py")
    check(result.get("path") == chosen,
          "a harness that names its own path gets exactly that path",
          f"got {result.get('path')!r}")

    # =====================================================================================
    section("3. a harness that asks for NOTHING still cannot reach the real log")
    # =====================================================================================

    before = REAL_MEMORY.read_text(encoding="utf-8") if REAL_MEMORY.exists() else ""

    result = _run_child(_INNOCENT, {}, "verify_innocent.py")
    landed = str(result.get("path", ""))
    check(landed != "" and Path(landed) != REAL_MEMORY,
          "a verify_* script that never isolates writes SOMEWHERE ELSE",
          f"landed in {landed!r}")
    check("conversation_memory.json" not in landed,
          "...and that somewhere is not the real conversation log")

    after = REAL_MEMORY.read_text(encoding="utf-8") if REAL_MEMORY.exists() else ""
    check(before == after,
          "LB's real conversation log is byte-identical afterwards",
          "this is the check the last three fixes were each supposed to make unnecessary")

    result = _run_child(_INNOCENT, {}, "measure_innocent.py")
    check("conversation_memory.json" not in str(result.get("path", "")),
          "a measure_* script is guarded the same way",
          f"landed in {result.get('path')!r}")

    # A real entry point must NOT be caught by the guard, or the assistant stops remembering.
    # `_RESOLVE_ONLY`, never `_INNOCENT` — see the comment on it. This is the one case that
    # expects the real path, so it is the one case that must not touch it.
    result = _run_child(_RESOLVE_ONLY, {}, "main.py")
    check(str(result.get("path", "")).endswith("conversation_memory.json"),
          "a normal entry point still gets the REAL log",
          f"got {result.get('path')!r} — a guard that catches main.py is worse than the leak")

    after = REAL_MEMORY.read_text(encoding="utf-8") if REAL_MEMORY.exists() else ""
    check(before == after,
          "...and the real log is STILL byte-identical after that check too",
          "the first version of this harness failed exactly here")

    # =====================================================================================
    section("4. the two paths never disagree, and a migration never runs behind an override")
    # =====================================================================================

    # `harness_memory_file()` invents a temp path. If it does not PUBLISH it, a later
    # `isolate()` setdefaults ODDBALL_MEMORY_FILE to a different temp directory — and five
    # agents import `format_memory_for_llm` at module scope, so merely importing one of them
    # is enough to trigger the first path before the harness ever calls the second.
    result = _run_child("""
import json, os
from tools import memory_manager                 # resolves through harness_memory_file()
from tools import harness_env
harness_env.isolate("late")                      # a harness isolating AFTERWARDS
print(json.dumps({"resolved": memory_manager.MEMORY_FILE,
                  "env": os.environ.get("ODDBALL_MEMORY_FILE", "")}))
""", {}, "verify_ordering.py")
    check(result.get("resolved") == result.get("env"),
          "the resolved log and ODDBALL_MEMORY_FILE agree, whichever ran first",
          f"resolved={result.get('resolved')!r} env={result.get('env')!r}")

    # `_adopt_legacy_file` derives the OLD name from the new one's parent. Pointed at a scratch
    # file in a directory that also holds a real sd_card_memory.json, an unguarded migration
    # renames LB's log onto the scratch path — and the next save_history overwrites it.
    work = Path(tempfile.mkdtemp(prefix="oddball-override-"))
    (work / "sd_card_memory.json").write_text(
        '[{"role":"user","content":"REAL TURN","timestamp":"2026-09-01T00:00:00"}]',
        encoding="utf-8")
    _run_child(_RESOLVE_ONLY, {"ODDBALL_MEMORY_FILE": str(work / "scratch.json")},
               "verify_override.py")
    legacy = work / "sd_card_memory.json"
    check(legacy.exists(), "an explicit ODDBALL_MEMORY_FILE does NOT trigger the migration",
          "the override is the input that says 'I know where the log is'")
    check(legacy.exists() and "REAL TURN" in legacy.read_text(encoding="utf-8"),
          "...and the legacy log still holds its turns")

    # =====================================================================================
    section("5. the switch works in both directions")
    # =====================================================================================

    result = _run_child(_INNOCENT, {"ODDBALL_HARNESS": "1"}, "anything_at_all.py")
    check("conversation_memory.json" not in str(result.get("path", "")),
          "ODDBALL_HARNESS=1 forces the guard on for a script with an unhelpful name")

    check(harness_env.running_under_harness() is True, "the in-process check still answers True")

    return 0 if FAILED == 0 else 1


def probe() -> int:
    """Turn the backstop off and require the leak to come back.

    Section 3 would pass on a machine where the guard did nothing but the real file happened to
    be missing, or where `add_message` silently failed. This is the negative control: with
    `ODDBALL_HARNESS=0` the innocent harness must write to the real file, and must be seen to.
    """
    print("\n  PROBE: backstop disabled — the leak must come back\n")

    if not REAL_MEMORY.exists():
        print("  cannot probe: the real log does not exist on this machine\n")
        return 1

    # On DISK, before anything is touched, and not only in memory. This is the one place in the
    # repo that deliberately writes to LB's real conversation log, and an in-process variable is
    # no use at all if the interpreter dies between the leak and the restore.
    backup = REAL_MEMORY.with_suffix(".json.probe-bak")
    backup.write_text(REAL_MEMORY.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"   backup written to {backup.name}")

    before = json.loads(REAL_MEMORY.read_text(encoding="utf-8"))
    result = _run_child(_INNOCENT, {"ODDBALL_HARNESS": "0"}, "verify_leaky.py")
    after = json.loads(REAL_MEMORY.read_text(encoding="utf-8"))

    print(f"   the innocent harness wrote to {result.get('path')!r}")
    print(f"   the real log went from {len(before)} entries to {len(after)}")

    leaked = after and after[-1].get("content", "").startswith("quiz me on underwater")
    if leaked:
        # Put it back. The probe demonstrates the leak; it does not get to keep the damage.
        REAL_MEMORY.write_text(json.dumps(before, indent=4), encoding="utf-8")
        restored = json.loads(REAL_MEMORY.read_text(encoding="utf-8"))
        print(f"   restored — {len(restored)} entries, last is "
              f"{restored[-1]['content'][:40]!r}" if restored else "   restored — empty")
        print("\n  The harness BITES: without the guard, section 3 goes red.\n")
        return 0

    print("\n  The harness is VACUOUS: disabling the guard changed nothing, so section 3 "
          "is not measuring it.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify harness isolation")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    code = main()
    print(f"\n  {PASSED} passed, {FAILED} failed\n")
    raise SystemExit(code)
