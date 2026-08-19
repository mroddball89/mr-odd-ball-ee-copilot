# Mr Odd Ball — EE Copilot

A specialised Electrical Engineering copilot for **LB**, built to run on a Raspberry Pi 5.
A Gemini router reads each question and hands it to the one agent that should answer it.

> **This tag (`v0-terminal`) is the engine as it ran in the terminal**, before Mr Odd Ball's
> voice, personality and animated face were merged in. It is kept so the original is always
> recoverable. Integration work happens on the `oddball-integration` branch.

## How it routes

`router.py` uses Pydantic structured output (`with_structured_output`) to classify a query
into one destination, then `main.py` dispatches it:

| Route | Agent | What it does |
|---|---|---|
| `FIRMWARE` | `agents/firmware_agent.py` | C/C++, RTOS, registers, datasheets |
| `HARDWARE` | `agents/hardware_agent.py` | PCB trace width via IPC-2221 (`tools/trace_calculator.py`) |
| `MATH` | `agents/math_agent.py` | writes and runs Python in a REPL sandbox |
| `OS` | `agents/os_agent.py` | runs terminal commands on the Pi — **asks first** |
| `QUIZ` | `agents/quiz_agent.py` | tutor mode; grades conceptually, not word-for-word |
| `WEB` | `agents/web_agent.py` | DuckDuckGo search — **asks first** |
| `GENERAL` | — | anything outside the scope |

## The two security gates

`OS` and `WEB` are the only routes that can touch the system or the network, and neither acts
without approval. The proposed command or query is printed, and execution waits on an explicit
`y`. Anything else aborts. `tools/os_controller.py` also holds a blocklist and a 15-second
timeout.

## Memory

`tools/memory_manager.py` logs the last 40 messages to `sd_card_memory.json` on the Pi's SD
card and injects them into every agent prompt as `{chat_history}`. It also watches a **15-day
clock** — once the oldest message in the log passes that age, every answer carries a reminder
to copy the file to an external drive before the card is the only copy.

## Setup

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip
cp .env.example .env                          # then add your key from aistudio.google.com/apikey
python main.py
```

`.env` is gitignored and must stay that way.

## Datasheet retrieval

Put PDFs under `data/` (there are `arduino/`, `espressif/`, `raspberry_pi/` and `sensors/`
subdirectories), then build the vector store once:

```bash
python tools/vector_db.py
```

It chunks at 500 characters with 150 of overlap — deliberately high, so register tables and
code blocks are not cut in half — embeds locally with `all-MiniLM-L6-v2`, and persists to
ChromaDB. Nothing leaves the machine to do it.

## Quiz mode

Ask to be quizzed and the router is bypassed until you say `exit quiz`; questions come from
`quiz_data.json`, which is created with three defaults if it does not exist.
