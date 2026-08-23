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
- [x] `verify_router.py` — 164 checks, probe bites 4/4. Ten routes now, not nine. The second
      half of this line was the real job: the zero-token hints (D27) refuse every ambiguous
      keyword, and M3 proves it by restoring the rejected dictionary and going 21 checks red
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
- [x] **Placement — resolved.** LB's call: 150x150, bottom-right. Wayland lets no client place
      its own window, so it is a labwc window rule (`tools/install_labwc_rule.sh`), which he
      authorised. Verified on the box: `Absolute upper-left 1746,906`, `150x150`.
      - the script bases on `/etc/xdg/labwc/rc.xml` when no user file exists, because a user
        `rc.xml` REPLACES the system one — writing a minimal file would drop 183 lines of Pi OS
        defaults
      - it validates the XML and reverts from backup on a parse error
      - it reloads with SIGHUP; `labwc --reconfigure` needs `LABWC_PID`, which an ssh shell
        does not have, and it was failing silently
      - `allowAlwaysOnTop="yes"` was a real find: labwc disallows X11 always-on-top by default,
        so `on_top=True` had been doing nothing
- [x] **CSS is in vmin now,** with the invariant `--ball/2 + --roll + --glow <= 50vmin` at the
      top of the file. The old hardcoded `translateX(±80px)` roll would have thrown the ball
      clean outside a 150px window. Measured over 4 frames of each animation: spans x 11..143,
      y 15..126 of 150 — inside at every extreme.
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


---

## Stage 12 — Scrap the second face; animate the one that already exists

**2026-08-22.** LB: *"I already have a main frontend UI rendering my character. I do NOT want a
separate glowing blue orb in the corner."* He is right. Full write-up in D17.

- [x] Deleted `ui/`, `launch_ui.py`, `config/mroddball.desktop`, `tools/wait_for_ui.sh`,
      `tools/install_labwc_rule.sh`, the `--avatar` flag and its in-process server, the
      `hud_bridge.set_state` mirror, and fastapi/uvicorn/pywebview from requirements
- [x] Reverted the labwc rule on the Pi, then removed `~/.config/labwc/rc.xml` once it was
      byte-identical to the system default — the box is back to how I found it
- [x] Removed the orphans on the Pi (tar does not delete) and the autostart entry
- [x] **`thinking` added to `STATES`** — it was missing, so `setState("thinking")` hit the
      `if (!STATES[next]) return` guard and his face had **never** reacted to thinking
- [x] `enter:"roll", loop:1` on `thinking`; `enter:"bounce", loop:1` on `speaking` — the rig's
      OWN existing gestures, not new ones
- [x] `loop` support in `sampleGesture`, safe because every channel returns to exactly 0 at p=1
- [x] Panel button + `t` keyboard shortcut, because `verify-rig.mjs` asserts STATES keys match
      the buttons and every state is reachable from the keyboard

### Verified

| check | result |
|---|---|
| `tools/verify-rig.mjs` | 38/38, 15 states loaded |
| STATES keys match panel buttons | pass |
| every state reachable from the keyboard | pass |
| roll and bounce keep him inside the viewBox | pass, worst margin 11.0px |
| `verify_chat` / `verify_engine` / `verify_typed` | 39/39, 97/97, 81/81 |
| live turn on the Pi | `sleeping -> thinking -> speaking` |
| thinking, 3 frames | moved **43px** horizontally, size varied **107px** |
| speaking, 3 frames | moved **11.1px** vertically, 0.7px horizontally |
| port 8000 | not listening |
| unit ExecStart | no `--avatar` |

`media/captures/2026-08-22-face-thinking-speaking.png`.

### What didn't work — kept, not deleted

The whole floating-avatar subsystem, D13-D16. It worked; it was the wrong thing. The captures
stay in `media/captures/` (`2026-08-22-avatar-render-before-after.png`,
`2026-08-22-avatar-on-desktop.png`, `2026-08-22-avatar-corner.png`) because a dead end that
gets quietly deleted is a lesson nobody can check. The WebKit findings in D16 are still true
and still useful if a pywebview window is ever wanted for something else.

### Still open

- [ ] `--system-site-packages` is still on in the Pi's venv. It was turned on for pywebview,
      which is gone. Harmless and reverting has its own risk, so it stays — noted so it is not
      a mystery later.
- [ ] `gir1.2-webkit2-4.1` is still installed on the Pi. Dropped from `stage_install.sh`'s
      list; nothing asks for it now. Left in place, harmless.
- [ ] Should `thinking` really tumble a full 540 degrees on repeat for a long Gemini call? It
      is what was asked for and it looks right for a 1-3s call. If it grates over a 10s one,
      the gentler option is a new `rock` gesture (small travel, no spin) rather than tuning
      `roll`, which `happy` also uses via `finish`.

## Follow-ups from D18 (2026-08-22)

- [ ] **Measure gesture detection rate before tuning it again.** `WARMUP_FRAMES` (4 -> 5) and
      `MIN_DETECTION_CONFIDENCE` (0.6 -> 0.5) were both changed on a *guess* at the cause — an
      underexposed frame. There is still no detection-rate-versus-warmup-count measurement, so
      the next nudge would be guessing on top of a guess (D14). Save a set of frames and score
      them.
