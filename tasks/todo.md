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

## Stage 9 — He reads LB's KiCad files ✅

**2026-08-21.** The HARDWARE agent could compute a trace width and could not look at a design.
D9 has the reasoning; this is what was done.

- [x] `pip install kiutils`; `kiutils>=1.4.8` added to `requirements.txt` under a Hardware
      agent heading — pure Python, ~93KB, and does **not** need KiCad installed
- [x] `tools/kicad_parser.py` — `extract_kicad_bom` (grouped by value + footprint, quantities,
      references) and `analyze_kicad_pcb` (copper layers, nets, footprints, thickness)
- [x] Sub-sheet walking, with **cycle** and **repeated sheet** told apart and both reported
- [x] Multi-unit parts de-duplicated by reference; the unit with a real footprint wins
- [x] A bare project **name** resolves under `ODDBALL_KICAD_ROOT` — a dictated path does not
      survive Whisper. Ambiguity is reported, never guessed between
- [x] `agents/hardware_agent.py` binds all three tools through a name→tool dict, keeps
      `AGENT_MODEL` (D3), `SPOKEN_INSTRUCTION` and the two-pass summary
- [x] `router.py` prompt: a question about a design **file** is HARDWARE, not OS
- [x] `tests/fixtures/kicad/` — 8 hand-written fixtures, one per hazard, with a README
- [x] `tools/verify_kicad.py` — 162 checks, offline, keyless; **9 mutations applied to the
      parser and all 9 caught**, so the green is worth something (L4)
- [x] `tools/verify_agents.py` — kicad_parser in the reachability sweep and in section 3
- [x] `media/` — the tutorial parser run live against the same fixtures, data + script + chart

Not built, deliberately: netlist/connectivity extraction, DRC, gerber export.

## Stage 9 — Opening applications, and getting off the API for the cheap things

Plan: `~/.claude/plans/check-mr-odd-ball-cheerful-emerson.md`. LB: *"he is struggling to open
Firefox and different apps on the pi."* Five defects on one path — see D10.

- [x] `Outcome` in `tools/os_controller.py` — the result is **stated**, not re-parsed from a
      prose prefix. A refusal is no longer reported as a malfunction, and a 15-second kill is
      no longer reported as "Done"
- [x] `tools/app_catalogue.py` — the machine's own `.desktop` database, not a curated table
      (a `which` sweep found `nautilus` missing from the old three-row list)
- [x] `tools/app_launcher.py` — `systemd-run --user --collect -p Type=exec`, Wayland socket
      discovered at launch time, its own cgroup so it survives a deploy
- [x] `orchestrator/launch_intent.py` — recognise a launch with **no model call**, verb +
      target + end anchor
- [x] The free tier moved IN FRONT of the router (`engine/core.py:_free_turn`). "open firefox"
      3 API calls -> **0**; time, date, convert, constants, definitions, arithmetic 1 -> **0**
- [x] `Pending.tool` so one gate serves two tools; `engine/core.py:_run_pending` unchanged
- [x] `tools/verify_launch.py` — 180 checks, 5 mutations, green on Windows **and** the Pi
- [x] Measured on the Pi: `media/data/2026-08-21-app-launch.csv` + script + chart
- [x] `config/oddball.service` — comments recording why there is no `Environment=` for the
      display, so the next person does not "fix" it
- [ ] A terminal/`Terminal=true` app has never been opened end to end — `lxterminal` is
      wrapped but only the argv is proven, not a real window

---

## Open question, to be answered with a number

Swapping `classify.py` (a pure function, ~0 ms) for `router_agent()` (a Gemini round trip,
~0.4–0.9 s) puts a turn near **2.5–3.2 s** against the 2.0 s budget in `~/oddball/docs/PLAN.md`.
This was LB's call and is not being second-guessed — Stage 8 measures it so the cost is a
number on a chart rather than a feeling.

## Review

**Stages 0-8 done, deployed, and running on the Pi.** ~11,000 checks green across 16 harnesses;
five carry a `--probe` that removes the guard and confirms the checks go red.

