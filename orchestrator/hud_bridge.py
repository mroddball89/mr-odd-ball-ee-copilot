#!/usr/bin/env python3
"""
Module:  hud_bridge.py
Purpose: Push state and gesture messages to the character rig over a WebSocket.
Author:  LB
Date:    2026-08-10

The rig (hud/face-preview.html) is a standalone file that happens to listen. This bridge
broadcasts to whoever is connected and does not care whether anyone is; the assistant must
work with no screen attached.

Wire format is three message types, matching the rig's mechanisms exactly:

    {"type": "state",   "value": "listening"}   -> setState()
    {"type": "gesture", "value": "startle"}     -> playGesture()
    {"type": "mouth",   "value": 0.42}          -> live lip-sync while speaking

The rig validates the first two against its own tables and ignores anything it does not
recognise, so an unknown name here is inert rather than breaking the animation loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

LOG = logging.getLogger("oddball.hud")

HUD_DIR = Path(__file__).resolve().parents[1] / "hud"

# Only these are ever served. A directory of two files does not need a general static server,
# and not writing one is the simplest way not to write a path-traversal bug.
SERVED = {
    "/": ("face-preview.html", "text/html; charset=utf-8"),
    "/face-preview.html": ("face-preview.html", "text/html; charset=utf-8"),
}


class HudBridge:
    """Broadcasts rig messages to every connected HUD client."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765,
                 resting_state: str = "sleeping") -> None:
        self._host = host
        self._port = port
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Replayed to each new client so a rig opened mid-session shows the truth rather
        # than sitting on its default until the next event happens to fire.
        #
        # Defaults to `sleeping`, and takes the caller's resting state so it cannot disagree
        # with `wake.resting_state`. It was hardcoded to `idle`, which meant a rig connecting
        # before the orchestrator's first broadcast was told he was awake — so he woke up on
        # connect and then had nothing left to do when the wake word actually fired.
        self._last_state = {"type": "state", "value": resting_state}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def _handle(self, ws) -> None:
        self._clients.add(ws)
        LOG.info("rig connected (%d attached)", len(self._clients))
        try:
            await ws.send(json.dumps(self._last_state))
            async for _ in ws:      # the rig never sends; this just parks until it leaves
                pass
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            LOG.info("rig disconnected (%d attached)", len(self._clients))

    def _serve_page(self, connection, request) -> Response | None:
        """Answer ordinary HTTP on the same port; return None to let a WebSocket through.

        Serving the rig here rather than opening it as a file solves three separate problems
        that each cost a debugging round:

        - **`file://` pages cannot open this socket.** Chromium loads the rig happily and the
          WebSocket silently never connects, so he sits in whatever state the page booted in
          and looks like he is ignoring you.
        - **The path is different on every machine** — `C:/Users/ironi/oddball/hud/...` on
          Windows, `/home/ironi/oddball/hud/...` on the Pi. Over HTTP there is one URL.
        - **`?ws=host:port` becomes unnecessary.** The rig already derives its socket from
          `location.host` when it was loaded over http, and because this is the *same port*,
          that derivation is right by construction instead of by being typed correctly.

        The rig remains a standalone file that works with no server at all — this is an
        additional way to reach it, not a replacement.
        """
        served = SERVED.get(request.path.split("?", 1)[0])
        if served is None:
            return None if request.path == "/ws" else connection.respond(
                HTTPStatus.NOT_FOUND, "no such page\n")
        # A WebSocket handshake also arrives as a GET; let those fall through to the real
        # handler rather than answering them with HTML.
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        name, content_type = served
        try:
            body = (HUD_DIR / name).read_bytes()
        except OSError as exc:
            LOG.error("cannot read %s: %s", name, exc)
            return connection.respond(HTTPStatus.INTERNAL_SERVER_ERROR, "rig missing\n")
        return Response(
            HTTPStatus.OK.value, "OK",
            Headers({"Content-Type": content_type, "Content-Length": str(len(body))}),
            body,
        )

    async def start(self):
        """Start serving. Returns the server; use as `async with await bridge.start()`."""
        self._loop = asyncio.get_running_loop()
        server = await serve(self._handle, self._host, self._port,
                             process_request=self._serve_page)
        shown = "localhost" if self._host in ("0.0.0.0", "") else self._host
        LOG.info("hud bridge listening on ws://%s:%d", self._host, self._port)
        LOG.info("open his face at  http://%s:%d/", shown, self._port)
        return server

    async def broadcast(self, msg: dict) -> None:
        """Send one message to every connected client, dropping any that have gone away."""
        if msg.get("type") == "state":
            self._last_state = msg
        data = json.dumps(msg)
        for ws in list(self._clients):
            try:
                await ws.send(data)
            except Exception:
                self._clients.discard(ws)

    def broadcast_threadsafe(self, msg: dict) -> None:
        """Same, callable from the audio thread.

        The microphone runs in its own thread because sounddevice's read() blocks; this is
        the one seam where that thread touches the event loop.
        """
        if self._loop is None:
            LOG.debug("dropping %s — bridge not started", msg)
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(msg), self._loop)

    # convenience wrappers, so callers never hand-build the envelope
    def set_state(self, name: str) -> None:
        self.broadcast_threadsafe({"type": "state", "value": name})

    def play_gesture(self, name: str) -> None:
        self.broadcast_threadsafe({"type": "gesture", "value": name})

    def set_mouth(self, value: float) -> None:
        """Drive his mouth from live audio. `value` is 0..1; anything outside is clamped.

        Called ~50 times a second from the speech thread while he talks (~1.5 KB/s), so it
        stays a plain broadcast with no buffering.

        Clamped here *and* in the rig. The rig's clamp is the one that matters — it cannot
        trust the network, and an out-of-range value would push his mouth through the
        silhouette, reopening a geometry bug tools/verify-rig.mjs already caught once. This
        clamp exists so a bug on this side never even puts a bad number on the wire.

        Deliberately not stored as the last state: a rig connecting mid-sentence must be
        replayed a *state*, not a mouth position, or it would sit with his jaw hanging open
        and no idea what he is doing.
        """
        self.broadcast_threadsafe({"type": "mouth", "value": max(0.0, min(1.0, float(value)))})