- [ ] **The persistent gesture worker.** This is the actual fix for approval latency: the
      measured 2,217 ms is 1,009 ms of `import mediapipe` paid per approval, against 204 ms to
      open the camera. Keeping a pipe open would bring it to ~850 ms. Deliberately not done as
      part of D18 — it turns a subprocess call into a lifecycle to manage.
- [ ] **Watch the hangover share.** `hangover_s` is 2.00 s, which is 80% of the 2.5 s answer
      budget, spent as silence on EVERY turn including the free lookups. `verify_stt.py` prints
      that share on every run. If LB starts complaining about waiting on fast answers instead of
      being cut off, the fix is not a smaller number either — it is push-to-talk, or a VAD that
      ends on intent rather than on a stopwatch.
- [ ] Grammar nit in `expand_symbols`: "a 10 kilohms resistor" should be "kilohm" when the unit
      is attributive. Correct when it stands alone ("that is 5 ohms"), which is the common case.
      Needs part-of-speech awareness to fix properly; not worth it yet.

## From D19 — KiCad 9.0.2 is now on the Pi (2026-08-22)

- [ ] **Wire the demo corpus into `verify_kicad.py` as an OPTIONAL section.** 85 schematics and
      16 boards at `/usr/share/kicad/demos`, written by KiCad rather than by us — the existing
      fixtures are all hand-written and cannot surprise us about the real format. Must skip
      cleanly when the directory is absent, so Windows and a fresh clone stay green (the same
      shape as `verify_academic.py --store`). Script to start from:
      `media/scripts/run_kicad_demos.py`.
- [ ] Re-run that corpus after any kiutils bump. It is the cheapest possible regression signal
      for a format-version change, and it costs no API calls and no fixtures to maintain.
- [ ] `vme-wren.kicad_pcb` (70 MB) is refused by the 50 MB guard. Correct for now. If LB ever
      has a board that big, the answer is streaming or a summary pass, not a bigger constant.

## From D20 (2026-08-22) — the Firefox session

- [ ] **The microphone, still.** Measured: captures that transcribe are CLIPPING at 0.9999
      peak; captures that fail sit at 0.015-0.019 RMS. Gain is already maxed (16/16, +30 dB),
      so there is no usable middle. D20 routes around it with the thumbs up; it does not fix
      it. Options in order of effort: move it closer / off the desk surface, drop the gain and
      re-measure the range, or a different microphone.
- [ ] **`oddball.service` has no `WAYLAND_DISPLAY`.** Confirmed on the running process — it
      starts ~8s after boot, before the desktop imports its environment into the user manager
      (D10 defect 1). Harmless today because `find_display()` globs the socket, but the unit is
      still wrong. Either `After=graphical-session.target` or an ExecStartPre that imports the
      environment.
- [ ] Re-measure gesture approval latency now that WARMUP_FRAMES is 5 (was 4). The 2,217 ms
      table in gesture_control.py is the 08-21 figure and says so.
- [ ] Consider a HUD chip showing "quota exhausted until <time>" when the latch is on. He says
      it once; the screen could keep saying it.

## From the boot-race fix (2026-08-23)

- [x] ~~Reboot the Pi to confirm the fix on the path it was written for.~~ **Done 2026-08-23**,
      and the race was real:

        04:54:53  boot
        04:55:01  unit starts — ExecStartPre begins
        04:55:01  labwc starts        <- the SAME SECOND. This is the collision.
        04:55:04  ExecStart runs, with WAYLAND_DISPLAY

      `ExecStartPre` held ~3s and the service came up with `WAYLAND_DISPLAY=wayland-0` and
      `DISPLAY=:0`, where before the fix it had `XDG_RUNTIME_DIR` and nothing else.
      `NRestarts=0`. Firefox launched end to end on the fresh boot.

      **Read it off the SERVICE process, not an ssh shell** — an ssh session never has
      `WAYLAND_DISPLAY`, so checking from one measures the wrong thing and reports a fallback
      that is not being used. That mistake was made once already here:

        PID=$(systemctl --user show oddball -p MainPID --value)
        tr '\0' '\n' < /proc/$PID/environ | grep WAYLAND_DISPLAY    # expect wayland-0
- [ ] `journalctl --user` captures nothing on this box, so `ExecStartPre`'s own log line is not
      retrievable and the unit timestamps had to stand in for it. Pre-existing (the app logs to
      `oddball.log` instead), not caused by the boot-race change, but it means anything a
      `ExecStartPre` or `ExecStopPost` prints is currently written to nowhere.
- [ ] **CRLF keeps coming back from the Windows side.** Every Python-scripted edit
      (`Path.write_text`) reintroduces it — 23 files on 2026-08-23, including
      `config/oddball.service`, where it reached the Pi's installed unit. systemd tolerated it;
      shell scripts do not. DEPLOY.md has the check and the fix script. Worth a pre-deploy hook
      rather than remembering.

## Upload pipeline — one paperclip, three destinations (2026-08-23)

LB: *"I want to be able to upload files directly through my existing chat UI"* — syllabi,
datasheets and KiCad schematics, sorted to the right agent without touching the file system.

