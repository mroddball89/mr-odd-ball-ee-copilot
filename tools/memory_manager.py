import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

# The conversation log — the last 40 turns, injected into EVERY agent prompt as PREVIOUS
# CONTEXT by `format_memory_for_llm`.
#
# ## The name
#
# `conversation_memory.json` since 2026-09-04. It was `sd_card_memory.json`, which was accurate
# on the Pi — the whole repo lived on a removable card, and the 15-day reminder below exists
# because that card was the only copy. The Pi was retired on 2026-08-26; this is a Windows 11
# box with fixed drives and no card in it, and a filename that describes hardware nobody has is
# a filename that makes people ask what it means.
#
# `_adopt_legacy_file` moves the old one across on first run, so nothing is lost. It is meant to
# be deleted once every checkout has started once.
LEGACY_FILENAME = "sd_card_memory.json"
FILENAME = "conversation_memory.json"

# **Anchored to the repo and overridable, and it was neither until 2026-08-29.** It was the bare
# relative string "sd_card_memory.json", which is two defects wearing one coat:
#
#   1. RELATIVE, so it resolved against the working directory — one file under `python main.py`
#      and a different one under a service that starts elsewhere. `knowledge_vault.VAULT_DIR`,
#      `corrections.LEDGER`, `reflections` and `hud_bridge.HUD_DIR` were all anchored for this
#      exact reason; this one was missed.
#
#   2. NOT OVERRIDABLE, so no harness could point it anywhere else — and `tools/verify_notes.py`
#      drives a real `Engine.ask()`, which calls `add_message` on every turn. It had been
#      writing its own test utterances into LB's real conversation log: "delete my op amp
#      pinouts note", "Awaiting approval to delete...". Those were then fed to every agent as
#      things LB had recently said, and a model answering "remember the 2N3904" replied with a
#      stale line out of the log instead.
#
# That is L22 exactly — *a new persistent file makes every existing harness a writer to it* —
# arriving from the other direction: an OLD file, and a NEW harness that became a writer to it.
# `ODDBALL_MEMORY_FILE` is the same escape hatch `ODDBALL_VAULT_DIR` gives the other three.
#
# **And an escape hatch nobody knows about does not get used.** On 2026-09-04 this file was
# measured 60% test fixture again — the quiz harnesses added on 2026-09-02 drive a real
# `Engine.ask()` and never set the variable, exactly as `verify_notes.py` had not in August.
# Two harnesses fixed by hand, a third arriving by the same route, so the third fix is not
# another hand-edit: `harness_env.harness_memory_file()` asks whether this process is a harness
# at all, and a harness that never heard of any of this gets a temp file anyway.
# See `tools/harness_env.py` for why a name-based test is allowed to be crude.


def _default_memory_file() -> str:
    """Where the conversation log lives, in order of authority.

    An explicit `ODDBALL_MEMORY_FILE` always wins — a harness that names its own path is making
    a deliberate choice and this must not second-guess it. The harness backstop comes next. The
    real file is the fallback, which is what the assistant itself always gets.
    """
    explicit = os.environ.get("ODDBALL_MEMORY_FILE")
    if explicit:
        return str(Path(explicit))

    try:
        from tools.harness_env import harness_memory_file
        guard = harness_memory_file()
        if guard:
            return guard
    except Exception:                                                     # noqa: BLE001
        # A missing or broken backstop must not stop the assistant remembering anything. It
        # degrades to the behaviour that existed before it was written.
        logging.getLogger("oddball.memory").debug(
            "the harness backstop could not be consulted", exc_info=True)

    return str(Path(__file__).resolve().parents[1] / FILENAME)


