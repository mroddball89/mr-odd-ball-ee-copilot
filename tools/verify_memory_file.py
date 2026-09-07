#!/usr/bin/env python3
"""
Module:  verify_memory_file.py
Purpose: The conversation log lives under the right name, and the backup clock can actually fire.
Author:  LB
Date:    2026-09-04

    python tools/verify_memory_file.py
    python tools/verify_memory_file.py --probe

No audio, no model, no key. Every check runs against a temp copy; nothing here reads or writes
the real log.

## What is actually being checked

Two things that were both broken in the same file, and only one of them was visible.

**The name.** `sd_card_memory.json` described a Raspberry Pi that was retired on 2026-08-26.
Renaming it is trivial; not losing the file on the way is not, because a rig running the old
code recreates the old name behind you. Section 2 covers the three states that can exist on
disk — old only, new only, both — and requires the third to be reported rather than resolved,
since the only safe merge of two conversation logs is one a person looks at.

**The clock.** `check_for_backup_reminder` compared now against `history[0]["timestamp"]` — the
oldest of a ROLLING 40-turn window. On any day LB actually uses the rig that entry is hours old,
so the check asked "was the 40th-most-recent thing he said more than a fortnight ago" and the
answer was always no. It had never fired once. Measured on 2026-09-04: the file was created
2026-08-19 and was 15 days old to the day; the window put it at 1.

Section 3 is written to fail against the old implementation, which is the only way to know the
new one is doing anything: it builds a file that is genuinely old and fills it with turns from
the last five minutes.

## The probe

`--probe` reimplements the old rolling-window check and runs it against that same file. It has
to answer "no backup needed" where the real one answers "yes", or section 3 is not measuring the
difference between them.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.harness_lib import bootstrap, check, counts as _tally, section  # noqa: E402

bootstrap()


from tools import harness_env                                        # noqa: E402

_TMP = harness_env.isolate("memory-file")

from tools import memory_manager                                     # noqa: E402


def _point_at(directory: Path) -> None:
    """Rebind the module's paths to `directory`. The constants are read at import time."""
    memory_manager.MEMORY_FILE = str(directory / memory_manager.FILENAME)


def _write_log(path: Path, turns: int = 4, minutes_ago: int = 2) -> None:
    """A log whose ENTRIES are recent, whatever the file's own age is."""
    now = datetime.now()
    path.write_text(json.dumps([
        {"role": "user", "content": f"turn {i}",
         "timestamp": (now - timedelta(minutes=minutes_ago)).isoformat()}
        for i in range(turns)], indent=4), encoding="utf-8")


def _age_file(path: Path, days: int) -> None:
    """Make the acknowledgement look `days` old — the clock's other input.

    Creation time cannot be moved portably, so the age is applied through the state file, which
    `backup_clock_started` consults FIRST and which is the input LB can actually change.
    """
    old = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    memory_manager._state_path().write_text(
        json.dumps({"acknowledged": old}), encoding="utf-8")


