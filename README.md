# Mr Odd Ball — EE Copilot

A specialised Electrical Engineering copilot for **LB**. He listens for his name, answers out
loud, and floats on the desktop as an animated face. A Gemini router reads each question and
hands it to the one agent that should answer it.

**Runs on Windows 11.** He lived on a Raspberry Pi 5 until 2026-08-26 and now runs on LB's
workstation (Ryzen 7 5700X, 32 GB, RX 6600). The Linux code was **deleted, not disabled** —
`tools/os_controller.py`, `tools/app_catalogue.py` and `tools/gesture_pointer.py` raise on
import off Windows, deliberately, so that nothing can quietly degrade into a guard that allows
everything. Restore from git history (or the `v0-terminal` tag) if the Pi ever comes back.

> The `v0-terminal` tag is the engine as it ran in the terminal, before the voice, personality
> and animated face were merged in. It is kept so the original is always recoverable.
> Work happens on the `oddball-integration` branch.

## What runs where

| | |
|---|---|
| **Shell** | `powershell.exe -NoProfile -NonInteractive`, invoked as an argv — never `shell=True`, which would put `cmd.exe` in front of it |
| **Face** | PyQt6 + QtWebEngine, frameless and always-on-top, optionally click-through via `WS_EX_LAYERED` |
| **Apps** | the Start Menu shortcut tree, read as this platform's desktop-entry database |
| **Audio** | `sounddevice`/PortAudio on WASAPI; Piper TTS and faster-whisper, both local |
| **Gestures** | `SendInput` through `ctypes`, mouse-only by construction |
| **Autostart** | a shortcut in `shell:startup` → `config/start_oddball.vbs` |

`docs/DEPLOY.md` is the long version, and is what to read when something breaks.

## How it routes

**A free tier runs first, and most short questions never reach an API at all.**
`engine/core.py:_free_turn` tries `orchestrator/instant.py` before the router: the time, the
date, unit conversions, physical constants, engineering definitions, arithmetic — *"open
Firefox"*, via `orchestrator/launch_intent.py` — and **his whole notebook**, via
`orchestrator/note_intent.py`. All of those cost **zero** API calls, so they keep working after
the daily quota is gone. Opening an application used to cost three, and so did taking a note.

When nothing free matches, `router.py` uses Pydantic structured output
(`with_structured_output`) to classify the query into one destination, and dispatches it:

| Route | Agent | What it does |
|---|---|---|
| `FIRMWARE` | `agents/firmware_agent.py` | C/C++, RTOS, registers, datasheets |
| `HARDWARE` | `agents/hardware_agent.py` | PCB trace width via IPC-2221 (`tools/trace_calculator.py`); reads your own KiCad schematics and boards (`tools/kicad_parser.py`) |
| `MATH` | `agents/math_agent.py` | writes and runs Python in a REPL sandbox |
| `OS` | `agents/os_agent.py` | runs PowerShell commands on this PC, and opens desktop applications (`tools/app_launcher.py`) — **asks first**, for both |
| `QUIZ` | `agents/quiz_agent.py` | tutor mode; grades conceptually, not word-for-word |
| `WEB` | `agents/web_agent.py` | DuckDuckGo search — **asks first** |
| `ACADEMIC` | `agents/academic_agent.py` | coursework deadlines from your **live Canvas feed** (`tools/canvas_sync.py`), and course policies from your syllabus notes in the vault. Canvas owns the dates; the notes own everything else |
| `SCREEN` | `agents/screen_agent.py` | takes a screenshot and says what is on it — *"what am I looking at"*, *"what does that error say"* — **asks first** (`tools/screen_capture.py`) |
| `UTILITY` | `orchestrator/instant.py` | the free lookups, when the router is reached anyway |
| `PERSONA` | `agents/persona_agent.py` | chit-chat and jokes — Mr Odd Ball himself |
| `GENERAL` | `agents/persona_agent.py` | anything outside the scope — **and files whatever you upload** (`tools/file_manager.py`), whichever kind of document it turns out to be |

Most note-taking never reaches this table at all — see **Taking notes** below.

## The three security gates

