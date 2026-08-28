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

### Bring-up on the Pi — the window would not open, twice over

First run on the box it was written for: banner, "camera released", exit 0. No window, no error.
**Two independent bugs both presenting as "no window", so fixing either alone showed nothing.**

- **OpenCV's Qt ships no Wayland plugin.** labwc means `XDG_SESSION_TYPE=wayland`, so Qt asks for
  a `wayland` platform plugin; the wheel contains only `libqxcb.so`. Xwayland is already up and
  owns `:0`, so xcb works — `ensure_qt_platform()` now detects this and sets it, plus `DISPLAY`.
- **The window title had an em dash in it.** With the platform fixed it *still* would not appear:
  `'ascii-name'` -> `visible=1.0`, `'MR ODD BALL — gesture tuning'` -> `visible=0.0`. OpenCV's Qt
  will not show a window with a non-ASCII title. This repo writes em dashes everywhere in prose
  and the habit walked into a string that is an identifier. Now ASCII, with an assert and an AST
  check that no non-ASCII literal reaches `imshow`/`putText`/`imwrite`.
- **The clean exit was the real problem.** `getWindowProperty(...) < 1` cannot distinguish "user
  closed it" from "never created", so a window that never opened read as one LB had closed. On
  frame 1 it can only be the latter; that now prints the platform, display and session type and
  returns 2. A diagnostic that exits 0 is worse than one that crashes.
- **Every no-camera check was green the whole time.** Neither bug is reachable from Windows — one
  needs a Wayland session, the other needs OpenCV's Qt backend rather than Win32. Verified fixed
  on the Pi: window held open 12 s, clean SIGINT, camera released.
- **`git pull` on the Pi does not work and never did.** `~/mr-odd-ball` is a tar deploy target
  with no `.git`. Runbook and the `scp` single-file path are now in `docs/DEPLOY.md`.

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

---

## The barehands port — measuring against the hand, not the image (2026-08-23)

LB: *"flick left and right often get confused with open palm. there is no thumbs down. besides
the addition of thumbs up and thumb down i want the same gestures and results as barehands."*
Plus screenshots, which is what actually cracked it.

- [x] Rotation-invariant geometry: `_curl` (segment dot product) and `_reach`, ported from
      `jaredrhod/barehands` along with its fitted constants
- [x] `THUMBS_DOWN` added; it declines at the security prompt, the first gesture allowed to
- [x] `FLICK` redefined as a pinch released at speed — plus `TAP` and `DRAG` from the same
      state machine. `CLAP` removed
- [x] `POINT` and `FIST` added; `media/captures/` had examples of both reading `NONE`
- [x] `s` now saves clean frames and a JSON of landmarks + metrics; `--label` records intent
- [x] `verify_gestures.py` rewritten — 48 checks, with a **rotation sweep**

### Review

| check | result |
|---|---|
| `verify_gestures.py`, Windows | 48/48 |
| `verify_gestures.py`, on the Pi in `.venv-gesture` | 48/48 |
| `--probe` — the old test, swept over rotation | opens a closed fist at **6 of 12 angles** |
| `verify_gate_state.py` — the security gate | 20/20 |
| the window, on the Pi | opens, labels, clean SIGINT, camera released |
| the new classifier over LB's own photographs | POINT no longer approves |

### What didn't work

- **The old classifier measured fingers against the IMAGE.** `tip.y < pip.y` is only correct
  while the hand is upright, and a thumbs up is made palm-side-on. LB's 17:11 photo is a clean
  thumbs up, every landmark right, classified `NONE`. Four days of "unreliable" was this, and
  it was never a threshold.
- **My own test suite could not see it.** Every synthetic hand written that morning was
  upright — the same assumption the code made — so 31/31 passed while the thing did not work.
  The rotation sweep exists because a photograph varied something the fixtures held constant.
- **FLICK was the wrong gesture.** Defined as a moving open palm, it was separated from a still
  open palm only by a speed bar between two frames 100 ms apart. barehands: *"Every gesture is
  a movement, not a pose"* — a flick is a **pinch released at speed**, which cannot overlap an
  open palm because it starts from a pinch.
- **A pointing hand approved a shell command.** Caught by running the new build over LB's
  photos before shipping. Index `reach 1.37` fell under the 1.45 bar, "not extended" was read
  as "closed", and a POINT came out `THUMBS_UP`. Fixed by making open and shut two predicates
  with a gap between them instead of one threshold with two sides.
- **The first capture session's data was unusable.** The saved PNGs have the skeleton drawn on
  them, so re-detecting to recover landmarks reads the overlay — three of seven returned "no
  hand". The tool now saves a clean frame and the raw numbers beside the picture.

### Still open — and this is the blocking one

- [ ] **No threshold here has been checked against a real hand.** The constants are barehands',
      fitted against barehands' pipeline, and the 48 synthetic checks prove internal
      consistency only. What is needed is a labelled capture session, one pose at a time:

      ```
      venv/bin/python tools/live_test_gestures.py --label thumbs_up    # hold it, press s a few times
      venv/bin/python tools/live_test_gestures.py --label thumbs_down
      venv/bin/python tools/live_test_gestures.py --label claw
      venv/bin/python tools/live_test_gestures.py --label pinch
      venv/bin/python tools/live_test_gestures.py --label point
      venv/bin/python tools/live_test_gestures.py --label open_palm
      venv/bin/python tools/live_test_gestures.py --label fist
      ```

      Each `s` writes `media/captures/data/*.json` with the landmarks, the metrics, the
      thresholds in force and the pose intended. Ten of each is a corpus; the thresholds can
      then be fitted to LB's hands the way barehands fitted them to Jared's, and the result
      written down as a measurement instead of a port.
- [ ] Nothing consumes any gesture but `THUMBS_UP` and now `THUMBS_DOWN`. TAP/DRAG/FLICK need
      the persistent worker before they can drive anything, since the one-shot path has no
      frame-to-frame state.

---

## Cut to two gestures: pinch to move, two pinches to scale (2026-08-23, later)

LB: *"i dont need a point or a claw right now. i just want to be pinch hold to and be able to
move things around up down around or rotate, double pinch fingers close zoom out make smaller
fingers moving away makes bigger zoom in."*

That is not a request for more gesture names — it is a request for a **control stream**. A token
like `DRAG` cannot move anything; whatever is being dragged needs to know *how far*. So the
layer returns a `Motion` carrying numbers, and the name is a label on top of them.

- [x] `Motion` — per-frame deltas: `dx`, `dy` (palm spans), `scale` (multiplicative),
      `rotation` (radians), `hands`
- [x] One pinch travelling -> `MOVE`. Two pinches -> `SCALE`, carrying zoom **and** twist from
      the same two points, the way every touchscreen has worked for fifteen years
- [x] Removed `CLAW`, `POINT`, `TAP`, `DRAG`, `FLICK` — five unused gestures are five more ways
      to misread the two that matter
- [x] Deadzones and per-frame clamps on all three channels
- [x] The window shows the live deltas under the banner; that is what a deadzone is tuned against

### Review

| check | result |
|---|---|
| `verify_gestures.py`, Windows and Pi | 45/45 both |
| `--probe` | still bites: old test opens a closed fist at 6 of 12 angles |
| `verify_gate_state.py` | 20/20 |
| the window on the Pi | opens, clean SIGINT, camera released |

### What didn't work

- **A slice assignment silently exploded the module docstring.** `lines[a:b] = "text"` assigns a
  *string* to a list slice, and Python iterates it **character by character** — 3,994 elements,
  one per character. `gesture_control.py` went from 1,000 lines to 5,156, and because the damage
  was inside a docstring it was still valid Python: `py_compile` passed, all 48 tests passed, it
  was committed and pushed. Caught only when a `sed` window printed one letter per line.
  The other two files used `.split("\n")` on the same line and were fine.
  **A syntax check is not a content check.** Nothing in the harness looks at prose, so nothing
  could have caught it; what caught it was reading the file.

### Still open

- [ ] **Thresholds are still barehands', not fitted to LB's hands.** Unchanged by this pass —
      `PINCH_MAX_RATIO` decides when a grip forms, and the whole manipulation layer sits on top
      of it, so it is now the single most load-bearing constant in the file. A labelled capture
      session (`--label pinch`, then `--label open_palm`) is still the next step.
- [ ] Nothing consumes `MOVE` or `SCALE` yet. They need a persistent worker — the one-shot
      approval path has no previous frame, so it cannot produce a difference.
- [ ] One-handed rotate (barehands promotes a held-still pinch into a rotate latch) is NOT
      built. Two-handed twist covers rotation for now.

---

## The pointer bridge, and PINCH_MAX_RATIO fitted to LB's hands (2026-08-23)

- [x] `PINCH_MAX_RATIO = 0.66`, LB's own number, checked against the 17 hands he captured
- [x] Two real bugs found by replaying those captures — see below
- [x] `tools/gesture_pointer.py` — a kernel virtual mouse driven by a pinch
- [x] `tools/verify_pointer.py` — 17 checks that it cannot type and cannot click
- [x] Section 7 of `verify_gestures.py` replays LB's captured landmarks as a regression corpus