def main() -> int:
    # =====================================================================================
    section("1. the name describes software, not a Pi that was thrown away")
    # =====================================================================================

    check(memory_manager.FILENAME == "conversation_memory.json",
          "the log is conversation_memory.json", memory_manager.FILENAME)
    check(memory_manager.LEGACY_FILENAME == "sd_card_memory.json",
          "...and the old name is still known, so it can be adopted")
    check(Path(memory_manager.MEMORY_FILE).name == memory_manager.FILENAME
          or "ODDBALL_MEMORY_FILE" in os.environ,
          "...and the log this process resolves to uses the new name",
          Path(memory_manager.MEMORY_FILE).name)
    check((REPO / memory_manager.FILENAME).exists(),
          "the repo holds a log named after a conversation")

    # NOT a check. Whether the old file is on disk depends on whether a process from before the
    # rename is still running — an environmental fact this code does not control, and a harness
    # that goes red because the assistant is up is a harness LB learns to ignore. It is still
    # worth saying out loud, because it is the one state that silently splits the log in two.
    legacy = REPO / memory_manager.LEGACY_FILENAME
    if legacy.exists():
        try:
            n = len(json.loads(legacy.read_text(encoding="utf-8")))
        except Exception:                                            # noqa: BLE001
            n = -1
        print(f"   NOTE  {memory_manager.LEGACY_FILENAME} is on disk with {n} turns in it.")
        print("           A process started before 2026-09-04 is still writing to it. Its "
              "turns are NOT in")
        print("           the new log until they are merged. Restart the rig.")

    src = (REPO / ".gitignore").read_text(encoding="utf-8")
    check(memory_manager.FILENAME in src, "the new name is gitignored — it is LB's data")
    check(memory_manager.LEGACY_FILENAME in src,
          "...and so is the old one, which a still-running old rig will recreate")

    # =====================================================================================
    section("2. adoption covers every state the disk can be in")
    # =====================================================================================

    # old only -> moved across, with its contents.
    work = Path(tempfile.mkdtemp(prefix="oddball-adopt-old-"))
    (work / memory_manager.LEGACY_FILENAME).write_text('[{"role":"user","content":"keep me"}]',
                                                       encoding="utf-8")
    memory_manager._adopt_legacy_file(str(work / memory_manager.FILENAME))
    check((work / memory_manager.FILENAME).exists(), "an old-name-only log is adopted")
    check(not (work / memory_manager.LEGACY_FILENAME).exists(), "...and the old name is gone")
    check("keep me" in (work / memory_manager.FILENAME).read_text(encoding="utf-8"),
          "...with the turns intact")

    # new only -> untouched.
    work = Path(tempfile.mkdtemp(prefix="oddball-adopt-new-"))
    (work / memory_manager.FILENAME).write_text('[{"role":"user","content":"mine"}]',
                                                encoding="utf-8")
    memory_manager._adopt_legacy_file(str(work / memory_manager.FILENAME))
    check("mine" in (work / memory_manager.FILENAME).read_text(encoding="utf-8"),
          "a new-name-only log is left alone")

    # BOTH -> the new one wins and the old one is NOT deleted. This is the state a rig still
    # running August code creates, and silently overwriting either way loses real turns.
    work = Path(tempfile.mkdtemp(prefix="oddball-adopt-both-"))
    (work / memory_manager.LEGACY_FILENAME).write_text('[{"role":"user","content":"older"}]',
                                                       encoding="utf-8")
    (work / memory_manager.FILENAME).write_text('[{"role":"user","content":"newer"}]',
                                                encoding="utf-8")
    memory_manager._adopt_legacy_file(str(work / memory_manager.FILENAME))
    check("newer" in (work / memory_manager.FILENAME).read_text(encoding="utf-8"),
          "with BOTH present the live log is not overwritten")
    check((work / memory_manager.LEGACY_FILENAME).exists(),
          "...and the old one is kept, not deleted",
          "the only safe merge of two conversation logs is one a person looks at")

    check(memory_manager._adopt_legacy_file(str(work / "nope" / "deeper" / "x.json")) is None,
          "a path that cannot exist does not raise")

    # =====================================================================================
    section("3. the backup clock measures the FILE, not the rolling window")
    # =====================================================================================

    work = Path(tempfile.mkdtemp(prefix="oddball-clock-"))
    _point_at(work)
    log = Path(memory_manager.MEMORY_FILE)

    check(memory_manager.check_for_backup_reminder() is False,
          "no log at all means nothing to back up")
    check(memory_manager.backup_clock_started() is None, "...and no clock to read")

    # The exact shape the old implementation could not see: an OLD file, RECENT turns.
    _write_log(log, turns=4, minutes_ago=2)
    _age_file(log, days=memory_manager.BACKUP_DAYS_LIMIT + 5)

    history = memory_manager.load_history()
    window_age = datetime.now() - datetime.fromisoformat(history[0]["timestamp"])
    check(window_age < timedelta(hours=1),
          "the rolling window says this log is minutes old",
          f"oldest entry is {window_age.total_seconds() / 60:.0f} minutes back")
    check(memory_manager.check_for_backup_reminder() is True,
          "...and the reminder fires anyway, because the FILE is not",
          "this is the check that never once fired before 2026-09-04")

    # Freshly acknowledged -> quiet again.
    _age_file(log, days=1)
    check(memory_manager.check_for_backup_reminder() is False,
          "a log backed up yesterday does not nag")

    # Right on the boundary.
    _age_file(log, days=memory_manager.BACKUP_DAYS_LIMIT)
    check(memory_manager.check_for_backup_reminder() is True,
          f"exactly {memory_manager.BACKUP_DAYS_LIMIT} days is due")
    _age_file(log, days=memory_manager.BACKUP_DAYS_LIMIT - 1)
    check(memory_manager.check_for_backup_reminder() is False,
          f"{memory_manager.BACKUP_DAYS_LIMIT - 1} days is not")

    # =====================================================================================
    section("4. it can be switched off, or the fix is a downgrade")
    # =====================================================================================

    _age_file(log, days=99)
    check(memory_manager.check_for_backup_reminder() is True, "an ancient log nags")
    check(memory_manager.acknowledge_backup() is True, "...acknowledging it is recorded")
    check(memory_manager.check_for_backup_reminder() is False,
          "...and it stops",
          "a correct reminder with no off switch fires on every turn forever, and then so "
          "does the next reminder LB learns to ignore")

    state = memory_manager._state_path()
    check(state.exists() and state.parent == log.parent,
          "the acknowledgement is a sidecar beside the log", state.name)
    check(json.loads(log.read_text(encoding="utf-8"))[0]["content"] == "turn 0",
          "...and the log itself is still a plain JSON list",
          "seven agents and four harnesses read it positionally; it must not become an object")

    # =====================================================================================
    section("5. the clock survives a write — and this is a TRAP for a change already planned")
    # =====================================================================================

    # `tasks/todo.md` carries an audit item: "temp-file + os.replace for both ledgers,
    # sd_card_memory.json, and the calendar". Atomic writes are right for the two ledgers.
    # Applied to the conversation log they would silently kill this clock, because os.replace
    # puts a NEW file at the path and creation time goes with it. The reminder would go back to
    # never firing, which is the exact bug this stage fixed, arriving by a different door.
    #
    # So the check is on the MECHANISM, not on the elapsed number. An earlier version compared
    # the clock before and after a write with a one-second tolerance — and passed under both
    # implementations, because the temp file it ran against was seconds old either way. It could
    # not have caught the thing it was written to catch.
    state.unlink()

    def identity(path: Path) -> tuple:
        """What must not change when a turn is written. `st_ino` is the file index on Windows
        and changes iff the file at the path was REPLACED rather than rewritten."""
        stat = path.stat()
        return (stat.st_ino, getattr(stat, "st_birthtime", stat.st_ctime))

    before_id = identity(log)
    before_clock = memory_manager.backup_clock_started()
    time.sleep(0.05)
    memory_manager.save_history(memory_manager.load_history())
    after_id = identity(log)
    after_clock = memory_manager.backup_clock_started()

    check(after_id[0] == before_id[0],
          "save_history rewrites the log IN PLACE — it does not replace it",
          "os.replace here resets creation time on every turn and the 15-day reminder can "
          "never fire again. See the atomic-write item in tasks/todo.md before changing this.")
    check(after_id[1] == before_id[1],
          "...so the file's creation time is byte-identical after a write",
          f"before {before_id[1]} after {after_id[1]}")
    check(before_clock is not None and after_clock is not None and before_clock == after_clock,
          "...and the clock reads exactly the same",
          "mtime moves on every turn; creation time is what the reminder is about")

    # The negative control, in-line: prove those checks can fail. If `identity` could not tell
    # the two write strategies apart, everything above would be decoration.
    victim = log.parent / "atomic-write-probe.json"
    victim.write_text("[]", encoding="utf-8")
    was = identity(victim)
    scratch = log.parent / "atomic-write-probe.tmp"
    scratch.write_text("[]", encoding="utf-8")
    os.replace(scratch, victim)
    check(identity(victim)[0] != was[0],
          "an os.replace on the same path IS detected by that check",
          "the check above is only evidence if this one holds")
    victim.unlink()

    # =====================================================================================
    section("6. the corrections ledger no longer carries a mis-transcription")
    # =====================================================================================

    from tools import corrections                                    # noqa: PLC0415

    ledger = REPO / "vault" / "corrections.md"
    if not ledger.exists():
        check(True, "no corrections ledger on this machine — nothing to check")
    else:
        text = ledger.read_text(encoding="utf-8")
        rules = [line for line in text.splitlines() if line.startswith("- **Rule:**")]
        check(not any("Don't make sure" in r for r in rules),
              "no rule instructs him NOT to make sure of something",
              "a garbled negation at the top of every prompt outranks everything else in it")
        check('"Don\'t make sure you can explain yourself."' in text,
              "...but the raw transcript is still on record, unedited",
              "`said` is the record of record; only the derived Rule was corrected")

    return 0 if _tally.failed == 0 else 1


