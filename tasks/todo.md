# Merging Mr Odd Ball into the EE Copilot

Branch: `oddball-integration`. The original terminal engine is preserved on `main` at tag
`v0-terminal`.

Plan: `~/.claude/plans/system-context-persona-misty-hickey.md`

## The shape of it

The EE Copilot is the **host**. Mr Odd Ball's wake word, STT, TTS, personality and animated
face move into it from `~/oddball`. `router.py`'s `AgentRoute` becomes the single brain
selector — the tier system (`classify.py`, `tiers.py`, `brains/`) is **not** carried over.

---

## Stage 0 — Preserve the original ✅

- [x] Rotate-and-move the Gemini key out of source into a gitignored `.env` (3 files)
- [x] `.gitignore`, `.env.example`, `requirements.txt`, `README.md`
- [x] `git init`, commit, **tag `v0-terminal`**
- [x] Push to `mroddball89/mr-odd-ball-ee-copilot` (private)
- [x] Branch `oddball-integration`
- [ ] **LB: rotate the key at aistudio.google.com/apikey** — it was in plaintext on disk

## Stage 1 — Bring the assistant across

- [x] Copy `audio/` (wake, listen, stt, say, gate) from `~/oddball`
- [x] Copy `orchestrator/hud_bridge.py`, `settings.py`, `formulas.py`, `memory/speakable.py`
- [x] Copy `hud/face-preview.html`, `assets/`, `models/hey_mr_odd_ball.onnx`, `config/oddball.toml`
- [x] Copy the `tools/verify_*.py` harnesses for what came across
- [x] Merge `requirements.txt` from both; note `--no-deps openwakeword` and the two apt packages
- [x] Prove the voice path still passes after the move

## Stage 2 — The response envelope (speech vs. visual)

- [x] `engine/response.py` — `Response(speech, cards, route, pending)` + `Card(kind, title, body, lang)`
- [x] `SPOKEN:` line appended to every agent prompt template
- [x] `engine/split.py` — fences → code cards, tables → table cards, terminal output → log cards
- [x] Fall back to `speakable.extract()` at `MAX_WORDS = 40` when no `SPOKEN:` line arrives
- [x] Hard filter: never speak code, tables, URLs, paths, hex literals, tracebacks
- [x] Second LLM pass in `math_agent` and `hardware_agent` — they return raw tool output today

## Stage 3 — Router as the only dispatcher

- [x] Add `PERSONA` (chit-chat, jokes, identity) and `UTILITY` (time, date, convert, constants)
- [x] Fix `ROUTER_PROMPT` — it documents 5 of 7 routes; `OS` and `QUIZ` are missing
- [x] `engine/core.py` — `Engine.ask(text) -> Response`, no `print()`, no `input()`

## Stage 3b — Wire the RAG pipeline into the firmware agent

- [x] Fix `CHROMA_PATH`/`DATA_PATH` — currently cwd-relative, writes outside the repo
- [x] `get_retriever(k=4)`, opened once at import, returns `None` when unbuilt
- [x] `{datasheet_context}` in `FIRMWARE_PROMPT_TEMPLATE`; prefer retrieval over model memory
- [x] Sources card from Chroma's `source` / `page` metadata
- [x] `glob="**/*.pdf"` so the `data/` subdirectories are recursed

## Stage 4 — The voice loop

- [x] `engine/turn.py`, modelled on `~/oddball/orchestrator/turn.py`
- [x] Keep: conditional greeting, `Timings`, WAV capture saving, mic gating
- [x] `main.py` becomes a launcher: `--voice` (default), `--text`, `--headless`

## Stage 5 — Gates and the quiz lock over voice

- [x] Split `os_agent` / `web_agent` into `propose()` / `resume(approved)`
- [x] `Pending` carries a spoken paraphrase AND the exact command; card renders first
- [x] Copy `is_yes()`; silence, mumble, timeout and refusal all mean no
- [x] Extend the `forbidden_commands` blocklist (3 -> 19 patterns, 60 checks, probe bites) (no `dd`, `shutdown`, `> /dev/sd*`, `chmod -R 777 /`)
- [x] Quiz lock: exit-phrase family, wake-word escape, visible QUIZ MODE chip

## Stage 6 — HUD chat panel

