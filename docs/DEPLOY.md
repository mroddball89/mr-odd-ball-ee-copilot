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
          --exclude=install.log --exclude=vault --exclude='*.task' . \
  | ssh oddball-pi "mkdir -p ~/mr-odd-ball && tar xzf - -C ~/mr-odd-ball"
echo "PIPESTATUS: ${PIPESTATUS[@]}"      # BOTH must be 0
```

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

## The floating avatar

Three pieces, and the split between them is the part worth understanding:

| | where it runs | started by |
|---|---|---|
| the FastAPI server | **inside the assistant** | `oddball.service`, via `--avatar` |
| the state | in-process, from `hud_bridge.set_state()` | — |
| the window | the desktop session | `~/.config/autostart/mroddball.desktop` |

**The server is not its own service, and must not become one.** State is published in-process:
`orchestrator/hud_bridge.set_state()` mirrors into `ui/avatar_state.py`, and `ui/server.py`
reads from there. A separately started server serves the page perfectly and then shows a ball
that never moves — it would look like a UI bug and be an architecture mismatch. So
`config/oddball.service` carries `--avatar`, and the window is the only thing autostarted.

```bash
bash tools/install_autostart.sh            # installs all three, rewrites paths to this checkout
bash tools/install_autostart.sh --status   # includes: does the unit actually carry --avatar
```

The autostart entry Execs `tools/wait_for_ui.sh`, which polls `/healthz` for up to 90 s before
opening the window. **A `sleep 5` would lose this race** — the assistant loads faster-whisper
off an SD card and `:8000` can be 20–30 s behind the desktop appearing, and a desktop entry has
no way to be ordered after a systemd user unit. The poll turns a guessed duration into a
checked fact, which is the same argument the rig already makes by retrying its WebSocket
instead of being ordered after the bridge.

```bash
curl -s localhost:8000/healthz          # {"ok":true,"state":"sleeping","clients":1}
tools/wait_for_ui.sh --timeout 10       # open the window now, without a reboot
pkill -f launch_ui.py                   # close it — it is frameless, there is no button
journalctl --user -t mroddball          # why it did not appear at login
```

`clients` in `/healthz` is the honest test that the window is actually attached, not merely
running: it counts live `/ws/state` subscribers.

**Opening it by hand over ssh needs the session environment**, which an ssh shell does not
inherit — the same trap as `XDG_RUNTIME_DIR` for audio:

```bash
env WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0     tools/wait_for_ui.sh --timeout 20
```

The autostart entry needs none of that — it runs inside the session already.

**Verified live on 2026-08-22**, with the real window attached: a typed `hey mr odd ball` on the
rig's 8765 socket took the avatar `sleeping -> listening`, and asking him a question drove the
full sequence `sleeping -> thinking -> speaking -> sleeping`. So the ball rolls while he thinks
and bounces while he talks, from the same `set_state()` call that drives the main rig.

Not verified from here: **what it looks like.** labwc composites, so `transparent=True` should
give a real transparent surface rather than an opaque square, but that is a claim about pixels
and nobody has looked at the screen yet.

It is deliberately not respawned when killed. A presence indicator that comes back when you
dismiss it is a nuisance rather than a feature.

### Pinning him to the corner — a labwc rule, because Wayland forbids the alternative

**Wayland lets no client place its own window.** Not a labwc quirk, the protocol: there is no
"put me at 1746,906" request for an ordinary toplevel. `hud/float.py` has the same constraint
and simply accepts wherever it lands. So the placement has to come from the compositor.

```bash
bash tools/install_labwc_rule.sh            # install / update, then reload labwc
bash tools/install_labwc_rule.sh --show     # print it, write nothing
bash tools/install_labwc_rule.sh --remove   # take it out again
```

It writes `~/.config/labwc/rc.xml`, computing the corner from the **live** output size rather
than a hardcoded 1920x1080 — re-run it after a resolution change. Verified on the box: the
window reports `Absolute upper-left 1746,906` at `150x150`, exactly what the rule asked for.

Three things the script is careful about, each of which would be a bad afternoon:

- **A user `rc.xml` REPLACES the system one, it does not merge.** `/etc/xdg/labwc/rc.xml` is
  183 lines of Pi OS defaults — keybindings, theme, mouse behaviour. Writing a minimal file
  would silently discard all of it and present as "the desktop changed for no reason". The
  script copies the system default as its base when no user file exists.
- **It validates the XML before handing it to the compositor,** and reverts from the backup if
  the edit is malformed. labwc falls back to defaults on a parse error, so one bad tag changes
  the whole desktop with nothing pointing at the cause.
- **It reloads with `SIGHUP`, not `labwc --reconfigure`.** `--reconfigure` reads `LABWC_PID`,
  which is exported into the desktop *session* and is not in an ssh shell's environment — it
  exits 1 with `[ERROR] LABWC_PID not set`. It is only a wrapper around `kill -HUP $LABWC_PID`
  anyway.

`SnapToEdge` looks like the right action and is not: it **resizes** the window to fill a
quarter of the output, which would turn a 150px ball into a 960x540 one. `MoveTo` is the one.

Two properties in the rule are not cosmetic:

- `allowAlwaysOnTop="yes"` — **labwc disallows X11 always-on-top requests by default**, so
  `on_top=True` in `launch_ui.py` had been doing nothing at all. The ball only looked like it
  was on top because it happened to be mapped last.
- `skipTaskbar="yes"` — a presence indicator with a taskbar button is incongruous. Drop the
  attribute if you would rather alt-tab to him.

`fixedPosition` is deliberately **not** set: it would nail him down properly and also disable
interactive move, leaving no way to shift him without editing the file. `Super+drag` still
works; the rule only decides where he starts.

The rule applies **when the window is mapped**, so an already-open ball does not move. Restart
it — and note the bracket, or `pkill -f` matches your own ssh command line and kills it:

```bash
pkill -f '[l]aunch_ui.py'
tools/wait_for_ui.sh --timeout 20
```

### The window is 150x150 and the CSS is in vmin

Down from 300x300 on 2026-08-22: a 300px ball mid-screen sat on top of the chat panel and read
as an application rather than a presence.

`ui/avatar.html` sizes **everything** in `vmin` — ball, roll travel, bounce height, glow — with
one invariant at the top of the file:

    --ball/2 + --roll + --glow  <=  50vmin        27 + 14 + 7 = 48

The first version hardcoded a 120px ball with a `translateX(±80px)` roll. Dropped into a 150px
window that animation throws the ball completely outside the viewport: he would vanish while
thinking, which reads as a crash. Measured after the change, over four frames of each
animation, the ball spans x 11..143 and y 15..126 of a 150px window — inside at every extreme.

### `--system-site-packages`, and why the venv had to change

`pywebview` reaches for PyGObject at `webview.start()`. PyGObject is a Debian *system* package
and is not pip-installable into a plain venv, so a sealed venv gives
`ModuleNotFoundError: No module named 'gi'`. `hud/float.py` dodges this by running on
`/usr/bin/python3`; `launch_ui.py` cannot, because it needs `pywebview` *from the venv*.

Confirmed on the box: `/usr/bin/python3 -c 'import gi'` gives 3.50.0, `venv/bin/python` gives
`ModuleNotFoundError`, and `venv/pyvenv.cfg` says `include-system-site-packages = false`.

New venvs get `python3 -m venv --system-site-packages venv`. The existing 1.9 G venv does not
need rebuilding — flip the one line:

```bash
sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' \
    ~/mr-odd-ball/venv/pyvenv.cfg
venv/bin/python -c 'import gi; print("gi ok")'
```

This is the one place the merged repo departs from "a lean venv on purpose". It is scoped to
making `gi` visible and installs nothing. venv packages still shadow system ones, so it does
not change which numpy or scipy is used.

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
