# Putting him on the Pi

_Last updated: 2026-08-22 — written during the first deploy of the merged copilot and extended
for the avatar, the vault and gesture approval. Everything here is what actually happened
rather than what should have._

## The box

```
ssh oddball-pi          10.0.0.96   Pi 5, Debian 13 (trixie), aarch64, Python 3.13.5
```

**RAM is 8 GB.** `~/oddball/CLAUDE.md` listed this as unconfirmed; `free -h` during the deploy
reported 7.9 GiB total with 4.7 free under load. Confirmed, and the note there can be retired.

96 GB free on `/`, so disk is not a constraint even with the optional RAG stack.

## It goes in a NEW directory

```
~/mr-odd-ball    the merged copilot. LIVE — systemd unit `oddball`, enabled.
~/oddball        the pre-merge assistant. Stopped and disabled. Kept as a fallback.
```

Not an overwrite, and not fussiness. The merged repo is a different tree — dropping it on top
would have left `brains/`, `orchestrator/classify.py`, `orchestrator/tiers.py` and
`run_wake.py` lying around beside the files that replaced them, with nothing to say which was
live. Two directories cost 200 MB and buy a fallback that is one `systemctl` away.

**The two cannot run at once**: both serve the rig on port 8765. The unit name is `oddball` for
both, so installing the new one overwrote the old one's definition — there is no second service
to keep in step, only a second directory to fall back to.

## The deploy

```bash
cd ~/OneDrive/Desktop/EE_copilot_project/MR_ODD_BALL
tar czf - --exclude=venv --exclude=__pycache__ --exclude='*.pyc' --exclude=.git \
          --exclude=.env --exclude=voices --exclude=chroma_db --exclude=raw_downloads \
          --exclude=captures --exclude=sd_card_memory.json --exclude=quiz_data.json \
          --exclude=install.log --exclude=vault --exclude='*.task' \
          --exclude=data/inbox --exclude=data/projects . \
  | ssh oddball-pi "mkdir -p ~/mr-odd-ball && tar xzf - -C ~/mr-odd-ball"
echo "PIPESTATUS: ${PIPESTATUS[@]}"      # BOTH must be 0
```

`data/inbox` and `data/projects` are excluded for the same reason as `sd_card_memory.json`:
they are **the Pi's** data, written there by the paperclip. Anything uploaded while testing on
Windows would otherwise ride along, and `tar` does not delete — so a file LB filed on the Pi
last week and a stale copy of the same name from the authoring box would end up side by side as
`amp.kicad_sch` and `amp-2.kicad_sch`, with nothing saying which is current.

**On the FIRST deploy these two directories will not exist on the Pi**, and nothing creates them
until the first upload. That is fine — `engine/server.py` and `tools/file_manager.py` both
`mkdir(parents=True, exist_ok=True)` before they write. Drop the two excludes on a first deploy
if you would rather the `.gitkeep`s arrive.

**Check `PIPESTATUS`, always.** The pipeline's exit status comes from `ssh`, so a `tar` that
failed outright still reports success. `~/oddball/CLAUDE.md` records this biting twice.

### `git status` clean does NOT mean the tarball is clean

Bit on 2026-08-22 and it is a property of this deploy method, not a one-off. `tar` ships the
**working-copy bytes**; git shows you the **normalised** ones. With `* text=auto eol=lf` in
`.gitattributes`, a working file full of CRLF hashes identically to its LF blob — so
`git status` is clean, `git diff` is empty, and the tarball still carries `\r` onto the Pi:

```
tools/install_autostart.sh: line 25: $'\r': command not found
tools/install_autostart.sh: line 26: set: pipefail: invalid option name
```

`.gitattributes` already predicted this exact failure and it happened anyway, because the
protection it offers is on the git path and the deploy does not use the git path. Any Windows
tool that rewrites a file — an editor, a script doing `Path.write_text()` — reintroduces CRLF
silently, and nothing in the git workflow will tell you.

Check the working copy directly before a deploy, not `git status`:

```bash
for f in stage_install.sh tools/*.sh config/*.desktop config/*.service; do
  grep -qU $'\r' "$f" && echo "CRLF  $f"
done
```

To fix, strip the CRs from the working copy — the blobs are already LF, so this shows up as
nothing to commit:

```bash
python - <<'EOF'
import subprocess, pathlib
TEXT = {".py",".sh",".md",".txt",".toml",".csv",".html",".svg",".json",".service",".desktop"}
for name in subprocess.run(["git","ls-files"],capture_output=True,text=True).stdout.split("\n"):
    p = pathlib.Path(name)
    if name and p.is_file() and p.suffix.lower() in TEXT:
        raw = p.read_bytes()
        if b"\r\n" in raw:
            p.write_bytes(raw.replace(b"\r\n", b"\n")); print("fixed", name)
EOF
```

Shell scripts and `.desktop` entries are where it bites first: a `#!/usr/bin/env bash\r` gives
`bad interpreter`, and a desktop entry's `Exec=` line carries a trailing `^M` into the command.

### Gitignored PDFs ride along too, and that undoes deliberate deletions

Hit on 2026-08-23. `data/**/*.pdf` is gitignored, so `git status` says nothing about it — and
`tar` ships it anyway. The deploy put `pi_cam3.pdf` and `pi_cam3_noir_wide.pdf` back onto the
Pi, where they had been **deliberately deleted** for being image-only (0 extractable characters,
D12). They were removed again by hand and `data/` restored to what it was.

Nothing was harmed — an image-only PDF makes `tools/vector_db.py` print "carried NO extractable
text" and skip it — but the shape is the one that matters: **a file deleted on the Pi comes back
on the next deploy if it still exists on the authoring box.** The same is true of any datasheet
LB decides against.