def _adopt_legacy_file(path: str) -> None:
    """Move `sd_card_memory.json` to the new name, once. **Never raises.**

    Only when the new file does not exist and the old one does: adopting on top of a live file
    would overwrite real turns with older ones. When BOTH exist the old one is left alone and
    named in a warning, because the only safe merge of two conversation logs is one a person
    looks at.

    Deletable once every checkout has started under the new name.
    """
    try:
        new = Path(path)
        old = new.parent / LEGACY_FILENAME
        if not old.exists() or new.exists():
            if old.exists() and new.exists():
                logging.getLogger("oddball.memory").warning(
                    "both %s and %s exist — the old one is NOT being read; merge it by hand "
                    "if it holds turns you want", old.name, new.name)
            return
        old.rename(new)
        logging.getLogger("oddball.memory").info(
            "adopted the conversation log from %s", old.name)
    except Exception:                                                     # noqa: BLE001
        logging.getLogger("oddball.memory").debug(
            "could not adopt the legacy conversation log", exc_info=True)


MEMORY_FILE = _default_memory_file()

# **Only when nobody named the path.** `_adopt_legacy_file` derives the old name from the new
# one's PARENT, so with `ODDBALL_MEMORY_FILE` pointed at a file in the repo root it would move
# LB's real `sd_card_memory.json` — 34 of his turns, at the time of writing — onto whatever
# scratch path a harness had chosen, and the next `save_history` would overwrite it.
#
# The override is the one input that says "I know where the log is"; a migration must not run
# behind it. The harness backstop is excluded for the same reason.
if not os.environ.get("ODDBALL_MEMORY_FILE"):
    _adopt_legacy_file(MEMORY_FILE)

# How long the log may go without being copied somewhere else before he says so. Fifteen days is
# the Pi-era number and it is kept: what it measures has changed, not how often LB wants asking.
BACKUP_DAYS_LIMIT = 15

def load_history():
    """Loads the chat history from disk."""
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, 'r') as f:
        return json.load(f)

def save_history(history):
    """Saves the chat history back to disk.

    Truncates in place rather than writing a temp file and renaming, which is what keeps the
    file's creation time stable — `backup_clock_started` depends on that.
    """
    with open(MEMORY_FILE, 'w') as f:
        json.dump(history, f, indent=4)

def add_message(role: str, content: str):
    """Adds a new message with a timestamp."""
    history = load_history()
    
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })
    
    # Keep the file from getting too large (stores last 40 messages)
    if len(history) > 40:
        history = history[-40:]
        
    save_history(history)

def _state_path() -> Path:
    """Where the last acknowledgement is recorded. Beside the log, named after it.

    A sidecar rather than a field inside the log, because the log is a plain JSON *list* that
    seven agents and four harnesses read positionally. Wrapping it in an object to carry one
    timestamp would break every one of them to store a date that has nothing to do with the
    conversation.
    """
    memory = Path(MEMORY_FILE)
    return memory.with_name(memory.stem + ".backup-state.json")


def backup_clock_started() -> datetime | None:
    """When the current backup interval began. None when it cannot be determined.

    In order: the last time LB said he had backed it up, then the log file's own creation time.

    **Creation time, not modification time.** `save_history` rewrites the file on every single
    turn, so mtime is always "seconds ago" and a reminder built on it could never fire. The file
    is truncated in place rather than replaced, so its creation time survives every write and is
    the honest answer to "how long has this been accumulating".
    """
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        return datetime.fromisoformat(state["acknowledged"])
    except Exception:                                                     # noqa: BLE001
        pass

    try:
        stat = Path(MEMORY_FILE).stat()
        # st_birthtime where the platform has it; st_ctime IS creation time on Windows, which is
        # the only platform this repo now runs on. On Linux st_ctime is the inode-change time —
        # close enough to be a useful lower bound, and wrong in the safe direction (it under-
        # reports age, so it nags late rather than early).
        return datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime))
    except OSError:
        return None


def check_for_backup_reminder() -> bool:
    """True when the conversation log has gone `BACKUP_DAYS_LIMIT` days without being copied off.

    ## What this used to measure, and why it could never fire

    It compared now against `history[0]["timestamp"]` — the oldest message in the log. But the
    log is a **rolling window of 40 turns**, so on any day LB actually talks to him that oldest
    message is hours old, not days. The reminder was asking "has the 40th-most-recent thing he
    said been said more than a fortnight ago", which on a used system is always no.

    Measured on 2026-09-04: the file was created 2026-08-19 and was **15 days old to the day**,
    and the rolling window put it at 1 day. The check had never once fired.

    It now measures the file. `backup_clock_started` says from when.
    """
    started = backup_clock_started()
    if started is None:
        # No file, or a stat that failed. Nothing has accumulated, so there is nothing to lose.
        return False
    return (datetime.now() - started) >= timedelta(days=BACKUP_DAYS_LIMIT)