### Live on the hardware

`~/mr-odd-ball` on `oddball-pi`, systemd unit `oddball` enabled, face autostarting at 560x900
with the chat box under him at 50% opacity. He starts asleep — config, bridge and rig all agree.
The pre-merge assistant at `~/oddball` is stopped and disabled, kept as a fallback.

### What measurement changed

| finding | consequence |
|---|---|
| Free tier is **20 req/day/model**, not ~1,500 | jobs split across models; D3 |
| UTILITY had **no EE acronyms** — "what does i2c stand for" cost an API call and 2.20s | 19 rows added; 0.73s and free |
| The RAG pipeline was **never queried** | retrieval on the answer path, sources cited |
| The math sandbox had **no sympy** | a spoken `ModuleNotFoundError`; `verify_agents.py` now imports every library the prompt promises |
| Mic peaks **0.035-0.17 RMS**, wake scores **0.17-0.28** vs a 0.76 threshold | typed control shipped as the channel that works; the mic itself is still open |
| Amperes: **5 of 14 questions answered wrong**, 6 more refused | three separate defects in `convert.py`; 14/14 right, 0 wrong; D8 |
| The textbook KiCad parser: **7 of 12 questions error, 3 answered wrong**, and every schematic question failed on one wrong attribute name | `kiutils` used properly; 12/12 right, 0 wrong; D9 |
| A two-layer board's layer table has **29 entries**, and every net count is **one too high** | copper counted separately, net 0 named and excluded; L7 |

### What didn't work

- **PyQt6 for the floating face** — proposed in the plan; LB had already shipped GTK4 +
  WebKitGTK (D41). The spike was promoted, not replaced.
- **Probes that could not fail.** The first asserted a patched lambda returned None — a
  tautology. Rewritten to drive the real code path; the gate probe then had the same shape and
  got the same treatment.
- **Four checks that punished their own documentation.** A script-tag literal in a comment, a
  promise never to use `innerHTML`, and a `shell=True` docstring before that. The comment gives
  way, never the check.
- **`~=` pins captured from Windows** were unbuildable on a fresh aarch64 venv, and pip's error
  named the wrong package.
- **A slice-and-append edit** that silently deleted the `case` dispatcher off the end of
  `install_autostart.sh`, which then exited 0 having done nothing.
- **`PASTE_NEW_KEY_HERE`** pasted verbatim from my own instructions, on both boxes.
- **A harness check that asserted a bug.** `verify_convert.py` pinned "a bare `m` is not a
  unit" — true of a whole sentence, false of the fragment `_find_unit` actually receives, and
  it was the reason "5 A in mA" was refused for a week. Green is not the same as right. D8.
- **`schematic.symbols`**, which does not exist — the field is `schematicSymbols`. Inside a
  bare `except` that became *"Failed to parse schematic"* on every file: our typo, reported as
  the user's corrupt file, at 100%. Ten seconds of `dataclasses.fields()` would have caught it.
  D9, L6.
- **Comments in the KiCad fixtures.** kiutils has no comment syntax, so `;` lines parse as
  *tokens*: the annotated `flat.kicad_sch` came back with 17 symbols instead of 15, two of them
  fragments of prose. The notes moved to `tests/fixtures/kicad/README.md`.
- **A `return` split across an implicit string concatenation** in `hardware_agent._for_summary`
  — the second half of the instruction was structurally unreachable, and the model would have
  been told the result was truncated without being told not to guess at the rest. Caught by the
  editor's own diagnostic, not by me.

### Still open

- [ ] **The microphone.** Gain is maxed; he cannot reliably hear the wake word. Move the C270,
      re-derive the threshold from fixtures in LB's voice, or switch STT to `base.en`.
- [ ] **An unexplained Pi reboot** on 2026-08-19 at 14:41:43. No undervoltage, no OOM, and
      journald is volatile so the evidence went with it. Enable persistent journald first.
      Still unenabled, and it cost real time again on 2026-08-21: a transient unit's journal
      was gone before it could be read, so a launch failure had to be reproduced with
      `StandardOutput=file:` instead.
