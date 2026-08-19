# Putting him on the Pi

_Last updated: 2026-08-19 — written during the first deploy of the merged copilot, so
everything here is what actually happened rather than what should have._

## The box

```
ssh oddball-pi          10.0.0.96   Pi 5, Debian 13 (trixie), aarch64, Python 3.13.5
```

**RAM is 8 GB.** `~/oddball/CLAUDE.md` listed this as unconfirmed; `free -h` during the deploy
reported 7.9 GiB total with 4.7 free under load. Confirmed, and the note there can be retired.

96 GB free on `/`, so disk is not a constraint even with the optional RAG stack.

## It goes in a NEW directory

```
~/oddball        the standalone assistant. STILL RUNNING. Untouched.
~/mr-odd-ball    the merged copilot.
```

Not an overwrite, and not fussiness. `~/oddball` holds the working assistant, its systemd unit
is `active` and `enabled`, and the merged repo is a different tree — dropping it on top would
leave `brains/`, `orchestrator/classify.py`, `orchestrator/tiers.py` and `run_wake.py` lying
around beside the files that replaced them, with nothing to say which was live. Two directories
costs 200 MB and buys a fallback that is one `systemctl` away.

The two cannot run at once: both serve the rig on port 8765.

## The deploy

```bash
cd ~/OneDrive/Desktop/EE_copilot_project/MR_ODD_BALL
tar czf - --exclude=venv --exclude=__pycache__ --exclude='*.pyc' --exclude=.git \
          --exclude=.env --exclude=voices --exclude=chroma_db --exclude=raw_downloads \
          --exclude=captures . \
  | ssh oddball-pi "mkdir -p ~/mr-odd-ball && tar xzf - -C ~/mr-odd-ball"
echo "PIPESTATUS: ${PIPESTATUS[@]}"      # BOTH must be 0
```

**Check `PIPESTATUS`, always.** The pipeline's exit status comes from `ssh`, so a `tar` that
failed outright still reports success. `~/oddball/CLAUDE.md` records this biting twice.

`sd_card_memory.json` rides along despite being gitignored — `tar` does not read `.gitignore`.
Delete it on the Pi so the box starts with its own memory and its own 15-day backup clock.

## What does NOT come in the tarball, and where to get it

| | how |
|---|---|
| `.env` | created by hand, once, per machine — see below |
| `voices/` | `cp ~/oddball/voices/en_US-joe-medium.onnx* voices/` |
| `models/whisper/` | `cp -r ~/oddball/models/whisper models/` — or let faster-whisper re-download |
| `venv/` | built on the box; wheels are architecture-specific |

`models/hey_mr_odd_ball.onnx` **is** committed and does arrive — it is the trained wake word,
790 KB, and losing it means a Colab run.

## The key

```bash
cd ~/mr-odd-ball
printf 'GOOGLE_API_KEY=%s\n' 'your-key' > .env
chmod 600 .env
```

`GEMINI_API_KEY` is accepted as an alias, because that is what the standalone assistant read.

**`~/oddball/.env` on this Pi is the template, not a filled-in file** — it contains
`GEMINI_API_KEY` as a bare name with no `=value`, so `brains/gemini.py:load_key()` returned ""
and Tier 3 was never actually reachable there. Do not copy it and assume you have a key.

## The venv — install in STAGES, and DETACH it

```bash
cd ~/mr-odd-ball
python3 -m venv venv
venv/bin/pip install --upgrade pip
setsid nohup ./stage_install.sh > install.log 2>&1 < /dev/null &
```

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

## Torch is NOT installed, deliberately

`requirements.txt` does not pull `sentence-transformers`, and therefore does not pull torch.
The RAG stack lives in `requirements-rag.txt` and is only needed to run `tools/vector_db.py`.
Without it `get_retriever()` returns None, the firmware agent answers ungrounded and says so,
and every other route is untouched. See the header of that file.

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
venv/bin/python hud/float.py --url 'http://127.0.0.1:8765/?chat=1' \
    --transparent --undecorated --width 560 --height 900
```

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
