#!/usr/bin/env bash
# Staged install, so a resolver backtrack is isolated to one group rather than stalling the
# whole requirements file with no output (pip -q gives you nothing while it backtracks).
#
# ## Why this file is in the repo
#
# It lived only on the Pi until 2026-08-21, while docs/DEPLOY.md instructed you to run it —
# so a deploy to a NEW box followed an instruction pointing at a file that did not exist. It
# is committed now, and the requirements audit below is the reason that mattered more than the
# missing file did.
#
# ## It must stay in step with requirements.txt
#
# The stages are hand-written rather than derived from requirements.txt, which is the point —
# grouping is what isolates a backtrack. The cost is that a package added to requirements.txt
# and not added here is installed **nowhere**, and nothing says so: the venv builds clean, the
# copilot starts, and the gap only appears as a spoken error on the one route that needed it.
#
# That had already happened twice by the time this was committed:
#
#   sympy    the MATH agent's REPL runs in THIS interpreter, so "available to the agent" and
#            "installed in the venv" are one statement. Missing, the Pi answers a derivative
#            question with "ModuleNotFoundError: no module named 'sympy'" — which is exactly
#            what it did on 2026-08-19, and what docs/DEPLOY.md describes.
#   kiutils  reads .kicad_sch and .kicad_pcb (D9). tools/kicad_parser.py wraps the import by
#            design so the HARDWARE agent still starts without it — meaning its absence is
#            silent, and every schematic question comes back as an install instruction.
#
# `python tools/verify_agents.py` is what catches both. Run it after this script, always.
#
# The RAG extras (requirements-rag.txt: torch, chromadb, embeddings) are deliberately NOT here.
# They are optional, they are gigabytes on aarch64, and the copilot is complete without them —
# see the header of that file.
cd "$HOME/mr-odd-ball" || exit 1
PIP="venv/bin/pip"

# ---------------------------------------------------------------------------------------
# The apt packages pip cannot supply. NOT installed here — this script is run detached and
# unattended, and `sudo` in a detached job either blocks forever on a password prompt or
# succeeds silently on a box where it should have asked. It CHECKS and reports instead, at
# the top, where the answer is still on screen when the pip stages finish.
#
# Each one fails in the same nasty way: the Python import succeeds and the thing breaks at
# runtime, hours later, with no pointer back here.
#
#   libportaudio2     PortAudio itself. Without it sounddevice imports and finds no device.
#   pipewire-alsa     ALSA's route to the PipeWire sink the Bluetooth speaker lives on.
#                     Without it playback "succeeds" into HDMI while the speaker sits silent.
#   python3-gi        \
#   gir1.2-webkit2-4.1 > pywebview's GTK backend. `import webview` succeeds without them and
#   python3-gi-cairo  /  webview.start() then dies looking for a toolkit (launch_ui.py).
# ---------------------------------------------------------------------------------------
APT_NEEDED=(libportaudio2 pipewire-alsa python3-gi gir1.2-webkit2-4.1 python3-gi-cairo)
MISSING=()
for pkg in "${APT_NEEDED[@]}"; do
  dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "=== APT PACKAGES MISSING — pip cannot provide these, install them yourself:"
  echo "      sudo apt install ${MISSING[*]}"
  echo "=== continuing with the pip stages; the gap is above, not below."
  echo
else
  echo "=== apt prerequisites all present"
  echo
fi

run() {
  local name="$1"; shift
  echo "=== STAGE $name START $(date +%H:%M:%S)"
  timeout 900 $PIP install --no-input "$@" > "/tmp/stage_$name.log" 2>&1
  local rc=$?
  echo "=== STAGE $name RC=$rc $(date +%H:%M:%S)"
  tail -2 "/tmp/stage_$name.log"
}

run llm    langchain-google-genai python-dotenv pydantic
run audio  sounddevice numpy websockets piper-tts faster-whisper 'onnxruntime>=1.10,<2' 'tqdm>=4,<5' 'scipy>=1,<2' 'scikit-learn>=1,<2' 'requests>=2,<3'
run wake   --no-deps 'openwakeword>=0.6.0'
run tools  langchain-community langchain-text-splitters langchain-experimental
run agents 'sympy>=1.13' 'kiutils>=1.4.8'
run search ddgs duckduckgo-search

# The desktop avatar (ui/server.py + launch_ui.py). Its own stage because it is OPTIONAL: the
# assistant runs without it, every import of it is guarded, and a failure here must not read
# as a failed install.
run ui     fastapi uvicorn pywebview

# Gesture approval is NOT a pip stage. Corrected 2026-08-22 (D15).
#
# mediapipe 1.0.1 installs on this venv and then SIGKILLs at XNNPACK delegate creation; the only
# version that RUNS here is 0.10.18, which needs a <=3.12 interpreter. So it lives in its own
# small venv and gesture_control.py shells out to it:
#
#     bash tools/install_gesture_venv.sh
#
# Putting it in this venv is worse than leaving it out — it pulls 111 MB of opencv-contrib and
# makes --backend report a backend that kills the process when used.

echo "=== ALL STAGES DONE $(date +%H:%M:%S)"
echo

# The gesture model is 7.8 MB, gitignored, and does not arrive in the tarball — same class as
# the whisper models. Fetched here rather than left to the docs because a missing model is a
# SILENT loss of the feature: get_gesture() reports NO_CAMERA, which reads as a camera fault.
# Fetched unconditionally: it is the SIDECAR venv that needs mediapipe, not this one, and the
# download itself needs neither.
if [ -f venv/bin/python ] && [ ! -f models/hand_landmarker.task ]; then
  echo "=== fetching the hand landmarker model"
  venv/bin/python tools/gesture_control.py --fetch-model || \
    echo "    fetch failed — rerun it by hand; gesture approval is off until it lands"
  echo
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "REMINDER — these apt packages are still missing:"
  echo "  sudo apt install ${MISSING[*]}"
  echo
fi

echo "Now run the harness — it is what catches a package that installed nowhere:"
echo "  venv/bin/python tools/verify_agents.py"
echo
echo "Then build the gesture sidecar venv — mediapipe cannot live in this one (D15):"
echo "  bash tools/install_gesture_venv.sh"
echo
echo "And check the new subsystems report for themselves:"
echo "  venv/bin/python tools/gesture_control.py --backend    # expect: worker says NONE"
echo "  venv/bin/python -m ui.server --demo &                 # then curl :8000/healthz"
echo "  bash tools/install_autostart.sh                       # boot: service + both windows"