**The spec named `engine/server.py` (FastAPI). There is no FastAPI server.** D17 deleted
`ui/server.py` and dropped `fastapi` / `uvicorn` / `pywebview` from requirements on purpose, so
the voice loop's import path stays clean. Re-adding an ASGI stack for one endpoint would
reverse that decision. `POST /upload` is built at the path LB named — `engine/server.py` — on
stdlib `http.server`, in a daemon thread, and it accepts `multipart/form-data` exactly as asked.

It cannot share port 8765: that port belongs to `websockets`, whose `process_request` hook is
handed a `Request` with **no body** (checked, websockets 15.0.1), so a POST body is unreadable
there. So the upload server takes its own port, and the rig learns it as a constant with a
`?up=` override, mirroring the existing `?ws=`.

- [x] `engine/server.py` — `POST /upload`, multipart, saves to `data/inbox/`, returns the path
- [x] Origin check: local http origins only, so a page on the internet cannot write to the inbox
- [x] `tools/file_manager.py` — `process_inbox_file(filename, category, project)`, `list_inbox`,
      `list_project_files`, `index_status`
- [x] Rebuilds run on a BACKGROUND thread — `build_vector_database()` loads torch and re-embeds
      everything; doing that inline would wedge the turn and freeze his face mid-`thinking`
- [x] `data/projects/` for schematics; zips extracted with zip-slip guards
- [x] `tools/kicad_parser.py` searches `data/projects/` as well as `ODDBALL_KICAD_ROOT`
- [x] Bind the file tools to GENERAL/persona (the filer), HARDWARE and FIRMWARE
- [x] Router: an upload announcement routes to GENERAL, whatever the file is
- [x] `hud/face-preview.html` — paperclip beside the input, native picker, fetch, and the
      injected "I just uploaded X." line
- [x] `tools/verify_upload.py` — parser, sanitiser, live round trip
- [x] `tools/verify-rig.mjs` must still pass: one `<script>` block, every `$("id")` in the markup

### Review — 2026-08-23

Done and proved. `tools/verify_upload.py` is **134 checks, all green**: the multipart parser
(including a payload whose own bytes contain the boundary), the filename sanitiser's traversal
table, the origin table, the picker's `accept` list against the server's allow-list, a live HTTP
round trip with all five refusals, the three filing destinations, and the agent wiring.

Everything that already existed still passes, unchanged: `verify-rig.mjs` 38, `verify_kicad`
168, `verify_engine` 106, `verify_split` 93, `verify_typed` 81, `verify_agents` 53, `verify_chat`
39, `verify_academic` 25.

Run end to end against the real tree, not only against fixtures — curl to the endpoint, then
`--list`, then `--as schematic`, then `extract_kicad_bom("amp board")` returning the actual BOM
of the uploaded file by its spoken name. That run is what found `.gitkeep` being offered for
filing ([[L15]]).

**Measured:** 16 KB in 1.5 ms, 1 MB in 4.0 ms, 60 MB (against the cap) in 181 ms.
`media/charts/upload-latency.svg`, `media/data/2026-08-23-upload-latency.csv`, regenerable with
`media/scripts/measure_upload.py`. The curve is flat across anything LB would actually attach,
which is what says the single-shot in-memory design is right and a streaming upload with a
progress bar would be complexity bought with nothing.

Three bugs found and fixed during the work, all recorded: `project_file` unreachable through the
category table ([[L16]]), `relative_to()` raising inside a tool called from a turn ([[L16]]), and
`.gitkeep` listed as a pending upload ([[L15]]).

### What was NOT built as asked, and why

The specification said FastAPI. **This repo deleted FastAPI on 2026-08-19 (D17)** and
`requirements.txt` still carries the note. The endpoint is at `engine/server.py`, the path LB
named, accepting `multipart/form-data` as he asked, on stdlib `http.server` — no new dependency
and nothing new on the voice loop's import path. D21 and [[L14]] have the argument.

It could not share port 8765 either: `websockets.http11.Request` has fields `path`, `headers`,
`_exception` and **no body**, checked against the installed 15.0.1. So 8767, with `?up=host:port`
on the rig mirroring the `?ws=` it already had.

### Deployed 2026-08-23, and the deploy found four things

Steps 1-4 and 6 of the runbook are **done on the Pi** (`familyhub`, 10.0.0.96). `verify_upload`
138/138 there, both ports bound in ~4 s, `NRestarts=0`, no errors in `oddball.log`. A real
schematic went through the whole chain — `curl` to the endpoint, `--list`, `--as schematic`,
then `kicad_parser "amp board"` returning the actual BOM by the dictated name. Refusals checked
against the live service: 415 bad extension, 403 foreign origin, 415 non-multipart. Test
artifacts removed; `data/` is back to exactly what it was.

**1. `/healthz` and `list_inbox` disagreed about what "waiting" means.** healthz said 1, the tool
said 0, both describing an inbox holding only the committed `.gitkeep`. I had taught the tool to
skip dotfiles and left the endpoint counting them — two definitions of one thing, which is the
mistake `engine/core.py` already warns about for the conversation log. `pending_uploads()` now
lives in `engine/server.py` and the tool calls it; `INBOX_DIR` is imported rather than restated.
[[L15]] again, one layer up.

**2. `python tools/file_manager.py` stopped working.** The fix above added a module-level
`from engine.server import ...`, and running the file as a SCRIPT puts `tools/` on `sys.path`,
not the repo root. Every check passed while that was broken, because the harness imports the
module. The harness now runs both CLIs as subprocesses.

