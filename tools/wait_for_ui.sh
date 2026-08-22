#!/usr/bin/env bash
#
# wait_for_ui.sh — wait for the avatar server, then open the window.
# Author: LB   Date: 2026-08-22
#
# Exec'd by ~/.config/autostart/mroddball.desktop at desktop login. Also usable by hand:
#
#     tools/wait_for_ui.sh              wait up to 90s, then launch
#     tools/wait_for_ui.sh --timeout 30
#
# ## Why a poll and not `sleep 5`
#
# The avatar server lives INSIDE the assistant (`main.py --voice --avatar`), because state is
# published in-process — see config/mroddball.desktop for the full argument. That means two
# things start at boot with no way to order them: a desktop autostart entry cannot depend on a
# systemd user unit.
#
# And the assistant is not quick. It loads faster-whisper and an onnxruntime wake model off an
# SD card, so :8000 can be 20-30 seconds behind the desktop appearing. A fixed `sleep 5` would
# usually lose the race and leave LB with no ball and no error — the worst combination, because
# it looks like the feature was never installed.
#
# Polling turns a guessed duration into a checked fact. Same reasoning as the rig retrying its
# WebSocket every 2s instead of being ordered after the bridge.
#
# ## It gives up rather than spinning
#
# If the assistant is stopped, or crashed, or the venv has no fastapi, :8000 never comes up.
# This exits non-zero after the timeout and says so in the journal. It does NOT keep polling —
# a login-session process that spins forever on a service that is not coming is a thing you
# find months later with `ps`.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="http://127.0.0.1:8000/healthz"
TIMEOUT=90
INTERVAL=2

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --url)     URL="$2";     shift 2 ;;
        *)         echo "usage: $0 [--timeout SECONDS] [--url URL]" >&2; exit 2 ;;
    esac
done

# `logger` so this lands in the journal. A desktop autostart entry has nowhere else to print —
# Terminal=false means stdout goes to /dev/null on most sessions, and a diagnostic nobody can
# read is not a diagnostic.
note() { printf '%s\n' "$*"; logger -t mroddball "$*" 2>/dev/null || true; }

[ -x "$REPO/venv/bin/python" ] || { note "no venv at $REPO/venv — not starting the avatar"; exit 1; }

note "waiting up to ${TIMEOUT}s for the avatar server at $URL"
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS --max-time 2 "$URL" >/dev/null 2>&1; then
        note "server is up; opening the window"
        cd "$REPO" || exit 1
        # exec, so the window REPLACES this script rather than leaving a shell parked behind
        # it for the whole session.
        exec "$REPO/venv/bin/python" "$REPO/launch_ui.py"
    fi
    sleep "$INTERVAL"
done

note "avatar server never came up on $URL after ${TIMEOUT}s — no window."
note "check:  systemctl --user status oddball    (it must be running with --avatar)"
exit 1