Check `data/` after a deploy, or exclude it when the Pi's copy is the one you want to keep:

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && find data -type f | sort'
# ...or, when the Pi's documents are authoritative:
#   --exclude=data
```

`sd_card_memory.json` rides along despite being gitignored — `tar` does not read `.gitignore`.
On the **first** deploy, delete it on the Pi so the box starts with its own memory and its own
15-day backup clock. On a **re-deploy, exclude it instead** — shipping the authoring machine's
copy overwrites the Pi's real conversation log and resets its backup clock. Same for
`quiz_data.json`. Added to the command above.

### tar does not delete, so a re-deploy leaves orphans

A file removed in a commit stays on the Pi forever. On 2026-08-21 that meant `DECISIONS.md`,
`lessons.md` and `todo.md` were still sitting at the repo root from an older layout, and the
root `DECISIONS.md` was a stale pre-D11 copy of the decision log — authoritative-looking, and
wrong. Same hazard the two-directory split exists to avoid, arriving inside one directory.

After a deploy that removed or moved any file, check for orphans:

```bash
git ls-files | LC_ALL=C sort > /tmp/git_files.txt
ssh oddball-pi "cd ~/mr-odd-ball && find . -type f -not -path './venv/*' \
  -not -path '*/__pycache__/*' -not -path './voices/*' -not -path './chroma_db/*' \
  | sed 's|^\./||' | LC_ALL=C sort" > /tmp/pi_files.txt
comm -13 /tmp/git_files.txt /tmp/pi_files.txt      # on the Pi, not in git
```

Expect `.env`, `venv/`, `models/whisper/`, `sd_card_memory.json`, `quiz_data.json`,
`install.log` and `/tmp` logs. Anything else is an orphan — confirm it holds nothing unique
(`diff` against its tracked twin) before removing it.

## What does NOT come in the tarball, and where to get it

| | how |
|---|---|
| `.env` | created by hand, once, per machine — see below |
| `voices/` | `cp ~/oddball/voices/en_US-joe-medium.onnx* voices/` |
| `models/whisper/` | `cp -r ~/oddball/models/whisper models/` — or let faster-whisper re-download |
| `venv/` | built on the box; wheels are architecture-specific |
| `models/hand_landmarker.task` | `venv/bin/python tools/gesture_control.py --fetch-model` (7.8 MB) |
| `vault/` | LB's own notes; the Pi keeps its own, excluded from the tar above |

`models/hey_mr_odd_ball.onnx` **is** committed and does arrive — it is the trained wake word,
790 KB, and losing it means a Colab run.

## The key

**The Pi is the only machine that has one** (D7). LB runs him on the Pi and writes him on
Windows, so the Windows checkout has no `.env` and must not be given one. The consequence binds
everything written from here: **every harness has to run keyless**, because the box they are
authored on has no key. `tools/verify_agents.py` substitutes a dummy when what it loads is
unusable and says so; `--live` is the only mode that needs the real thing.

Paste-safe — echoes nothing, and confirms the length so a truncated paste is visible:

```bash
cd ~/mr-odd-ball
read -rsp 'Paste your key, then press Enter: ' KEY \
  && printf 'GOOGLE_API_KEY=%s\n' "$KEY" > .env \
  && chmod 600 .env && echo && echo "wrote ${#KEY} characters"
```

A real key is about 39 characters. **If it says 18, the placeholder went in** — which is exactly
what happened on 2026-08-19, on both boxes, and cost twenty minutes of him answering every
question with "my API key isn't working" while the reason sat in a log on a headless machine.
`engine/models.py` now refuses placeholder text, short keys and embedded whitespace at startup,
naming the file to fix.

`GEMINI_API_KEY` is accepted as an alias, because that is what the standalone assistant read.

**`~/oddball/.env` on this Pi is the template, not a filled-in file** — it contains
`GEMINI_API_KEY` as a bare name with no `=value`, so `brains/gemini.py:load_key()` returned ""
and Tier 3 was never actually reachable there. Do not copy it and assume you have a key.

## The venv — install in STAGES, and DETACH it

```bash
cd ~/mr-odd-ball
python3 -m venv --system-site-packages venv
venv/bin/pip install --upgrade pip
setsid nohup ./stage_install.sh > install.log 2>&1 < /dev/null &
```

> **SUPERSEDED 2026-08-23.** Everything in this block was for `pywebview`, and `pywebview` went
> with the floating avatar (D17). Nothing that ships today needs `--system-site-packages`:
> `hud/float.py` runs on `/usr/bin/python3` and finds `gi` there. It is kept because a venv that
> already has it does not need rebuilding, and because the trap it describes is real for anything
> that ever reaches for a Debian system package from inside a venv.

**`--system-site-packages` is new as of 2026-08-22 and it is for `pywebview`.** PyGObject is a
Debian *system* package (`python3-gi`) and is not pip-installable into a plain venv, so a
sealed venv gives `launch_ui.py` a `ModuleNotFoundError: No module named 'gi'` at
`webview.start()` — the same trap `hud/float.py` sidesteps by running on `/usr/bin/python3`.
`launch_ui.py` cannot do that, because it needs `pywebview` *from the venv*. So the venv has to
be able to see both.

The existing venv was built without it. Rather than rebuild 1.9 G, `--system-site-packages` can
be turned on in place by editing one line in `venv/pyvenv.cfg`:

```bash
sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' venv/pyvenv.cfg
venv/bin/python -c 'import gi; print("gi ok")'
```

This is the one place the merged repo departs from "a lean venv on purpose". It is scoped to
making `gi` visible; nothing is installed into the venv by it.

Then, from the desktop, poll `install.log`. Three things were learned the hard way:

1. **Detach it.** The first attempt ran `pip` directly over `ssh`. When the connection dropped
   the remote shell got SIGHUP and the install died — and because `pip -q` prints nothing while
   it works, this was indistinguishable from "still going" for several minutes.
2. **Do not use `pip -q` for this.** While the resolver backtracks it emits nothing at all, so
   a genuinely stuck resolution and a working install look identical. `stage_install.sh` runs
   each group separately and prints a return code per stage.
3. **Two pips on one venv will race.** The apparently-dead first attempt was in fact alive and
   backtracking; starting a second one against the same venv is how a venv gets corrupted. Kill
   first, verify with `pgrep -f 'pip install'`, then rebuild.

`sudo apt install libportaudio2 pipewire-alsa` is still required and is not a Python package.
`pipewire-alsa` is the one that is easy to miss: PortAudio speaks ALSA, and without that plugin
ALSA has no route to the PipeWire sink the Bluetooth speaker lives on — playback "succeeds"
into the HDMI card while the speaker sits silent.

`openwakeword` installs with `--no-deps`; it declares `tflite-runtime`, which publishes no
wheels for Python 3.12+, and we never load tflite (`framework = "onnx"` in the config).

## The apt packages pip cannot give you

Five, and every one of them fails the same nasty way: the Python import succeeds and the thing
breaks later, at runtime, with nothing pointing back here.

```bash
sudo apt install libportaudio2 pipewire-alsa python3-gi gir1.2-webkit2-4.1 python3-gi-cairo
```

| package | what breaks without it |
|---|---|
| `libportaudio2` | `sounddevice` imports and finds no device |
| `pipewire-alsa` | playback "succeeds" into HDMI while the Bluetooth speaker sits silent |
| `python3-gi` | `webview.start()` dies looking for a GUI toolkit |
| `gir1.2-webkit2-4.1` | pywebview's GTK backend has no web view to put in the window |
| `python3-gi-cairo` | the transparent surface has no renderer |

**Checked on the box, 2026-08-22:** `python3-gi`, `python3-gi-cairo`, `libportaudio2` and
`pipewire-alsa` were already installed — they came in with `hud/float.py`. `gir1.2-webkit2-4.1`
was the only one missing, and it is the one the avatar needs: the Pi has `gir1.2-webkit-6.0`
and `libwebkitgtk-6.0-4`, which is the GTK4 WebKit `float.py` uses, and pywebview's GTK backend
asks for the GTK3 WebKit2 4.1 typelib **by name** and will not take the 6.0 one in its place.

**Installed and confirmed working**, `2.52.5-1~deb13u1`. With it in, `Gtk 3.0` and `WebKit2 4.1`
both import from the venv and `launch_ui.py` opens the window. All five are now present.

> **SUPERSEDED 2026-08-23.** `gir1.2-webkit2-4.1` was pywebview's alone, and pywebview went with
> the floating avatar (D17). It is dropped from `stage_install.sh` and left installed on the Pi,
> where it is harmless. `hud/float.py` uses the GTK4 WebKit 6.0 that was already there.

One harmless line appears on every launch and is not worth chasing:

```
dbind-WARNING: AT-SPI: Error retrieving accessibility bus address:
  org.freedesktop.DBus.Error.ServiceUnknown: The name org.a11y.Bus was not provided