def acknowledge_backup() -> bool:
    """Restart the 15-day clock. Returns True if it was written.

    **Without this the fix is a downgrade.** The old check could never fire; a correct one fires
    on every turn from the moment it comes true, forever, because nothing in the system can see
    LB copy a file to a drive. A reminder with no off switch gets ignored, and then so does the
    next reminder.

    Not wired to a voice intent, deliberately — "I backed it up" is a sentence `base.en` would
    have to get right to silence a data-loss warning, and the cost of a false positive there is
    the warning going quiet while the backup has not happened. It is a typed command:

        python tools/memory_manager.py --backed-up
    """
    try:
        _state_path().write_text(
            json.dumps({"acknowledged": datetime.now().isoformat(timespec="seconds")}, indent=4),
            encoding="utf-8")
        return True
    except OSError:
        logging.getLogger("oddball.memory").exception("could not record the backup")
        return False

def format_memory_for_llm() -> str:
    """Formats the history so the LLM can read it as context.

    Also carries the self-context block — LB's standing corrections, past mistakes, and the
    machine's current state. **This is the one place shared context enters an agent prompt.**

    That is not a tidy place to have put it; it is the ONLY place. Every agent in `agents/`
    calls this function and interpolates the result as `{chat_history}`, and none of them share
    a prompt template. Adding the block here reaches all seven, and reaches the eighth agent
    written next month for free — see `tools/self_context.py` for the full argument.

    The block goes BEFORE the conversation log, not after. A standing rule that outranks
    everything else must not sit underneath forty lines of last week's chat.
    """
    history = load_history()
    log = "No previous memory."
    if history:
        log = "PREVIOUS CONTEXT:\n"
        for msg in history:
            log += f"{msg['role'].upper()}: {msg['content']}\n"

    # Imported here, not at module scope: `self_context` reads three other tool modules, and a
    # circular import at load time would take down every agent at once. A local import costs a
    # dict lookup after the first call.
    try:
        from tools.self_context import preamble
        return preamble() + log
    except Exception:                                                     # noqa: BLE001
        # A failure to build the preamble must never cost the conversation history. This is the
        # function every agent depends on; it degrades, it does not fail.
        logging.getLogger("oddball.memory").exception(
            "self-context could not be built; answering with history only")
        return log

def main(argv: list[str] | None = None) -> int:
    """Inspect the conversation log and its backup clock.

        python tools/memory_manager.py
        python tools/memory_manager.py --backed-up
    """
    import argparse

    ap = argparse.ArgumentParser(description="the conversation log and its backup clock")
    ap.add_argument("--backed-up", action="store_true",
                    help="record that you have copied the log somewhere; restarts the clock")
    args = ap.parse_args(argv)

    if args.backed_up:
        if not acknowledge_backup():
            print("  could not record it")
            return 1
        print(f"  noted. Next reminder in {BACKUP_DAYS_LIMIT} days.")
        return 0

    history = load_history()
    started = backup_clock_started()
    print(f"  log:      {MEMORY_FILE}")
    print(f"  turns:    {len(history)}")
    if history:
        print(f"  spanning: {history[0]['timestamp'][:19]} .. {history[-1]['timestamp'][:19]}")
    if started is None:
        print("  age:      unknown — no file yet")
        return 0

    days = (datetime.now() - started).days
    source = "last backup" if _state_path().exists() else "file created"
    print(f"  {source}: {started.isoformat(timespec='seconds')} ({days} days ago)")
    if check_for_backup_reminder():
        print(f"  BACKUP DUE — over {BACKUP_DAYS_LIMIT} days. Copy it, then --backed-up.")
    else:
        print(f"  backup due in {BACKUP_DAYS_LIMIT - days} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