`OS`, `WEB` and `SCREEN` are the only routes that can touch the system, the network, or your
display, and none of them acts without approval. The exact command, query or argv is shown on a
card **before** the question is asked, and nothing runs without a clear yes — silence, a mumble
and a refusal all decline. `tools/os_controller.py` also holds a **33-pattern blocklist** and a
15-second timeout, applied even after approval.

> **The blocklist is platform-specific, and it has been wrong once.** Every pattern was Linux
> syntax until 2026-08-26, and pointed at a Windows shell it matched *nothing* — 16 of 17
> destructive commands (`format C: /y`, `del /s /q C:\`, `vssadmin delete shadows`) were
> reported as allowed, while every harness stayed green. It does not fail loudly: it finds no
> match and answers "allowed". The measurement is in
> `media/data/2026-08-26-windows-blocklist-gap.csv` and the lesson is **L23**. The module now
> refuses to import off Windows for exactly this reason.

Opening an application goes through the same gate, and what is on the card comes from the
machine's own Start Menu entry rather than from anything a model wrote — so what is approved,
what is spoken and what is shown are the same thing. Launched apps are handed to Explorer via
`os.startfile`, so they are not children of the assistant and survive a restart of it.

**The screenshot gate is the one you may want to switch off**, and it is the only one that says
so. A frame of your desktop goes to Gemini, and your desktop may have a terminal with a key in
the scrollback — so the default is to ask. But you are the one who asked to be looked at, so
`ODDBALL_SCREEN_CONFIRM=0` makes it instant and `ODDBALL_SCREEN=0` turns the route off entirely.
Frames are kept in `data/screen/` (gitignored) so you can open the exact image that was sent
rather than take it on trust.

## Looking at the screen

Ask *"what's on my screen"*, *"what am I looking at"* or *"read that error"* and he takes one
frame, downscales it, and describes what is actually in it. `orchestrator/route_hint.py` answers
those phrasings with **no routing call at all**, so getting to the screen costs one API call
rather than two.

He is describing **one still frame**. He cannot watch the screen, cannot see what changed, and
cannot click, type or scroll — the prompt says so, because a model shown a screenshot will
otherwise happily offer to press the button in it.

The backend is PowerShell's `System.Drawing` capture, and it needs nothing installed. The
`gnome-screenshot` are tried as X11 fallbacks, and Windows works too so the harness can run on
the authoring box. With none of them installed he says he cannot see the screen instead of
failing in a way that looks like a bug. `python tools/verify_screen.py --backends` says which
one this machine would use.

## Reading your KiCad files

The HARDWARE agent can read your own schematics and boards, entirely offline — no KiCad
install required. Ask it what's on a schematic, whether a part is on there, or how many layers
a board has, and it parses the real file with `kiutils` rather than guessing.

- `extract_kicad_bom` — reads a `.kicad_sch` and returns a grouped bill of materials
  (quantity, value, footprint, references), walking hierarchical sub-sheets automatically.
- `analyze_kicad_pcb` — reads a `.kicad_pcb` and reports copper layer count, nets, footprints
  placed and board thickness.

Point it at a real path, or just say the project's name — a name is searched for in
`data/projects/`, where anything you upload with the paperclip is filed, and then under
`ODDBALL_KICAD_ROOT` (set in `.env`; defaults to `~/kicad`). Names rather than paths, because a
dictated file path rarely survives speech-to-text intact. If a name matches more than one file,
it asks which one rather than guessing.

The FIRMWARE agent has both tools too, so "which pin is the HX711 clock on" is answered by
reading your board rather than from a reference design it half-remembers.

See `docs/DECISIONS.md` (D9) for why `kiutils` rather than a hand-rolled parser, and
`tools/verify_kicad.py` for the harness.

## Taking notes

**Tell him or type it, and it goes into your vault. It costs nothing.**

> **You:** Take a note in a new folder called amp board that the reg is an LM317, not a 7805
> **Him:** What should I call it?
> **You:** Regulator choice
> **Him:** Written down in amp board.

Five things he can do, all of them free, all of them without a model anywhere in the path:

| Say | What happens |
|---|---|
| *take a note that the reg is an LM317* | asks what to call it, then writes it |
| *add to my regulator note that it needs a heatsink* | appends, under a `---` rule |
| *read me my regulator note* | says it aloud, full text on a card |
| *what notes do I have in amp board* | lists that folder |
| *delete my scratch note* | **asks first**, then moves it to `vault/.trash/` |

A bare *"take a note"* asks what to write down. A folder you name is **created on demand** —
it does not have to exist, and it does not have to be one of his. Say *"never mind"*, say
nothing, or dismiss him, and the note is dropped.

**What is stored is exactly what you said**, sliced out of the raw text, never a model's
paraphrase of it. That is the same rule as your standing rules above, for the same two
reasons: it works with the quota gone, and a part number that goes through a paraphrase is a
part number that can quietly change.

Taking a note used to cost **three** Gemini calls — route, tool call, follow-up — which against
the 20-a-day free tier was six notes and then nothing. Measured 2026-08-28: **0 of 8** of the
phrasings above were free before, 8 of 8 now, at 18–22 ms each.
`media/data/2026-08-28-note-turn-cost.csv`, and D50 for why none of it uses a model.

**Deleting is a move, not a shred.** The note goes to `vault/.trash/` with a timestamp on it —
it stops being searched, read back or found, but it is still a file you can drag back. The
exact path is on a card *before* he asks, so what you approve is what gets moved.

```bash
python tools/knowledge_vault.py --list             # every note, and what is in the trash
python tools/knowledge_vault.py --read "regulator choice"   # what he'd SAY and what he'd SHOW
python -m orchestrator.note_intent                 # what he matches, and what he refuses
```

He deliberately will **not** edit a line inside a note by voice, and *"remember that…"* is
deliberately not a note verb — it means *recall* as often as it means *record*. Both are in D50.

## Memory

`tools/memory_manager.py` logs the last 40 messages to `sd_card_memory.json` on the local
card and injects them into every agent prompt as `{chat_history}`. It also watches a **15-day
clock** — once the oldest message in the log passes that age, every answer carries a reminder
to copy the file to an external drive before the card is the only copy.

That same function carries three more things in front of the conversation log, and because every
agent already calls it, every agent gets them. `tools/self_context.py` composes the block.

## Correcting him, and having it stick

**Tell him he got something wrong and he writes it down, permanently, in your own words.**

> **You:** Always use absolute paths instead.
> **Him:** Got it, I've written that down. The rule is: Always use absolute paths instead.

The rule goes into `vault/corrections.md` and is injected into **every agent prompt from the next
turn onward**, above everything else in the prompt, stated as overriding his own judgement. Typed
or spoken, it works the same way — `engine/core.py` catches it at the one entry point both
channels come through.

It costs **no API call**. The rule is a slice of your raw sentence, never a model's paraphrase of
it, so paths and part numbers survive intact and it still works with the quota gone — which
matters, because being annoyed at him is exactly the moment a turn must not fail.

A bare *"that was wrong"* carries no rule — "that" means nothing in tomorrow's prompt — so he
records it and asks what he should have done instead. Your answer becomes the rule.

```bash
python tools/corrections.py --list      # every standing rule, numbered
python tools/corrections.py --prompt    # exactly what every agent is being told right now
```

That second one is what to reach for when he starts behaving oddly. A rule you forgot you gave is
the first thing to suspect. To withdraw one, delete its entry from the file.

## Learning from his own mistakes

`vault/reflections.md` is the other half, and deliberately a **separate file**: a correction is an
instruction and is obeyed, a recorded failure is evidence and is considered. Merging them would
either soften your rules into suggestions or harden one timeout into a refusal.

He writes to it himself when a command errors, the blocklist refuses something, an app is not
installed, a turn raises, or **a turn takes longer than 45 seconds even though it worked** — the
slow success being the failure nobody escalates. Before answering, the failures that look like
what you just asked are put in front of him, matched on shared words with identifiers like
`ECE350` weighted double.

```bash
python tools/reflections.py --list
python tools/reflections.py --similar "open firefox"
```

Both ledgers are plain Markdown under `vault/`, gitignored like the rest of it, created on first
write, and safe to edit by hand.

## Knowing what he is

`tools/system_state.py` puts his own CPU temperature, load average, free memory, disk space,
uptime, which ports are listening (8765 for the face and its WebSocket, 8767 for uploads) and
which capabilities are actually installed into every prompt. So "how hot are you" is answered
without a tool call, and an answer given while the CPU is at 81 °C is allowed to mention it.

Every reading is a `/proc` or `/sys` read cached for 15 seconds plus two loopback connects — no
model, no subprocess. **Anything unreadable is stated as unreadable rather than omitted**, because
an assistant that confidently reports a temperature it never read is worse than one that says it
cannot see the sensor. The capability list is derived from which modules exist on disk, so
deleting a tool removes the claim.

```bash
python tools/system_state.py            # temperature, load, memory, ports, capabilities
python tools/self_context.py "open firefox"   # the whole block, as one agent prompt sees it
```

`ODDBALL_SELF_CONTEXT=0` turns all three blocks off; `ODDBALL_STATE=0` drops only the machine
state and leaves your rules in place.

## Setup

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
python main.py --text                         # typing. No audio hardware, no HUD.
python main.py                                # voice: wake word, ears, voice, face
```

**Needs Python 3.12.** Not 3.13: `mediapipe` — the hand tracker behind the gesture pointer and
the thumbs-up approval — publishes no wheel that works on it. Everything else is happy on
either, so this is the one version constraint in the project and it comes from one package.

The Gemini key goes in `.env`, which is gitignored and must stay that way. Paste it without
letting it reach your shell history, which PSReadLine keeps in plain text forever:

```powershell
$k = Read-Host 'paste the key' -AsSecureString
[Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($k)) |
  ForEach-Object { "GOOGLE_API_KEY=$_" } |
  Set-Content -Encoding utf8 .env
```

Get a key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — the free tier
is 20 requests per model per day, which is what the free-tier routing above exists to protect.

**To have him start with Windows:**

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 install
powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 status
```

That puts a shortcut in `shell:startup` pointing at `config/start_oddball.vbs`, which runs
`config/start_oddball.bat` with no console window. `remove` takes it back out.

**To check everything still works** — 29 harnesses, 12,441 checks, no API key required:

```powershell
Get-ChildItem tools\verify_*.py | ForEach-Object { python $_.FullName }
```

## Uploading documents — the paperclip

There is a paperclip beside the chat input. Press it, pick a file, and it is uploaded, sorted
and indexed without touching the file system.

```
📎  ->  data/inbox/  ->  "I just uploaded ECE350_syllabus.pdf."  ->  he files it
```

The upload is a `POST /upload` to `engine/server.py` on port **8767** (stdlib `http.server`,
started alongside the rig by `engine/run_voice.py`). On success the page injects that sentence
into the chat as though you had typed it — so it reaches `router.py` like any other question,
routes to GENERAL, and the persona agent calls `process_inbox_file`. If the filename does not
make the category obvious, he asks which it is rather than guessing.

| he files it as | it lands in | and then |
|---|---|---|
| `academic` | `data/academic/` | vector store **and** deadline calendar rebuild |
| `datasheet` | `data/<folder>/` | vector store rebuild |
| `schematic` | `data/projects/<project>/` | nothing — it is readable immediately |

Accepts `.pdf`, `.txt`, `.md`, `.csv`, `.kicad_sch`, `.kicad_pcb`, `.kicad_pro`, `.kicad_prl`,
`.net`, `.zip` and common image formats, up to 64 MB. A zip — a gerber bundle, a zipped KiCad
project — is unpacked into the project folder, with any member pointing outside it skipped and
reported.

**An index rebuild runs in the background**, because it loads torch and re-embeds everything
under `data/` — measured on the Pi at **11.4 s before it embeds anything** (not re-measured
since the move to Windows, where it will be substantially faster), then 14.4 ms per
chunk, and the per-chunk part multiplies by your whole library rather than by the new file. He
is prompted to say a document is *being indexed*, never that it is ready, and never to promise
you a duration; ask him whether it is done and he checks. A KiCad file needs no rebuild at all —
it is parsed off the disk at question time, so it is answerable the moment it is filed.

The same thing from a shell, if you prefer one:

```bash
curl -F file=@ECE350_syllabus.pdf http://127.0.0.1:8767/upload
python tools/file_manager.py --list
python tools/file_manager.py --file ECE350_syllabus.pdf --as academic
```

The endpoint binds loopback only, and a request carrying an `Origin` header must carry a local
one — a `multipart/form-data` POST is a CORS simple request, so without that check any page in
any browser on the machine could write into the inbox. `docs/DECISIONS.md` (D21) has the rest,
including why this is not FastAPI and why it cannot share port 8765 with the rig.

## Local document retrieval

This is the **firmware** agent's datasheet store, and it is the only RAG in the system.
Upload PDFs with the paperclip and he does this for you. To do it by hand, put them under
`data/` (there are `arduino/`, `datasheets/`, `espressif/`, `raspberry_pi/` and `sensors/`
subdirectories) and build the vector store once:

```bash
python tools/vector_db.py
```

It chunks at 500 characters with 150 of overlap — deliberately high, so register tables and
code blocks are not cut in half — embeds locally with `all-MiniLM-L6-v2`, and persists to
ChromaDB. Nothing leaves the machine to do it.

**One collection, and one exclusion.** Everything under `data/` goes to the `datasheets`
collection and is read by the FIRMWARE agent — except `data/academic/`, which is skipped. There
used to be a second `academic` collection there; it is gone, and the exclusion is what remains.
It matters more without it: a semantic search ranks by similarity alone and cannot tell a course
outline from a datasheet, so dropping it would let a syllabus ground a firmware answer and be
cited as one, with no collection boundary left to catch it.

## Coursework and deadlines

**Deadlines come from your live Canvas feed, not from the PDFs.** A syllabus is a snapshot; a
date moved in week four is right in Canvas and wrong in the PDF, and the PDF's version is the one
that got extracted. The feed also costs no API call at all — one HTTP GET against a 20-a-day
quota.

```bash
python tools/canvas_sync.py             # pull the live dates
python tools/canvas_sync.py --dry-run   # see what it would import, write nothing
```

Or just say **"sync Canvas"** / **"update my schedule"** and he does it — `sync_canvas_calendar`
is bound to the ACADEMIC agent.

The feed URL is a **credential**: anyone holding it reads your whole calendar with no login. It
goes in `.env`, which is gitignored and is not deployed:

```
ODDBALL_CANVAS_ICS=https://<school>.instructure.com/feeds/calendars/user_....ics
```

Canvas gives you the link under **Calendar → Calendar Feed**, bottom right. Reset it there if it
ever leaks.

### Course policies live in the vault, not in a vector store

Upload a syllabus with the paperclip and it is read **once**, into a plain Markdown note in
`vault/courses/`:

```bash
python tools/syllabus_to_vault.py              # every syllabus without a note yet
python tools/syllabus_to_vault.py --file x.pdf --dry-run   # extract and print, write nothing
```

One API call per document, then it is a file. `read_from_vault` — which the HARDWARE, FIRMWARE
and GENERAL agents already carry — greps it for free after that, so "what's the late policy"
works with no retrieval, no embeddings and no torch on the answer path. The note keeps the
instructor, office hours, the grading breakdown, the late policy and any standing rules.

**A scanned syllabus is refused before the model is called.** An image-only PDF has no
extractable text, and a model handed an empty document does not report an empty document — it
invents a complete, plausible course policy, which then sits in your notes as fact. If you see
`only 0 characters of text`, OCR it or find a text-bearing copy.

**Anything the syllabus does not say is written as *not stated*, not guessed.** The note is
stamped with its source file and the date so you can always tell a machine wrote it, and due
dates are deliberately excluded — those come from Canvas and would go stale here.

```bash
python tools/academic_calendar.py   # print the calendar, and what the agent is shown
python tools/knowledge_vault.py --search "late policy"
```

The ACADEMIC route reads the note too, via `read_from_vault` — so "what's the POSC 201 late
policy?" works whether you phrase it as coursework or not. **Canvas owns every date and the notes
own everything else**, and the agent is forbidden to take a date out of a note: a note is a
syllabus snapshot, and Canvas may already have moved the date.

If it has no note for a course it says so and points you at the paperclip, rather than describing
what a course usually does.

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