```

That is GTK looking for an accessibility bus this headless-ish session does not run. The window
opens regardless.

`stage_install.sh` now checks all five with `dpkg-query` at the top of the run and prints the
exact `apt install` line for whichever are missing. It does **not** install them: the script is
run detached and unattended, and `sudo` in a detached job either blocks forever on a password
prompt or succeeds silently where it should have asked.

## The boot race, and why `After=graphical-session.target` is not the fix here

**2026-08-23.** `Linger=yes` starts the systemd **user manager at boot**, so `oddball.service`
came up ~8 seconds after power-on — measured 2026-08-22: boot at 21:07:03, PID 1336 at
21:07:11 — while lightdm was still bringing labwc up. A process started then inherits no
`WAYLAND_DISPLAY`, because the compositor has not published one yet. Confirmed on the running
service: it had `XDG_RUNTIME_DIR` and nothing else. That is D10 defect 1, still live.

### The obvious fix does nothing on this box

The textbook answer is `After=graphical-session.target`. Measured here:

```
$ systemctl --user is-active graphical-session.target
inactive

$ systemctl --user list-units --type=target --state=active
basic.target  default.target  paths.target  sockets.target  timers.target
```

**The session is not systemd's.** lightdm execs `labwc-pi`, which execs `labwc`:

```
1274  /usr/sbin/lightdm
1305    lightdm --session-child 10 14
1329      /usr/bin/labwc -m
```

Nothing in that chain populates or activates `graphical-session.target`, and **ordering after a
target that never activates imposes no wait at all**. The directive reads like a fix and is
inert. It is still declared in the unit — it is correct wherever the session *is* systemd-managed,
and it costs nothing where it is not — but it is not what does the work here.

### What actually waits: `tools/wait_for_display.sh`

An `ExecStartPre=` that polls until `WAYLAND_DISPLAY` appears **in the systemd user manager's
environment**, which is the thing a service started afterwards actually inherits. Not the socket
file: nothing on this box runs `systemctl --user import-environment`, the variable arrives via
`dbus-update-activation-environment` from the compositor or the portal at a moment we do not
control, so the honest thing to wait on is the end state rather than any of the steps.

**The property this rests on was measured, not assumed.** A variable set — including set
*externally*, while `ExecStartPre` is still running — is visible to `ExecStart`:

```
ExecStartPre=/bin/sh -c 'sleep 8'      # PROBE2 set from another shell at t=3s
ExecStart  -> captured=[set_externally_at_3s]
```

And the whole race, simulated end to end without a reboot:

```
t=0   systemctl --user unset-environment WAYLAND_DISPLAY ; restart oddball
t=4   SubState=start-pre                     <- holding, which is the point
t=4   systemctl --user set-environment WAYLAND_DISPLAY=wayland-0
t=12  active/running, NRestarts=0
      WAYLAND_DISPLAY in the process -> wayland-0