### Review

| check | result |
|---|---|
| `verify_gestures.py` | 50/50 (Windows and Pi) |
| `verify_pointer.py` on the Pi | 17/17 |
| `verify_pointer.py --probe` | bites — guard removed gives a 0 px click |
| `verify_gate_state.py` | 20/20 |
| every captured hand replayed | 12/12 classify as LB intended |
| live dry-run on the Pi | 52 frames, pause handshake in and out, clean exit |
| the real device | registers as `oddball-gesture-pointer`, `/devices/virtual/input/input6` |

### What the captures revealed — 0.66 was treating a symptom

LB's genuine pinches measure **0.09 to 0.32**. Every one was already inside the old 0.32
ceiling, so the gap was never what rejected them. Two other things were:

- **The aspect bound threw a textbook pinch away.** `ASPECT_GARBAGE = 6.0` came from barehands'
  pipeline. LB's pinching pose turns the palm side-on, which foreshortens the knuckle row and
  drives that ratio up legitimately — one capture sits at 6.30 with a perfect signature (gap
  0.17, contrast 0.53) and was discarded as a hallucinated hand. Now 9.0, fitted on **our**
  landmarks. A ported constant is only as good as the pipeline it was fitted on.
- **`OPEN_PALM` was tested before `PINCH`.** In a real pinch the index **arcs** to meet the
  thumb rather than folding, so its curl stays high — LB has a capture reading index curl +0.74,
  over the 0.6 "straight" bar. All four fingers looked open, `OPEN_PALM` matched, and the pinch
  below was never reached. **No gap threshold could have fixed this**, which is why loosening
  the ceiling felt like it helped without ever curing it.

0.66 is kept because it is LB's measured preference and it is safe — but it is a *margin*, not
the decision. The contrast law is what actually separates a touch: his thumbs-up measures gap
0.49, well inside 0.66, and is rejected at −0.03 contrast. Section 7 asserts exactly that, so
deleting the contrast law goes red instead of approving a shell command with a fist.

### The pointer cannot type and cannot click

Not by policy — by what the device can emit.

- **No keyboard capability.** None of the kernel's 514 `KEY_*` codes is declared, so it cannot
  answer the `input()` prompt in `agents/os_agent.py`. This is the guarantee worth the most.
- **No click.** Press happens only after travel; before releasing, if the pointer has not moved
  `CLICK_GUARD_PX` (160) it is displaced that far first. Checked across 120 drag lengths — 118
  pressed, closest press/release pair 160 px. No widget activates on a release that far from
  its press.
- **Left button only**, so no context menu. No `EV_ABS`, so it cannot warp to a screen position.
- **Inert during an approval.** The pause file releases the camera *and* stops all injection.

### Still open

- [ ] Nothing autostarts the daemon. It runs by hand; a systemd user unit is a small addition
      once LB has decided he wants it always on.
- [ ] `POINTER_GAIN` (900 px per palm span) is a first guess, unlike the pinch constants. It is
      `--gain` on the command line; whatever feels right should be written back as the default.
- [ ] Rotation is measured and published to the state file but never injected — there is no
      pointer event for it. Zoom is a plain wheel, deliberately: Ctrl+scroll would need a
      keyboard capability and that costs the first guarantee.
- [ ] The two-hand SCALE path has never been exercised on real hands with the pointer running.

---

## Self-awareness and the correction loop (2026-08-25)

LB asked for three things: let him look at the screen, let him know his own state, and let him
learn from mistakes — his own, and the ones LB points out.

### Plan

- [x] `tools/reflections.py` — the mistake ledger (`vault/reflections.md`). Append, rotate,
      read back, and match past failures against the current question.
- [x] `tools/corrections.py` — LB's standing rules (`vault/corrections.md`), plus the free
      detector that decides a line is a correction rather than a question.
- [x] `tools/system_state.py` — CPU temperature, load, memory, disk, uptime, which ports are
      listening, and which capabilities are actually installed. No model, no subprocess.
- [x] `tools/self_context.py` — compose the three into one bounded block, in priority order.
- [x] `tools/memory_manager.py` — prepend the block in `format_memory_for_llm()`. **The one
      seam**: every agent already calls it.
- [x] `tools/screen_capture.py` — grim / scrot / imagemagick / gnome-screenshot / PowerShell,
      first available wins. Downscaled JPEG, size-capped, kept on disk.
- [x] `agents/screen_agent.py` — propose/resume pair, gated like the shell, vision model.
- [x] `router.py` + `orchestrator/route_hint.py` — the SCREEN route, and the free hint that
      answers "what's on my screen" without paying for a routing call.
- [x] `engine/core.py` — intercept corrections above the router and below the gate; dispatch
      and resume SCREEN; record exceptions and slow turns as reflections.
- [x] `agents/os_agent.py` — record failed and refused OS actions. They never raise, so the
      engine's exception hook cannot see them.
- [x] `agents/quiz_agent.py` — wire it into the seam. It was the one agent that was not.
- [x] Harnesses: `verify_corrections`, `verify_reflections`, `verify_awareness`,
      `verify_screen`, each with a `--probe` that proves it bites.
- [x] Update `verify_engine`, `verify_router`, `verify_agents` for the new route and path.
- [x] `docs/DECISIONS.md` D29, `docs/DEPLOY.md`, `README.md`, and the AMSI measurement under
      `media/data/`.

### Review

**What went in.** Five new modules, four new harnesses, and one changed function that reaches
every agent. 730 checks across the affected harnesses, all green, every probe biting.

**The elegant part.** There is no system prompt in this repo to inject into — seven agents each
build their own. But all of them already call `format_memory_for_llm()`. Prepending the block
there reaches every agent, needs no prompt template edited, and reaches the next agent written
for free. `tools/verify_engine.py` was already monkeypatching that function, which is what
identified it as the seam rather than a guess.

**Two real bugs the harnesses found, not me.**

1. `quiz_agent.py` did not call the seam function. So every standing correction LB gives would
   have applied everywhere except quiz grading — including "always spell out the units", in the
   one place he is being marked on units. Found by `verify_awareness.py` asserting over
   `agents/*_agent.py` on disk rather than over a list I typed.
2. "Don't do that" was being filed as the standing rule *"Don't do that"*. It parses as a
   directive and it is not one — "that" has no referent once the turn scrolls away. It now reads
   as a bare rebuke and he asks what he should have done instead, and *that* answer becomes the
   rule. The general form: a phrase that points at the last turn is never a standing instruction.

`verify_router.py` and `verify_agents.py` both went red the moment the SCREEN route existed,
because each asserts it has been told about every route. Two harnesses doing their job, and the
reason the new route's negatives were written before it shipped.

**What didn't work, and is worth keeping.**

The Windows screenshot backend — which exists only so the harness can capture a screen on the
authoring box — was blocked by Windows Defender twice before it ran. The first fix is the one
every source names (`-File` instead of `-Command`) and it was **not sufficient**. Bisecting found
the trigger was the JPEG-quality encoder block, not `CopyFromScreen`, which is the call everyone
assumes is matched. Recorded in `media/data/2026-08-25-screen-capture-amsi.csv` with the four
variants and their byte counts, because the failure presents as a PowerShell *ParserError* and
reads exactly like a syntax bug in code that is already correct.

**The judgement call LB should overrule if he disagrees.** The screenshot is **gated by default**
— he is asked before a frame is captured and sent. It is his desktop and it goes to Google; the
first frame taken while building this had a browser and a chat window in it, both legible. But he
*asked*, and a yes/no question in front of "what's on my screen" is ceremony. So
`ODDBALL_SCREEN_CONFIRM=0` makes it instant and `ODDBALL_SCREEN=0` turns it off entirely. One
environment variable either way.

### Addendum — two things found after the code was written

**The new file made every old harness a writer to it.** `Engine.ask` and `agents/os_agent.py`
record failures to `vault/reflections.md`, and several existing harnesses drive failures on
purpose. So `verify_engine.py` (deliberate 400s) and `verify_launch.py` (an unknown-tool path)
were appending junk to LB's real ledger — where it would then be injected into every agent prompt
as things that had "gone wrong". Both harnesses were green throughout; the only thing that found
it was listing the ledger after a sweep.

Fixed with one override, `ODDBALL_VAULT_DIR`, honoured by both ledgers, plus one line at the top
of the seven harnesses that can reach a write. Written up as `tasks/lessons.md` **L22**, and the
sweep now ends by asserting both real ledgers come back empty.

**The documented CLIs did not run.** `python tools/corrections.py --list` — which DEPLOY.md and
the README both tell LB to type — died with `ModuleNotFoundError: No module named 'orchestrator'`,
because run as a script the file is not inside a package and the repo root is not on the path.
Three modules now carry a `if __package__ in (None, "")` guard. All eight documented commands
were then run and checked.

### Final state