- [x] Outbound `card` / `transcript` / `mode` / `pending` messages in `hud_bridge.py`
- [x] Inbound receive loop: typed `text`, `approve` — `broadcast_threadsafe` in reverse
- [x] Chat column in `face-preview.html`; code cards, real tables, scrollable logs
- [x] Panel hidden entirely unless `?chat=1`

## Stage 7 — Floating on the Pi desktop — **mostly already built**

Not PyQt6. `tools/spike_gtk_face.py` already does this with GTK4 + WebKitGTK, and D41 records
him running on the Pi desktop transparently today:
`--url 'http://127.0.0.1:8765/?solo=1' --transparent --undecorated`.
`tools/install_autostart.sh` already installs the systemd user unit and the XDG autostart entry.

- [x] Transparent, undecorated, always-on-top face window — exists, proven on hardware
- [x] Autostart at boot — `config/oddball.service` + `config/oddball-face.desktop`
- [x] Promote `spike_gtk_face.py` to `hud/float.py` — it is the application now, not a spike
- [x] A third rig mode: face **+ chat panel**, no rig chrome (`?solo=1` hides the panel today)
- [x] Repoint `oddball.service` `ExecStart` and the .desktop Exec at the merged entry point
- [ ] `sudo apt install gir1.2-gtk-4.0 gir1.2-webkit-6.0 libwebkitgtk-6.0-4 python3-gi`

## Stage 8 — Verification

- [x] `verify_split.py` — **and prove it bites**: plant a fenced C++ block in the prose path
- [x] `verify_engine.py` (gates + quiz + failure lines, 97 checks, probe bites) — nothing executes without an explicit yes
- [ ] `verify_router.py` — all 9 routes reachable, `PERSONA`/`UTILITY` don't swallow EE questions
- [x] quiz covered by `verify_engine.py`; `verify_chat.py` + `verify_os_guard.py` added
- [ ] `verify_rag.py` — needs datasheet PDFs in `data/` to be meaningful
- [ ] End-to-end on the Pi, six scenarios
- [x] `media/` — turn-latency data (before/after), script and chart

---

## Open question, to be answered with a number

Swapping `classify.py` (a pure function, ~0 ms) for `router_agent()` (a Gemini round trip,
~0.4–0.9 s) puts a turn near **2.5–3.2 s** against the 2.0 s budget in `~/oddball/docs/PLAN.md`.
This was LB's call and is not being second-guessed — Stage 8 measures it so the cost is a
number on a chart rather than a feeling.

## Review

**Done: stages 0-7, and most of 8.** 10,717 checks green across 13 harnesses; three of them
carry a `--probe` that removes the guard and confirms the checks go red.

### What the measurements changed

- **D3 — the free tier is 20 requests/day, not ~1,500.** Found by exhausting it in five
  questions on the first end-to-end run. Jobs now split across models because the quota is
  per model: routing on `flash-lite` (~750ms, its own bucket), agents on `flash`.
- **UTILITY was missing every EE acronym.** `media/scripts/measure_turn.py` timed ten
  questions; "what does i2c stand for" missed the tables, fell through to the persona agent,
  and cost 2.20s and one of the twenty. 19 acronym rows later it is 0.73s and free, and
  0/10 exceed the 2.0s budget instead of 1/10. Chart: `media/charts/turn-latency.svg`.
- **The RAG pipeline was never connected.** Built, embedded, persisted, and never queried.

### What didn't work

- **PyQt6 for the floating face.** Proposed in the plan and wrong — LB had already shipped
  GTK4 + WebKitGTK (D41). The spike was promoted rather than replaced.
- **A probe that asserted a patched lambda returns None.** A tautology dressed as a
  non-vacuity check. Rewritten to drive the real `split()`; then the gate probe had the same
  shape and got the same treatment.
- **Three checks that punished their own documentation.** `verify-rig` counting script tags
  hit a literal tag in a comment; `verify_chat`'s "never innerHTML" hit the comment promising
  never to use innerHTML. The comment gives way, never the check.

### Left for LB

- [ ] **Rotate the Gemini key** — it was in plaintext in three files
- [ ] Ingest datasheet PDFs into `data/`, run `python tools/vector_db.py`, then `verify_rag.py`
- [ ] Deploy to the Pi and run the six end-to-end scenarios; nothing here has run on hardware
- [ ] Decide on D3's option 3: put a local model back for PERSONA, which has no quota