```

Before this change the same process had no `WAYLAND_DISPLAY` at all.

### It never blocks the boot, and `TimeoutStartSec` had to move

On timeout (90 s, `ODDBALL_DISPLAY_WAIT_S`) the script **exits 0** and lets him start anyway.
He is a *voice* assistant: wake word, ears, answers and speech need no screen, and refusing to
start because no monitor is attached would be a worse failure than the one being fixed.
`app_launcher.find_display()` still globs `$XDG_RUNTIME_DIR/wayland-[0-9]` as the fallback —
which is the only reason launching worked at all on 2026-08-22.

`TimeoutStartSec` went 180 -> **300**, because it must exceed the 90 s wait plus real startup or
systemd kills the unit *while it is legitimately waiting*, which presents as a crash loop.

**The script must be executable.** `install_autostart.sh` chmods it, rather than trusting the
tarball: it is packed on Windows, where the exec bit is not reliably carried, and a
non-executable `ExecStartPre` fails the unit with `status=203/EXEC` — which reads like a missing
interpreter rather than a missing permission.

### Checking it after a reboot

```bash
systemctl --user show oddball -p ActiveState -p NRestarts
PID=$(systemctl --user show oddball -p MainPID --value)
tr '\0' '\n' < /proc/$PID/environ | grep WAYLAND_DISPLAY     # expect wayland-0
journalctl --user -u oddball -b | grep wait_for_display      # how long it waited
```

## Gesture approval: a second, small Python 3.12 venv

**This section was wrong twice before it was right. The short version:** mediapipe lives in its
own venv beside the main one, because the only version that runs on this Pi needs Python 3.12
and the main venv is 3.13.5. Full history in D14 and D15.

```bash
bash tools/install_gesture_venv.sh          # ~2 minutes, no sudo, ~400 MB
bash tools/install_gesture_venv.sh --check  # is it there AND does it run
venv/bin/python tools/gesture_control.py --backend
```

`--backend` from the main venv is the one command to run when gesture approval misbehaves. It
reports this interpreter, the model file, which worker will open the camera, what that worker
answered, and the worker's own view of itself.

### What was measured, on the box, 2026-08-22

| mediapipe | interpreter | installs | **runs** |
|---|---|---|---|
| 1.0.1 (`py3-none` aarch64 wheel) | 3.13.5 | yes, cleanly | **no — SIGKILL** |
| 0.10.18 (cp39–cp312 wheels) | 3.12.14 | yes | **yes**, 88 ms/frame |
| 0.10.20+ | — | no aarch64 wheel at all | — |

The 1.0.1 failure is worth recognising because it is silent and looks like nothing:

```
$ venv/bin/python tools/gesture_control.py --backend
INFO: Created TensorFlow Lite XNNPACK delegate for CPU.
Killed
exit=137
```

Every `mediapipe.tasks.vision` task dies there — `HandLandmarker` and `GestureRecognizer`
alike — with **no OOM, no throttling and 6.4 GB free**. mediapipe wraps that construction in
`CallWithCoreDumpProtection`, which converts a fatal signal into SIGKILL to suppress the core
dump, so the underlying fault is masked and exit 137 is all you ever see.

### The interpreter comes from uv, because Debian will not give you one

Debian 13 ships exactly one Python 3 and it is 3.13. On this box `apt-cache policy python3.12`
returns **nothing at all**, and neither `uv` nor `pyenv` was installed. `install_gesture_venv.sh`
uses `uv`, which downloads a prebuilt python-build-standalone aarch64 3.12 in seconds with no
compiler and no root:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # once; lands in ~/.local/bin
```

pyenv also works and takes about twenty minutes of compiling instead.

### Why not just rebuild the main venv on 3.12

That was the first plan and it is the wrong trade. The main venv is 1.9 GB of faster-whisper,
ctranslate2, piper, onnxruntime and the LangChain stack, all verified on 3.13.5. Rebuilding it
to move **one leaf feature** risks the thing that actually talks — and if a single cp312 wheel
turns out to be missing, the assistant is down rather than the camera. The sidecar is 400 MB,
builds in two minutes, and is deleted with `--remove` if it ever stops earning its place.

**Do not `pip install mediapipe` into the main venv.** It is worse than leaving it out: it pulls
111 MB of `opencv-contrib-python`, and it makes the in-process backend *look* available while
being a landmine.

### It always runs in a child process, even when it could not crash

`get_gesture()` never constructs mediapipe in the caller's interpreter. It spawns
`tools/gesture_control.py --once` and reads one token off stdout, with a 20 s ceiling.

That is crash isolation, not tidiness, and this Pi is why. A SIGKILL cannot be caught by
`try`/`except` — an in-process construction that dies takes the **assistant** with it, at an
OS-approval prompt, which is the worst place in the program for it to happen. A child that dies
is a returncode. Every failure — non-zero exit, timeout, unparseable output, missing
interpreter — becomes `NO_CAMERA`, and `NO_CAMERA` falls through to the keyboard.

### The model file, which does not arrive in the tarball

`models/hand_landmarker.task`, 7.8 MB, gitignored — same class as the whisper models.
`stage_install.sh` fetches it; by hand it is:

```bash
venv/bin/python tools/gesture_control.py --fetch-model
```

A missing model degrades to `NO_CAMERA`, which reads as a camera fault and sends you to the
wrong place, so it is fetched by the installer rather than left to this document.

### An approval costs 2.2 seconds, and only 102 ms of that is detection

Median of 10 trials on the Pi, end to end from the assistant's venv:

```
interpreter start          22 ms
import mediapipe        1,009 ms   <- paid per approval; the child is built and thrown away
build HandLandmarker       55 ms
open camera               204 ms
4 warmup frames           602 ms   <- 150 ms each: the webcam gives ~6.6 fps, not the 15 asked
inference                  47 ms
-----------------------------------
TOTAL                   2,217 ms   (min 2,197, max 2,271 — very tight)
```

`media/charts/gesture-approval-latency.svg`, with both CSVs beside it in `media/data/`.

The fix, if it becomes annoying, is a **persistent worker**: pay the 1.0 s import once and hold
a pipe open, which would bring an approval to roughly 850 ms. Not done — it turns a subprocess
call into a lifecycle to manage, and 2.2 s at a prompt that has already stopped to ask a
question is tolerable. Tracked in `tasks/todo.md`.

`WARMUP_FRAMES` is deliberately not tuned down to reclaim the 602 ms. The first frames off a
freshly opened camera are auto-exposure garbage, and there is no measurement of detection rate
versus warmup count to trade against. Guessing there is exactly the mistake D14 is about.

## The floating avatar — REMOVED, see D17

**This section described a feature that no longer exists**, and it is replaced rather than
deleted because what it documented is worth knowing was tried.

