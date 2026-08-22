#!/usr/bin/env bash
#
# install_autostart.sh — make Mr Odd Ball come up when the Pi does.
# Author: LB   Date: 2026-08-13
#
# THREE pieces, installed separately because they have genuinely different lifetimes:
#
#   config/oddball.service       -> ~/.config/systemd/user/    the assistant (audio, no screen)
#   config/oddball-face.desktop  -> ~/.config/autostart/       his face, the full rig
#   config/mroddball.desktop     -> ~/.config/autostart/       the floating avatar ball
#
# The assistant is a systemd user unit so it starts at boot (this user has lingering enabled)
# and restarts if it falls over. The two faces are XDG autostart entries because they need the
# Wayland session, which only exists once the desktop is up.
#
# The avatar's SERVER is not any of these three — it runs inside the assistant, because state
# is published in-process (see config/mroddball.desktop). `oddball.service` carries --avatar
# for that; `mroddball.desktop` only opens the window, via tools/wait_for_ui.sh.
#
#   bash tools/install_autostart.sh            install and enable both
#   bash tools/install_autostart.sh --status   show what is installed and running
#   bash tools/install_autostart.sh --remove   take both back out
#
# Run it ON THE PI, from the repo root.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
UNIT="oddball.service"
DESKTOP="oddball-face.desktop"
AVATAR="mroddball.desktop"
WAITER="tools/wait_for_ui.sh"

say() { printf '  %s\n' "$*"; }

status() {
    echo "== systemd user unit =="
    if [ -f "$UNIT_DIR/$UNIT" ]; then
        say "installed: $UNIT_DIR/$UNIT"
        say "enabled:   $(systemctl --user is-enabled "$UNIT" 2>&1 || true)"
        say "active:    $(systemctl --user is-active "$UNIT" 2>&1 || true)"
    else
        say "not installed"
    fi
    echo "== desktop autostart: the rig =="
    if [ -f "$AUTOSTART_DIR/$DESKTOP" ]; then
        say "installed: $AUTOSTART_DIR/$DESKTOP"
    else
        say "not installed"
    fi
    echo "== desktop autostart: the avatar ball =="
    if [ -f "$AUTOSTART_DIR/$AVATAR" ]; then
        say "installed: $AUTOSTART_DIR/$AVATAR"
        say "server:    $(curl -fsS --max-time 2 http://127.0.0.1:8000/healthz 2>/dev/null || echo 'not answering on :8000')"
    else
        say "not installed"
    fi
    echo "== does the unit actually carry --avatar? =="
    if [ -f "$UNIT_DIR/$UNIT" ]; then
        if grep -q -- '--avatar' "$UNIT_DIR/$UNIT"; then
            say "yes"
        else
            say "NO — the ball will never get state. Re-run this installer."
        fi
    else
        say "no unit installed"
    fi
    echo "== lingering (what lets a user unit start at boot with no login) =="
    say "$(loginctl show-user "$USER" -p Linger 2>&1 || true)"
}

remove() {
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT" "$AUTOSTART_DIR/$DESKTOP" "$AUTOSTART_DIR/$AVATAR"
    systemctl --user daemon-reload
    say "removed both; he will not start on boot any more"
}