```
26 harnesses, 12,220 checks, all green
4 new harnesses, every --probe biting
both real ledgers empty after a full sweep
all 8 documented CLI commands run
```

### Still open

- [ ] **None of this has run on the Pi.** The screen path is proven on Windows through the
      PowerShell backend; `grim` is selected by the same code and has not been exercised under
      labwc. That is the check that matters and it is LB's to make: `sudo apt install grim`,
      then `python tools/verify_screen.py --capture`.
- [ ] The vision model shares `AGENT_MODEL`'s daily bucket. `ODDBALL_VISION_MODEL` points it at
      its own 20 requests a day with no code change, and whether that is worth doing depends on
      how often he actually asks.
- [ ] `SLOW_TURN_S = 45` is a first guess, unlike the other constants here. It should be set from
      a week of real `Turnlog` totals on the Pi, not from arithmetic about the router's 9.8 s.
- [ ] A correction is never *withdrawn* except by editing `vault/corrections.md` by hand. If LB
      finds himself doing that often, "forget that rule" is the missing verb.
- [ ] Nothing yet reflects on a *wrong* answer that did not fail — the case where he answers
      confidently and is simply incorrect. That is what the correction ledger is for, and it
      needs LB to notice. There is no automatic path to it and probably should not be.

---

## The Windows port — off the Pi, onto the workstation (2026-08-26)

Target: AMD Ryzen 7 5700X, 32 GB RAM, RX 6600, Windows 11. Branch `oddball-integration`.
Performance constraints are gone; **platform constraints replace them, and they are not the
same shape.** The Pi's limits were about how much work fit in the budget. Windows' limits are
about what the operating system will let a process do to another process, and three of the
five areas below are load-bearing safety properties rather than features.

### What is already portable, and needs nothing

Checked before planning any work, because the fastest port is the one already done:

| Layer | State |
|---|---|
| `audio/say.py`, `wake.py`, `stt.py`, `listen.py` | **Already cross-platform.** `sounddevice` is PortAudio; `say.py` already carries Windows-specific handling for MME/DirectSound/WASAPI host-API mismatches and `check_output_settings` resampling. Piper and faster-whisper are pip wheels on both. |
| `tools/screen_capture.py` | Already has a `_powershell_capture` backend, selected by the same code that selects `grim`. |
| `engine/`, `agents/`, `orchestrator/`, `router.py` | No OS calls on the turn path. |
| `main.py` | Already reconfigures stdout to UTF-8 for cp1252 consoles. |

So area 5 (media and audio) is **already complete** and the work there is verification, not code.

### The five areas, re-scoped

#### 1. Autostart — `config/oddball.service` to `config/start_oddball.vbs`

Dead code, as stated. Replaced by a `.bat` that starts the backend and the face, plus a `.vbs`
one-liner that runs the `.bat` with no console window — the batch file alone leaves a black
`cmd.exe` rectangle on screen forever, which is not a background service.

- [ ] `config/start_oddball.bat` — starts `main.py` and `hud/float.py`, logs to `data/`
- [ ] `config/start_oddball.vbs` — the silent wrapper for `shell:startup`
- [ ] `tools/install_autostart.ps1` — install / remove / status, mirroring `install_autostart.sh`
- [ ] Delete `config/oddball.service`, `config/oddball-face.desktop`, `tools/wait_for_display.sh`

**The boot race does not disappear, it changes shape.** `hud/float.py`'s `_keep_trying()` exists
because the face's HTTP GET raced the server's bind (2026-08-22). `shell:startup` gives even
less ordering than systemd did, so `_keep_trying` is carried across unchanged and is the *only*
thing preventing the same bug. Nothing in this port may remove it.

#### 2. The face — GTK4/WebKitGTK to PyQt6 + QtWebEngine

`hud/float.py` is GTK4 + WebKitGTK. Neither exists usefully on Windows.

**PyQt6-WebEngine, not pywebview.** The decision turns on one property: `--transparent` is
load-bearing, not cosmetic. The solo look is the character composited onto the desktop with no
rectangle seam, and it needs the *page* transparent and the *window* transparent together.
pywebview on Windows drives WebView2, whose transparency story is a `DefaultBackgroundColor`
that does not reliably reach a layered top-level window. Qt does it directly:
`WA_TranslucentBackground` on the window, `page().setBackgroundColor(Qt.transparent)` on the
view. PyQt6 is already installed on this box; only `PyQt6-WebEngine` is missing.

Mapping, one to one:

| GTK4 / labwc | Qt6 / Win32 |
|---|---|
| `win.set_decorated(False)` | `Qt.FramelessWindowHint` |
| labwc rule: always-on-top | `Qt.WindowStaysOnTopHint` (`WS_EX_TOPMOST`) |
| labwc rule: skip taskbar | `Qt.Tool` |
| `view.set_background_color(RGBA 0,0,0,0)` | `WA_TranslucentBackground` + `page().setBackgroundColor` |
| `Gtk.EventControllerKey` in CAPTURE phase | `QShortcut` on the window |
| `load-failed` / `load-changed` | `loadFinished(bool)` |

- [ ] `hud/float.py` rewritten on Qt6, same CLI flags, `_keep_trying` preserved
- [ ] **Click-through.** A frameless always-on-top window swallows every click inside its
      rectangle, including the large fraction of it that is transparent nothing.
      `WS_EX_LAYERED | WS_EX_TRANSPARENT` via `ctypes.windll.user32.SetWindowLongPtrW` makes the
      transparent pixels pass clicks to the desktop underneath. Behind `--click-through` and OFF
      by default, because a window that cannot be clicked also cannot be closed by mouse — the
      same reasoning as `add_escape_hatch`, which stays.
- [ ] `PyQt6-WebEngine` added to `requirements.txt` with the reasoning above

#### 3. Gestures — `/dev/uinput` to `SendInput`, and the guarantee that does not survive

**Read `tools/gesture_pointer.py`'s security header before touching this.** Four guarantees.
Three port cleanly. One does not, and pretending otherwise is the failure this repo keeps
writing lessons about.

| Guarantee | On the Pi | On Windows |
|---|---|---|
| 2 — press and release never co-located (`CLICK_GUARD_PX`) | pure logic | **unchanged** |
| 3 — only `BTN_LEFT` exists | device capability | emission chokepoint |
| 4 — inert while a security prompt is up (`PAUSE_FILE`) | pure logic | **unchanged** |
| 1 — **cannot type**, so cannot answer the approval gate | `EV_KEY` declares no keyboard key; kernel-enforced | **downgraded** |

Guarantee 1 was enforced by the kernel: the device physically had no keyboard capability, so no
bug in that file could type a `y` into `agents/os_agent.py`'s approval prompt. Windows has no
user-mode equivalent — `SendInput` is one call that takes mouse *or* keyboard structures, and
any process that can move the cursor can also type.

**So the plan does not use `pyautogui` or `pynput`, and that is a deliberate departure from the
brief.** Both are single libraries that can move, click, right-click and type; with either one
imported, guarantee 1 becomes "we were careful about which functions we called" — a code review,
re-run on every future edit, standing in for something the kernel used to guarantee.

Instead: `tools/win_input.py`, a small `ctypes` binding to `SendInput` that declares **only**
`MOUSEINPUT` and never `KEYBDINPUT`. That buys back as much as Windows allows:

- The keyboard struct is not defined in the process, so there is no function to call by mistake.
  Guarantee 1 becomes **grep-auditable** — one file, one `INPUT` union member — rather than
  kernel-enforced. Weaker than uinput, and the docstring says so in those words.
- `MOUSEEVENTF_MOVE` without `MOUSEEVENTF_ABSOLUTE` is a **relative** delta, which is exactly
  what `REL_X`/`REL_Y` were. The port is arithmetically identical; no coordinate rework, and
  `--gain` keeps its measured meaning.
- `MOUSEEVENTF_RIGHTDOWN` is never referenced. Guarantee 3 holds.

It is also the smaller dependency: pyautogui pulls Pillow, pytweening, pyscreeze and mouseinfo
for features this deliberately refuses to have.

- [ ] `tools/win_input.py` — `SendInput`, mouse-only, with the honest downgrade documented
- [ ] `tools/gesture_pointer.py` — `Pointer._move` / `_button` / `wheel` retargeted; every guard
      above them untouched. `--check` reports the Windows preconditions.
- [ ] `tools/verify_pointer.py` — it already fakes `_move`/`_button`, so the guard tests should
      pass unchanged. **If they need editing, a guarantee moved, and that is the finding.**
- [ ] The mediapipe sidecar venv (`tools/install_gesture_venv.sh`) is **not needed here**: this
      box is Python 3.12.10 and `mediapipe` and `cv2` already import in the main interpreter. The
      sidecar's whole reason was the Pi's 3.13 venv. Keep the shell-out path working, skip it
      when the in-process import succeeds.

#### 4. The app catalogue — XDG `.desktop` to the Start Menu, **not** a hardcoded table