**3. The deploy silently restored two deleted PDFs.** `data/**/*.pdf` is gitignored, so `git
status` says nothing — and `tar` ships it anyway. `pi_cam3.pdf` and `pi_cam3_noir_wide.pdf`
came back onto the Pi, where D12 had deliberately removed them for being image-only. Removed
again, `data/` restored, and DEPLOY.md now has the hazard and `--exclude=data`.

**4. "Minutes" was wrong, and it was written into a prompt.** Measured on the Pi: **11.4 s**
before a single chunk is embedded (torch 2.1 s, langchain_huggingface 1.3 s, model off the SD
card 8.0 s), then 14.4 ms/chunk. Three trials, spread under 0.15 s.
`media/data/2026-08-23-index-rebuild-familyhub.csv`, `media/scripts/measure_index_rebuild.py`.

The design does not change — an 11 s freeze on the turn path is still unacceptable, and the
per-chunk cost multiplies by the whole corpus rather than the new file. What changed is what he
SAYS: every "a few minutes" was removed, and the tools now promise **no duration at all**,
because the honest answer depends on how much has been uploaded.

### Still open

- [ ] **Step 5 — the paperclip itself — still needs a human.** Everything up to it is proved on
      the Pi; pressing a button in a window on a physical display is not something a deploy can
      do. `hud/float.py` logs when WebKit asks for a chooser, so the log separates "no click"
      from "no dialog". `docs/DEPLOY.md` has the six-step runbook
      ordered so each step narrows where a failure is. Step 5 is the only claim no harness can
      ordered so each step narrows where a failure is.
- [ ] Re-run `media/scripts/measure_upload.py` on the Pi. The committed upload numbers are still
      Windows loopback and the meta file says so. The rebuild fixed cost IS now measured there.
- [ ] **Still no PDFs on the Pi.** `data/` holds a README and three `.gitkeep`s, and `chroma_db`
      has never been built — so no upload has yet triggered a real rebuild end to end, and the
      per-chunk figure has not been checked against an actual datasheet. This is the same open
      item as "Put real PDFs in", and the first real syllabus upload will close both.
- [x] ~~`config/oddball.toml` gained `[hud] upload_port` and `settings.py` validates strictly.~~
      Now **step 1 of the Pi runbook** in `docs/DEPLOY.md`, because the failure mode is "he
      stopped starting after the deploy" and it should be the first thing ruled out.
- [ ] The three-way tool split in `agents/firmware_agent.py` answers from the FIRST family that
      matched — vault, then files, then KiCad — so a model asking for a schematic AND a vault
      write in one turn silently loses the vault note. Deliberate for now; the case has not been
      seen. If it ever is, the fix is one prompt holding all three result sets.
- [x] ~~A file uploaded while the assistant is not running sits in the inbox and nobody is told.~~
      `engine/run_voice.py` now logs what is waiting at startup. He still does not *say* it —
      speaking an inbox backlog unprompted would be an alarm, and this is a reminder — so the
      log is the channel. Revisit if a file ever goes missing in practice.
- [ ] The upload endpoint has no rate limit. It is loopback-only with an Origin check, so the
      threat is a script LB ran himself, but a runaway loop would fill the SD card.

## Canvas replaces the syllabus PDFs as the source of dates (2026-08-23)

LB: *"The static syllabus PDFs contain outdated dates. Instead, we are switching to a live
Canvas LMS `.ics` calendar feed."* Full reasoning in D22.

- [x] `tools/canvas_sync.py` — fetch, parse with `icalendar`, write `academic_calendar.json`
- [x] `sync_canvas_calendar` bound to the ACADEMIC agent, with prompt rules for when NOT to call
      it (a sync per question is a network round trip per turn)
- [x] `router.py` — "sync Canvas" / "update my schedule" routes to ACADEMIC, **not OS**, which is
      what an imperative about "my schedule" otherwise looks like
- [x] Syllabus RAG untouched — `data/academic/` still embeds into the `academic` collection and
      the agent still retrieves policy prose. Only date extraction retired.
- [x] `tools/file_manager.py` — an academic upload no longer extracts dates. Two model calls to
      zero, and the sentence he says points at Canvas instead.
- [x] `extract_deadlines_from_syllabi()` preserves Canvas rows on every run, so the old script
      cannot revert the calendar to stale dates
- [x] `icalendar` in `requirements.txt` **and** in `stage_install.sh` — `verify_agents.py` caught
      that the second one was missing, which would have failed every fresh Pi install
- [x] The feed URL is in `.env` as `ODDBALL_CANVAS_ICS`, never in source. `verify_upload.py`
      greps for a token and fails if one reappears.
- [x] `format_calendar_for_llm` bounded — one course produced 139 deadlines against a syllabus
      extraction's ten or twenty

### Verified on the Pi

`venv/bin/python tools/canvas_sync.py` against the live feed: **139 events, 1 course, 0 skipped,
0 duplicates**, written to `academic_calendar.json`. Read back through the real readers:
139 entries load, 0 due within 3 days (nearest is 2026-08-30, correct), 23 within 10.
No token in the written file. `verify_upload.py` 160/160 and every other harness green.

