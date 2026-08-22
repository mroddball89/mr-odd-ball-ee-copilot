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

## What has to be true for the transparency to work

`transparent=True` needs a compositing window manager. Pi OS Bookworm's Wayfire (the Wayland
session) composites; the X11 session with no compositor running does not, and there the
window falls back to an opaque background rather than failing. That is a cosmetic difference,
not a broken UI, so this file does not try to detect it.

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


def start_ui():
    """
    Launches the Mr Odd Ball animated avatar in a lightweight, frameless,
    transparent native window, connecting to the local FastAPI server.
    """
    # Imported here, not at module scope, so the two checks in `__main__` still run on a box
    # without pywebview. At the top it produced a bare ModuleNotFoundError traceback and the
    # "is anything even serving /ui" check never got to fire — the least useful of the three
    # possible messages, printed instead of the most useful.
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