- [ ] **`usb 1-2` resets itself roughly hourly** — `dmesg` shows
      `reset high-speed USB device number 2 using xhci-hcd` at 2855s, 20661s, 21028s, 21125s,
      21387s, 21541s uptime. If device 2 is the C270 that is the wake-word microphone
      dropping out and back, which would fit "he cannot reliably hear the wake word" better
      than gain does. Confirm with `lsusb -t` before chasing the mic any further.
- [x] ~~`pip install -r requirements-rag.txt` on the Pi~~ — **done 2026-08-21**, CPU-only torch,
      venv 885 M -> 1.9 G with **0** nvidia packages. Retrieval proven end-to-end on the box. D12.
- [ ] **Put real PDFs in.** The stack is installed and working but there is nothing to read:
      LB's two Pi camera PDFs were image-only (0 extractable characters) and were removed, so
      `data/` is empty. Drop **text-bearing** datasheet PDFs under `data/`, syllabi under
      `data/academic/`, then `python tools/vector_db.py` — and check the printed line says
      *usable page(s)*, not *carried NO extractable text*. Then
      `python tools/academic_calendar.py` for the deadline banner (one API call per syllabus).
- [ ] **Consider dropping torch entirely.** `onnxruntime` is already installed for the wake word
      and Piper, and Chroma ships an ONNX build of the same `all-MiniLM-L6-v2`. That would take
      the RAG extras from ~1 G to near zero on the SD card. Needs a change to
      `tools/vector_db.py`'s embedding path and its own re-verification, so it is its own
      decision — measure the two embedding paths against the same fixtures before switching. D12.
- [ ] An image-only PDF is currently a dead end. If LB's real datasheets turn out to be scans,
      the options are OCR (`ocrmypdf`, needs tesseract) or sourcing text PDFs. Decide when a
      real one arrives rather than building for a case that may not occur.
- [ ] Re-measure turn latency **on the Pi** — the router leg logged 9.8s there against 750ms on
      Windows, and 52.7s for a first sympy import. Both want a warm re-run.
- [ ] Consider D3 option 3: a local model for PERSONA, which has no quota.

---

## Stage 10 — The vault, the desktop avatar, and gesture approval

**2026-08-21.** Three features on `oddball-integration`. Full reasoning in `docs/DECISIONS.md` D13.

- [x] `tools/knowledge_vault.py` — Markdown long-term memory under `vault/`, with a path-traversal
      guard and a 24k-character cap on what one search may put into a prompt
- [x] Bound `save_to_vault` / `read_from_vault` to **HARDWARE, FIRMWARE and GENERAL/persona** as
      the same two tool objects; FIRMWARE and PERSONA gained their first tool-call path, bounded
      at one round by unbinding the tools on the second invoke
- [x] `ui/avatar.html` — the floating ball: roll while thinking, bounce while speaking, dimmed
      while asleep, greyed when the socket is gone, with backoff reconnect
- [x] `ui/server.py` — FastAPI. `GET /ui`, `GET /healthz`, `WS /ws/state`
- [x] `ui/avatar_state.py` — stdlib-only fan-out, so `hud_bridge` can mirror state into the
      overlay without dragging FastAPI onto the voice loop's import path
- [x] `HudBridge.set_state()` mirrors to the avatar — **one writer of state, two surfaces**
- [x] `launch_ui.py` — frameless transparent pywebview window, not Chromium
- [x] `engine/run_voice.py --avatar [--avatar-port]`, in-process because state is published
      in-process; `python main.py --avatar` forwards it
- [x] `tools/gesture_control.py` — thumbs up at the camera instead of typing `y`
- [x] Wired into the terminal security checks in `agents/os_agent.py` and `agents/web_agent.py`
- [x] `requirements.txt` + `stage_install.sh` — separate `ui` and `vision` stages

### Review

**Verified, on the Windows authoring box:**