def probe() -> int:
    """Reimplement the old check and require it to disagree.

    Section 3 asserts the reminder fires on an old file with recent turns. That is only evidence
    if the implementation it replaced would have said no — otherwise the check passes on any
    implementation and measures nothing.
    """
    print("\n  PROBE: the old rolling-window check, on the same file\n")

    work = Path(tempfile.mkdtemp(prefix="oddball-probe-clock-"))
    _point_at(work)
    log = Path(memory_manager.MEMORY_FILE)
    _write_log(log, turns=4, minutes_ago=2)
    _age_file(log, days=memory_manager.BACKUP_DAYS_LIMIT + 5)

    def old_check() -> bool:
        """Verbatim from memory_manager before 2026-09-04."""
        history = memory_manager.load_history()
        if not history:
            return False
        oldest = datetime.fromisoformat(history[0]["timestamp"])
        return (datetime.now() - oldest) >= timedelta(days=memory_manager.BACKUP_DAYS_LIMIT)

    was, now = old_check(), memory_manager.check_for_backup_reminder()
    print(f"   the file is {memory_manager.BACKUP_DAYS_LIMIT + 5} days past its last backup")
    print(f"   its newest turns are 2 minutes old")
    print(f"   old rolling-window check -> {was}")
    print(f"   current check            -> {now}")

    shutil.rmtree(work, ignore_errors=True)
    if now and not was:
        print("\n  The harness BITES: the old check misses exactly this case, which is every "
              "case where LB is actually using the rig.\n")
        return 0
    print("\n  The harness is VACUOUS: both implementations agree, so section 3 is not "
          "measuring the change.\n")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the conversation log's name and clock")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if args.probe:
        raise SystemExit(probe())

    code = main()
    print(f"\n  {_tally.passed} passed, {_tally.failed} failed\n")
    raise SystemExit(code)
