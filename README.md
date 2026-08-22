# Mr Odd Ball — EE Copilot

A specialised Electrical Engineering copilot for **LB**, built to run on a Raspberry Pi 5.
A Gemini router reads each question and hands it to the one agent that should answer it.

> **This tag (`v0-terminal`) is the engine as it ran in the terminal**, before Mr Odd Ball's
> voice, personality and animated face were merged in. It is kept so the original is always
> recoverable. Integration work happens on the `oddball-integration` branch.

## How it routes

**A free tier runs first, and most short questions never reach an API at all.**
`engine/core.py:_free_turn` tries `orchestrator/instant.py` before the router: the time, the
date, unit conversions, physical constants, engineering definitions, arithmetic — and *"open
Firefox"*, via `orchestrator/launch_intent.py`. All of those cost **zero** API calls, so they
keep working after the daily quota is gone. Opening an application used to cost three.

When nothing free matches, `router.py` uses Pydantic structured output
(`with_structured_output`) to classify the query into one destination, and dispatches it:

| Route | Agent | What it does |
|---|---|---|
| `FIRMWARE` | `agents/firmware_agent.py` | C/C++, RTOS, registers, datasheets |
| `HARDWARE` | `agents/hardware_agent.py` | PCB trace width via IPC-2221 (`tools/trace_calculator.py`); reads your own KiCad schematics and boards (`tools/kicad_parser.py`) |
| `MATH` | `agents/math_agent.py` | writes and runs Python in a REPL sandbox |
| `OS` | `agents/os_agent.py` | runs terminal commands on the Pi, and opens desktop applications (`tools/app_launcher.py`) — **asks first**, for both |
| `QUIZ` | `agents/quiz_agent.py` | tutor mode; grades conceptually, not word-for-word |
| `WEB` | `agents/web_agent.py` | DuckDuckGo search — **asks first** |
| `ACADEMIC` | `agents/academic_agent.py` | your syllabi and coursework deadlines — answers from your own uploaded documents **only** |
| `UTILITY` | `orchestrator/instant.py` | the free lookups, when the router is reached anyway |
| `PERSONA` | `agents/persona_agent.py` | chit-chat and jokes — Mr Odd Ball himself |
| `GENERAL` | `agents/persona_agent.py` | anything outside the scope |

## The two security gates

`OS` and `WEB` are the only routes that can touch the system or the network, and neither acts
without approval. The exact command, query or argv is shown on a card **before** the question is
asked, and nothing runs without a clear yes — silence, a mumble and a refusal all decline.
`tools/os_controller.py` also holds a 19-pattern blocklist and a 15-second timeout, applied even
after approval.

Opening an application goes through the same gate, and the argv on the card comes from the
machine's own desktop entry rather than from anything a model wrote — so what is approved, what
is spoken and what is shown are the same thing. Launched apps get their own transient systemd
unit, which is what lets them survive a restart of the assistant.

## Reading your KiCad files

The HARDWARE agent can read your own schematics and boards, entirely offline — no KiCad
install required. Ask it what's on a schematic, whether a part is on there, or how many layers
a board has, and it parses the real file with `kiutils` rather than guessing.

- `extract_kicad_bom` — reads a `.kicad_sch` and returns a grouped bill of materials
  (quantity, value, footprint, references), walking hierarchical sub-sheets automatically.
- `analyze_kicad_pcb` — reads a `.kicad_pcb` and reports copper layer count, nets, footprints
  placed and board thickness.

Point it at a real path, or just say the project's name — a name is searched for under
`ODDBALL_KICAD_ROOT` (set in `.env`; defaults to `~/kicad`), since a dictated file path rarely
survives speech-to-text intact. If a name matches more than one file, it asks which one rather
than guessing.

See `docs/DECISIONS.md` (D9) for why `kiutils` rather than a hand-rolled parser, and
`tools/verify_kicad.py` for the harness.

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

## Local document retrieval

Put PDFs under `data/` (there are `arduino/`, `espressif/`, `raspberry_pi/` and `sensors/`
subdirectories), then build the vector store once:

```bash
python tools/vector_db.py
```

It chunks at 500 characters with 150 of overlap — deliberately high, so register tables and
code blocks are not cut in half — embeds locally with `all-MiniLM-L6-v2`, and persists to
ChromaDB. Nothing leaves the machine to do it.

**Two collections, one store.** Everything under `data/` goes to the `datasheets` collection
and is read by the FIRMWARE agent. `data/academic/` is the exception: syllabi go there, into a
separate `academic` collection read only by the ACADEMIC agent. A semantic search ranks by
similarity alone and cannot tell a course outline from a datasheet, so one pool would let a
syllabus ground a firmware answer and be cited as one.

## Coursework and deadlines

Drop your syllabi into `data/academic/`, then run both build steps:

```bash
python tools/vector_db.py           # embeds them into the academic collection
python tools/academic_calendar.py   # extracts dates into academic_calendar.json
```

The ACADEMIC agent answers from those documents and **only** those documents — asked something
the syllabi do not cover, it says it does not know rather than describing what a course
"usually" does. There is no public record of your professor's late policy, so a fluent guess
would be a fabrication with nothing to check it against.

Anything due within **3 days** is then appended to every answer he gives, on any subject — the
same way the 15-day backup reminder works. It is shown, never spoken, and costs no API call:
the dates were extracted once, and the check is a JSON read. See `docs/DECISIONS.md` (D11).

## Quiz mode

Ask to be quizzed and the router is bypassed until you say `exit quiz`; questions come from
`quiz_data.json`, which is created with three defaults if it does not exist.