| check | result |
|---|---|
| every touched file byte-compiles | 13/13 |
| all five agents import; vault bound as the same objects | pass |
| three prompts format and name both tools | pass |
| vault: write, append, search by name, search by body, miss | pass |
| vault: `folder="../../etc"`, `filename="../../../pwned.md"` | contained at `vault/etc/pwned.md` |
| gesture classifier, 6 cases, no camera | 6/6 |
| `get_gesture()` / `gesture_approves()` with no camera | `NO_CAMERA` / `False` |
| `GET /ui`, `/healthz`, `/` redirect | 200 |
| `bridge.set_state()` → `/ws/state` | `sleeping, thinking, speaking, idle` |
| subscribers released on disconnect, ×3 cycles | 0 clients each time |
| `verify_chat.py` (HudBridge — the edited file) | 39/39 |
| `verify_os_guard.py` | 75/75 |
| `verify_engine.py` | 97/97 |
| `verify_typed.py` | 81/81 |
| `verify_speakable.py` | 59/59 |

### What didn't work

- **The obvious thumbs-up test approves an open palm.** `thumb_tip < index_mcp and thumb_tip <
  wrist` is true of any raised hand. On the OS path that turns a wave into an approved shell
  command. Fixed by requiring all four fingers curled and testing `OPEN_PALM` first; the case is
  now row 3 of the classifier test.
- **The `/ws/state` handler leaked subscribers.** Blocked on `await queue.get()`, so a closed
  window went unnoticed until the next state change and `/healthz` over-counted. A task now races
  the receive side.
- **`import webview` at the top of `launch_ui.py`** meant a box without pywebview got a bare
  `ModuleNotFoundError` and the "is anything even serving `/ui`?" check never ran — the least
  useful of three possible messages, printed instead of the most useful. Import moved inside.

### Still open — needs the Pi, and no number here came off it

- [ ] **mediapipe has no cp313 aarch64 wheel** and the Pi is on 3.13.5, so gesture approval is
      expected to be *uninstalled* there. Decide: leave it (keyboard still approves) or move the
      copilot to a 3.12 venv. `opencv-python` installs fine either way.
- [ ] `sudo apt install python3-gi gir1.2-webkit2-4.1 python3-gi-cairo` before `launch_ui.py`
- [ ] Measure camera-open + inference latency per approval → `media/data/`
- [ ] Measure overlay RSS vs. a Chromium window on the same page → `media/data/`
- [ ] Confirm `transparent=True` composites under Bookworm's Wayfire session
- [ ] A `tools/verify_vault.py` in the house style, so the checks above are a committed harness
      rather than something run once in a session

---

## Stage 11 — Pi deployment: autostart, apt deps, and the gesture interpreter

**2026-08-22.** Deployed to `oddball-pi` (10.0.0.96). Reasoning in D14 and D15.

- [x] `sudo apt install ...` line tracked — `stage_install.sh` `dpkg-query`s all five prerequisites
      at the top of the run and prints the exact command for whichever are missing. It does not
      run `sudo` itself: the script runs detached, where a password prompt hangs forever.
- [x] `config/mroddball.desktop` → `~/.config/autostart/`, installed by `install_autostart.sh`
- [x] `tools/wait_for_ui.sh` — polls `/healthz` up to 90 s, then Execs `launch_ui.py`
- [x] `--avatar` added to `config/oddball.service`, because the server must be in-process
- [x] `venv/pyvenv.cfg` flipped to `include-system-site-packages = true` on the Pi, so pywebview
      can reach the system PyGObject. One line, not a 1.9 G rebuild.
- [x] `tools/install_gesture_venv.sh` — the Python 3.12 sidecar venv, built and working
- [x] `get_gesture()` reworked to **always** run out-of-process
- [x] Latency measured, charted, data + script committed

### Verified ON THE PI

