@echo off
setlocal EnableDelayedExpansion
REM ==========================================================================================
REM  start_oddball.bat - Mr Odd Ball, on Windows. The replacement for config/oddball.service.
REM  Author: LB    Date: 2026-08-26
REM
REM  Starts two processes, in the same order and for the same reasons the Pi did:
REM
REM    1. main.py --voice     the assistant. Wake word, capture, STT, router, voice, and the
REM                           HUD bridge on port 8765. Needs no screen and must run without
REM                           one - same as the systemd unit, which deliberately had nothing
REM                           to do with the display.
REM    2. hud/float.py        his face. A separate process, exactly as it was a separate
REM                           .desktop autostart entry, so either can run without the other.
REM
REM  Each writes to its own log in data/ - oddball.log and face.log. Separate files because
REM  the two interleave badly: the face logs a retry line every few seconds while the server
REM  is still loading models, which would bury the assistant's startup output exactly when it
REM  is the thing you are trying to read.
REM
REM  DO NOT put a `timeout` between them to "let the server come up first".
REM
REM  That was the fix attempted on the Pi and it is not the fix. Port 8765 binds only after
REM  faster-whisper and an onnxruntime wake model have loaded, which is seconds of variable
REM  work, so any sleep is either too short (and the face shows a connection error) or too
REM  long (and he is late for no reason). `hud/float.py::_keep_trying()` retries the page load
REM  forever with backoff and is the actual answer. It survived the port for this reason.
REM
REM  INSTALLING
REM    Do not put THIS file in shell:startup - it would leave a console window on screen for
REM    as long as he runs. Use the VBScript wrapper beside it:
REM
REM      1. Win+R  ->  shell:startup
REM      2. Put a shortcut to config\start_oddball.vbs in the folder that opens
REM
REM    Or let the installer do it, which also handles the uninstall and can tell you whether
REM    it is currently installed:
REM
REM      powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 install
REM ==========================================================================================

REM Repo root is this file's directory, minus \config. %~dp0 always ends in a backslash, so
REM the `..` lands correctly and this works whatever directory the shortcut was invoked from -
REM which matters, because shell:startup does not set one you can rely on.
set "REPO=%~dp0.."
pushd "%REPO%" || exit /b 1
set "REPO=%CD%"

REM The interpreter. A venv is preferred and is what the Pi used; falling back to whatever
REM `python` resolves to is deliberate, because a missing venv should degrade to "he starts
REM with the system Python" rather than to silence. If neither exists, say so in the log
REM rather than exiting 1 into a void nobody is watching.
set "PY=%REPO%\venv\Scripts\pythonw.exe"
set "PYC=%REPO%\venv\Scripts\python.exe"
if not exist "%PY%" (
    for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
    for /f "delims=" %%P in ('where python.exe  2^>nul') do if not defined PYC2 set "PYC2=%%P"
    set "PY=!PYW!"
    set "PYC=!PYC2!"
)

REM pythonw.exe, not python.exe: it is the interpreter with no console attached, which is half
REM of what makes this silent. The .vbs wrapper is the other half - it suppresses the console
REM this batch file itself would otherwise get.
if not exist "%PY%" set "PY=%PYC%"

set "LOGDIR=%REPO%\data"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM One log per process, appended, with a start marker carrying the timestamp. Appended rather
REM than truncated because the interesting question after a bad night is "did he restart, and
REM how often", and a truncating log cannot answer it.
echo. >> "%LOGDIR%\oddball.log"
echo ===== start_oddball.bat  %DATE% %TIME% ===== >> "%LOGDIR%\oddball.log"
echo   repo:        %REPO%                        >> "%LOGDIR%\oddball.log"
echo   interpreter: %PY%                          >> "%LOGDIR%\oddball.log"

if not exist "%PY%" (
    echo   FATAL: no Python found. Install one, or create %REPO%\venv >> "%LOGDIR%\oddball.log"
    popd
    exit /b 1
)

