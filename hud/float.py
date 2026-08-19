#!/usr/bin/env python3
"""
Module:  float.py
Purpose: Mr Odd Ball's window — the character, floating on the Pi's desktop.
Author:  LB
Date:    2026-08-13 (was tools/spike_gtk_face.py; promoted 2026-08-19)

**This was a spike and is now the application.** It was written to answer one question before
any chat UI was designed around it — does rendering `hud/face-preview.html` inside a native
GTK4 + WebKitGTK window cost meaningfully less than a Chromium kiosk? — and the answer was
yes, so it shipped, and `config/oddball-face.desktop` has been running it on the Pi since
2026-08-13 (D41).

The chat UI now exists, and nothing in this file had to change to host it. That is the point
of `?chat=1` being a mode of the same rig rather than a second page: the window loads a URL
and composites it, and the URL decides whether that is him alone or him beside a transcript.

    python hud/float.py --url 'http://127.0.0.1:8765/?chat=1'         --transparent --undecorated --width 1100 --height 620

The rename is not cosmetic. `config/oddball-face.desktop` carried a note saying this Exec line
was the one thing that would have to change when the chat application existed; leaving a file
called "spike" as the shipped entry point is how the note stays true forever.

What it proves, if the number is good:

- The rig runs **unmodified** — the same file, byte for byte, that Chromium loads. It reaches
  the WebSocket the same way too, because `hud_bridge` serves the page over HTTP on the same
  port the socket lives on and the rig derives its socket from `location.host`.
- The window is a real application surface — no browser chrome, no URL bar, no tab strip.
- The view's background can be fully transparent, so he composites onto whatever is behind
  the window with no rectangle seam.

That last property is no longer hypothetical: `--transparent` plus the rig's `?solo=1` is how
he now runs on the Pi, drawn as himself on the desktop with no backdrop at all (D41). It is
also what will let him sit above a chat transcript later and look embedded rather than framed.

Run it against a stage (`tools/face_stage.py`) or against the live orchestrator:

    python hud/float.py --url http://127.0.0.1:8766/ --fullscreen

    # how he runs on the Pi — just the character, on the desktop
    python hud/float.py --url 'http://127.0.0.1:8765/?solo=1' \
        --transparent --undecorated --width 600 --height 600

Needs, from apt rather than pip — PyGObject is a system package on Debian and building it
from source on the Pi is a fight nobody needs:

    sudo apt install gir1.2-gtk-4.0 gir1.2-webkit-6.0 libwebkitgtk-6.0-4 python3-gi
"""

from __future__ import annotations

import argparse
import os
import sys

# No accessibility bus on a kiosk-style Pi session, and GTK logs a warning per launch looking
# for one. Set before Gtk is imported, or it has already looked.
os.environ.setdefault("GTK_A11Y", "none")

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from gi.repository import Gdk, GLib, Gtk, WebKit  # noqa: E402  (must follow require_version)

# His stage colour, from the rig's own `--stage` token. The window paints this so the
# transparent WebView has something to composite onto, and so the edges of the window match
# the page instead of flashing white on open.
STAGE = "#0E1016"

# Marker carried in argv purely so tools/measure_face.py can find this process and its
# children by pattern. It is never parsed for meaning.
MARKER = "oddball-spike"


