#!/usr/bin/env bash
#
# install_labwc_rule.sh — pin the avatar ball to the bottom-right corner, undecorated.
# Author: LB   Date: 2026-08-22
#
#   bash tools/install_labwc_rule.sh            install / update the rule and reload labwc
#   bash tools/install_labwc_rule.sh --show     print the rule and where it would go
#   bash tools/install_labwc_rule.sh --remove   take the rule out again
#   bash tools/install_labwc_rule.sh --margin 40 --width 150 --height 150
#
# Run ON THE PI. No sudo — this only touches ~/.config/labwc/rc.xml.
#
# ## Why a compositor rule and not code
#
# **Wayland lets no client place its own window.** That is not a labwc quirk, it is the
# protocol: there is no "put me at 1746,906" request for an ordinary toplevel. `hud/float.py`
# has the same constraint and simply accepts wherever it lands (D41 notes gtk4-layer-shell,
# which would fix it properly, is not installable here).
#
# So the placement has to come from the compositor side, and labwc's windowRules are the
# supported way to say it.
#
# ## What the rule does, and why each line is there
#
#   MoveTo x,y            the actual point of the exercise. SnapToEdge sounds right and is
#                         not: it RESIZES the window to fill a quarter of the output, which
#                         would turn a 150px ball into a 960x540 one.
#   ResizeTo w,h          belt and braces. launch_ui.py already asks for 150x150; this makes
#                         the corner arithmetic true even if that ever drifts.
#   serverDecoration=no   redundant TODAY, because GDK_BACKEND=x11 makes pywebview's
#                         set_decorated(False) work (D16). Kept so the rule is self-sufficient
#                         if that backend choice is ever revisited — the failure it prevents is
#                         a title bar on a 150px ball, which is most of the window.
#   allowAlwaysOnTop=yes  NOT redundant. labwc disallows X11 always-on-top requests by
#                         default, so `on_top=True` in launch_ui.py has been doing nothing.
#                         The ball only looked like it was on top because it was mapped last.
#   skipTaskbar=yes       a presence indicator with a taskbar button is incongruous. Remove
#                         this line if you would rather be able to alt-tab to him.
#
# `fixedPosition` is deliberately NOT set. It would nail him to the corner properly, and it
# also disables interactive move — so there would be no way to shift him without editing this
# file. Super+drag stays available; the rule only decides where he STARTS.
#
# ## The arithmetic
#
# labwc has no "anchor to corner" primitive, so MoveTo takes absolute output coordinates and
# they are computed here from the real output size rather than typed in:
#
#     x = output_width  - window_width  - margin
#     y = output_height - window_height - margin
#
# Re-run this script after changing resolution or window size. It prints what it computed.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_DIR="$HOME/.config/labwc"
RC="$CFG_DIR/rc.xml"
SYSTEM_RC="/etc/xdg/labwc/rc.xml"

# Must match launch_ui.WINDOW_TITLE. If they drift the rule silently stops applying.
TITLE="Mr. Odd Ball"
WIDTH=150
HEIGHT=150
MARGIN=24

# Markers so the block can be found and replaced without a real XML parser. A sed-based
# injection needs an unambiguous fence, and "the windowRules element" is not one — the user
# may legitimately have their own.
BEGIN="<!-- BEGIN mr-odd-ball avatar rule (tools/install_labwc_rule.sh) -->"
END="<!-- END mr-odd-ball avatar rule -->"

say() { printf '  %s\n' "$*"; }
die() { printf '  %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --margin) MARGIN="$2"; shift 2 ;;
        --width)  WIDTH="$2";  shift 2 ;;
        --height) HEIGHT="$2"; shift 2 ;;
        --title)  TITLE="$2";  shift 2 ;;
        --show)   ACTION=show;   shift ;;
        --remove) ACTION=remove; shift ;;
        *) die "usage: $0 [--show|--remove] [--margin N] [--width N] [--height N]" ;;
    esac
done
ACTION="${ACTION:-install}"


output_size() {
    # "WIDTHxHEIGHT" of the current mode, from the compositor rather than from a constant.
    # wlr-randr prints e.g. "  1920x1080 px, 60.000000 Hz (preferred, current)".
    WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
        wlr-randr 2>/dev/null \
        | awk '/current/ {print $1; exit}'
}

build_rule() {
    local ow oh x y size
    size="$(output_size)"
    [ -n "$size" ] || die "could not read the output size — is labwc running, and is wlr-randr installed?"
    ow="${size%x*}"; oh="${size#*x}"
    x=$(( ow - WIDTH  - MARGIN ))
    y=$(( oh - HEIGHT - MARGIN ))
    [ "$x" -ge 0 ] && [ "$y" -ge 0 ] || die "window ${WIDTH}x${HEIGHT} + margin $MARGIN does not fit in ${ow}x${oh}"

    COMPUTED="output ${ow}x${oh}, window ${WIDTH}x${HEIGHT}, margin ${MARGIN} -> MoveTo ${x},${y}"
    RULE=$(cat <<XML
  $BEGIN
  <!-- Pins the desktop avatar to the bottom-right corner. Regenerate with
       bash ~/mr-odd-ball/tools/install_labwc_rule.sh
       $COMPUTED -->
  <windowRules>
    <windowRule title="$TITLE" serverDecoration="no" skipTaskbar="yes" allowAlwaysOnTop="yes" />
    <windowRule title="$TITLE">
      <action name="ResizeTo" width="$WIDTH" height="$HEIGHT" />
      <action name="MoveTo" x="$x" y="$y" />
    </windowRule>
  </windowRules>
  $END
XML
)
}