| check | result |
|---|---|
| deploy, both `PIPESTATUS` | `0 0` |
| `desktop-file-validate config/mroddball.desktop` | VALID |
| `GET /healthz`, `GET /ui` | 200, `{"ok":true,"state":"sleeping"}` |
| `bridge.set_state()` → `/ws/state`, in-process | `sleeping, thinking, speaking, idle` |
| vault writes and reads | ok |
| sidecar venv | Python 3.12.14, mediapipe 0.10.18, **runs** |
| `--backend` from the main 3.13 venv | worker says `NONE`, parent survives |
| approval latency | 2,217 ms median of 10 (min 2,197, max 2,271) |
| main venv after cleanup | mediapipe and opencv-contrib removed |

### What didn't work

- **mediapipe 1.0.1 installs on Python 3.13 and cannot run there.** SIGKILL at XNNPACK delegate
  creation, every vision task, no OOM and 6.4 GB free. D14 said the opposite on the strength of
  a `pip --dry-run`; D15 retracts it. LB's original instinct — a 3.12 interpreter — was correct.
- **The first sidecar tried in-process first and fell back.** On the Pi the fallback was
  unreachable: the process died constructing the detector it was about to decide not to use. A
  fallback after an uncatchable failure is not a fallback.
- **`import webview` at module scope** in `launch_ui.py` — fixed the day before, same class of
  bug, worth noting it recurred in a different file.

### Still open

- [x] `bash tools/install_autostart.sh` run on the Pi — unit + both desktop entries installed,
      `--avatar` confirmed present, `Linger=yes`
- [x] `systemctl --user restart oddball` — the live process now carries `--avatar` and answers
      on :8000. Verified live: a typed wake on the rig's 8765 socket drove the avatar's
      `/ws/state` from `sleeping` to `listening`.
- [x] Line endings — the deploy shipped CRLF shell scripts and broke `install_autostart.sh` on
      the Pi. Working copy normalised to LF; see the new section in DEPLOY.md.
- [x] `sudo apt install gir1.2-webkit2-4.1` — done by LB, `2.52.5-1~deb13u1`. All five apt
      prerequisites now present.
- [x] **The window opens.** `launch_ui.py` running on the Pi, `/healthz` reports `clients: 1`,
      and a full turn drove `sleeping -> thinking -> speaking -> sleeping` on `/ws/state` with
      the real window attached. Stage 10 and 11 are complete.
- [x] **Looked at it — and it was broken.** LB's photo showed an empty rectangle with a title
      bar. Two defects, neither of which emits any error (D16):
      - `WEBKIT_DISABLE_DMABUF_RENDERER=1` — without it WebKitGTK paints torn buffer garbage
        instead of the page. A JS probe inside the live window proved the DOM was perfect.
      - `GDK_BACKEND=x11` — without it `frameless=True` is ignored; GTK3's Wayland backend
        never negotiates xdg-decoration so labwc decorates anyway.
      Both now set by `launch_ui._prepare_env()`. Verified by screenshot.
- [x] CSS: `.sleeping` box-shadow never applied — `#ball` outranks a bare class. Now
      `#ball.sleeping`.
- [x] `media/captures/2026-08-22-avatar-on-desktop.png` and the before/after pair committed.
- [ ] **Placement.** The ball lands on top of the chat panel. Wayland lets no client place its
      own window, so labwc decides — same constraint `hud/float.py` has. `Super+drag` moves
      him. Whether 300x300 always-on-top in the middle of the screen is the right presence is
      a preference, not a measurement: LB's call.
- [ ] Overlay RSS vs a Chromium window on the same page — still not measured.
- [ ] Persistent gesture worker: pay the 1.0 s `import mediapipe` once instead of per approval.
      Would take 2,217 ms → ~850 ms. Not built; 2.2 s at a prompt that already stops to ask is
      tolerable, and it trades a subprocess call for a lifecycle to manage.
- [ ] Measure detection rate vs `WARMUP_FRAMES`. It is 4 (602 ms of the 2,217) and cutting it
      without that measurement is guessing.
- [x] `transparent=True` composites under labwc — confirmed by screenshot, and it survives
      the move to XWayland.
- [ ] `tools/verify_vault.py` and `tools/verify_gesture.py` in the house style, so the classifier
      cases and the vault traversal guard are committed harnesses rather than session scratch.
