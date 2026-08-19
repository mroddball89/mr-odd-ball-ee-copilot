#!/usr/bin/env bash
#
# install_autostart.sh — make Mr Odd Ball come up when the Pi does.
# Author: LB   Date: 2026-08-13
#
# Two pieces, installed separately because they have genuinely different lifetimes:
#
#   config/oddball.service       -> ~/.config/systemd/user/    the assistant (audio, no screen)
#   config/oddball-face.desktop  -> ~/.config/autostart/       his face (needs the desktop)
#
# The assistant is a systemd user unit so it starts at boot (this user has lingering enabled)
# and restarts if it falls over. The face is an XDG autostart entry because it needs the Wayland
# session, which only exists once the desktop is up.
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
    echo "== desktop autostart =="
    if [ -f "$AUTOSTART_DIR/$DESKTOP" ]; then
        say "installed: $AUTOSTART_DIR/$DESKTOP"
    else
        say "not installed"
    fi
    echo "== lingering (what lets a user unit start at boot with no login) =="
    say "$(loginctl show-user "$USER" -p Linger 2>&1 || true)"
}

remove() {
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT" "$AUTOSTART_DIR/$DESKTOP"
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
             "$REPO/venv/bin/python" "$REPO/orchestrator/run_wake.py" \
             "$REPO/tools/spike_gtk_face.py"; do
        [ -e "$f" ] || { echo "missing: $f" >&2; exit 1; }
    done

    mkdir -p "$UNIT_DIR" "$AUTOSTART_DIR"
    command install -m 0644 "$REPO/config/$UNIT" "$UNIT_DIR/$UNIT"
    command install -m 0644 "$REPO/config/$DESKTOP" "$AUTOSTART_DIR/$DESKTOP"

    systemctl --user daemon-reload
    systemctl --user enable "$UNIT"
    say "installed and enabled"

    # Lingering is the difference between "starts at boot" and "starts when you log in".
    if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
        say "WARNING: lingering is OFF — he will only start after a login."
        say "         fix with:  sudo loginctl enable-linger $USER"
    fi

    cat <<'NOTE'

  He will now come up on boot: the assistant as a service, his face on the desktop.

  Useful:
    systemctl --user status oddball        is he running
    systemctl --user restart oddball       restart him
    systemctl --user stop oddball          stop him (takes llama-server down too)
    journalctl --user -u oddball -f        follow his log

  His face is JUST HIM — a 600x600 transparent, undecorated window with no backdrop, so
  he sits on the desktop rather than covering it (D41). Wayland places it, not us.
    Super+drag  move him (there is no title bar to grab)
    Escape      close him
    Ctrl+Q      quit
  Both keys need focus, so click him first. Start him again with:
    ~/.config/autostart/oddball-face.desktop      (or re-run its Exec line)
  For the old fullscreen-on-a-backdrop look, edit that file's Exec line: drop
  --transparent --undecorated --width 600 --height 600, add --fullscreen, and remove
  ?solo=1 from the URL to get the development page with all its buttons back.

NOTE
}

case "${1:-}" in
    --status) status ;;
    --remove) remove ;;
    "")       do_install ;;
    *)        echo "usage: $0 [--status|--remove]" >&2; exit 2 ;;
esac