ensure_rc() {
    mkdir -p "$CFG_DIR"
    if [ -f "$RC" ]; then
        return
    fi
    # A ~/.config/labwc/rc.xml REPLACES the system one, it does not merge with it. Creating a
    # minimal file here would silently drop all 183 lines of Pi OS defaults — keybindings,
    # theme, mouse behaviour — and present as "the desktop changed for no reason".
    if [ -f "$SYSTEM_RC" ]; then
        cp "$SYSTEM_RC" "$RC"
        say "no user rc.xml existed; copied the system default from $SYSTEM_RC"
        say "  ($(wc -l < "$RC") lines, so nothing Pi OS ships is lost)"
    else
        printf '%s\n' '<?xml version="1.0"?>' '<labwc_config>' '</labwc_config>' > "$RC"
        say "no rc.xml anywhere; wrote a minimal one"
    fi
}

strip_rule() {
    # Remove any previous block, so this is idempotent and --remove is the same code path.
    if grep -qF "$BEGIN" "$RC" 2>/dev/null; then
        sed -i "/$(printf '%s' "$BEGIN" | sed 's/[][\.*^$/]/\\&/g')/,/$(printf '%s' "$END" | sed 's/[][\.*^$/]/\\&/g')/d" "$RC"
        return 0
    fi
    return 1
}

reload_labwc() {
    # SIGHUP, not `labwc --reconfigure`.
    #
    # `--reconfigure` is the documented way and it does not work from here: it reads
    # `LABWC_PID` to find the compositor, that variable is exported into the desktop SESSION,
    # and an ssh shell does not inherit it. It exits 1 with `[ERROR] LABWC_PID not set`, which
    # this script previously swallowed and reported as "could not reload automatically".
    #
    # `--reconfigure` is only a wrapper around `kill -HUP $LABWC_PID` anyway, so finding the
    # pid ourselves is the same operation with one fewer thing that can be unset. Verified on
    # the box: labwc picks up the new rc.xml and stays running.
    local pid
    pid="$(pgrep -x labwc | head -1)"
    if [ -n "$pid" ] && kill -HUP "$pid" 2>/dev/null; then
        sleep 1
        if pgrep -x labwc >/dev/null; then
            say "labwc reloaded its config (SIGHUP to pid $pid)"
        else
            die "labwc EXITED on reload — the desktop is gone. Restore with: cp $RC.bak $RC"
        fi
    else
        say "labwc is not running, so nothing to reload — the rule applies at next login."
    fi
}

case "$ACTION" in
show)
    build_rule
    say "$COMPUTED"
    say "would go in: $RC"
    echo
    printf '%s\n' "$RULE"
    ;;

remove)
    [ -f "$RC" ] || die "no $RC to edit"
    cp "$RC" "$RC.bak"
    if strip_rule; then
        say "rule removed (backup at $RC.bak)"
        reload_labwc
    else
        say "no rule was installed; nothing to do"
    fi
    ;;

install)
    build_rule
    ensure_rc
    cp "$RC" "$RC.bak"
    strip_rule || true

    # Insert immediately before the closing tag. labwc's root element is <labwc_config>;
    # openbox_config is accepted too and older configs use it, so match either.
    CLOSE=$(grep -n -E '^\s*</(labwc_config|openbox_config)>' "$RC" | tail -1 | cut -d: -f1)
    [ -n "$CLOSE" ] || die "could not find the closing tag in $RC — not editing it blind"

    TMP="$(mktemp)"
    head -n $(( CLOSE - 1 )) "$RC"  > "$TMP"
    printf '%s\n' "$RULE"          >> "$TMP"
    tail -n +"$CLOSE" "$RC"        >> "$TMP"
    mv "$TMP" "$RC"

    # Validate before handing it to the compositor. A malformed rc.xml is not a small problem:
    # labwc falls back to defaults, so the whole desktop changes and the cause is one bad tag
    # in a file nobody is looking at.
    if command -v xmllint >/dev/null 2>&1; then
        xmllint --noout "$RC" 2>/dev/null \
            && say "rc.xml is well-formed" \
            || { cp "$RC.bak" "$RC"; die "the edit produced invalid XML — reverted from $RC.bak"; }
    else
        python3 -c "import xml.etree.ElementTree as E,sys; E.parse('$RC')" 2>/dev/null \
            && say "rc.xml is well-formed" \
            || { cp "$RC.bak" "$RC"; die "the edit produced invalid XML — reverted from $RC.bak"; }
    fi

    say "$COMPUTED"
    say "written to $RC (backup at $RC.bak)"
    reload_labwc
    echo
    say "The rule applies WHEN THE WINDOW IS MAPPED, so an already-open ball does not move."
    say "Restart it:"
    say "  pkill -f '[l]aunch_ui.py'   # bracket, or the pattern matches your own ssh command"
    say "  tools/wait_for_ui.sh --timeout 20"
    ;;
esac