**Measured, and it changed the design:** the calendar in the prompt went from ~2,400 tokens
(139 lines) to ~1,583 (89 lines) once bounded, and it states what it left out. Five courses
would have been ~12,000 tokens on every academic turn.

The UTC-to-local conversion is pinned: `2026-09-02T03:59Z -> 2026-09-01`. All 139 of LB's
current events are all-day dates so it does not bite today, but Canvas emits a datetime for
anything with a time and an 11:59 PM due time is stored as 03:59Z the next day.

### Still open

- [ ] **Only one course is in the feed** (`POSC201`). The course-code cleaning, the type
      inference and the prompt bound are all validated against one course's shape. When his
      other courses appear, re-run `--dry-run` and look at the `courses` count and the type
      split before trusting the banner.
- [ ] **Type inference is keyword-based and untested against real exams.** LB's feed has
      57 assignments and 82 quizzes and **no exams at all** — so the `exam` branch, which is the
      one that matters most, has never matched a real event. `_TYPE_RULES` puts exam first
      deliberately; confirm it fires when a midterm actually appears.
- [ ] `--keep-syllabus` exists but has never been exercised against a calendar that holds both
      kinds of row, because the PDF extractor has never run on the Pi (no syllabi are ingested).
- [ ] The sync is not on a schedule. It runs when LB asks. A daily `systemd --user` timer is the
      obvious next step and is deliberately not built — a background job that rewrites his
      deadlines without being asked is a thing to decide on, not to assume.

## Excise the syllabus RAG — ACADEMIC is Canvas-only (2026-08-23)

LB: *"having a redundant system that parses syllabus PDFs is creating unnecessary complexity and
risk of conflicting data... completely excise the Syllabus PDF / Academic RAG feature."* D23.

**Said before doing it: the premise had already expired.** D22 retired PDF date extraction one
commit earlier, so nothing conflicted. What was actually deleted is the only path that could
answer a course-POLICY question — late penalties, grading splits, exam formats. LB confirmed.

- [x] `agents/academic_agent.py` — retrieval gone, Sources card gone, `NO_SYLLABI` gone, prompt
      rewritten as a schedule manager
- [x] **New prompt section teaching him what he CANNOT answer.** Removing context does not stop
      a model answering a policy question; it makes it answer from convention. That is D11's
      fabrication through a door D11 did not cover.
- [x] `tools/vector_db.py` — `ACADEMIC_COLLECTION` and its build removed; **the `data/academic/`
      exclusion KEPT**, and it matters more now that there is no second pool to catch a leak
- [x] `tools/academic_calendar.py` — `extract_deadlines_from_syllabi`, `_documents_by_source`,
      `EXTRACTION_PROMPT`, `Deadline`, `SyllabusExtraction`, and the pydantic/typing imports.
      A reader now; its CLI prints the calendar instead of building it.
- [x] `pypdf` reaches `data/` through exactly one caller — `vector_db.load_pdfs`, for datasheets
- [x] Upload category `academic` KEPT, as a store-only destination. Deleting it would send an
      uploaded syllabus to `datasheet` and into the firmware agent's pool.
- [x] `tools/verify_academic.py` rewritten — section 2 now proves retrieval does NOT happen, and
      that he is told he cannot answer policy questions
- [x] Firmware/hardware/KiCad/upload endpoint/paperclip: **zero-line diff**, verified with
      `git diff --stat`

### Verified

`verify_upload` 166/166, `verify_academic` 29/29, `verify_agents` 53/53, `verify_engine`
106/106, `verify_chat` 39/39, `verify_typed` 81/81, `verify_split` 93/93, `verify_kicad`
168/168, `verify_os_guard` 75/75, `verify_speakable` 59/59, `verify-rig` 38/38.

### Still open

- [x] ~~`--store` has never been run since the change.~~ **Run on the Pi 2026-08-23: 34/34**,
      with real embeddings. A syllabus under `data/academic/` is provably not retrievable by the
      firmware agent, which is the only barrier left now that the second collection is gone.
      It stays opt-in (it pulls torch), so re-run it after any change to `load_pdfs`:
      `venv/bin/python tools/verify_academic.py --store`
- [ ] Any existing `chroma_db/` on a box still holds the retired `academic` collection. Nothing
      opens it, so it is inert — but it is stale bytes on an SD card. A rebuild drops it:
      `rm -rf chroma_db && venv/bin/python tools/vector_db.py`
- [ ] He now has no answer at all for "what's the late policy". If that turns out to matter, the
      cheapest restoration is not the RAG — it is `save_to_vault`, which already exists: LB tells
      him the policy once and it is in the Markdown vault, greppable, for the term.

## Syllabus -> vault note: policies back, without the RAG (2026-08-23)

LB: *"dropping it into the Markdown Vault takes 5 seconds, avoids RAG overhead, and keeps the
system fast and lightweight."* D24. This puts back what D23 removed, at a fraction of the cost.

- [x] `tools/syllabus_to_vault.py` — pypdf -> one structured Gemini call -> `vault/courses/*.md`
- [x] **A textless PDF never reaches the model.** The guard is upstream of the API call, so a
      folder of scans costs nothing. A model handed an empty document invents a whole syllabus.
- [x] **Absence is written as absence.** Every empty field renders *not stated in the syllabus*
      rather than being dropped — otherwise "no late policy" and "never converted" look the same
