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

- [ ] Copy `audio/` (wake, listen, stt, say, gate) from `~/oddball`
- [ ] Copy `orchestrator/hud_bridge.py`, `settings.py`, `formulas.py`, `memory/speakable.py`
- [ ] Copy `hud/face-preview.html`, `assets/`, `models/hey_mr_odd_ball.onnx`, `config/oddball.toml`
- [ ] Copy the `tools/verify_*.py` harnesses for what came across
- [ ] Merge `requirements.txt` from both; note `--no-deps openwakeword` and the two apt packages
- [ ] Prove the voice path still passes after the move

## Stage 2 — The response envelope (speech vs. visual)

- [ ] `engine/response.py` — `Response(speech, cards, route, pending)` + `Card(kind, title, body, lang)`
- [ ] `SPOKEN:` line appended to every agent prompt template
- [ ] `engine/split.py` — fences → code cards, tables → table cards, terminal output → log cards
- [ ] Fall back to `speakable.extract()` at `MAX_WORDS = 40` when no `SPOKEN:` line arrives
- [ ] Hard filter: never speak code, tables, URLs, paths, hex literals, tracebacks
- [ ] Second LLM pass in `math_agent` and `hardware_agent` — they return raw tool output today

## Stage 3 — Router as the only dispatcher

- [ ] Add `PERSONA` (chit-chat, jokes, identity) and `UTILITY` (time, date, convert, constants)
- [ ] Fix `ROUTER_PROMPT` — it documents 5 of 7 routes; `OS` and `QUIZ` are missing
- [ ] `engine/core.py` — `Engine.ask(text) -> Response`, no `print()`, no `input()`

## Stage 3b — Wire the RAG pipeline into the firmware agent

- [ ] Fix `CHROMA_PATH`/`DATA_PATH` — currently cwd-relative, writes outside the repo
- [ ] `get_retriever(k=4)`, opened once at import, returns `None` when unbuilt
- [ ] `{datasheet_context}` in `FIRMWARE_PROMPT_TEMPLATE`; prefer retrieval over model memory
- [ ] Sources card from Chroma's `source` / `page` metadata
- [ ] `glob="**/*.pdf"` so the `data/` subdirectories are recursed

## Stage 4 — The voice loop

- [ ] `engine/turn.py`, modelled on `~/oddball/orchestrator/turn.py`
- [ ] Keep: conditional greeting, `Timings`, WAV capture saving, mic gating
- [ ] `main.py` becomes a launcher: `--voice` (default), `--text`, `--headless`

## Stage 5 — Gates and the quiz lock over voice

- [ ] Split `os_agent` / `web_agent` into `propose()` / `resume(approved)`
- [ ] `Pending` carries a spoken paraphrase AND the exact command; card renders first
- [ ] Copy `is_yes()`; silence, mumble, timeout and refusal all mean no
- [ ] Extend the `forbidden_commands` blocklist (no `dd`, `shutdown`, `> /dev/sd*`, `chmod -R 777 /`)
- [ ] Quiz lock: exit-phrase family, wake-word escape, visible QUIZ MODE chip

## Stage 6 — HUD chat panel

- [ ] Outbound `card` / `transcript` / `mode` / `pending` messages in `hud_bridge.py`
- [ ] Inbound receive loop: typed `text`, `approve` — `broadcast_threadsafe` in reverse
- [ ] Chat column in `face-preview.html`; code cards, real tables, scrollable logs
- [ ] Panel collapses when a turn produces no cards

## Stage 7 — Floating on the Pi desktop — **mostly already built**

Not PyQt6. `tools/spike_gtk_face.py` already does this with GTK4 + WebKitGTK, and D41 records
him running on the Pi desktop transparently today:
`--url 'http://127.0.0.1:8765/?solo=1' --transparent --undecorated`.
`tools/install_autostart.sh` already installs the systemd user unit and the XDG autostart entry.

- [x] Transparent, undecorated, always-on-top face window — exists, proven on hardware
- [x] Autostart at boot — `config/oddball.service` + `config/oddball-face.desktop`
- [ ] Promote `spike_gtk_face.py` to `hud/float.py` — it is the application now, not a spike
- [ ] A third rig mode: face **+ chat panel**, no rig chrome (`?solo=1` hides the panel today)
- [ ] Repoint `oddball.service` `ExecStart` at the merged entry point
- [ ] `sudo apt install gir1.2-gtk-4.0 gir1.2-webkit-6.0 libwebkitgtk-6.0-4 python3-gi`

## Stage 8 — Verification

- [ ] `verify_split.py` — **and prove it bites**: plant a fenced C++ block in the prose path
- [ ] `verify_gates.py` — nothing executes without an explicit yes
- [ ] `verify_router.py` — all 9 routes reachable, `PERSONA`/`UTILITY` don't swallow EE questions
- [ ] `verify_quiz.py`, `verify_rag.py`
- [ ] End-to-end on the Pi, six scenarios
- [ ] `media/` — turn-latency data, script and chart

---

## Open question, to be answered with a number

Swapping `classify.py` (a pure function, ~0 ms) for `router_agent()` (a Gemini round trip,
~0.4–0.9 s) puts a turn near **2.5–3.2 s** against the 2.0 s budget in `~/oddball/docs/PLAN.md`.
This was LB's call and is not being second-guessed — Stage 8 measures it so the cost is a
number on a chart rather than a feeling.

## Review

_Filled in as stages land._
