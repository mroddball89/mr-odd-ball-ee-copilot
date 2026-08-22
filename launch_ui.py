#!/usr/bin/env python3
"""
Module:  launch_ui.py
Purpose: Open Mr Odd Ball's floating avatar — a native window, not a browser.
Author:  LB
Date:    2026-08-21

    python main.py --avatar &      # the assistant, serving the UI on 127.0.0.1:8000
    python launch_ui.py            # the window that shows it

## Why not Chromium

A Chromium window for a 120px ball costs roughly 250MB of RAM and a core of a Pi 5 that is
already running faster-whisper, piper and onnxruntime. pywebview wraps the **system WebKit
view** — WebKitGTK on Pi OS, already resident because the desktop uses it — so the window is
one process holding one page and no renderer farm.

## Two environment variables, and without them the window is broken (D16)

Both were found by screenshotting the Pi, not by reading anything. See `_prepare_env`.

    WEBKIT_DISABLE_DMABUF_RENDERER=1   or the page never paints — the window shows torn
                                       buffer garbage: white, black dashes, fragments of
                                       other windows. The DOM is perfectly fine.
    GDK_BACKEND=x11                    or `frameless=True` is silently ignored and labwc
                                       puts a title bar with a close button on it.

## What has to be true for the transparency to work

`transparent=True` needs a compositing window manager. The Pi's labwc session composites, and
XWayland windows are composited too, so the two settings above do not cost the transparency —
verified on the box. An X11 session with no compositor at all would give an opaque background
rather than failing, which is cosmetic, so this file does not try to detect it.

Two apt packages are needed and pip will not tell you they are missing — `import webview`
succeeds and `webview.start()` then fails looking for a GUI toolkit:

    sudo apt install python3-gi gir1.2-webkit2-4.1 python3-gi-cairo

## The server must be the assistant's own process

The window points at whatever is serving `/ui`. That server has to be running **inside** the
assistant (`main.py --avatar`, or `python -m ui.server` for a page with nothing driving it),
because state is published in-process — see `ui/avatar_state.py`. A separately started server
serves the page correctly and then shows a ball that never moves.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

UI_URL = "http://127.0.0.1:8000/ui"
HEALTH_URL = "http://127.0.0.1:8000/healthz"


def _server_is_up(timeout: float = 1.0) -> bool:
    """True if something is answering on 8000.

    Checked before opening the window because the failure otherwise is silent and confusing:
    pywebview opens a frameless, transparent, 300x300 window onto a connection error, which
    on a compositing desktop is an invisible rectangle. Better to say so on the terminal.
    """
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _prepare_env() -> None:
    """Set the two variables WebKitGTK needs here, before `import webview`.

    Both were diagnosed by screenshotting the Pi and looking, after the window came up
    apparently empty. Neither produces any error message at all when it is missing, which is
    why they are set in code rather than written down in a README somewhere.

    **`WEBKIT_DISABLE_DMABUF_RENDERER=1`** — without it the WebKit surface never paints the
    page. What appears is torn buffer content: white, rows of black dashes, and horizontal
    fragments of whatever else is on screen. The page itself is *fine* — a JS probe inside the
    running window reports `#ball` present, 120x120, at (90,90), visible, gradient applied. The
    pixels simply never arrive. Disabling the DMA-BUF path makes it render perfectly.

    **`GDK_BACKEND=x11`** — without it `frameless=True` does nothing and labwc draws a title
    bar with minimise/maximise/close on a 300px ball. pywebview *does* call
    `set_decorated(False)` (`webview/platforms/gtk.py:229`), but GTK3's Wayland backend never
    negotiates xdg-decoration, so the compositor never hears about it and applies its
    server-side default. Under XWayland the same call goes through X11 hints, which labwc
    honours. Transparency and always-on-top both survive the move — verified on the box.

    Only set when `DISPLAY` exists: forcing x11 with no XWayland running would take a window
    that opens with an unwanted title bar and turn it into one that does not open at all.

    `setdefault`, so an explicit value in the environment always wins.
    """
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    if os.environ.get("DISPLAY"):
        os.environ.setdefault("GDK_BACKEND", "x11")


def start_ui():
    """
    Launches the Mr Odd Ball animated avatar in a lightweight, frameless,
    transparent native window, connecting to the local FastAPI server.
    """
    _prepare_env()

    # Imported here, not at module scope, so the two checks in `__main__` still run on a box
    # without pywebview — AND so `_prepare_env` runs first, because WebKit reads those
    # variables at import time, not at `webview.start()`.
    import webview

    window = webview.create_window(
        title='Mr. Odd Ball',
        url=UI_URL,
        frameless=True,
        transparent=True,
        width=300,
        height=300,
        on_top=True
    )
    webview.start()
    return window


if __name__ == '__main__':
    if not _server_is_up():
        print(f"Nothing is serving {UI_URL}.", file=sys.stderr)
        print("Start the assistant with the UI attached:", file=sys.stderr)
        print("    python main.py --avatar", file=sys.stderr)
        print("or serve the page on its own (the ball will not move):", file=sys.stderr)
        print("    python -m ui.server --demo", file=sys.stderr)
        raise SystemExit(1)
    try:
        start_ui()
    except ImportError:
        print("pywebview is not installed, so there is no window to open.", file=sys.stderr)
        print("    pip install pywebview", file=sys.stderr)
        print("    sudo apt install python3-gi gir1.2-webkit2-4.1 python3-gi-cairo",
              file=sys.stderr)
        print(f"\nThe server IS up — any browser can reach {UI_URL} in the meantime.",
              file=sys.stderr)
        raise SystemExit(1)