- [x] `knowledge_vault.write_note(..., replace=True)` — a regenerated note REPLACES. The
      `save_to_vault` tool still appends and does not expose `replace`: a build step may rebuild
      its own artifact, a model may not erase a note LB dictated.
- [x] Wired to the upload path via the `_Indexer`'s new `syllabus` job — off the turn, one call,
      named files only (never `convert_all`)
- [x] `pypdf` declared in `requirements.txt` and `stage_install.sh`. It was installed everywhere
      already, but only TRANSITIVELY via langchain-community.
- [x] `tests/fixtures/make_syllabus_pdf.py` — a hand-built text-bearing PDF, so the harness needs
      neither a real syllabus in a public repo nor a PDF library on the Pi
- [x] `tools/verify_syllabus.py` — 40 checks, no key, no API call

### Verified on the Pi, against LB's real syllabus

`Morgan Syllabus POSC 201 Fall 2026` — 13 pages — produced `vault/courses/POSC201.md`: instructor
with office and email, office hours verbatim, a five-line grading breakdown, the full late policy,
and five standing rules (attendance, classroom rules, textbook, Respondus lockdown, plagiarism).

**Nothing invented, and no due dates carried across** — the fixture deliberately contains
"Homework 4 is due on October 14, 2026" and no date reached the note. The vault note is named
`POSC201`, which is exactly the course code the Canvas feed uses.

### The bug only running it could find

`read_from_vault` is a **substring** scan with no tokenising. The first real note had a heading
called "Late and missed work", so:

    read_from_vault("office hours")  -> found
    read_from_vault("late policy")   -> NOT FOUND      <- the likeliest question there is

Fixed in the NOTE, not the searcher — tokenising a tool three agents depend on would make every
two-word query match too much. Every note now carries a `*Search terms:*` line. All six phrasings
verified against the real searcher on the Pi.

### Still open

- [ ] **The extraction quality is proved on ONE syllabus.** POSC 201 is a well-structured
      13-page document with explicit headings. A terse two-page outline, or one that states its
      grading as prose rather than a list, has never been tried. Convert the next one with
      `--dry-run` first and read it before it goes in the vault.
- [ ] **`MIN_USABLE_CHARS = 400` is a guess**, not a measurement. It is well clear of the two
      cases seen (0 characters for a scan, 794 for the fixture, thousands for the real one), but
      nobody has found the boundary where a real syllabus falls under it.
- [ ] A syllabus already in `data/academic/` before this existed is not converted until
      `--all` is run by hand. Only the upload path triggers it.
- [ ] The note is reached by HARDWARE, FIRMWARE and GENERAL through `read_from_vault` — but NOT
      by ACADEMIC, which has no vault tool and answers from the calendar alone. So "what's my
      late policy" works best asked plainly; asked as a coursework question it routes to ACADEMIC
      and he says he does not know. Worth deciding whether ACADEMIC should carry the vault tool.

## ACADEMIC gets the vault key (2026-08-23)

LB: *"Yes, absolutely add `read_from_vault` to the ACADEMIC route."* D25. This closes the open
item D24 shipped with.

- [x] `read_from_vault` bound to ACADEMIC alongside `sync_canvas_calendar`
- [x] Prompt rewritten: he must **search before refusing**. The notes are behind a tool call, so
      the failure mode moved from *inventing* a policy to *refusing without looking* — and to LB
      those are the same thing.
- [x] "Never take a date out of a note" — Canvas owns every date, even when a note carries one
- [x] Tool loop generalised: all requested calls run before the second pass, so "sync Canvas and
      remind me of the late policy" cannot silently drop half the request
- [x] Harnesses updated — three checks pinned the old "he cannot answer policy questions"

### Verified live on the Pi, both directions

- **POSC 201** (note exists) → searched the vault, returned all five clauses of the real policy
- **ECE 350** (no note) → *"I have no notes on that course yet. You can upload the syllabus with
  the paperclip."* — looked, found nothing, and did not invent one

`verify_academic` 31/31, `verify_upload` 169/169, `verify_syllabus` 40/40, plus agents 53,
engine 106, chat 39, typed 81, split 93, kicad 168, os_guard 75, speakable 59, rig 38.

### Still open

- [ ] The vault search is a substring scan, so a policy question that shares no literal word with
      the note still misses. The `*Search terms:*` line covers the phrasings anticipated; a real
      miss will look like "I have no notes on that course" and is worth reporting when it happens.
- [ ] A policy question now costs **two** model calls — one to decide to search, one to answer.
      Against a 20-a-day tier that is worth watching. Pre-loading the notes into the prompt would
      make it one, at the cost of carrying every course's note on every academic turn; not done,
      because one course is 2.5 KB and five would start to look like the calendar problem D22 hit.

---

## Advanced gestures — the barehands vocabulary, and a window to tune it in (2026-08-23)

- [x] `tools/live_test_gestures.py` — live camera window, skeleton drawn, gesture named large
- [x] `GestureRecognizer` upgraded: `PINCH`, `CLAP`, `CLAW`, `FLICK` added to `THUMBS_UP`/`OPEN_PALM`
- [x] `max_num_hands` / `num_hands` 1 → 2, or `CLAP` can never fire
- [x] `get_gesture()` returns the active gesture as a string, via `_classify_frame`
- [x] `tools/verify_gestures.py` — 31 checks, no camera, `--probe` proves it bites