# NOT named `install`. A shell function shadows the external command of the same name, so an
# `install()` that calls `install -m 0644 ...` to copy its files recurses into itself until the
# stack runs out — bash exits with SIGSEGV and no error message. It cost a confused round trip
# looking for a hardware fault on a Pi that was perfectly healthy.
do_install() {
    # Fail loudly here rather than at 3am on a boot nobody is watching.
    for f in "$REPO/config/$UNIT" "$REPO/config/$DESKTOP" \
             "$REPO/venv/bin/python" "$REPO/main.py" \
             "$REPO/hud/float.py"; do
        [ -e "$f" ] || { echo "missing: $f" >&2; exit 1; }
    done

    # float.py needs system PyGObject, which a plain venv does not have. Checked here rather
    # than discovered on the next reboot, when nobody is watching and the face simply does not
    # appear.
    /usr/bin/python3 -c 'import gi' 2>/dev/null || {
        echo "missing: python3-gi (his face needs it)" >&2
        echo "  sudo apt install gir1.2-gtk-4.0 gir1.2-webkit-6.0 libwebkitgtk-6.0-4 python3-gi" >&2
        exit 1
    }

    mkdir -p "$UNIT_DIR" "$AUTOSTART_DIR"

    # The committed files carry the default paths so they read as working examples. They are
    # REWRITTEN to wherever this checkout actually lives, so relocating or renaming the repo
    # cannot leave a unit pointing at a directory that is no longer there — which is exactly
    # what happened when the merged copilot landed in ~/mr-odd-ball beside the old ~/oddball
    # and the installed unit still named the old one.
    REPO_REL="${REPO#"$HOME"/}"
    sed "s|%h/mr-odd-ball|%h/$REPO_REL|g" "$REPO/config/$UNIT" > "$UNIT_DIR/$UNIT"
    sed "s|/home/[^/]*/mr-odd-ball|$REPO|g" "$REPO/config/$DESKTOP" > "$AUTOSTART_DIR/$DESKTOP"
    sed "s|/home/[^/]*/mr-odd-ball|$REPO|g" "$REPO/config/$AVATAR"  > "$AUTOSTART_DIR/$AVATAR"
    chmod 0644 "$UNIT_DIR/$UNIT" "$AUTOSTART_DIR/$DESKTOP" "$AUTOSTART_DIR/$AVATAR"
    # The desktop entry Execs this directly, so the bit has to be set. A tarball deploy does
    # preserve the mode, but a fresh `git clone` on a box with a odd umask does not always —
    # and the failure is the entry silently doing nothing at login.
    chmod 0755 "$REPO/$WAITER"
    say "paths point at $REPO"

    systemctl --user daemon-reload
    systemctl --user enable "$UNIT"
    say "installed and enabled"

    # Lingering is the difference between "starts at boot" and "starts when you log in".
    if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
        say "WARNING: lingering is OFF — he will only start after a login."
        say "         fix with:  sudo loginctl enable-linger $USER"
    fi

    # pywebview reaches for system PyGObject at runtime. Same class of check as the float.py
    # one above, and the same reason: catch it here, not on a boot nobody is watching.
    if [ -x "$REPO/venv/bin/python" ]; then
        "$REPO/venv/bin/python" -c 'import webview' 2>/dev/null || {
            say "WARNING: pywebview is not importable from the venv — the ball will not open."
            say "         venv/bin/pip install pywebview"
            say "         sudo apt install python3-gi gir1.2-webkit2-4.1 python3-gi-cairo"
        }
        "$REPO/venv/bin/python" -c 'import fastapi, uvicorn' 2>/dev/null || {
            say "WARNING: fastapi/uvicorn missing — --avatar will warn and serve nothing."
            say "         venv/bin/pip install fastapi uvicorn"
        }
    fi

    cat <<'NOTE'

  He will now come up on boot: the copilot as a service, his face on the desktop.

  He starts ASLEEP, in three places that agree: config/oddball.toml sets
  wake.resting_state = "sleeping", the bridge replays that to every rig that connects, and the
  rig's own BOOT constant is "sleeping" — so he is asleep before the socket even opens and
  stays that way until the wake word fires.

  Useful:
    systemctl --user status oddball        is he running
    systemctl --user restart oddball       restart him
    systemctl --user stop oddball          stop him
    journalctl --user -u oddball -f        follow his log

  His face is a 560x900 transparent, undecorated window: him at the top, the chat box under
  him at 50% opacity. Wayland places it, not us.
    Super+drag  move him (there is no title bar to grab)
    Escape      close him
    Ctrl+Q      quit
  Both keys need focus, so click him first. Start him again with the Exec line in
    ~/.config/autostart/oddball-face.desktop

  For him ALONE with no chat box — the look before the merge — change ?chat=1 to ?solo=1 in
  that file and use --width 600 --height 600. For the development page and all its buttons,
  drop the query string entirely.

  THE FLOATING BALL is the third piece, and it has a different shape to the other two:

    the SERVER runs inside the assistant   oddball.service now carries --avatar
    the WINDOW is the autostart entry      ~/.config/autostart/mroddball.desktop

  That split is not arbitrary. State is published in-process, so a separately started server
  would serve the page and then show a ball that never moves. The entry runs
  tools/wait_for_ui.sh, which polls http://127.0.0.1:8000/healthz for up to 90s before opening
  the window — the assistant loads whisper off an SD card and can be 20-30s behind the desktop,
  and a desktop entry has no way to be ordered after a systemd unit.

    curl -s localhost:8000/healthz         is the server up, and what state is it holding
    tools/wait_for_ui.sh --timeout 10      open the window now, without waiting for a reboot
    pkill -f launch_ui.py                  close it (it is frameless; there is no button)
    journalctl --user -t mroddball         why the window did not appear at login

  It is deliberately not respawned when you kill it. A presence indicator that comes back when
  dismissed is a nuisance.

NOTE
}

case "${1:-}" in
    --status) status ;;
    --remove) remove ;;
    "")       do_install ;;
    *)        echo "usage: $0 [--status|--remove]" >&2; exit 2 ;;
esac
