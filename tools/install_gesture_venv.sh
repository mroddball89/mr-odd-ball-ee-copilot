#!/usr/bin/env bash
#
# install_gesture_venv.sh — a small Python 3.12 venv beside the main one, for mediapipe only.
# Author: LB   Date: 2026-08-22
#
#   bash tools/install_gesture_venv.sh            build it
#   bash tools/install_gesture_venv.sh --check    is it there and does it work
#   bash tools/install_gesture_venv.sh --remove   delete it
#
# Run ON THE PI, from the repo root. Takes about two minutes and needs no sudo.
#
# ## Why a second venv rather than moving the first one to 3.12
#
# Measured on this Pi, 2026-08-22 (D15):
#
#   mediapipe 1.0.1 on Python 3.13   installs, then SIGKILLs at XNNPACK delegate creation
#   mediapipe 0.10.18 on Python 3.12 works — Hands() constructs, 88 ms/frame
#
# So gesture control needs a 3.12 interpreter. The main venv is 1.9 GB of faster-whisper,
# ctranslate2, piper, onnxruntime and the LangChain stack, all of it verified on 3.13.5.
# Rebuilding that to move ONE LEAF FEATURE is a large, risky change to the thing that actually
# talks — and if a cp312 wheel turns out to be missing for any of it, the assistant is down
# rather than the camera.
#
# This venv holds mediapipe and its opencv, nothing else, ~400 MB. `tools/gesture_control.py`
# shells out to it for one token per approval. Everything else stays on 3.13, untouched.
#
# ## Where the 3.12 comes from
#
# Debian 13 (trixie) ships exactly one Python 3 and it is 3.13 — `apt-cache policy python3.12`
# on this box returns nothing at all. So the interpreter comes from `uv`, which downloads a
# prebuilt python-build-standalone aarch64 build in seconds and needs no compiler and no root.
# pyenv would also work and takes ~20 minutes of building instead.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/.venv-gesture"
PY="$VENV/bin/python"
UV="${UV:-$HOME/.local/bin/uv}"

# Pinned, and both halves matter. 0.10.18 is the LAST release with an aarch64 wheel at all —
# 0.10.20 onward publish none — and it is the newest that still has `mp.solutions`, which is
# the API that runs here. An unpinned install resolves to 1.0.1 and reintroduces the SIGKILL.
MEDIAPIPE_PIN="mediapipe==0.10.18"
PYTHON_VERSION="3.12"

say() { printf '  %s\n' "$*"; }

check() {
    if [ ! -x "$PY" ]; then
        say "not built — run: bash tools/install_gesture_venv.sh"
        return 1
    fi
    say "interpreter: $("$PY" -V 2>&1)"
    say "mediapipe:   $("$PY" -c 'import mediapipe; print(mediapipe.__version__)' 2>&1 | tail -1)"
    # The real check is that it RUNS, not that it imports. That distinction is the whole
    # reason this file exists — mediapipe 1.x imports perfectly on 3.13 and then dies.
    if "$PY" - <<'PROBE' 2>/dev/null
import numpy as np, mediapipe as mp
h = mp.solutions.hands.Hands(max_num_hands=1)
h.process(np.zeros((480, 640, 3), dtype=np.uint8))
h.close()
PROBE
    then
        say "runs:        yes — Hands() constructed and processed a frame"
        say ""
        say "point the copilot at it (already the default path, so usually unnecessary):"
        say "  export ODDBALL_GESTURE_PYTHON=$PY"
        return 0
    fi
    say "runs:        NO — it imports but dies in use. See D15."
    return 1
}

remove() {
    rm -rf "$VENV"
    say "removed $VENV"
}

build() {
    if [ ! -x "$UV" ] && ! command -v uv >/dev/null 2>&1; then
        say "uv is not installed. It is how a 3.12 is obtained on a box Debian gives only 3.13:"
        say "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        say "then re-run this script."
        exit 1
    fi
    [ -x "$UV" ] || UV="$(command -v uv)"

    say "building $VENV on Python $PYTHON_VERSION"
    "$UV" venv --python "$PYTHON_VERSION" "$VENV" || { say "uv venv failed"; exit 1; }

    say "installing $MEDIAPIPE_PIN (pulls its own opencv; ~400 MB)"
    "$UV" pip install --python "$PY" "$MEDIAPIPE_PIN" || { say "install failed"; exit 1; }

    echo
    check
}

case "${1:-}" in
    --check)  check ;;
    --remove) remove ;;
    "")       build ;;
    *)        echo "usage: $0 [--check|--remove]" >&2; exit 2 ;;
esac