### Review

**Verified, on the Windows authoring box** (mediapipe 1.0.1 / Tasks, opencv 5.0.0, no webcam):

| check | result |
|---|---|
| `verify_gestures.py` — six gestures, pure geometry | 31/31 |
| `verify_gestures.py --probe` — old order re-approves | CLAW **and** PINCH bite |
| `verify_gate_state.py` — the security gate, unchanged | 20/20 |
| all three files byte-compile | 3/3 |
| headless: recogniser builds, `detect_hands` on a real ndarray | pass, backend `tasks` |
| headless: skeleton drawn for extended / fist / claw hands | 3/3, pixels on the frame |
| headless: numbers panel, 1 hand / 2 hands / 0 hands | 5 / 11 / 0 lines |
| `gesture_control.py --backend`, full sidecar round trip | worker answers, protocol intact |

**Not verified, and it needs LB and a camera:** the window itself. No webcam on this box, so
`cv2.imshow`, the frame rate, and every threshold's behaviour against a real hand are untested.
That is the point of shipping the window — the thresholds are geometry-derived starting values,
not measurements, and the panel exists to replace them with numbers.

### What didn't work

- **A claw was already an approved shell command, and so was a pinch.** `THUMBS_UP` requires
  the four fingers *not extended* (`tip.y < pip.y` false). A claw satisfies that — it holds the
  tips below the PIPs — and so does a pinch, which curls the index down onto the thumb. Both
  put the thumb above the index knuckle. So both classified as `THUMBS_UP` on `os_agent`'s
  path. Latent since 2026-08-19; the request to make the claw a deliberate gesture is what
  would have made it routine. Fixed by testing both **before** `THUMBS_UP` and discriminating
  on tip-vs-MCP, which separates a fist from a claw by geometry rather than by threshold.
  `verify_gestures.py --probe` is the before-state, preserved and runnable. See D26.
- **The classifier test this repo has cited since 2026-08-19 did not exist.** `docs/DECISIONS.md`
  described "six cases, all green"; nothing in the repo ran them. Four days of reviewers reading
  a sentence instead of a suite, which is plausibly how the claw collision survived.
- **A fixed pinch distance is wrong at every camera distance but one.** Landmarks are normalised
  to the frame, so `dist(4, 8) < 0.05` tuned at 40 cm reads a relaxed hand as a pinch at 80 cm.
  Every threshold is now a ratio against `_hand_scale()`, the rigid wrist-to-knuckle span.
- **`FLICK` cannot work on the approval path at all.** One frame, from a camera that is then
  closed, in a child process that then exits — there is no previous frame and nothing that could
  remember one. Not a bug to fix; a consequence of D15's crash isolation. `classify_stream()` is
  the continuous path and the live window is its only caller today.
- **"Palms facing each other" is not what `CLAP` tests.** Two palm normals from 21 landmarks
  each, compared, at 640×480 and 6.6 fps, from a triangle that is degenerate in exactly the pose
  being measured. The docstring says what it actually tests — two upright open hands, close
  together — rather than implying precision that is not there.

### Still open

- [ ] **The thresholds are guesses and are labelled as such.** `PINCH_MAX_RATIO` 0.40,
      `CLAP_MAX_GAP_RATIO` 1.8, `CLAP_MIN_FINGER_RISE` 0.6, `FLICK_MIN_SPEED` 1.1 — all derived
      from geometry, none measured. `live_test_gestures.py --pinch R` changes one for a run
      without editing code, and the panel prints the live value beside the threshold. Move the
      constant, re-run `verify_gestures.py`, and write the measurement down.
- [ ] The same measurement `WARMUP_FRAMES` has been waiting on since 2026-08-22 — detection rate
      against warmup count and confidence — is now cheap to take, because the window reports
      frames and hands live. Sibling problem, same fix.
- [ ] **Nothing consumes the four new gestures yet.** They are detected, named and tested; no
      route acts on one. Wiring `FLICK` to anything needs the persistent worker in the note
      above, since the one-shot path cannot produce it.
- [ ] `media/` has no chart for this. A detection-rate-vs-threshold sweep, run from the saved
      frames the window's `s` key collects, is the figure — and per the house rule a chart is not
      finished without the CSV beside it.

---

## 2026-08-23 — Zero-token route hints, and the keyword list that was refused

Asked for an extensive local keyword dictionary in `router.py` to skip the Gemini Flash
classification. Goal was right and the cost is measured (D3, D1: a router round trip on every
paid turn, 750 ms on Windows and **9.8 s on the Pi**). The implementation had two problems that
only showed up on reading the routing path first, and both are written up in **D27**.

### What was already built — and the three apps that turned out not to be

**Most of the app-launch half.** `open`/`launch`/`start`/`run`/`bring up`/`fire up`/`pull up`
plus KiCad, Firefox, Chromium and the terminal have cost zero tokens since D10 landed on
2026-08-21 (`orchestrator/launch_intent.py`), resolving targets against the Pi's XDG desktop
database rather than a hardcoded list. Rebuilding it in `router.py` would have been dead code
at best — the free tier runs before the router — or a launch path that skips `Engine._gate`
at worst.