def add_escape_hatch(win: Gtk.ApplicationWindow, starts_fullscreen: bool) -> None:
    """Always give the window a way out. This is not a nicety.

    The first version of this spike ran fullscreen AND undecorated: no title bar, no close
    button, and no key bindings. That is a window with no exit — it covered the whole display
    with no way to dismiss it from the machine itself, and the only remedy was another box with
    an SSH session. Never ship a surface that can take the screen without also taking a key that
    gives it back.

    Escape leaves fullscreen, and leaves the window entirely if it is already windowed.
    Ctrl+Q always quits. F11 toggles.

    The controller runs in the CAPTURE phase deliberately: the WebView fills the window and
    would otherwise consume the keystroke first — the rig's own handler binds plain letters and
    would swallow `q` — so a bubbling handler here would never see it.
    """
    is_fs = {"on": starts_fullscreen}

    def on_key(_controller, keyval, _keycode, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_Escape:
            if is_fs["on"]:
                win.unfullscreen()
                is_fs["on"] = False
            else:
                win.close()
            return True
        if ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            win.close()
            return True
        if keyval == Gdk.KEY_F11:
            win.unfullscreen() if is_fs["on"] else win.fullscreen()
            is_fs["on"] = not is_fs["on"]
            return True
        return False

    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", on_key)
    win.add_controller(keys)


def build_window(app: Gtk.Application, url: str, fullscreen: bool, decorated: bool,
                 width: int, height: int, transparent: bool) -> Gtk.ApplicationWindow:
    """Create the window and put a transparent WebView in it showing `url`."""
    win = Gtk.ApplicationWindow(application=app, title="Mr Odd Ball")
    win.set_default_size(width, height)
    win.set_decorated(decorated)
    add_escape_hatch(win, fullscreen)

    view = WebKit.WebView()

    # Transparent, so the page composites onto the window rather than over an opaque white
    # sheet. WebKitGTK honours this only when the page's own background is also transparent,
    # which is what `?solo=1` does to the rig — without that param the page paints its stage
    # colour over this alpha and the window's own background never shows.
    # Constructed empty and filled in: Gdk.RGBA is a boxed type, and PyGObject deprecated
    # passing its fields to __init__ — they were silently ignored, which would have left the
    # colour at whatever uninitialised memory held and made this line a no-op that looked fine.
    transparent = Gdk.RGBA()
    transparent.red = transparent.green = transparent.blue = transparent.alpha = 0.0
    view.set_background_color(transparent)

    settings = view.get_settings()
    # A rig is not a browser: nothing here should be able to open a window, run a plugin, or
    # keep a back/forward list. Turning these off is also the honest configuration to measure,
    # because it is the configuration the real application would ship.
    settings.set_enable_developer_extras(False)
    settings.set_enable_webgl(False)
    settings.set_enable_media(False)
    settings.set_javascript_can_open_windows_automatically(False)
    settings.set_enable_html5_database(False)
    settings.set_enable_html5_local_storage(False)
    # ALWAYS rather than the ON_DEMAND default: the rig animates continuously at 30fps, so
    # letting the compositor tear the layer up and down is churn for no benefit.
    settings.set_hardware_acceleration_policy(WebKit.HardwareAccelerationPolicy.ALWAYS)

    view.load_uri(url)
    win.set_child(view)

    # What sits behind the page.
    #
    # `transparent` is the whole point of the solo look: the window paints nothing, the page
    # paints nothing, so the only pixels this surface contributes are the character himself
    # and the compositor shows the desktop through the rest. It needs BOTH halves — a window
    # that paints and a page that does not looks identical to no change at all, which is a
    # miserable thing to debug over SSH, so each half's comment names the other.
    #
    # `window.background` is not redundant with `window`: GTK4 applies the `.background`
    # style class to toplevels and the theme's rule for it is more specific than a bare
    # `window` selector, so setting only the latter leaves the theme's colour winning.
    #
    # Otherwise paint his stage colour, so the frame around a non-fullscreen view matches the
    # page instead of showing the theme's background. That is the configuration D36 measured,
    # and it stays the default so that measurement stays reproducible.
    css = Gtk.CssProvider()
    backdrop = "transparent" if transparent else STAGE
    css.load_from_data(f"window, window.background {{ background: {backdrop}; }}".encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    if fullscreen:
        win.fullscreen()
    return win


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8765/",
                    help="where the rig is served (default: the live orchestrator's port)")
    ap.add_argument("--fullscreen", action="store_true",
                    help="take the whole display, to match a browser kiosk for measurement. "
                         "Escape leaves fullscreen, Ctrl+Q quits, F11 toggles")
    ap.add_argument("--undecorated", action="store_true",
                    help="drop the title bar. Decorations are ON by default so a windowed "
                         "run always has a visible close button")
    ap.add_argument("--transparent", action="store_true",
                    help="paint no backdrop, so the desktop shows through and only the "
                         "character is drawn. Pair it with ?solo=1 on the URL — the page "
                         "must clear its own background too, or it paints over this")
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="quit after this long; 0 means run until closed")
    ap.add_argument(f"--marker", default=MARKER, help=argparse.SUPPRESS)
    args = ap.parse_args(argv[1:])

    app = Gtk.Application(application_id="uk.lb.oddball.spike")

    def on_activate(a: Gtk.Application) -> None:
        win = build_window(a, args.url, args.fullscreen, not args.undecorated,
                           args.width, args.height, args.transparent)
        win.present()
        if args.seconds > 0:
            GLib.timeout_add_seconds(int(args.seconds), lambda: (a.quit(), False)[1])

    app.connect("activate", on_activate)
    # Gtk.Application would try to parse our own flags as its own; it gets an empty argv.
    return app.run([])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