The brief asks for `C:\Program Files\KiCad\8.0\bin\kicad.exe` and similar. That is the exact
thing `tools/app_catalogue.py` exists to refuse, and its docstring records the measurement:
`~/oddball/hardware/apps.py` was a hand-written table of three rows, and a `which` sweep on
2026-08-21 found one of the three already missing. A curated list is a second copy of the truth
and it drifts. Hardcoding a version number (`\8.0\`) drifts on the next KiCad update.

**Windows keeps the same list the Pi did, in a different place.** The Start Menu shortcut tree
is the direct analogue of the XDG desktop-entry database — the list the OS itself draws its menu
from:

    %ProgramData%\Microsoft\Windows\Start Menu\Programs    ==  /usr/share/applications
    %AppData%\Microsoft\Windows\Start Menu\Programs        ==  ~/.local/share/applications

Enumerated on this machine while planning: **68 shortcuts**, user shadowing system exactly as
XDG specifies. The mapping is close to exact:

| `.desktop` | `.lnk` |
|---|---|
| `Name=` | the filename stem — "KiCad 8.0", "Arduino IDE", "OpenSCAD" |
| `Exec=` | the shortcut's target, resolved by Windows |
| `Categories=` | the containing Start Menu folder ("Accessories", "Autodesk") |
| `NoDisplay=true` | no analogue; excluded by folder instead |
| user shadows system | same, same order |

So "every app, current and future" stays true: install KiCad 9 and he opens KiCad 9, with no
edit here. All four resolution tiers, `ROLES`, `ALIASES`, the ambiguity refusal and the
whole-word phrase matching are **untouched** — only the loader changes.

- [ ] `tools/app_catalogue.py` — `load_catalogue()` picks a backend by platform; `parse_entry`
      keeps the XDG path, new `parse_shortcut` for `.lnk`
- [ ] `Categories` from the Start Menu folder; `ROLES["browser"]` additionally consulted against
      the registry's `UrlAssociations\http\UserChoice`, which names the **default** browser — a
      better answer than the Pi's "there are two, which did you mean"
- [ ] `SESSION_ENDING` rewritten for Windows: `shutdown.exe`, `logoff.exe`, `diskpart.exe`, and
      the `Administrative Tools` folder wholesale
- [ ] `tools/app_launcher.py` — `systemd_run_argv` becomes `os.startfile` on the `.lnk`, which
      makes Windows resolve the target, working directory and arguments itself. `find_display`
      (a Wayland socket probe) collapses to a `GetSystemMetrics(SM_CMONITORS)` check; the
      `no-display` outcome kind survives, because a headless RDP session is real.
- [ ] `KINDS` and `agents/os_agent.py::_SPEECH` are **unchanged** — the vocabulary of outcomes
      was never platform-specific, which is the payoff for having made it a flat table.

#### 5. Media and audio — verification only

- [ ] `python audio/wake.py --list-devices` and `python audio/say.py --list-devices` on this box
- [ ] Set `[wake].device` in `config/oddball.toml` — it is currently `"C270"`, the Pi's webcam mic
- [ ] Re-measure the wake threshold. `[wake].threshold = 0.76` was fitted to the C270, and wake
      has been scoring **0.17–0.28** against it. A different microphone is a different
      distribution: the number must be re-fitted, not inherited.

### The area the brief did not list, and it is the one that matters most

**`tools/os_controller.py`'s blocklist is 100% Linux syntax and will silently stop working.**

Every pattern in `FORBIDDEN` is a Linux command shape: `rm -rf`, `mkfs`, `dd of=/dev/`, `shred`,
`chmod 777`, `curl | sh`, `cat ~/.ssh/id_rsa`. Point `subprocess.run(shell=True)` at `cmd.exe`
and **not one of them can match anything a Windows shell would run.** The blocklist does not
fail loudly — it matches nothing, returns `None`, and reports "allowed" for every command.
`tools/verify_os_guard.py` stays green throughout, because it tests Linux strings.

That is a confident success on a safety backstop, which is the precise failure mode
`os_controller.py`'s own docstring was written about.

The Windows shapes that need the same treatment:

| Destroying the filesystem | Taking the machine away | Credentials |
|---|---|---|
| `del /s /q`, `rd /s /q` | `shutdown /s`, `/r`, `/f` | `type ...\.env` |
| `format`, `diskpart` | `Stop-Computer`, `Restart-Computer` | `.ssh\id_rsa` |
| `Remove-Item -Recurse -Force` | `Stop-Service oddball` | `cmdkey /list` |
| `cipher /w`, `vssadmin delete shadows` | `bcdedit`, `bootrec` | `Get-Content $PROFILE` |
| `Format-Volume`, `Clear-Disk` | `taskkill /f` on his own process | |

Plus one shape with no Linux equivalent: `iwr ... | iex` / `Invoke-Expression (Invoke-WebRequest
...)`, which is PowerShell's `curl | sh` and is the most common real-world Windows attack shape.

And one decision to make explicitly: **`shell=True` on Windows runs `cmd.exe`, not PowerShell.**
Two shells means two syntaxes means two blocklists. Recommend pinning to PowerShell — the shell
the model will write for, and the one LB actually uses — and blocking `cmd`/`cmd.exe` invocation
from inside it, rather than trying to be total over both.

- [ ] `FORBIDDEN` rewritten for Windows; the Linux table kept and selected by platform, so the
      Pi stays runnable from the same tree
- [ ] `normalise()` — Windows **is** case-insensitive, unlike the current comment, which
      deliberately does not fold case. `RM` is not `rm` on Linux; `DEL` *is* `del` here.
- [ ] `tools/verify_os_guard.py` — a Windows corpus, and a `--probe` that fails if the running
      platform's table is empty

### Order of execution

1. `tools/win_input.py` + `gesture_pointer.py` — the safety-critical one, done first and slowly
2. `os_controller.py` blocklist — the hole above, before anything can reach a shell
3. `app_catalogue.py` + `app_launcher.py` — the Start Menu backend
4. `hud/float.py` on Qt6
5. The autostart scripts
6. Audio verification and the wake-threshold re-fit
7. Full harness sweep; `docs/DEPLOY.md` rewritten; **D30** written up in `docs/DECISIONS.md`

### Open questions for LB

- **`pyautogui` was asked for and is not in the plan.** The reasoning is above; if the
  grep-auditable `SendInput` chokepoint is more ceremony than the feature is worth, say so and
  it becomes three lines of pyautogui — but then guarantee 1 should be struck from the header
  rather than left there untrue.
- **Does the Pi stay runnable?** Every item above is written to keep both platforms working from
  one tree. If the Pi is genuinely retired, roughly 400 lines of Wayland handling can be deleted
  outright and several files get much shorter. Cheaper to keep than to re-add.
- **`cmd.exe` or PowerShell** for the OS route's shell.

### Review — session 1 of the port (2026-08-26)

Three of the five areas are done and proven on this box, plus the one the brief did not list.
Two areas remain. All 27 harnesses green, 1,299 checks.

#### Done

- [x] **Area 3 — gestures.** `tools/win_input.py` (new): `SendInput` via `ctypes`, mouse only.
      `gesture_pointer.py`'s three emission methods now call a backend adapter, so every guard
      above them is platform-free; `_UInputMouse` wraps evdev behind the same four methods.
      `--check` reports the Windows preconditions and verifies the union has one member.
      **17/17 in `verify_pointer.py`, with sections 2–4 running the real, unmodified guard
      code.** The click guard holds at 160 px across all 120 drag lengths on Windows.
- [x] **Area 4 — the app catalogue.** Start Menu backend: `start_menu_dirs`, `shortcut_target`
      (a dependency-free MS-SHLLINK reader), `parse_shortcut`, `load_start_menu`.
      `load_catalogue()` dispatches by platform; `resolve()`, `ROLES`, `ALIASES` and the
      ambiguity refusal are untouched and shared. **36 applications discovered on this box.**
      `verify_launch.py` 238/238 with a new section 7 covering the Windows backend against
      synthesized `.lnk` fixtures — which means the Pi can prove the Windows reader still works.
- [x] **Area 1 — autostart.** `config/start_oddball.bat`, `config/start_oddball.vbs`,
      `tools/install_autostart.ps1` (install / status / remove). Path arithmetic verified from
      `config\`. `status` runs clean. **`install` deliberately not run** — it writes to LB's
      startup folder and that is his call.
- [x] **The blocklist hole the brief did not list.** `os_controller.FORBIDDEN` now selects
      `_WINDOWS` (33 patterns) or `_LINUX` (17, verbatim) at import. `normalise()` folds case
      on Windows only. `active_table_name()` added. `verify_os_guard.py` 94/94 with a new
      section 0 asserting the running platform has a non-empty table.

#### Not done, and why

- [ ] **Area 2 — the face on Qt6.** `PyQt6-WebEngine` is not installed and it is a ~150 MB
      download. Not installed unprompted. Everything else about the rewrite is specified in
      the plan above; it is the one remaining piece of real code.
- [ ] **Area 5 — audio.** Already cross-platform, so the work is measurement: list the devices
      on this box, set `[wake].device` (still `"C270"`, the Pi's webcam mic), and **re-fit
      `[wake].threshold`**, which is 0.76 fitted to a microphone that is not here.

#### What the measurements said

**The blocklist was 94% ineffective and every harness was green.**
`media/data/2026-08-26-windows-blocklist-gap.csv` + `.meta.json`. 16 of 17 destructive Windows
commands passed the pre-port blocklist — `format C: /y`, `del /s /q C:\`, `Remove-Item -Recurse
-Force`, `vssadmin delete shadows`, `iwr | iex`. The one refusal, `shutdown /s /t 0`, was luck:
the word is spelled the same on both systems. After: 0 of 17 pass, and 0 of 28 ordinary
commands are wrongly refused.

**The hardcoded-path table would have scored one in three, again.** Measured against LB's own
three examples: KiCad **not installed**, Firefox **not installed**, VS Code installed at
`C:\Users\ironi\AppData\Local\Programs\Microsoft VS Code\Code.exe` — a per-user path, not the
`C:\Program Files` one anybody would have written down. The Start Menu reader finds VS Code and
answers "no such application" for the other two, which is the honest answer a hardcoded path
cannot give. Same score the Pi's `apps.py` got with `nautilus`, five years of hindsight apart.

**"The browser" got BETTER on Windows.** The Pi found Firefox and Chromium and had to ask
which. Windows records a default browser in the registry, so `_default_browser_target()` reads
it and `resolve("the browser")` returns Microsoft Edge outright.

#### What didn't work, or was wrong first

- **`pyautogui` / `pynput`, as specified in the brief.** Not used. Both are single libraries
  that can move, click, right-click AND type, and `gesture_pointer.py`'s guarantee 1 — it
  cannot type, so it cannot answer the approval prompt in `agents/os_agent.py` — was
  kernel-enforced on the Pi by a uinput device that declared no keyboard capability. Windows
  has no equivalent. `win_input.py` declares only `MOUSEINPUT` and never `KEYBDINPUT`, which
  reduces the guarantee to one union member somebody would have to add a struct to defeat.
  **It is still a downgrade** and is documented as one in that file's header rather than
  glossed. This is the open question for LB.
- **`dict(os.environ)` loses Windows' case-insensitivity.** `os.environ["AppData"]` works;
  `dict(os.environ)["AppData"]` raises, because the plain dict has the uppercase key. The
  symptom was a catalogue of **zero applications on a machine with 68 shortcuts, with no error
  anywhere** — `load_start_menu` iterated an empty directory list and succeeded. The XDG reader
  never hit this only because XDG variable names are already uppercase.
- **`\bfirewall\b` does not match `advfirewall`.** No word boundary between "adv" and
  "firewall", so the pattern missed the only spelling anybody types. The harness caught it.
- **`verify_os_guard.py` was executing a command.** Section 4 called
  `run_command("rm -rf /")` on a literal. On Windows that is not refused, so it fell through
  to `subprocess.run` — against the promise in the harness's own first line, "Nothing is
  executed by this harness." It surfaced only as two unrelated-looking assertion failures.
  The general shape: **a harness that reaches real execution when a guard MISSES has no way to
  report that the guard missed.**
- **Exact-match exclusion let an uninstaller into the catalogue.** Four bare `Uninstall`
  shortcuts were correctly dropped and `Uninstall Node.js` was not, so `resolve("uninstall")`
  found exactly one hit and offered to run it. Now matched as a prefix.
- **Raw docstrings.** Three modules gained `\P`, `\|` and `\.` inside prose and threw
  `SyntaxWarning` on import. Windows paths in documentation need `r"""`.

#### Still open

- [ ] **`cmd.exe` or PowerShell** for the OS route. `shell=True` runs `cmd.exe`; the model
      writes PowerShell. The Windows table covers both, which is correct but is a wider surface
      than pinning one shell would be.
- [ ] **No restart-on-failure.** The Pi's unit had `Restart=on-failure` / `RestartSec=5`.
      `shell:startup` has nothing equivalent — if he falls over at 3am he stays down until the
      next logon. Task Scheduler is the upgrade path; deliberately not taken yet, because a
      scheduled task is far harder for LB to see and disable than a shortcut in a folder.
- [ ] **38 of 68 shortcuts carry no readable target**, so the "is it actually installed" check
      is skipped for them. They still launch (`os.startfile` resolves an IDList fine). Refusing
      to launch them would break every Control Panel entry, so the gap is accepted and marked.
- [ ] **Pointer gain must be re-fitted.** `POINTER_GAIN = 900` was measured under libinput.
      Windows applies its own acceleration curve to relative motion, and it is non-linear.
- [ ] **Nothing has been run end-to-end.** No harness starts the assistant. `main.py --text`,
      then `--voice`, then the face, in that order, is the check that matters.
- [ ] The Pi has not been re-tested since the split. Both tables and both catalogue readers are
      kept and unit-tested from Windows, but `_LINUX` and `_load_xdg` have not run on Linux
      since the port.

### Review — session 2: the prune, PowerShell, and the face on Qt (2026-08-26)

LB's three answers: **PowerShell**, **prune the Pi**, **keep `win_input.py`**. All five areas
are now done. **27 harnesses green, 12,272 checks.**

#### The prune, measured

```
677 lines   6 files deleted outright
            config/oddball.service, config/oddball-face.desktop,
            tools/wait_for_display.sh, tools/install_autostart.sh,
            tools/install_gesture_venv.sh, stage_install.sh

~330 lines  Linux paths removed from live modules
            os_controller._LINUX (53)      app_catalogue XDG reader (163)
            gesture_pointer evdev (75)     screen_capture grabbers (31)

261 lines   app_launcher.py rewritten (193 in) — the systemd-run argv builder,
            Display's four Wayland fields, _unit_name, PINNED_PATH, TERMINALS
```

**Every removal fails loudly.** `os_controller`, `app_catalogue` and `gesture_pointer` raise on
import off Windows, naming what was deleted and where to restore it from. That constraint came
straight from L23: the blocklist's gap was invisible *because* an irrelevant table returned
"allowed" instead of erroring, and deleting the Linux branches would have recreated exactly
that hazard. `verify_os_guard` asserts the import guard exists.

#### Area 2 — the face, on PyQt6 + QtWebEngine

Runs. Frameless, always-on-top, out of the taskbar, transparent, and `--click-through` via
`WS_EX_LAYERED | WS_EX_TRANSPARENT`. Verified against `tools/face_stage.py` on 8766: the page
loaded first try, and against a dead port the retry loop backed off 1s → 2s as designed.

`_keep_trying()` survived the port **unchanged and had to**. It exists because the face's HTTP
GET races the server's bind, and `shell:startup` gives even *less* ordering than systemd did —
no `After=`, no `ExecStartPre`. It is now the only thing preventing that bug.

`--click-through` is off by default. A window the mouse passes through cannot be closed by
mouse either, so the escape hatch (Escape / Ctrl+Q / F11) is bound before anything else — the
same reasoning that put it there originally. It also warns if paired with `?chat=1`, which it
would make untypeable.

#### Area 5 — audio, and the thing the device list actually said

The C270 **is on this box**, and is the system default input. So the memory-note framing was
wrong in a useful way: the low wake scores are not a different-microphone problem, they follow
the C270 itself. The Bose Flex 2 is also connected here, so the HFP trap `[wake].device` was
pinned against is live on this machine too — the pin's reasoning survives the port intact.

What did break: **`device = "C270"` no longer resolves.** PortAudio exposes one physical device
once per host API, and Windows has four, so it matched MME, DirectSound, WASAPI and WDM-KS and
`sounddevice` refused to guess. Now `"C270 HD WebCam), Windows WASAPI"` — WASAPI chosen because
it opens the C270 at its native 48000 Hz, an exact 3:1 decimation to the wake loop's 16000,
where MME and DirectSound report 44100 and resample.

#### What didn't work, or was wrong first

- **Three textual checks defeated by this repo's own prose.** `source.split('"""')[-1]` takes
  only the file tail, because every function docstring splits it too. A grep for `shell=True`
  matched seven comments explaining why it is *not* used. A regex for `^\s*import (\w+)`
  reported `a` and `his` as undeclared dependencies, from sentences like "import a second
  copy". All three now use `ast`. In a codebase where comments outnumber statements, a textual
  check about code is a check about prose.
- **A harness anchored on code shape, not on a claim.** The `shell=False` check bracketed the
  source between `if _IS_WINDOWS:` and `else:`. Deleting the Linux branch removed both markers
  and the check *crashed* rather than passing or failing.
- **`ROLES` lost five of its seven rows**, and this is a genuine capability loss, not a
  cleanup. On the Pi they were read off `Categories=`, which genuinely says what a program IS.
  Windows Start Menu folders are named after **vendors** — "Autodesk", "Git", "NVIDIA" — and
  not one of LB's 68 shortcuts sits in a folder naming a role. Keeping `editor`, `terminal`,
  `calculator`, `file manager` would have kept four rows that can never match while still
  occupying the role tier and blocking the name tiers that *would* have found the program.
  `browser` survives because the registry answers it.
- **The free-launch path regressed, silently, and the harness caught it.** On the Pi
  `vlc.desktop` gave `entry_id == "vlc"` — one stable lowercase token per app, feeding
  `launch_intent._targets` for free. A Windows shortcut has no such id, so "VLC media player"
  yields no bare "vlc", and *"fire up vlc"* and *"open code"* stopped being free launches and
  fell through to the paid router for no reason a person could see. Fixed by admitting single
  words that are not in `DESCRIPTOR` or `ROLES` — and the harness immediately caught the first
  attempt letting *"open the player"* launch VLC, which is how `player`, `viewer`, `suite`,
  `tool`, `client` and `reader` came to be added to `DESCRIPTOR`.
- **`dict(os.environ)` drops Windows' case-insensitivity** (session 1, still the best example):
  a catalogue of zero applications on a machine with 68 shortcuts, with no error anywhere.
- **The key-missing message gave bash commands beside a Windows path.** `read -rs KEY && ... &&
  chmod 600 C:\Users\...`. A wrong instruction is worse than none — it sends the reader looking
  for a shell they do not have. Now PowerShell, using `Read-Host -AsSecureString` so the key
  does not land in PSReadLine's history file, which `os_controller` refuses to read for exactly
  that reason.
- **Two dependencies were undeclared and one was mine.** `PyQt6`/`PyQt6-WebEngine` (added this
  session and never declared) and `langchain-core`, imported by name in every agent and only
  ever arriving transitively through `langchain-google-genai`. `cv2` and `mediapipe` are now
  declared too: the sidecar-venv argument was entirely about the Pi's 3.13 interpreter, and
  this box is 3.12.10 where both import in-process.

#### Still open

- [ ] **No `.env` on this machine, so nothing has run end to end.** `main.py --text` stops at
      the key check. That is LB's to supply — the message now gives the PowerShell recipe. Until
      then `--voice`, the face against the live orchestrator, and a real `launch_app` through
      the approval gate are all unverified together.
- [ ] **Re-fit `[wake].threshold`.** Still 0.76. Same microphone as the Pi, so the low scores
      follow the hardware, not the platform — but the room, the distance and the WASAPI 48 kHz
      path are all different. Measure before trusting it.
- [ ] **Autostart has no restart-on-failure.** The Pi's unit had `Restart=on-failure` /
      `RestartSec=5` and gave up after 3 failures in 5 minutes. `shell:startup` has nothing
      equivalent: if he falls over at 3am he stays down until the next logon. Task Scheduler is
      the upgrade path, deliberately not taken yet because a scheduled task is much harder for
      LB to see and disable than a shortcut in a folder.
- [ ] **`POINTER_GAIN = 900` is still the libinput figure.** Windows applies its own
      acceleration curve to relative motion and it is not linear. Needs re-fitting on the
      desktop.
- [ ] **38 of 68 shortcuts carry no readable target**, so the "is it installed" guard is skipped
      for them. They launch correctly; the card says the check was skipped rather than implying
      it passed.
- [ ] `tools/measure_face.py` and `tools/live_test_gestures.py` still carry Wayland/labwc
      handling. Both are measurement tools off the turn path, so they were left for now — but
      `measure_face.py` counts `labwc` processes and will report zeros here.
- [ ] `docs/DEPLOY.md` and `README.md` still describe the Pi throughout. They are the next
      thing to rewrite, and they are what LB reads when something breaks at 2am.

### Review — session 3: the role map and the documentation purge (2026-08-26)

**27 harnesses green, 12,280 checks.** Both docs rewritten and every command in them run.

#### 1. `PROGRAM_ROLES` — the role tier, restored

`ROLES` had dropped to two rows in the prune, because Windows Start Menu folders are named
after vendors and cannot say what a program *is*. It is now 34 spoken phrases over 12 role
tokens, fed by `PROGRAM_ROLES`: **65 executable stems → role tokens**.

**Keyed on the executable, not the display name**, and that is the whole durability argument.
A Start Menu name carries a version — "KiCad 8.0", "Creality Print 7.2", "Python 3.12
(64-bit)" — and goes stale on the next update, which is precisely the failure mode of
hardcoding `C:\Program Files\KiCad\8.0\bin\kicad.exe`. `kicad.exe` has been `kicad.exe` for
twenty years.

This is a curated table in a module whose docstring refuses curated tables, and the note above
it draws the line explicitly, the same way `ALIASES` already did:

> The table this module refuses says **which apps exist**, and went stale the moment one was
> installed. `PROGRAM_ROLES` says **what kind of thing** a program is, *if* it is here. It
> claims nothing about what exists — "the schematic editor" with KiCad uninstalled resolves to
> nothing, exactly as "eeschema" does.

A tool not in the table still resolves by name and still launches; it is only unreachable by
role. That graceful degradation is why the table can stay short, which is what LB asked for.

Working on the real machine:

```
'the editor'     -> AMBIGUOUS: OpenCode, Visual Studio Code
'the ide'        -> AMBIGUOUS: Arduino IDE, OpenCode, Visual Studio Code
'the cad program'-> AMBIGUOUS: Autodesk Fusion, OpenSCAD
'the slicer'     -> AMBIGUOUS: Anycubic Slicer Next, Creality Print 7.2, CrealityPrint, OrcaSlicer
'the terminal'   -> AMBIGUOUS: Windows PowerShell, Windows PowerShell (x86)
'the browser'    -> Microsoft Edge          <- resolved outright, from the registry
```

Ambiguity is reported, never guessed. Four slicers means he asks.

**Two harness checks were added that are worth more than the table itself**, because they catch
the way a two-sided map rots:

- every token in `PROGRAM_ROLES` must have a `ROLES` phrase reaching it — otherwise a program
  carries a category nobody can ask for, which *looks* like coverage
- every `ROLES` phrase must point at a token some program can carry (`WebBrowser` excepted; it
  comes from the registry)

**One exclusion was wrong and is fixed.** `windows powershell` was in `EXCLUDED_FOLDERS`
alongside `administrative tools`, and it should not have been: that folder holds four ordinary
shells, and excluding it made "open the terminal" resolve to nothing on a machine with several.
The system folders are excluded because their contents end sessions or edit the registry —
`Registry Editor`, `services`, `RecoveryDrive`, `Disk Cleanup`. A shell does neither, and what
gets *typed* into one is `os_controller`'s business, not the catalogue's. (`startup` stays
excluded, which is also what stops him launching himself.)

#### 2. The documentation purge

**`docs/DEPLOY.md`: 1,320 lines → 590, rewritten from scratch.** It was tarballs, staged pip
installs, `apt` packages, udev rules, a systemd unit, and a boot race against a Wayland
compositor. None of that exists now — **there is no deploy step, because he runs where he is
authored**, and that is the single largest thing the port bought.

What it documents now: the box and the 3.12 constraint (and that upgrading to 3.13 breaks
gestures at XNNPACK delegate creation, so it will look like a crash rather than a missing
dependency); the key, the model files, autostart and its missing restart-on-failure; the Qt
face and why not pywebview; every labwc rule and what replaced it; the audio host-API problem;
PowerShell and each flag's reason; the blocklist and the day it was 94% useless; the Start Menu
catalogue; the gesture guarantee that is weaker here; ports; and the two harness lessons.

**`README.md`: surgical, not rewritten.** Most of it is routing, agents and gates — accurate
and unchanged by the port, and rewriting it wholesale would have destroyed good content to no
purpose. Seven passages changed: the header (plus a new "What runs where" table), the OS route
row, the security-gate paragraph, the screenshot backend, the memory line, Setup, and one
measurement now labelled as Pi-era and not re-measured.

**Every command in both files was run**, and every `path/to/file` reference cross-checked
against the filesystem. Two apparent misses are correct: `install_gesture_venv.sh` is named in
past tense as a deleted file, and `os_controller.FORBIDDEN` is a symbol.

#### What was wrong first

- **A stale count, caught by checking rather than trusting.** The docs said "38 of 68
  shortcuts"; un-excluding the PowerShell folder made it 70 raw shortcuts, and the figure that
  actually matters for the launcher's guard was never the raw one — it is **14 of the 41
  applications he can open** that carry no readable target. Corrected in 8 places across 4
  files. A doc with wrong numbers is worse than no doc, and this one had been wrong for two
  sessions because nobody re-ran the count after changing what was excluded.

#### Still open — unchanged, and all of it needs the machine

- [ ] **`.env`, then end to end.** LB is doing this now. Nothing has run past the key check.
- [ ] **Re-fit `[wake].threshold`** (0.76, scores 0.17–0.28) and **`POINTER_GAIN`** (900, still
      the libinput figure).
- [ ] **No restart-on-failure** in `shell:startup`. Task Scheduler is the upgrade path.
- [ ] `tools/measure_face.py` and `tools/live_test_gestures.py` still carry labwc handling.
      Off the turn path; `measure_face.py` will report zeros here.

### Handover note — a bug found in the last ten minutes (2026-08-26)

Checking the calibration tooling before LB used it turned up two things.

**1. `python audio/wake.py --meter` did not run at all.** The pin to WASAPI, added earlier the
same day, made `mic_frames()` fail on every open:

```
sounddevice.PortAudioError: Error opening InputStream: Invalid sample rate [PaErrorCode -9997]
```

WASAPI is the only Windows host API that refuses a rate the hardware does not natively support.
`SAMPLE_RATE_HZ` is 16000 (openWakeWord's requirement) and the C270's native rate is 48000. MME
and DirectSound are shims that resample silently, which is why this never surfaced until the
device was pinned. Fixed with `_wasapi_settings()`, which passes
`sd.WasapiSettings(auto_convert=True)` when — and only when — the device is on WASAPI.

**The comment justifying the pin was wrong, and that is the more useful half.** It said WASAPI
opened the C270 at 48000 for "an exact 3:1 decimation" to 16000. That was reasoning, not
measurement: nothing in this repo decimates, and the stream did not open for it to try. WASAPI
resamples too — the same thing MME was doing invisibly — and what it actually buys is "not a
shim, better converter", which is a smaller claim. Corrected in `config/oddball.toml` and
`docs/DEPLOY.md`, both marked as corrections rather than quietly edited.

`audio/say.py` never had the bug: it was written on Windows, met the same refusal on the output
side, and already carries `check_output_settings` with a resample fallback. `audio/wake.py` was
written for the Pi's ALSA, where one name meant one device and every rate was accepted.

**2. `config/oddball.toml` referenced a script that does not exist.** The threshold comment
pointed at `tools/tune_threshold.py`, deleted at some earlier point. `python audio/wake.py
--meter` is the live tool — it prints a running score, a peak, and a recommended threshold —
and the comment now says so.

Verified after the fix: the meter runs, scores frames, and reports
`peak score 0.004 against threshold 0.76 - never fired`, which is correct for a silent room.
TTS confirmed separately: 2.43 s of audio, 107 KB WAV.

**The general shape, for the third time in this port:** a claim that was reasoned rather than
measured. The Start Menu role map was measured, the blocklist gap was measured, and this one
was not — it was inferred from what WASAPI *ought* to do with a native rate. Running the
command is what found it, and it took ten seconds.

### Go-live attempt — blocked on the key, and it found two bugs (2026-08-26)

**`.env` does not exist on this machine.** Checked four places: no `.env` in the repo (only
`.env.example`), no `GOOGLE_API_KEY` in the shell environment, and no user or machine
environment variable. So `main.py` cannot start and he is not live.

The startup sequence was run anyway — `wscript config\start_oddball.vbs`, exactly what logon
does — and it found two real defects that only appear on that path.

**1. The autostart threw away the one message that explained the failure.**

First run: the face was on screen, the assistant was not, and `data/oddball.log` contained only
the batch file's own header. `main.py` had printed a perfectly good multi-line explanation of
the missing key and **not one byte of it was written anywhere.**

Two causes stacked. `pythonw.exe` has no console, so its stdout and stderr handles are invalid
unless redirected; and a redirection written on a `start` line binds to `start` itself, not to
the process it launches. Fixed by wrapping each launch in `cmd /c "... >> log 2>&1"`, which
puts the redirection inside the child.

That is the worst shape a startup failure can have: **the program said exactly what was wrong
and the plumbing discarded it.** Same family as the `Outcome` work — a producer that states the
result, and a consumer that loses it.

**2. The first fix broke both launches, and the log said so.**

`^` line-continuations inside `cmd /c "..."` do not survive: a caret continues a line for the
BATCH parser, and the text after `cmd /c` is a quoted string handed to a second parser that
never sees it. The command split in half and cmd tried to run `--log` as a program —
`'--log' is not recognized as an internal or external command`. Both launches are single lines
now, with a comment saying why they cannot be wrapped.

**3. Logs are UTF-8; `Get-Content` on PS 5.1 reads ANSI.** Em-dashes came back as `â€"`, so a
correct log looked corrupted. `-Encoding utf8` added to `install_autostart.ps1` and to both
places in `docs/DEPLOY.md` that tell the reader to read a log. Ω, µ and ° would have had the
same problem, and those are most of what this thing prints.

**Also split the logs.** `oddball.log` is the assistant, `face.log` is the window. They
interleave badly — the face writes a retry line every few seconds while the server loads
models, burying the assistant's startup output at exactly the moment it is being read.

### Verified working, without a key

- **The `.env` pipeline.** Proven end to end with a throwaway key: `load_dotenv` reads the
  file, `_key_problem` accepts a well-formed key, `engine.models` imports, and `Engine()`
  constructs the router chain, every agent and every tool. The throwaway file was deleted
  immediately; `.env` is absent and gitignored.
- **The autostart path.** `config/start_oddball.vbs` → `.bat` → two processes, no console
  window, both logging correctly.
- **The face, live.** Starts from the autostart path and sits retrying with the designed
  backoff — 1s, 2s, 3s, 4s — waiting for port 8765. Exactly what it is supposed to do when the
  server is not up yet, and the reason `_keep_trying()` survived the port.
- 27 harnesses, 12,280 checks.

### The one remaining step is LB's

```powershell
$k = Read-Host 'paste the key' -AsSecureString
[Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($k)) |
  ForEach-Object { "GOOGLE_API_KEY=$_" } |
  Set-Content -Encoding utf8 .env

python main.py --text                       # cheapest first check
wscript config\start_oddball.vbs            # then the full rig
```

---

# The vault notebook — dictate a note, add to it, read it back, delete it

**2026-08-28.** LB: *"I want to be able to tell or type to Mr Odd Ball to make him take notes
and save it to whatever folder or make a new folder in the vault I ask him to"* — and then,
*"I also want him to be able to go back and edit / add more of what I say to, or delete notes.
And read them back to me."*

## What was already there, and what was not

- [x] `tools/knowledge_vault.py:write_note` already wrote Markdown, already created a named
      folder on demand, already **appended** rather than overwrote. The "make a new folder"
      half of the request needed no new storage code at all.
- [x] `agents/persona_agent.py` already had `save_to_vault` bound.
- [x] **Nothing recognised the request.** Measured before writing anything: 8 phrasings LB
      uses, `0/8` matched the free tier. All eight fell through to the paid router — 3 Gemini
      calls per note, against a 20-a-day tier.
- [x] Nothing could delete a note, and nothing could read one *aloud*.

## Built

- [x] `orchestrator/note_intent.py` — pure function of a string, injected into
      `instant.Router` as a planner. Five ops: new, append, read, list, delete.
- [x] `tools/knowledge_vault.py` — `notes()`, `find_notes()`, `read_note()`, `append_note()`,
      `list_notes()`, `trash_note()`; honours `ODDBALL_VAULT_DIR`; `--find` and `--read` CLI.
- [x] `engine/core.py` — `NoteDraft` and the three-turn dictation, the cancel escapes, and
      delete through the existing `Pending` gate (`kind="note"`).
- [x] `router.py` — one prompt line, so a refused phrasing lands on GENERAL and not OS.
- [x] `tools/verify_notes.py` — 125 checks, `--probe`.
- [x] `docs/DECISIONS.md` D50, `tasks/lessons.md` L24 and L25, README **Taking notes**.
- [x] `media/scripts/measure_note_turn.py` → `media/data/2026-08-28-note-turn-cost.csv`;
      `media/scripts/plot_note_cost.py` → `media/charts/2026-08-28-note-cost.svg`.

## Review

**It works end to end, and the cost went to zero.** 0/8 phrasings free before, 8/8 after, 0 API
calls, 18–22 ms per operation — and the measurement script carries a tripwire on
`router.router_agent`, so "no API call" is a number rather than a claim.

**Three bugs the harness found, all of them mine, all worth recording.**

1. **`_strip_lead` ate the subject of a sentence.** Its list held "it" and "is", so *"add to my
   regulator note that IT needs a heatsink"* stored the note "needs a heatsink" — a sentence
   with its subject removed, filed as LB's own words. The list now holds only joints between
   the command and the content.

2. **The cancel did not fire, and two checks after it went green anyway.** `is_sleep` does not
   contain "never mind" — correctly, it is a dismissal matcher, not a cancel. The draft stayed
   open, "never mind" became the content, the *next* command became its name, and by the time
   the silence check ran there was no draft left to cancel, so it passed having tested nothing.
   L24, and the checks are now written to be independently falsifiable.

3. **The spoken form ran 41 words against a 40-word ceiling.** The clip budget was a constant
   (`MAX_WORDS - 14`) and the framing sentence is longer for a multi-entry note than a single
   one. The frame is now built first and the clip gets what is left.

**Two pre-existing problems fixed on the way past.** `knowledge_vault` was the only one of the
three vault modules not honouring `ODDBALL_VAULT_DIR`, so every harness driving a vault write
was writing to LB's real vault — L22, with the file already in place. And `read_from_vault`
walked `rglob("*.md")` with no exclusions, which would have made a trashed note keep answering
questions. There is now one walk, `notes()`, used by search, listing, resolution and the CLI.

**Two things deliberately not built**, both in D50: editing a line inside a note by voice, and
nested folders.

**The numbering jumps to D50.** D30–D47 are cited throughout this codebase and belong to the
*assistant's* decision log, not this one. Continuing at D30 would have put two decisions behind
one citation. A note explaining the gap is now at the top of `docs/DECISIONS.md`.

**28 harnesses green, 12,405 checks.** `verify_upload.py`'s single failure is pre-existing and
unrelated — confirmed by stashing this work and re-running it.

## Addendum — the last red check, and it was a leftover from the Pi

**2026-08-28.** `tools/verify_upload.py` had one failure predating the notebook work:

    FAIL  float.py watches the file chooser, and a bad signal name cannot break the window

**Cause: a WebKitGTK signal that the Windows port correctly deleted.** `run-file-chooser` is a
GTK API. The 2026-08-26 port replaced GTK4/WebKitGTK with PyQt6/QtWebEngine and removed the
guarded `view.connect("run-file-chooser", …)` along with the rest of the GTK code — rightly,
because the paperclip is a plain `<input type="file">` in `hud/face-preview.html` and
QtWebEngine gives it a native dialog with no code at all. The GTK block's own comment said it
"is a LOG LINE". Nothing was broken; only the check was stale. **L23, one more time.**

**It was also one comment away from being worse than red.** The check was
`"run-file-chooser" in chooser and "except TypeError" in chooser`. The only surviving
occurrence of `run-file-chooser` is inside a comment referring back to the GTK build — so the
first half was passing on prose. Had the port written a bare `except TypeError:` instead of
`except (AttributeError, TypeError):`, this would have gone **green while testing a signal
connection that no longer exists**. That is L25, found in the wild an hour after writing it.

**Replaced with three parsed checks**, on the invariant that still applies — a diagnostic must
never cost him the window:

- the web-settings loop is inside a `try` that catches `TypeError`
- `float.py` imports no `gi`/`gtk`/`webkit` — the Pi's toolkit was deleted, not half-wired
- no live `connect("run-file-chooser", …)`, however the comments describe the old build

The first attempt asserted *every* `setAttribute` was guarded and went red on three correct
`QWidget.setAttribute(Qt.WidgetAttribute.WA_…)` calls — core Qt enums that will not be renamed.
Overreach in the opposite direction from the substring check it replaced; narrowed to the loop.

**Mutation-tested, because a check that cannot fail is worth nothing.** Guard removed → red.
`TypeError` dropped from the tuple → red. GTK signal reinstated → caught. And the comment alone
→ still correctly reports no signal, though the text is in the file.

**28 harnesses, 12,407 checks, 0 failures. The suite is 100% green.**

## The corpus I should have used first — LB's own recordings (2026-08-28)

Going to re-fit the wake threshold, I opened `captures/` — what
`engine/run_voice.py --save-captures` writes after every wake — and found **31 wakes across
three days, 27 of them with real speech.** Two things fell out, and neither was what I went
looking for.

### 1. The wake threshold is not the problem, and I was wrong to rank it first

A capture only exists **after a wake fires**. 22 on 2026-08-26, 3 on 08-27, 6 on 08-28. The
board's *"scores 0.17–0.28, mostly did not fire"* is from **2026-08-19, on the Pi**, and is
stale: the wake word demonstrably fires on Windows. 4 of the 31 are `empty.wav` — woken by the
room, heard nothing — so the false-wake rate is ~13% and worth watching, but "he cannot be
woken" is not true and has not been for a while. **Re-fitting is demoted to a tuning job.**

### 2. He had already tried to take a note by voice, and I never checked

At **08:49:50 and 08:50:47 on 2026-08-28** — ten minutes before asking for this feature:

    "can you save a note for me in the vault"
    "i need to add a note to the vault"

Both failed against the matcher I had just shipped:

- the first **matched but stored a note whose entire body was "in the vault"** — a destination
  filed as content
- the second was **missed outright**, because it opens with "i need to" and `_drop_filler`
  works word-by-word

**Neither could have been found by the corpus in section 1 of the harness, because I wrote that
corpus.** L15, exactly: a test that builds its own world never sees what the repository put in
the real one. The real data was on disk the whole time.

#### Fixed

- `_PREAMBLE` — indirect openings as PHRASES: "i need to", "i want to", "help me", "let me".
  Phrases and not bare words, and that is the whole safety argument: **"i" cannot go in FILLER**,
  or *"I take notes in Python"* strips to "take notes in python", matches `_NEW`, and files a
  note saying "in python". Four such statements are now checks.
- `_VAULT_TAIL` — "…in the vault" / "…to my vault" is a DESTINATION naming the vault itself.
  Stripped before any branch reads the remainder, and deliberately not a `_FOLDER_PATTERNS`
  entry, which would have filed the note into `vault/vault/`.

#### And a result worth keeping

Running all 27 real utterances through the matcher as first written: **zero false positives.**
The two anchors held against real, badly-transcribed speech — "sink my schedule", "family home
yes k t o p 3 nsu 5dk" — which is the thing the invented negatives could only argue for.

Section 2b of `tools/verify_notes.py` now carries all of it permanently. **Copied in rather than
read from `captures/`**, because `.gitignore:93` excludes `captures/**` — a harness reading that
directory would pass here and pass vacuously on a fresh clone, which is L15 wearing the other hat.

**28 harnesses, 12,416 checks, 0 failures.**

## STT: the model was fitted to the Pi, and it never heard "sync my schedule" (2026-08-28)

Approved after both earlier board items died on their evidence. Full reasoning in **D51**.

- [x] `media/scripts/measure_stt_models.py` — all 27 real recordings through tiny.en, base.en
      and small.en, timed, and each transcript put through the REAL `route_hint` and free tier
      to see where it lands. Offline, keyless, no microphone.
- [x] `media/data/2026-08-28-stt-tiny-vs-base.csv` — every transcript from every model.
- [x] `media/charts/2026-08-28-stt-models.svg` + `media/scripts/plot_stt_models.py`.
- [x] `config/oddball.toml` — `[stt].model = "base.en"`, with the old block kept and marked
      superseded, the way the `[wake].threshold` block already does it.

### The headline

**tiny.en transcribed "sync my schedule" correctly 0 times out of 6.** Not degraded — by voice
that feature did not work at all, and the captures show LB simply saying it again.

|          | median | worst  | "sync my schedule" | known intents | false routes |
|----------|--------|--------|--------------------|---------------|--------------|
| tiny.en  | 0.29 s | 1.99 s | **0 / 6**          | 8 / 14        | 0            |
| base.en  | 0.57 s | 2.63 s | 3 / 6              | 9 / 14        | 1            |
| small.en | 1.76 s | 4.63 s | 4 / 6              | 11 / 14       | 1            |

### Two things I got wrong on the way, both caught by checking

1. **"The dropped frames are corrupting audio."** 3,940 `utterance buffer full` warnings — 72%
   of `oddball.log`, in 5 bursts, the largest 2,739 frames over **219 s** (the documented 217 s
   quota-retry stall). I was about to report this as audio loss. It is not:
   `run_voice.py:484` drains the queue at end of turn anyway, so those frames were headed for
   the bin regardless. It is a **logging defect** — the comment "if it is full something is
   wedged" is wrong, it is full because the turn outlived the 16 s buffer — and it buries 33
   real ERROR lines. Still open, still worth fixing, but it is not what I first said.

2. **"A minimum-duration guard would kill the false dismissal."** base.en hears a mumbled "So."
   as "That's all." and ends the conversation. A duration floor looked obvious and does not
   work: every recording is 2.4–4.4 s because **2.0 s of it is `listen.hangover_s` silence**.
   "So." is 2.56 s; "go to sleep" is 2.88 s. Nothing separates them. Refused, and recorded here
   rather than deleted.

### Still open

- [ ] **The log flood.** Collapse the dropped-frame warnings to one line per burst with a count
      and a duration, and correct the "something is wedged" comment.
- [ ] **`POINTER_GAIN = 900`** is still the libinput figure. Needs LB's hands.
- [ ] **Restart-on-failure.** Deliberately NOT built: `oddball.log` spans 14 hours with exactly
      one start marker and no crash on record. Also found the trap — the `.vbs` -> `.bat` chain
      exits in under a second, so a Task Scheduler task pointed at it records "completed
      successfully" and would never restart anything. If it is ever wanted, it needs two tasks
      whose actions block, one per process.
- [ ] **`[wake].threshold`.** Demoted: 31 wakes across three days says it fires. ~13% of them
      were silent (false wakes), which is the number to watch if it becomes annoying.
