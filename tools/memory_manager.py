import os
import json
import logging
from datetime import datetime, timedelta

# This file will be saved directly on the Pi's SD card
MEMORY_FILE = "sd_card_memory.json"
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