**But three of the named targets did not work,** and only a fixture showed it. Handing the
launcher a Pi-shaped `.desktop` tree (six entries, `TemporaryDirectory`, on Windows) caught
`schematic editor`, `pcb editor` and `vscode` all falling through to the paid router:

- `_targets` offered only whole names, so `resolve()`'s fourth tier — *a whole-word phrase in
  the Name*, which answers "schematic editor" against "KiCad Schematic Editor" — was never
  reachable from the free path. Trailing sub-phrases of multi-word names are now targets, two
  words minimum (one trailing word is "editor"/"manager"/"player", which `ROLES` owns).
- `vscode` is a nickname and no tier can reach one. `app_catalogue.ALIASES` maps it and
  "vs code" to "visual studio code" before the tiers run — an exact key, not a fuzzy match,
  and it implies nothing about what is installed.

`verify_launch.py` is 208 checks now, up from 194; its five mutations still bite.

### What was refused, and why

Six collisions between the specified rules and routes they did not account for. Two worth
repeating here:

- `[a-z]{3,4}` + digits → ACADEMIC matches **`esp32`**, `stm32`, `msp430`, `pic16` — the
  FIRMWARE keywords from the same request. Two rules in one document claiming the same string.
- `vault`/`remember`/`save` → VAULT, and **there is no VAULT route**. The vault is a tool bound
  into HARDWARE, FIRMWARE, ACADEMIC and PERSONA. "Remember I used a 10k resistor on the amp
  board" wants `save_to_vault` *inside* the agent that can also read the KiCad file; routing it
  anywhere strips the tools off it.

### What shipped

- `orchestrator/route_hint.py` — a pure function of a string, called from `_routed_turn`
  between `_free_turn` and the quota latch (above the latch on purpose, so hinted turns survive
  a dry router). ACADEMIC for Canvas-sync / course-paperwork / deadline **phrases**, OS for
  machine-stat **phrases**. Course codes are read from `vault/courses/*.md` — the ACADEMIC
  agent's own notes — the way `app_catalogue` reads apps from XDG, which is what kills the
  `esp32` collision structurally instead of by exception. An upload is refused before anything
  else, because a new upload is always GENERAL.
- `instant._is_bare` — the end-anchor rule, extracted. It existed three times over
  (`_is_dismissal`, `is_wake`, and the social intents that needed it); both old call sites now
  delegate.
- `hello` / `thanks` / `identity` promoted into `Engine.FREE_INTENTS`, labelled PERSONA rather
  than UTILITY. They needed the anchor first: `hello` fired on a bare "hey", so *"hey what's the
  trace width for 5 amps"* was answered **"Hey LB."**, and `identity` claimed *"who would you
  recommend for a resistor supplier"*. Both now fall through correctly.
- `tools/verify_router.py` — 164 checks, keyless, closes the Stage 8 item.

### The numbers

| turn | before | after |
|---|---|---|
| "hello" / "thanks" / "who are you" | 1 router + 1 persona | **0** |
| "sync canvas" | 1 router + academic | academic only |
| "what's the CPU temp" | 1 router + 2 OS calls | 2 OS calls |
| "what's the trace width for 5 amps" | 1 router + agent | unchanged, deliberately |

**Only the social three are actually free.** The ACADEMIC and OS hints save one call of two or
three — the agent still runs — and saying otherwise would overstate what this does.

### Verified

164/164 on the new harness, 4/4 mutations bite. Full sweep green with no regressions:
`verify_launch` (194, 5/5 probes), `verify_engine` (106, probe bites), `verify_academic` (31),
`verify_agents` (53), `verify_upload` (169), `verify_formulas` (894), `verify_calc` (820),
`verify_define` (7068), `verify_convert` (1599), `verify_wake` (42), `verify_typed` (81),
`verify_stt` (36), `verify_chat` (39), `verify_gate_state` (20), `verify_os_guard` (75),
`verify_split` (93), `verify_speakable` (59), `verify_syllabus` (40).

The two mutations worth naming restore *designs that were specified*, not bugs that shipped:
M3 puts the bare-keyword dictionary back (**21 checks red**) and M4 the naive course-code regex
(**9 red**). Without those, D27 is an opinion; with them it is a thing that fails on demand.

### Still open

- [ ] **Nothing here has run on the Pi.** `vault/courses/` does not exist on Windows, so the
      course-code tier returned an empty tuple for every check in section 6 except the one that
      builds a fake vault in a `TemporaryDirectory`. The rule is exercised; the real course
      list is not. First Pi run should confirm `known_courses()` finds his actual notes.
- [ ] **The 9.8 s Pi router leg is still unmeasured after this change.** The hint removes that
      leg for coursework and stat turns, which is exactly the family LB asks most, but the
      re-measure at `Stage 8` is still owed a number. `media/` was explicitly out of scope for
      this pass — the before/after chart is the natural next artifact and the data for it is one
      `measure_turn.py` run on the Pi.
- [ ] `_STATS` and `_POLICY` are phrase lists, and phrase lists are wrong at the edges by
      construction. "is the sd card full" missed on the first harness run because the phrase was
      written as "how full is the sd card". Every future miss costs one router call and should be
      added as a phrase, never widened into a keyword.