A 120px gradient ball in a `pywebview` window, fed by a FastAPI server on port 8000, pinned to a
corner by a labwc rule. It worked. It was also a *second* character standing next to the one
`hud/face-preview.html` had been rendering all along — LB: *"I do NOT want a separate glowing
blue orb in the corner."* D17 has the whole account, including the real bug the orb had been
hiding: the rig had no `thinking` state, so his face had never once reacted to him thinking.

Deleted with it: `ui/`, `launch_ui.py`, `config/mroddball.desktop`, `tools/wait_for_ui.sh`,
`tools/install_labwc_rule.sh`, the `--avatar` flag, and `fastapi` / `uvicorn` / `pywebview` from
`requirements.txt`. **Nothing on this box listens on port 8000.** The labwc rule was reverted on
the Pi and `~/.config/labwc/rc.xml` removed once it was byte-identical to the system default.

The window you actually want is `hud/float.py`, below, and it needs no venv change: it runs on
`/usr/bin/python3` and uses the GTK4 WebKit that was already there. The
`--system-site-packages` edit this section used to prescribe was for `pywebview` alone and is
not needed by anything that ships today.

## Uploading a file — the paperclip

`data/inbox/` is fed by `POST /upload`, and Mr Odd Ball empties it. Nothing is dropped into
`data/academic/` by hand any more.

```bash
curl -F file=@ECE350_syllabus.pdf http://127.0.0.1:8767/upload
#   {"ok": true, "filename": "ECE350_syllabus.pdf", "relpath": "data/inbox/...", "bytes": 84213}

curl -s http://127.0.0.1:8767/healthz     # {"ok":true,"waiting":1,...}
```

Or press the paperclip beside the chat input, which is the point of it: the browser POSTs the
file, then injects *"I just uploaded ECE350_syllabus.pdf."* into the chat as though it had been
typed. That sentence goes to the router like any other, `router.py` sends it to GENERAL, and the
persona agent files it — asking which kind of document it is when the filename does not say.

**The endpoint is a SECOND port, 8767, and it has to be.** The rig and its WebSocket share 8765
because `orchestrator/hud_bridge.py` serves the page from `websockets`' `process_request` hook —
and that hook is handed a request object with headers and **no body**, so a POST body cannot be
read there. D21 has the argument and the check. 8766 is left free for `tools/face_stage.py`.

| | port | started by |
|---|---|---|
| the rig, and its WebSocket | 8765 | `engine/run_voice.py`, from `[hud] port` |
| the upload endpoint | 8767 | `engine/run_voice.py`, from `[hud] upload_port` |
| a face stage, if one is running | 8766 | `tools/face_stage.py`, by hand |

Both bind wherever `[hud] host` says, so `--host 0.0.0.0` opens both or neither — there is no
configuration where the page is reachable from the LAN and its paperclip is not.

```bash
venv/bin/python main.py --voice --no-upload    # run without it; the paperclip then says so
venv/bin/python engine/server.py               # run ONLY it, to test uploads with no assistant
```

### Where things land, and what each costs

| he calls it | it goes to | and then |
|---|---|---|
| `academic` | `data/academic/` | vector store **and** deadline calendar rebuild |
| `datasheet` | `data/<folder>/` | vector store rebuild |
| `schematic` | `data/projects/<project>/` | nothing — unless it brought a PDF |

**A rebuild runs on a background thread**, and that is deliberate:
`tools/vector_db.py` imports torch and re-embeds every page under `data/`, and doing that inside
an agent turn would freeze his face mid-`thinking` with the microphone shut. He is prompted to
say a document is *being indexed*, never that it is ready; ask him and he calls `index_status`.

A `.kicad_sch` or `.kicad_pcb` needs no rebuild at all — `tools/kicad_parser.py` reads it off the
disk at question time, and now searches `data/projects/` before `ODDBALL_KICAD_ROOT`. So a
schematic is answerable the second it is filed:

```bash
venv/bin/python tools/file_manager.py --list
venv/bin/python tools/file_manager.py --file amp.kicad_sch --as schematic --project amp_board
venv/bin/python tools/kicad_parser.py 'amp board'      # reads it by name, no path
```

The CLI blocks until the rebuild finishes; the tool does not. That asymmetry is on purpose — a
daemon thread dies with the interpreter, so a CLI that returned immediately would report a
rebuild that never ran a line of code.

### Guards, and the one that is not about the network

Binding to 127.0.0.1 keeps the *network* out. It does not keep out **a browser on this machine**:
a `multipart/form-data` POST is a CORS simple request, so any page LB has open could fire one at
a loopback port with no preflight. So an `Origin` header, if present, must be loopback http(s).
Absent (curl, a script) is allowed — that is a shell on the box, and a shell already has `cp`.
`null`, which is what a `file://` page sends, is refused: it is also what every sandboxed iframe
on the internet sends, and the paperclip only matters while the page is served over HTTP anyway.

Also enforced: a 64 MB cap, refused from the `Content-Length` **before the body is read**; an
extension allow-list matching the picker's `accept` attribute (`tools/verify_upload.py` fails if
the two drift apart); filenames sanitised to one path segment; collisions suffixed `-2`, never
overwritten; and zip members that point outside their project folder skipped and reported.

Measured — the paperclip is disabled while a POST is in flight, so this is how long the button is
dead. 16 KB schematic **1.5 ms**, 1 MB datasheet **4.0 ms**, 16 MB gerber bundle 22.9 ms, 60 MB
against the cap 181 ms — median of three, and that top rung spread 181–220 ms across two runs of
the same script, which is an SSD and a scheduler rather than anything in the code.
`media/charts/upload-latency.svg`, data beside it. **Windows loopback, not the Pi** — re-run
`media/scripts/measure_upload.py` there before quoting it at anyone.

```bash
venv/bin/python tools/verify_upload.py         # 134 checks, no key, no network off-box
```

### Restarting the face window after a deploy

**The paperclip lives in `hud/face-preview.html`, which the window fetched when it started.** A
deploy does not reach a window that is already open, and neither does restarting `oddball` — the
rig's WebSocket reconnects, but nothing reloads the page. So after any change to the rig or to
`hud/float.py`, the window has to be restarted or the change is invisible.

**Kill it by PID, never with `pkill -f`.** The bracket trick does not save you here, because a
kill-and-relaunch command line contains the plain name in its relaunch half:

```bash
ssh oddball-pi 'pkill -f "[f]loat.py"; setsid nohup python3 hud/float.py ... &'
#                       ^ safe                    ^ this half matches, and kills the ssh session
```

That killed the session on 2026-08-23 with the bracket applied correctly. `tasks/lessons.md` L9.
Read the environment out of the running process **first** — once it is dead, `/proc/<pid>/environ`
goes with it and `WAYLAND_DISPLAY` has to be guessed:

```bash
# 1. read the environment out of the LIVE process, and note the PID
ssh oddball-pi 'PID=$(pgrep -f "hud/float" | head -1); echo "pid $PID"; \
  tr "\0" "\n" < /proc/$PID/environ | grep -E "^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR|DISPLAY)="'

# 2. kill it by that PID, then start the new one detached
ssh oddball-pi 'cd ~/mr-odd-ball; kill $(pgrep -f "hud/float" | head -1); sleep 2; \
  export WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0; \
  setsid nohup /usr/bin/python3 hud/float.py --url "http://127.0.0.1:8765/?chat=1" \
    --transparent --undecorated --width 560 --height 900 > float.log 2>&1 < /dev/null &'
```

`pgrep` in step 2 is safe where `pkill` was not: it only *reports*, and `kill` is then given a
number rather than a pattern.

Smoke-test a change to `float.py` **before** killing the live one — it has a `--seconds` flag for
exactly this, so a broken window costs eight seconds of an overlapping square rather than a
desktop with no face on it:

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000   /usr/bin/python3 hud/float.py --url "http://127.0.0.1:8765/?chat=1" --seconds 8; echo "exit $?"'
```

Confirm the new page actually carries what you deployed:

```bash
ssh oddball-pi 'curl -s "http://127.0.0.1:8765/?chat=1" | grep -c chatClip'   # expect 1
```

### Bringing it up on the Pi the first time

**Written before this had ever run on the Pi.** Everything below except step 5 is proved by
harness on the Windows box; step 5 is the one claim that cannot be — whether a GTK file dialog
appears over a transparent, undecorated window on labwc. The steps are ordered so that each one
narrows where a failure is, rather than leaving "the paperclip doesn't work" as one symptom with
six possible causes.

**1. The config gained a key, and validation is strict.**

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && grep -A2 "^\[hud\]" config/oddball.toml'
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python orchestrator/settings.py | head -20'
```

`[hud] upload_port = 8767` must be there. `orchestrator/settings.py` raises `KeyError` on a
missing key by design, so a config without it fails the service at **startup**, not at question
time. The deploy carries `config/oddball.toml`, so this should be a non-event — check it anyway,
because the failure mode is "he stopped starting after the deploy" and this is the first thing
to rule out.

**2. The harness, on the box that matters.** No key, no network off-box.

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/verify_upload.py | tail -4'
```

**3. The endpoint, with the assistant running.** This is the server alone — no browser, no
router, no model.

```bash
systemctl --user restart oddball
sleep 20 && curl -s http://127.0.0.1:8767/healthz
curl -s -F file=@/tmp/whatever.pdf http://127.0.0.1:8767/upload
```

`healthz` refusing the connection while `systemctl --user is-active oddball` says active means
the bind failed — check `oddball.log` for `no upload endpoint on port 8767`. Something else has
the port.

**4. The filing, without the model.** Proves the destination and the index without spending
quota or waiting on a router decision.

```bash
cd ~/mr-odd-ball
venv/bin/python tools/file_manager.py --list
venv/bin/python tools/file_manager.py --file whatever.pdf --as datasheet --folder sensors
```

The CLI blocks until the rebuild finishes and prints the result; the tool called from a turn
does not, deliberately. **This is the step that will be slow** — it loads torch and re-embeds
everything under `data/`. Time it, because that number is what the background thread exists for
and nobody has it for this box yet.

**5. The paperclip.** The part that has never run.

Press it in the chat panel. `hud/float.py` logs when WebKit asks for a chooser, so:

```bash
journalctl --user -t mroddball -n 20        # or wherever float.py's stderr lands
# expect: float: page asked for a file chooser — letting WebKit show its own
```

- **No log line** → the click never reached WebKit. A page problem: check the panel is
  `?chat=1` and not a stale cached copy of the rig.
- **Log line, no dialog** → WebKit asked and GTK did not deliver. That is the portal /
  `GtkFileChooserNative` path, and it is the one genuinely unknown here.
- **Dialog, then "Upload failed"** → the card names the URL it tried and the reason. Compare
  against step 3; if curl works and the browser does not, it is the `Origin` check, and the
  card will say `403`.

**6. The whole loop.** Upload a syllabus with the paperclip and watch for three things in
order: the injected line *"I just uploaded X."* appears as your own message; the route chip
reads `general`; he says he has filed it and that it is **being** indexed, not that it is ready.

```bash
tail -f ~/mr-odd-ball/oddball.log | grep -E "route|saved|filed|rebuild"
```

If the chip says something other than `general`, the router rule is not biting — that is a
prompt problem, and the file is still in the inbox where `--list` will find it. Nothing is lost.

## Canvas calendar sync

Deadlines come from LB's live Canvas `.ics` feed. `tools/academic_calendar.py`'s PDF extractor
is a fallback and is no longer called by anything automatic — a syllabus is a snapshot, and a
date moved in week four is right in Canvas and wrong in the PDF.

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/canvas_sync.py'
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/canvas_sync.py --dry-run'
```

Or say "sync Canvas" / "update my schedule" — `sync_canvas_calendar` is bound to the ACADEMIC
agent, and `router.py` has an explicit rule sending those phrases there rather than to OS, which
is what an imperative about "my schedule" otherwise looks like.

