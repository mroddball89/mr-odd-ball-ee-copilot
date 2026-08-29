import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

# The conversation log — the last 40 turns, injected into EVERY agent prompt as PREVIOUS
# CONTEXT by `format_memory_for_llm`.
#
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
MEMORY_FILE = str(Path(os.environ.get("ODDBALL_MEMORY_FILE")
                       or Path(__file__).resolve().parents[1] / "sd_card_memory.json"))
BACKUP_DAYS_LIMIT = 15

def load_history():
    """Loads the chat history from the SD card."""
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, 'r') as f:
        return json.load(f)

def save_history(history):
    """Saves the chat history back to the SD card."""
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

def check_for_backup_reminder() -> bool:
    """Checks if the memory file is 15 days old."""
    history = load_history()
    if not history:
        return False
        
    # Look at the timestamp of the oldest message in our current log
    oldest_message_time = datetime.fromisoformat(history[0]["timestamp"])
    time_elapsed = datetime.now() - oldest_message_time
    
    if time_elapsed >= timedelta(days=BACKUP_DAYS_LIMIT):
        return True
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