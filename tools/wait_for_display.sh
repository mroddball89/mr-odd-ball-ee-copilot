#!/usr/bin/env bash
#
# wait_for_display.sh — hold the assistant back until the compositor's environment exists.
# Author: LB   Date: 2026-08-23
#
# Run as `ExecStartPre=` from config/oddball.service. Always exits 0.
#
# ## The race this closes
#
# `Linger=yes` starts the user manager at BOOT, so `oddball.service` came up roughly 8 seconds
# after power-on — measured 2026-08-22, PID 1336 at 21:07:11 against a 21:07:03 boot — while
# lightdm was still bringing labwc up. A process started then has no `WAYLAND_DISPLAY`, because
# the compositor had not yet published one. Confirmed on the running service: it had
# `XDG_RUNTIME_DIR` and nothing else. D10 defect 1, still live nine days later.
#
# ## Why not `After=graphical-session.target`
#
# Because on THIS box that target is never activated. Measured 2026-08-23:
#
#     systemctl --user is-active graphical-session.target   ->  inactive
#     active user targets: basic, default, paths, sockets, timers
#
# The session is started by **lightdm**, not by systemd — lightdm execs `labwc-pi`, which execs
# `labwc`. Nothing in that chain populates or activates systemd's graphical-session.target, so
# ordering against it imposes no wait at all. `After=` on a target that never becomes active is
# a directive that reads like a fix and does nothing. The unit still declares it, because it IS
# correct wherever the session is systemd-managed, and it costs nothing where it is not — but
# this script is what actually does the waiting here.
#
# ## What it waits for, and why that exact thing
#
# Not the socket file. `WAYLAND_DISPLAY` **in the systemd user manager's environment**, because
# that is what a service started afterwards will actually inherit. Nothing on this box runs
# `systemctl --user import-environment`; the variable arrives via
# `dbus-update-activation-environment` from the compositor or the portal, at a moment we do not
# control. So the honest thing to wait on is the end state, not any of the steps.
#
# Verified 2026-08-23 with a throwaway unit: a variable set during `ExecStartPre` IS visible to
# `ExecStart`. That is the property this whole approach rests on, so it was measured rather
# than assumed.
#
# ## It never blocks the boot
#
# On timeout it exits 0 and lets the assistant start anyway. Two reasons, and the second is the
# important one:
#
#   1. He is a VOICE assistant. Wake word, ears, answers and speech need no screen at all, and
#      refusing to start because no monitor is attached would be a worse failure than the one
#      being fixed.
#   2. `tools/app_launcher.find_display()` already discovers the socket by globbing
#      `$XDG_RUNTIME_DIR/wayland-[0-9]` when the variable is missing, which is why launching
#      worked at all yesterday. This script removes the need for that fallback on a normal
#      boot; it does not become a new single point of failure.

set -u

TIMEOUT_S="${ODDBALL_DISPLAY_WAIT_S:-90}"
INTERVAL_S=1

if [ "${TIMEOUT_S}" -le 0 ] 2>/dev/null; then
    echo "wait_for_display: disabled (ODDBALL_DISPLAY_WAIT_S=${TIMEOUT_S})"
    exit 0
fi

waited=0
while [ "${waited}" -lt "${TIMEOUT_S}" ]; do
    if systemctl --user show-environment 2>/dev/null | grep -q '^WAYLAND_DISPLAY='; then
        value="$(systemctl --user show-environment 2>/dev/null | sed -n 's/^WAYLAND_DISPLAY=//p')"
        echo "wait_for_display: WAYLAND_DISPLAY=${value} after ${waited}s"
        exit 0
    fi
    sleep "${INTERVAL_S}"
    waited=$((waited + INTERVAL_S))
done

# Not a failure. Said plainly, because a silent timeout here would present later as "he cannot
# open anything" with nothing in the log pointing back at the cause.
echo "wait_for_display: no WAYLAND_DISPLAY after ${TIMEOUT_S}s — starting anyway."
echo "wait_for_display: voice is unaffected; app launching falls back to socket discovery"
echo "wait_for_display: (tools/app_launcher.find_display). Headless? Then this is expected."
exit 0