**The feed URL is a credential and belongs in `.env`.** The token in it is the whole
authentication: anyone holding the URL reads the calendar with no login, until it is reset in
Canvas. `.env` is gitignored AND excluded from the deploy tarball, so it is written once per
machine — exactly like `GOOGLE_API_KEY` (D7).

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && grep -c ODDBALL_CANVAS_ICS .env'   # expect 1
```

Canvas gives the link under **Calendar -> Calendar Feed**, bottom right. A reset token produces
a **200 with a login page**, not a 401 — `fetch_ics` checks for `BEGIN:VCALENDAR` and says so,
because the alternative is "0 events imported", which reads as an empty semester.

Needs `icalendar`, which is in `requirements.txt` and in `stage_install.sh`'s `agents` stage.
On an already-built venv:

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/pip install "icalendar>=6.0,<8"'
```

### What one course does to the prompt

LB's single Canvas course produced **139 deadlines**, against the ten or twenty a syllabus
extraction gave. `format_calendar_for_llm()` puts the calendar in the prompt of **every**
ACADEMIC turn, so that is 9,580 characters — ~2,400 tokens — per question, and five courses
would be ~12,000.

It is bounded now: everything inside 60 days, plus every exam and project at any date, capped at
120 lines, and it **states what it omitted** so a model cannot read an absence as "nothing is
due then". Measured on the Pi after the first real sync: 89 lines listed of 139, 6,332
characters, ~1,583 tokens.

## Syllabus -> vault note

Course policies are a one-time extraction into `vault/courses/`, not a vector store. Uploading a
syllabus with the paperclip triggers it on the background thread; by hand:

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/syllabus_to_vault.py --all'
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/syllabus_to_vault.py --all --force'
```

**One Gemini call per document**, against a 20-a-day tier (D3), so `--all` skips anything that
already has a note and `--force` is what redoes them. The background job converts only the file
that was just uploaded, never the whole folder.

Verified on the Pi with LB's real 13-page POSC 201 syllabus: instructor, office hours, a
five-line grading breakdown, the full late policy and five standing rules, with no due dates
carried across and nothing invented.

**A scanned PDF is refused before the API call.** Zero extractable characters is the normal state
of a photographed syllabus, and a model handed an empty document writes a plausible invented one.
Check for this first if a conversion produces nothing:

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python -c "
import sys; sys.path.insert(0,\".\")
from tools.syllabus_to_vault import read_pdf_text, SYLLABUS_DIR
for p in SYLLABUS_DIR.glob(\"*.pdf\"):
    t, n = read_pdf_text(p)
    print(len(t), \"chars\", n, \"pages\", p.name)"'
```

Needs `pypdf`, now declared in `requirements.txt` and in `stage_install.sh`'s `agents` stage. It
was already installed everywhere — but only transitively, via `langchain-community`.

```bash
ssh oddball-pi 'cd ~/mr-odd-ball && venv/bin/python tools/verify_syllabus.py'   # 40 checks
```

## The vault

`vault/` is LB's long-term Markdown memory (D13), written by the HARDWARE, FIRMWARE and
GENERAL agents. **It is his data, in the same class as `sd_card_memory.json`** — gitignored
except for a `.gitkeep`, and it must be **excluded from a re-deploy** or the authoring box's
copy overwrites the Pi's. Already in the tar command above.

```bash
venv/bin/python tools/knowledge_vault.py --list
```

## sympy IS installed, and that was not obvious

`requirements.txt` carries `sympy` because the MATH agent's REPL runs **in this interpreter** —
"available to the agent" and "installed in the venv" are one statement. On 2026-08-19 the Pi
answered a derivative question with *"the calculation resulted in a ModuleNotFoundError because
there is no module named 'sympy'"*: the route was reachable, the sandbox executed, and there was
no maths library to import. It had been present on Windows by accident of some other dependency,
which is why nothing caught it until the Pi ran a lean venv.

`tools/verify_agents.py` now imports each library the MATH prompt invites the sandbox to use —
math, cmath, statistics, numpy, scipy, sympy — **from inside the sandbox**. That check is the
one that would not exist if the harness only verified imports.

## Torch is NOT installed, deliberately

`requirements.txt` does not pull `sentence-transformers`, and therefore does not pull torch.
The RAG stack lives in `requirements-rag.txt` and is only needed to run `tools/vector_db.py`
and `tools/academic_calendar.py`. Without it `get_retriever()` returns None, the firmware agent
answers ungrounded and says so, the academic agent says it does not know, and every other route
is untouched. See the header of that file.

The deadline banner is unaffected by any of this: `load_calendar()` returns `[]` when
`academic_calendar.json` does not exist, so a Pi with no syllabi and no torch simply never shows
one. Absent is the normal state, not a broken install.

### When you DO install it — never plain `pip install -r requirements-rag.txt`

**Installed 2026-08-21.** `sentence-transformers` → `torch`, and the default PyPI torch on
Linux/aarch64 is the CUDA build: **2,377 MB of `nvidia-*` wheels plus triton**, onto a Pi with no
NVIDIA GPU. Install CPU torch FIRST, so the rest finds it already satisfied:

```bash
cd ~/mr-odd-ball
( setsid venv/bin/pip install --no-input \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple torch > rag_install.log 2>&1 < /dev/null & )
# wait for it, then:
( setsid venv/bin/pip install --no-input -r requirements-rag.txt >> rag_install.log 2>&1 < /dev/null & )
```

Detached, per the three rules above — this is a ~1 GB install and an ssh drop would SIGHUP it.
Poll with `tail -f rag_install.log` from the desktop. To wait without self-matching, use a
bracket so the pattern cannot match your own command line (L9):

```bash
while pgrep -f '[v]env/bin/pip' >/dev/null; do sleep 15; done
```

**Verify with the wheel list, not the version string.** A `+cpu` torch with CUDA wheels beside it
means the index was ignored:

```bash
venv/bin/python -c "import torch; print(torch.__version__)"     # 2.13.0+cpu
ls venv/lib/python3.13/site-packages | grep -c nvidia           # MUST be 0
du -sh venv/                                                    # 1.9 G, not ~7 G
```

Then `venv/bin/python tools/vector_db.py`. **Read its output** — a PDF with no text layer loads
as a valid page with zero characters, and the build now names those files rather than writing an
empty collection that looks like a working one. See D12.

## Known: an unexplained reboot

The Pi **rebooted at 14:41:43** on 2026-08-19, part-way through installing scipy and
scikit-learn. Not diagnosed:

- `vcgencmd get_throttled` -> `0x0`. No undervoltage, no thermal throttling.
- 7.9 GiB RAM with 4.7 free. No OOM kill in the surviving log.
- `journalctl --list-boots` shows **only the current boot**, so the evidence went with it.

That last point is the actionable one. Persistent journald would have kept it:

```bash
sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

Worth doing before the next long job on this box — a reboot with no log is a reboot that will
happen again and still not be explained.

## Running it

```bash
cd ~/mr-odd-ball

# the layout, with no key, no model and no microphone — costs nothing against the free tier
venv/bin/python tools/demo_chat.py
# then open http://127.0.0.1:8765/?chat=1

# type at him
venv/bin/python main.py --text

# the whole thing: wake word, ears, voice, face
venv/bin/python main.py --voice
```

`XDG_RUNTIME_DIR` **must be set** or PipeWire is unreachable and audio falls back to something
that is not the Bluetooth speaker. A systemd *user* service gets it for free; an ssh session
does not.

## The float window

```bash
sudo apt install gir1.2-gtk-4.0 gir1.2-webkit-6.0 libwebkitgtk-6.0-4 python3-gi
/usr/bin/python3 hud/float.py --url 'http://127.0.0.1:8765/?chat=1' \
    --transparent --undecorated --width 560 --height 900
```

**`/usr/bin/python3`, not the venv.** PyGObject is a system package on Debian and is not
pip-installable into a plain venv, so `venv/bin/python` fails here with
`ModuleNotFoundError: No module named 'gi'`. The old entry worked only because `~/oddball`'s
venv had system site-packages. `float.py` imports nothing from the project, so the system
interpreter is enough — and `tools/install_autostart.sh` now preflights `python3 -c 'import gi'`
so a missing face is caught at install time rather than on a boot nobody is watching.

Three things make the transparency work and **all three are required** — drop any one and the
effect disappears completely rather than degrading: `?chat=1` (the page clears its own
background), `--transparent` (the window paints no backdrop), `--undecorated` (no title bar,
which is a backdrop with a name on it).

There is no close button: **Escape** closes a windowed face, **Ctrl+Q** always quits. Both need
keyboard focus, so click him first.

## Switching the service over

Not done yet, and deliberately. `~/oddball` stays live until the merged copilot has been used
in anger. When it has:

```bash
# config/oddball.service already points at main.py --voice; it needs the path updating
systemctl --user stop oddball
# edit WorkingDirectory and ExecStart to ~/mr-odd-ball, then
systemctl --user daemon-reload && systemctl --user start oddball
```


## Typed control

The chat box can do everything the voice can, which matters because the microphone currently
cannot (see the measurements below):

| typed | effect |
|---|---|
| `hey mr odd ball`, `wake up`, `oddball` | wakes him: startle, listening, conversation opens |
| `go to sleep`, `goodnight`, `that's all` | back to sleeping immediately |
| anything else | answered, whether or not he is awake |
| `yes` / `no`, or the Approve/Deny buttons | resolves a permission gate; 90 s then declines |

Both matchers are end-anchored — the phrase has to BE the line. *"why did my board go to
sleep"* is a question and gets answered.

## Open: the microphone

Measured 2026-08-19, unresolved, and the cause of most of what looked like software faults:

```
capture gain    16/16, +30 dB — already maxed, no software headroom
peak mic RMS    0.035–0.17     (healthy speech is ~0.1–0.3)
wake scores     0.17–0.28      against a threshold of 0.76
```

Whisper on audio that quiet hallucinates — the conversation log has him answering *"Don't you?
Hey, hey, thank you. Everybody, I want to let you hold me."* The persona agent replying politely
to that is what made an input fault look like an agent fault.

The 0.76 threshold was derived from recorded fixtures whose quietest positive scored 0.9771, and
raised deliberately because 45% of the band let the television wake him on 2026-08-14. Lowering
it trades false accepts back in — do not do it silently.

Three options, none chosen: move the C270 closer; record fixtures in LB's voice at real desk
distance and re-derive the threshold from those; or switch STT to `base.en`, which tolerates
poor audio better at roughly 1 s more latency.

## The gesture tuning window

`tools/live_test_gestures.py` draws the MediaPipe skeleton live and names the gesture, so the
thresholds in `tools/gesture_control.py` can be tuned against numbers instead of guesses.

**It must run from the Pi's own desktop session, not over plain `ssh`** — it needs somewhere to
put a window. From a terminal on the Pi:

```bash
cd ~/mr-odd-ball
venv/bin/python tools/live_test_gestures.py
```

It re-executes itself into `.venv-gesture` on its own (mediapipe cannot run in the 3.13 venv,
D15) and sets `QT_QPA_PLATFORM=xcb` on its own (this Pi is Wayland and the opencv wheel ships
only the xcb plugin, D26 bring-up). Neither needs doing by hand.

    q or ESC   quit, releasing the camera
    s          save the current frame to media/captures/
    h          hide/show the numbers panel

The numbers panel is the point: it prints each live ratio beside the threshold it was compared
against, so a gesture that will not fire says *by how much* it is missing.

```bash
venv/bin/python tools/live_test_gestures.py --pinch 0.35     # try a threshold, no edit
venv/bin/python tools/live_test_gestures.py --camera 1       # a second webcam
```

If it says **"The window never opened"** it will print the platform, display and session type,
and exit 2. Over SSH that is expected; from the desktop it means the Qt plugin needs forcing:

```bash
QT_QPA_PLATFORM=xcb DISPLAY=:0 venv/bin/python tools/live_test_gestures.py
```

**`~/mr-odd-ball` is not a git checkout** — it is a tar deploy target, so `git pull` there fails
with "not a git repository". Code arrives by the deploy at the top of this file. For iterating on
a single file, `scp` it:

```bash
scp tools/live_test_gestures.py oddball-pi:~/mr-odd-ball/tools/
```

No camera needed to check the classifier itself — that is pure geometry:

```bash
venv/bin/python tools/verify_gestures.py            # 31 checks
venv/bin/python tools/verify_gestures.py --probe    # proves the suite bites
```
