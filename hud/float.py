#!/usr/bin/env python3
r"""
Module:  float.py
Purpose: Mr Odd Ball's window — the character, floating on the desktop.
Author:  LB
Date:    2026-08-13 (was tools/spike_gtk_face.py; promoted 2026-08-19;
                     ported GTK4/WebKitGTK -> PyQt6/QtWebEngine 2026-08-26)

**This was a spike and is now the application.** It was written to answer one question before
any chat UI was designed around it — does rendering `hud/face-preview.html` inside a native
window cost meaningfully less than a Chromium kiosk? — and the answer was yes, so it shipped.

The chat UI now exists, and nothing in this file had to change to host it. That is the point
of `?chat=1` being a mode of the same rig rather than a second page: the window loads a URL
and composites it, and the URL decides whether that is him alone or him beside a transcript.

    python hud/float.py --url "http://127.0.0.1:8765/?chat=1" \
        --transparent --undecorated --width 560 --height 900

    # just the character, on the desktop, ignoring the mouse entirely
    python hud/float.py --url "http://127.0.0.1:8765/?solo=1" \
        --transparent --undecorated --click-through --width 600 --height 600

    # half size, and pinned above everything the way he used to be
    python hud/float.py --scale 0.5 --always-on-top

# ==========================================================================================
# THE PORT — why Qt, and what each labwc rule became
# ==========================================================================================

GTK4 + WebKitGTK does not exist usefully on Windows. Two replacements were considered:

**pywebview** drives WebView2, which is already on every Windows 11 box and would have been
the smaller dependency. It was refused on one property: `--transparent` is load-bearing, not
cosmetic. The solo look is the character composited onto the desktop with **no rectangle
seam**, and that needs the page transparent and the window transparent together. WebView2
exposes `DefaultBackgroundColor`, which does not reliably reach a layered top-level window.

**PyQt6 + QtWebEngine** does it directly and provably: `WA_TranslucentBackground` on the
window, `page().setBackgroundColor(Qt.transparent)` on the view. It is a much larger install
(~150 MB) and on this workstation that costs nothing — which is exactly the kind of trade the
Pi could never make, and the first place the new hardware actually bought something.

The window flags map one for one onto what labwc rules used to do:

    labwc / GTK4                          Qt6 / Win32
    ------------------------------------  --------------------------------------------
    win.set_decorated(False)              Qt.FramelessWindowHint
    <rule> keep-above                     SetWindowPos, dynamically — see below
    <rule> skipTaskbar                    Qt.Tool
    view.set_background_color(0,0,0,0)    WA_TranslucentBackground + page background
    Gtk.EventControllerKey (CAPTURE)      QShortcut on the window
    load-failed / load-changed            loadFinished(bool)

## He lives at the BACK, and comes forward when spoken to

The first Windows version pinned him always-on-top, which is right for a HUD and wrong for a
character who is present all day: he sat over every window LB was actually working in.

So his resting position is the **bottom** of the z-order — behind every application, above the
wallpaper — and he rises to the front only while he is doing something. `ACTIVE_STATES` is the
list, and it is the states in which he is busy: `startle`, `listening`, `thinking`, `speaking`.
Anything else, including whatever `[wake].resting_state` is, sends him back down.

He learns his state by opening **his own WebSocket** to the port the page is already served
from. `hud_bridge` is a broadcaster whose docstring says it "broadcasts to whoever is connected
and does not care whether anyone is", so a second listener is the thing it was built for — and
it replays the last state on connect, so this window knows where to sit immediately rather than
at the next transition.

`WS_EX_NOACTIVATE` is set alongside, so he never steals focus. Without it, rising at the wake
word would take a keystroke out of whatever LB is typing into at the exact moment he starts
speaking, which is the worst possible instant to do it.

`--always-on-top` restores the old behaviour.

## Size

Ctrl+ and Ctrl- resize him, Ctrl+0 resets, and the size is remembered in `data/face_scale`
between runs. Keys rather than a drag handle because the window is frameless: there is no edge
to grab, and drawing one would mean putting a rectangle around a character whose whole point is
not having one. He scales about his own CENTRE, so he does not walk across the desktop as he
grows.

## Click-through, and why it is OFF by default

A frameless always-on-top window swallows every click inside its rectangle — including the
large fraction of it that is transparent nothing. Put him on the desktop and he becomes an
invisible dead zone over whatever is behind him.

`--click-through` sets `WS_EX_LAYERED | WS_EX_TRANSPARENT`, and then the transparent pixels
pass clicks to the desktop underneath.

It is **off by default**, deliberately, and for the same reason `add_escape_hatch` exists: a
window that cannot be clicked also cannot be closed, moved or focused by mouse. Turning it on
without a keyboard route out would rebuild exactly the trap the first version of this spike
shipped with. So the escape hatch is not optional and is bound before anything else.

It also has a second cost worth stating: `?chat=1` has a paperclip and a text box in it. A
click-through window means neither can be clicked. Click-through is for `?solo=1`, and pairing
it with `?chat=1` produces a chat panel nobody can type into.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow

# His stage colour, from the rig's own `--stage` token. The window paints this so a
# non-transparent view has something to composite onto, and so the edges of the window match
# the page instead of flashing white on open.
STAGE = "#0E1016"

# Carried in argv purely so tools/measure_face.py can find this process and its children by
# pattern. It is never parsed for meaning.
MARKER = "oddball-spike"

# How long to wait before re-trying a page that would not load, and the ceiling it backs off
# to. 1s first because at startup the server is usually only a few seconds behind; 8s after
# that because a server that is not coming is not going to arrive sooner for being asked
# harder.
RETRY_FIRST_S = 1.0
RETRY_MAX_S = 8.0

# Where the chosen size is remembered between runs. In `data/` beside the logs rather than in
# the config, because it is a thing LB adjusts by pressing a key twenty times, not a thing he
# edits — and `config/oddball.toml` is hand-written and full of reasoning that a program
# rewriting the file would destroy.
SCALE_FILE = Path(__file__).resolve().parents[1] / "data" / "face_scale"

# How far he can be scaled, and by how much per keypress. The floor is not arbitrary: below
# about a third he is a smudge rather than a face, and the point of him is legibility at a
# glance. 1.1 per press gives roughly 7 presses to double, which is fine enough to land on a
# size you like and coarse enough to get there quickly.
MIN_SCALE = 0.3
MAX_SCALE = 3.0
SCALE_STEP = 1.1


class Face(QMainWindow):
    """The window, and the WebView filling it."""

    def __init__(self, url: str, fullscreen: bool, decorated: bool,
                 width: int, height: int, transparent: bool, click_through: bool,
                 always_on_top: bool = False, scale: float | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Mr Odd Ball")

        self._url = url
        self._delay = RETRY_FIRST_S
        self._is_fullscreen = fullscreen
        self._always_on_top = always_on_top
        self._raised = always_on_top
        self._ws = None

        # The size he was last left at. `--width`/`--height` stay the BASE that scale
        # multiplies, so the remembered scale means the same thing across a change to either.
        self._base_w, self._base_h = width, height
        # CLAMPED here as well as in `_load_scale`. An explicit `--scale` used to go straight
        # through unchecked, so `--scale 99` asked for a 59400-pixel window — which Qt does not
        # refuse, it simply creates something no display can show and that cannot be closed by
        # mouse. The bounds belong to the value, not to one of the ways of supplying it.
        raw = scale if scale is not None else self._load_scale()
        self._scale = max(MIN_SCALE, min(MAX_SCALE, raw))
        if scale is not None and self._scale != raw:
            print(f"float: --scale {raw:g} is outside {MIN_SCALE}-{MAX_SCALE}, "
                  f"using {self._scale:g}", file=sys.stderr, flush=True)
        self.resize(int(width * self._scale), int(height * self._scale))
        print(f"float: scale {self._scale:.2f}  "
              f"({int(width * self._scale)}x{int(height * self._scale)})",
              file=sys.stderr, flush=True)

        # ws:// on the same host and port the page is served from, derived rather than
        # configured — `hud_bridge` puts the socket and the page on one port precisely so that
        # neither has to be told about the other.
        self._ws_url = url.split("?")[0].replace("http://", "ws://").replace("https://", "wss://")

        # --- window flags: what labwc used to be told ------------------------------------
        flags = Qt.WindowType.Window
        if not decorated:
            # Frameless and out of the taskbar. `Qt.Tool` is the skipTaskbar equivalent;
            # without it he gets a taskbar button, which is not what a face on the desktop
            # should have.
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            if always_on_top:
                # Only under --always-on-top. By default the z-order is managed dynamically in
                # `_set_z()`, and this flag would override every one of those calls — a Qt
                # window flag is a fixed property, and "sometimes on top" is not a property.
                flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        # --- transparency: BOTH halves, or neither works ---------------------------------
        #
        # The window must paint nothing AND the page must paint nothing. A window that paints
        # with a page that does not looks identical to no change at all, which is a miserable
        # thing to debug, so each half's comment names the other. The page half is `?solo=1`,
        # which clears the rig's own background — without that param the page paints its stage
        # colour over this alpha and the window's transparency never shows.
        if transparent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            # Qt still paints a system background for a translucent toplevel unless this is
            # off. Setting only WA_TranslucentBackground leaves a faint sheet behind the page.
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        else:
            self.setStyleSheet(f"QMainWindow {{ background: {STAGE}; }}")

        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)

        page = self.view.page()
        # The page half of the transparency. `Qt.GlobalColor.transparent` rather than a
        # QColor with alpha 0: QtWebEngine special-cases the named transparent colour to
        # actually disable the background layer, where an alpha-0 QColor is still a fill.
        page.setBackgroundColor(QColor(Qt.GlobalColor.transparent) if transparent
                                else QColor(STAGE))

        # A rig is not a browser: nothing here should be able to open a window, run a plugin,
        # or keep local storage. Turning these off is also the honest configuration to
        # measure, because it is the configuration the real application ships.
        s = self.view.settings()
        A = QWebEngineSettings.WebAttribute
        for attribute, value in (
            (A.JavascriptCanOpenWindows, False),
            (A.LocalStorageEnabled, False),
            (A.PluginsEnabled, False),
            # ON, unlike the GTK build, and this is a deliberate difference rather than an
            # oversight. WebKitGTK's `set_enable_media(False)` was a memory saving on a Pi
            # with 8 GB shared with everything else; here it costs nothing, and leaving media
            # available means the rig can gain a sound or a video without this file changing.
            (A.ScreenCaptureEnabled, False),
        ):
            try:
                s.setAttribute(attribute, value)
            except (AttributeError, TypeError):
                # A Qt version that spells one of these differently must not cost him his
                # face. Same judgement as the GTK build's guarded `run-file-chooser` connect.
                print(f"float: cannot set {attribute!r} — continuing", file=sys.stderr)

        self.view.loadFinished.connect(self._on_load_finished)
        self.view.load(QUrl(url))

        self._add_escape_hatch()
        self._add_size_keys()
        if not decorated:
            self._add_frameless_controls()

        if fullscreen:
            self.showFullScreen()

        # LAST, and after the window exists. Applying an extended style needs a real HWND, and
        # `winId()` is what forces Qt to create one — calling this in __init__ before the
        # widget is realised silently does nothing.
        if click_through:
            self._make_click_through()

        # An explicit --scale is remembered, so it becomes the size he comes back at. Without
        # this, `--scale 0.5` shrank him for exactly one run and the next start was full size
        # again, which reads as the setting not working.
        if scale is not None:
            self._save_scale()

        if not always_on_top:
            # Never take focus, and start at the bottom. Both need the HWND, so both are here
            # rather than up with the flags.
            self._set_z(False)
            self._watch_state()

    # -- the escape hatch ------------------------------------------------------------------
    def _add_escape_hatch(self) -> None:
        """Always give the window a way out. This is not a nicety.

        The first version of this spike ran fullscreen AND undecorated: no title bar, no close
        button, and no key bindings. That is a window with no exit — it covered the whole
        display with no way to dismiss it from the machine itself, and the only remedy was
        another box with an SSH session. Never ship a surface that can take the screen without
        also taking a key that gives it back.

        `--click-through` makes this sharper still, not softer: with the mouse passing through,
        these keys are the ONLY way to close him.

        Escape leaves fullscreen, and leaves the window entirely if it is already windowed.
        Ctrl+Q always quits. F11 toggles.

        `WindowShortcut` context deliberately: the WebView fills the window and the rig's own
        handler binds plain letters, so a shortcut scoped to the focused widget would never
        fire. This is the Qt equivalent of the GTK build putting its controller in the CAPTURE
        phase, and it is here for the same reason.
        """
        for key, handler in ((Qt.Key.Key_Escape, self._on_escape),
                             (QKeySequence("Ctrl+Q"), self.close),
                             (Qt.Key.Key_F11, self._toggle_fullscreen)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)

    def _add_frameless_controls(self) -> None:
        """Give a frameless window the two things the title bar was providing.

        A frameless window has no caption to drag and no `WS_THICKFRAME` to pull, so without
        this it cannot be moved or resized at all — which is fine for a character sitting in a
        corner and useless for a chat panel.

        **The hard part is that a `QWebEngineView` is a native child window.** It renders in
        its own process and takes its own mouse events, so a `mousePressEvent` on this window
        never fires over the page — which is most of the surface. Overriding
        `mousePressEvent`/`mouseMoveEvent` on the QMainWindow, the obvious approach, moves the
        window only when you happen to grab a pixel the view does not cover.

        Two things that DO work, and both are needed:

        * **`QSizeGrip`** is a real child widget stacked above the view, so it gets its own
          events regardless. It goes bottom-right, where every resize grip has always been.
        * **Ctrl+drag anywhere**, via `QWindow.startSystemMove()`. That hands the drag to
          Windows itself, which is what makes it feel identical to dragging a title bar —
          including snapping. The Ctrl is what lets a plain click still reach the page, so
          the chat box keeps working; a bare drag would have to be stolen from the view.
        """
        from PyQt6.QtWidgets import QSizeGrip

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(18, 18)
        self._grip.setToolTip("drag to resize")
        # Above the web view in the stacking order, or the view's native window covers it.
        self._grip.raise_()
        self._position_grip()

    def _position_grip(self) -> None:
        grip = getattr(self, "_grip", None)
        if grip is not None:
            grip.move(self.width() - grip.width(), self.height() - grip.height())
            grip.raise_()

    def resizeEvent(self, event):                                      # noqa: N802
        super().resizeEvent(event)
        self._position_grip()

    def mousePressEvent(self, event):                                  # noqa: N802
        """Ctrl+drag moves the window. Anything else falls through to the page."""
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            handle = self.windowHandle()
            if handle is not None:
                # Windows takes the drag from here, so it snaps and feels native. There is no
                # position bookkeeping in this file at all, which is the point of using it.
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def _add_size_keys(self) -> None:
        """Ctrl + and Ctrl - to resize, Ctrl 0 to reset.

        Keys rather than a drag handle, because the window is frameless: there is no edge to
        grab, and giving him one would mean drawing a border over a character whose whole point
        is not having a rectangle around him.

        Both spellings of plus are bound. On most layouts the key is shift-equals, so a
        shortcut bound only to `Ctrl++` never fires and the one bound to `Ctrl+=` is the one
        that actually works — binding both is the difference between "resizing is broken" and
        "resizing works", and costs one line.
        """
        for keys, handler in (
            (("Ctrl++", "Ctrl+="), lambda: self._nudge_scale(SCALE_STEP)),
            (("Ctrl+-", "Ctrl+_"), lambda: self._nudge_scale(1 / SCALE_STEP)),
            (("Ctrl+0",), lambda: self._apply_scale(1.0)),
        ):
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.activated.connect(handler)

    def _load_scale(self) -> float:
        """The size he was left at, or 1.0. Never raises — a missing file is a first run."""
        try:
            value = float(SCALE_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 1.0
        return max(MIN_SCALE, min(MAX_SCALE, value))

    def _on_escape(self) -> None:
        if self._is_fullscreen:
            self.showNormal()
            self._is_fullscreen = False
        else:
            self.close()

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self._is_fullscreen else self.showFullScreen()
        self._is_fullscreen = not self._is_fullscreen

    # ==========================================================================================
    # Z-ORDER: he lives at the BOTTOM, and rises only when spoken to
    # ==========================================================================================
    #
    # The first version pinned him always-on-top, which is right for a HUD and wrong for a
    # character who is present all day: he sat over every window LB was actually working in.
    #
    # So the resting position is the BOTTOM of the z-order — behind every application, above
    # the wallpaper — and he rises to the top only while he is doing something. Two Win32 calls
    # do the whole thing, and neither is a Qt window flag, because a flag is a fixed property
    # and this has to change while he runs. `Qt.WindowStaysOnTopHint` is therefore NOT set;
    # setting it would win over every `SetWindowPos` below.
    #
    # `SWP_NOACTIVATE` on both, and it is not optional: without it, raising him would steal
    # keyboard focus from whatever LB is typing into at the moment he says the wake word, which
    # is the worst possible instant to take a keystroke.

    _HWND_TOP = -1
    _HWND_BOTTOM = 1
    _HWND_TOPMOST = -1
    _HWND_NOTOPMOST = -2
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _SWP_FLAGS = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE

    # The states in which he is DOING something and should be visible. Anything else — most
    # importantly the resting state, whatever `[wake].resting_state` is set to — sends him back
    # down. Deliberately a set of things that RAISE rather than a set that lower: a state this
    # file has never heard of is far more likely to be a new activity than a new kind of idle,
    # and being visible when you should not be is a smaller failure than being invisible when
    # you should not be.
    ACTIVE_STATES = frozenset({
        "startle",      # the wake word landed
        "listening",    # taking the question
        "thinking",     # the router and the agent are working
        "speaking",     # Piper is playing
    })

    def _set_z(self, topmost: bool) -> None:
        """Put the window at the top or the bottom of the z-order. Never raises.

        Guarded throughout, because this is a PRESENCE feature: if it fails he is in the wrong
        layer, which is annoying. If it raised, he would be gone, which is not.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            if topmost:
                # TOPMOST rather than TOP: plain TOP is beaten by anything that IS topmost,
                # and the whole point of rising is to be seen over what is already there.
                user32.SetWindowPos(hwnd, self._HWND_TOPMOST, 0, 0, 0, 0, self._SWP_FLAGS)
            else:
                # NOTOPMOST only — deliberately NOT followed by `HWND_BOTTOM` any more.
                #
                # The first version shoved him to the very bottom of the z-order, which was
                # taken literally from "he should live behind everything". It is too literal:
                # a window pinned to the bottom cannot be brought forward by clicking it,
                # because the click raises it and the next state change slams it back down.
                # It stops behaving like a window.
                #
                # Clearing the topmost bit is enough. He then sits in the ordinary z-order
                # like VS Code or Firefox — he goes behind what you click on, and comes
                # forward when you click him — and `_set_z(True)` still lifts him above
                # everything for the moment he is actually saying something.
                user32.SetWindowPos(hwnd, self._HWND_NOTOPMOST, 0, 0, 0, 0, self._SWP_FLAGS)
        except Exception as exc:                                       # noqa: BLE001
            print(f"float: could not change z-order ({exc})", file=sys.stderr, flush=True)

    # `WS_EX_NOACTIVATE` USED TO BE SET HERE AND IS NOT ANY MORE. (2026-08-26.)
    #
    # It was added to stop him stealing focus when he rises at the wake word, and it did that
    # perfectly — by making the window **unactivatable**, permanently. A window with that
    # style cannot be given keyboard focus by anything, including a deliberate click, so the
    # chat box could be clicked and could not be typed into. The symptom looked like an input
    # problem in the page and was a window-style problem two layers below it.
    #
    # The surgical version was already in place and doing the real work: `SWP_NOACTIVATE` on
    # the `SetWindowPos` calls in `_set_z()`. That says "raise this window WITHOUT activating
    # it", which is exactly the narrow thing wanted — it applies to the programmatic raise and
    # says nothing about what the user may do. `WS_EX_NOACTIVATE` is the same idea applied to
    # every possible activation forever, which is a much larger promise than anybody wanted.
    #
    # The general shape, worth keeping: a per-EVENT flag and a per-WINDOW style are not two
    # spellings of one idea. Reach for the event flag first.

    def _on_state(self, name: str) -> None:
        """A state arrived from the assistant. Raise or lower him to match."""
        active = name in self.ACTIVE_STATES
        if active == self._raised:
            return                                  # already in the right layer
        self._raised = active
        self._set_z(active)
        print(f"float: state {name!r} -> {'front' if active else 'back'}",
              file=sys.stderr, flush=True)

    # ==========================================================================================
    # WATCHING HIS STATE
    # ==========================================================================================

    def _watch_state(self) -> None:
        """Open a WebSocket to the same port the page uses, and follow his state.

        **A second connection to a socket the page is already reading**, and that is deliberate
        rather than wasteful. The alternatives were worse:

          * ask the PAGE, over QWebChannel — couples this window to the rig's internals, and
            the rig is a file that gets edited for reasons that have nothing to do with here
          * poll a state file — needs the engine to write one it does not currently write, and
            trades a 5 ms event for a polling interval
          * parse `data/oddball.log` — no

        `hud_bridge` is explicitly a broadcaster: its own docstring says it "broadcasts to
        whoever is connected and does not care whether anyone is". A second listener is the
        thing it was built for, and it replays `_last_state` to every client on connect, so
        this window learns his state immediately rather than at the next transition.

        `QWebSocket` runs in Qt's event loop, so there is no thread here and no lock.
        """
        from PyQt6.QtWebSockets import QWebSocket

        self._ws = QWebSocket()
        self._ws.textMessageReceived.connect(self._on_ws_message)
        self._ws.connected.connect(
            lambda: print("float: watching his state", file=sys.stderr, flush=True))
        # Reconnect forever, for the same reason `_keep_trying` reloads the page forever: a
        # restart of the assistant mid-session should heal itself rather than need a human.
        self._ws.disconnected.connect(
            lambda: QTimer.singleShot(2000, self._connect_ws))
        self._ws.errorOccurred.connect(
            lambda _e: QTimer.singleShot(2000, self._connect_ws))
        self._connect_ws()

    def _connect_ws(self) -> None:
        """Open the socket if it is not already open or opening.

        Compared against the ENUM, never against 0. PyQt6 wraps Qt enums in Python enum
        classes that do NOT compare equal to bare ints, so `state() == 0` is always False —
        the reconnect guard passed for a socket that was closed, the branch never ran, and the
        socket was never opened at all. Silent, because a state watcher that never connects
        looks exactly like an assistant that never changes state.
        """
        from PyQt6.QtNetwork import QAbstractSocket

        if self._ws is None:
            return
        if self._ws.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._ws.open(QUrl(self._ws_url))

    def _on_ws_message(self, text: str) -> None:
        """One message from the bridge. Only `state` matters here.

        The bridge also carries `mouth`, `gesture` and `chat` messages, all of which are the
        page's business and none of which are this window's. Anything unparseable is ignored:
        a malformed frame must not cost him his layer.
        """
        try:
            msg = json.loads(text)
        except (ValueError, TypeError):
            return
        if isinstance(msg, dict) and msg.get("type") == "state":
            value = msg.get("value")
            if isinstance(value, str):
                self._on_state(value)

    # ==========================================================================================
    # SIZE
    # ==========================================================================================

    def _apply_scale(self, scale: float, save: bool = True) -> None:
        """Resize him about his own centre, and remember it.

        About the CENTRE rather than the top-left, because he is a character sitting in a
        corner of the desktop: growing from the top-left walks him across the screen every time
        the size changes, and after three presses he is somewhere else entirely.
        """
        self._scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        w = int(self._base_w * self._scale)
        h = int(self._base_h * self._scale)

        centre = self.geometry().center()
        self.resize(w, h)
        # move() takes the top-left, so put the old centre back.
        self.move(centre.x() - w // 2, centre.y() - h // 2)

        print(f"float: scale {self._scale:.2f}  ({w}x{h})", file=sys.stderr, flush=True)
        if save:
            self._save_scale()

    def _nudge_scale(self, factor: float) -> None:
        self._apply_scale(self._scale * factor)

    def _save_scale(self) -> None:
        """Persist the size, so it survives a restart. Never raises — it is a convenience."""
        try:
            SCALE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SCALE_FILE.write_text(f"{self._scale:.4f}\n", encoding="utf-8")
        except OSError:
            pass

    # -- click-through ----------------------------------------------------------------------
    def _make_click_through(self) -> None:
        """Let the mouse pass through to whatever is behind him.

        `WS_EX_TRANSPARENT` is the flag that does it; `WS_EX_LAYERED` is required alongside it
        for the hit-testing to actually be skipped rather than merely reported.

        Guarded, because this whole method is a CONVENIENCE and a convenience must never cost
        him his face. If the call fails, he is still on screen — he just eats clicks in his own
        rectangle, which is the behaviour without the flag at all.
        """
        if sys.platform != "win32":
            print("float: --click-through is Windows-only; ignoring", file=sys.stderr)
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020

            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            # SetWindowLongPtrW on 64-bit, SetWindowLongW on 32-bit. The names differ and
            # calling the wrong one truncates the style word.
            get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            put(hwnd, GWL_EXSTYLE, get(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            print("float: click-through on — the keyboard is now the only way to close him",
                  file=sys.stderr, flush=True)
        except Exception as exc:                                       # noqa: BLE001
            print(f"float: could not set click-through ({exc}) — he will still eat clicks "
                  f"in his own rectangle", file=sys.stderr, flush=True)

    # -- the retry loop ---------------------------------------------------------------------
    def _on_load_finished(self, ok: bool) -> None:
        """Reload until the page actually loads, instead of showing an error page.

        ## The bug this fixes, seen on a real reboot 2026-08-22

            Could not connect to 127.0.0.1: Connection refused

        The assistant reported ready, this window started, and port 8765 was not bound until
        several seconds after that — it binds only after faster-whisper and an onnxruntime
        wake model have loaded.

        `config/oddball-face.desktop` argued that ordering did not matter because *"the rig
        retries its WebSocket every 2s forever"*. That is true and it is not enough: **the page
        itself is fetched over HTTP from the same port.** When that GET fails there is no page,
        so there is no JavaScript, so nothing retries anything — the window sits on an error
        string until somebody notices. The retry that was relied on lived inside the thing that
        failed to arrive.

        **This survived the port to Windows unchanged, and it had to.** `shell:startup` gives
        even less ordering than systemd did — there is no `After=`, no `ExecStartPre`, and no
        way to say "not before the server". Waiting here is now the only mechanism there is.

        Retrying forever, deliberately, and for the same reason the rig's own socket does: this
        is a face that is supposed to be present, and a server restart mid-session should heal
        itself rather than need a human. Each attempt is logged, so a genuinely wrong URL is
        visible in the log rather than silent.
        """
        if ok:
            if self._delay != RETRY_FIRST_S:
                print("float: page loaded", file=sys.stderr, flush=True)
            self._delay = RETRY_FIRST_S          # a LATER failure starts its backoff over
            return

        print(f"float: {self._url} did not load — retrying in {self._delay:.0f}s",
              file=sys.stderr, flush=True)
        QTimer.singleShot(int(self._delay * 1000), lambda: self.view.load(QUrl(self._url)))
        self._delay = min(self._delay * 1.6, RETRY_MAX_S)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8765/",
                    help="where the rig is served (default: the live orchestrator's port)")
    ap.add_argument("--fullscreen", action="store_true",
                    help="take the whole display. Escape leaves fullscreen, Ctrl+Q quits, "
                         "F11 toggles")
    ap.add_argument("--undecorated", action="store_true",
                    help="drop the title bar and stay out of the taskbar. Decorations are "
                         "ON by default so a windowed run always has a visible close button. "
                         "Z-order is separate — see --always-on-top")
    ap.add_argument("--transparent", action="store_true",
                    help="paint no backdrop, so the desktop shows through and only the "
                         "character is drawn. Pair it with ?solo=1 on the URL — the page "
                         "must clear its own background too, or it paints over this")
    ap.add_argument("--click-through", action="store_true",
                    help="let the mouse pass through to the desktop behind him. OFF by "
                         "default: it also means he cannot be closed with the mouse, and it "
                         "makes ?chat=1 untypeable. Use it with ?solo=1")
    ap.add_argument("--dim", type=float, default=None, metavar="A",
                    help="how opaque the chat panel's own background is, 0.15-1.0 "
                         "(default 0.30). Only affects ?chat=1 — the panel holds code and "
                         "tables, so it cannot go fully transparent and stay readable")
    ap.add_argument("--always-on-top", action="store_true",
                    help="pin him above every window, the old behaviour. OFF by default: he "
                         "now sits BEHIND everything and rises only while he is listening, "
                         "thinking or speaking")
    ap.add_argument("--scale", type=float, default=None, metavar="X",
                    help=f"size multiplier on --width/--height ({MIN_SCALE}-{MAX_SCALE}). "
                         f"Omit to use the size he was last left at. Ctrl+ and Ctrl- adjust "
                         f"him while running, and Ctrl+0 resets")
    ap.add_argument("--width", type=int, default=800,
                    help="the BASE width, which --scale multiplies")
    ap.add_argument("--height", type=int, default=800,
                    help="the BASE height, which --scale multiplies")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="quit after this long; 0 means run until closed")
    ap.add_argument("--marker", default=MARKER, help=argparse.SUPPRESS)
    args = ap.parse_args(argv[1:])

    # `--dim` becomes a query parameter, appended HERE rather than written into the URL by
    # whatever launched us.
    #
    # That is not a convenience, it is a workaround for a real failure. The URL already
    # carries `?chat=1`, so a second parameter needs an `&` — and in a Windows batch file `&`
    # separates commands. Quoting the URL is not enough when the line is already inside
    # `cmd /c "..."`: the outer parse consumes the inner quotes, the `&` is left bare, and cmd
    # runs `dim=0.3` as a program. The face then starts with no dim, or does not start at all.
    #
    # Building the URL in Python removes the shell from the problem entirely. `start_oddball.bat`
    # passes `--dim 0.3`, which contains nothing any parser argues about.
    url = args.url
    if args.dim is not None:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}dim={args.dim:g}"

    if args.click_through and "solo=1" not in url:
        # A warning rather than a refusal: it is LB's window and there may be a reason. But
        # silently shipping a chat panel that cannot be typed into is worse than a line of
        # stderr.
        print("float: --click-through with a URL that is not ?solo=1 — the chat panel will "
              "not be clickable or typeable", file=sys.stderr, flush=True)

    app = QApplication([argv[0]])
    app.setApplicationName("Mr Odd Ball")

    window = Face(url, args.fullscreen, not args.undecorated,
                  args.width, args.height, args.transparent, args.click_through,
                  always_on_top=args.always_on_top, scale=args.scale)
    window.show()

    # AFTER show(). `show()` raises the window as a side effect — every toolkit does — so
    # sending him to the bottom before it puts him straight back on top, which looks exactly
    # like the feature not working. Once more, one event loop turn later, for the same reason:
    # Qt finishes composing the window after show() returns, and on some drivers that final
    # step re-raises it too.
    if not args.always_on_top:
        window._set_z(False)
        QTimer.singleShot(0, lambda: window._set_z(False))

    if args.seconds > 0:
        QTimer.singleShot(int(args.seconds * 1000), app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