REM --- Why every launch below is wrapped in `cmd /c` ------------------------------------------
REM
REM So that the child's stdout and stderr reach the log. This was NOT the case in the first
REM version and it cost a real diagnosis:
REM
REM   `pythonw.exe` has no console, so its stdout and stderr handles are invalid unless they
REM   are redirected somewhere. And a redirection written on a `start` line binds to `start`
REM   itself, not to the process it launches. So `main.py` exited on the missing-API-key check
REM   - printing a perfectly good multi-line explanation - and NOT ONE BYTE of it was written
REM   anywhere. The observable symptom was: the face is on screen, the assistant is not, and
REM   the log contains only this script's own header.
REM
REM   That is the worst shape a startup failure can have. The program said exactly what was
REM   wrong and the plumbing threw it away.
REM
REM `cmd /c "... >> log 2>&1"` puts the redirection INSIDE the child, where it belongs. The
REM `start "" /B` still returns immediately, so the two are still started in parallel.
REM
REM The empty "" after `start` is the window TITLE argument and is not optional: without it,
REM `start` treats a quoted path as the title and never runs it - the single most common way
REM this file gets broken by an edit.

REM --- 1. the assistant ---------------------------------------------------------------------
REM ONE LINE, no `^` continuations. A caret continues a line for the BATCH parser, and the
REM text after `cmd /c` is a quoted string being handed to a second parser that never sees it
REM - so the first attempt at this split the command in half and cmd tried to run `--log` as a
REM program. Long lines are the price of the redirection; splitting them is not available.
start "" /B cmd /c ""%PY%" "%REPO%\main.py" --voice --log "%LOGDIR%\oddball.log" --save-captures "%REPO%\captures" >> "%LOGDIR%\oddball.log" 2>&1"

REM --- 2. his face ---------------------------------------------------------------------------
REM Started immediately and deliberately. See the note about `timeout` at the top.
REM
REM The URL is quoted because `?` and `&` are reserved to the shell. On the Pi this same line
REM in a .desktop file needed quoting for the same reason and for a different parser, which is
REM a good sign that the quoting is real and not superstition.
REM
REM Its stderr matters as much as the assistant's: `float.py` logs every retry of the page
REM load, and "float: ... did not load - retrying in 8s" repeating forever is precisely the
REM evidence that separates "the server never came up" from "the face never started".
REM
REM ## FRAMELESS AND TRANSPARENT - and on Windows you cannot have both of those and a title
REM ## bar. Measured 2026-08-26.
REM
REM This was briefly decorated, to give LB a normal window he could move and resize after he
REM reported being unable to type. The typing turned out to be a separate bug entirely
REM (WS_EX_NOACTIVATE - see hud/float.py), and fixing THAT is what made this configuration
REM possible: the frameless window can be clicked into and typed in now.
REM
REM The decorated version was then measured and could not be kept, because it was not
REM transparent:
REM
REM   decorated  + --transparent  ->  WS_EX_LAYERED False   (NOT transparent)
REM   frameless  + --transparent  ->  WS_EX_LAYERED True    (transparent)
REM
REM A native title bar means a standard window frame, and Windows will not composite a
REM layered translucent client area behind one. Qt does not work around it and neither
REM should this file. So it is one or the other, and transparency is the one LB asked for.
REM
REM What replaces the title bar, both in hud/float.py:
REM
REM   Ctrl+drag anywhere   -> moves the window, via QWindow.startSystemMove() so it snaps
REM                           exactly like a real title-bar drag
REM   corner grip          -> a QSizeGrip, bottom right, for resizing
REM   Ctrl+ / Ctrl- / Ctrl0-> scale him, remembered in data/face_scale
REM   Escape / Ctrl+Q      -> close him. This is the only way out with no title bar, which
REM                           is why the escape hatch is bound before anything else.
REM
REM Ctrl+drag rather than a plain drag because the QWebEngineView is a NATIVE CHILD WINDOW
REM that takes its own mouse events. A plain drag has to reach the page, or the chat box and
REM the paperclip stop working; the modifier is what lets both live on the same pixels.
REM
REM `--dim 0.3` is the chat COLUMN's own background - the panel holding code and tables,
REM which cannot go fully transparent and stay readable. The page clamps it at 0.15.
REM Passed as a FLAG, not as a second URL parameter: that would need an `&`, and in cmd a
REM bare `&` separates commands. Quoting does not save it here because this line is already
REM inside `cmd /c "..."` - the outer parse eats the inner quotes, and cmd tried to run
REM `dim=0.3` as a program. float.py builds the query string itself, where there is no shell.
start "" /B cmd /c ""%PY%" "%REPO%\hud\float.py" --url "http://127.0.0.1:8765/?chat=1" --dim 0.3 --transparent --undecorated --width 560 --height 900 >> "%LOGDIR%\face.log" 2>&1"

popd
endlocal
exit /b 0
