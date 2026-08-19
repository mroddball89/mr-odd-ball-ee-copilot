import os
import json
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
    """Formats the history so the LLM can read it as context."""
    history = load_history()
    if not history:
        return "No previous memory."
        
    formatted = "PREVIOUS CONTEXT:\n"
    for msg in history:
        formatted += f"{msg['role'].upper()}: {msg['content']}\n"
    return formatted