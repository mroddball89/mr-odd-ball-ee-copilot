#!/usr/bin/env python3
"""
Module:  hud_bridge.py
Purpose: Push state and gesture messages to the character rig over a WebSocket.
Author:  LB
Date:    2026-08-10

The rig (hud/face-preview.html) is a standalone file that happens to listen. This bridge
broadcasts to whoever is connected and does not care whether anyone is; the assistant must
work with no screen attached.

Wire format — the first three drive his face, the rest drive the chat panel:

    {"type": "state",      "value": "listening"}          -> setState()
    {"type": "gesture",    "value": "startle"}            -> playGesture()
    {"type": "mouth",      "value": 0.42}                 -> live lip-sync while speaking

    {"type": "transcript", "role": "you", "value": "..."} -> a line in the chat column
    {"type": "card",       "value": {kind,title,body,lang}} -> code, table, log, prose
    {"type": "route",      "value": "hardware"}           -> which agent answered
    {"type": "mode",       "value": "quiz"}               -> the QUIZ MODE chip
    {"type": "pending",    "value": {kind,spoken,shown}}  -> Approve / Deny buttons; null clears

The rig validates state and gesture against its own tables and ignores anything it does not
recognise, so an unknown name here is inert rather than breaking the animation loop.

**And it talks back**, which it did not before 2026-08-19:

    {"type": "text",    "value": "how wide for 3 amps"}   -> a typed question
    {"type": "approve", "value": true}                    -> a click on a pending action

Both land on `self.inbound` for the turn loop to drain. A typed question and a spoken one
reach the same `Engine.ask()`; an Approve click and a spoken "yes" are the same event. Two
ways in, one decision path — which is the point of splitting the terminal out of main.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from collections import deque
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

# How many chat messages a reconnecting panel gets replayed. Enough to see the exchange you
# were in the middle of; not a log.
HISTORY = 40

# Message types that belong to the chat panel and so are worth replaying to a late joiner.
# `state`, `mouth` and `gesture` are not here: they are momentary, and replaying a mouth
# position from four minutes ago would leave his face stuck mid-syllable.
_REPLAYABLE = {"transcript", "card", "route", "mode"}


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
        # What the rig sends back: {"type":"text","value":"..."} for a typed question, and
        # {"type":"approve","value":true|false} for a click on a pending action. Drained by
        # the turn loop. Bounded, because an unbounded queue fed by a browser is a memory leak
        # with a UI attached.
        self.inbound: "queue.Queue[dict]" = queue.Queue(maxsize=64)
        # The last N chat messages, replayed to a client that connects mid-session. Capped:
        # this is a convenience for a reopened panel, not the conversation log — that lives on
        # the SD card in tools/memory_manager.py, and having two of them is how they disagree.
        self._history: "deque[dict]" = deque(maxlen=HISTORY)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def _handle(self, ws) -> None:
        self._clients.add(ws)
        LOG.info("rig connected (%d attached)", len(self._clients))
        try:
            await ws.send(json.dumps(self._last_state))
            # A late-joining panel gets the transcript so far, so opening the HUD mid-session
            # shows the conversation rather than an empty column that fills only from the next
            # question onward. Same reasoning as `_last_state`, applied to the chat.
            for msg in list(self._history):
                await ws.send(json.dumps(msg))

            # The rig used to be write-only — `async for _ in ws: pass`. It talks back now:
            # a typed question, or an Approve/Deny click on a pending action. Both arrive here
            # and go onto `self.inbound`, which the turn loop drains. This is
            # `broadcast_threadsafe` in reverse, and it is what makes the typed channel and
            # the spoken channel two ways into the same Engine rather than two code paths.
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    LOG.warning("rig sent something that is not JSON: %r", str(raw)[:80])
                    continue
                if not isinstance(msg, dict) or "type" not in msg:
                    continue
                try:
                    self.inbound.put_nowait(msg)
                except queue.Full:
                    # Dropping the OLDEST is right here: these are user actions, and the most
                    # recent one is the one still being waited on. A full queue means the turn
                    # loop is wedged, which is a bug to see rather than to buffer through.
                    LOG.warning("inbound queue full — dropping the oldest message")
                    try:
                        self.inbound.get_nowait()
                        self.inbound.put_nowait(msg)
                    except (queue.Empty, queue.Full):
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
        kind = msg.get("type")
        if kind == "state":
            self._last_state = msg
        elif kind in _REPLAYABLE:
            self._history.append(msg)
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

    # --- the chat channel ----------------------------------------------------------------
    #
    # Added 2026-08-19 for the merged copilot. The rig was a face; it is now a face with a
    # transcript and a card column beside it, because a firmware answer contains a C snippet
    # and a register table, and neither of those can be said out loud (engine/split.py).

    def say_line(self, role: str, text: str) -> None:
        """One line of the transcript. `role` is "you" or "oddball"."""
        if text and text.strip():
            self.broadcast_threadsafe({"type": "transcript", "role": role, "value": text})

    def show_card(self, card) -> None:
        """One card — code, a table, a log, prose. Accepts an engine.response.Card or a dict.

        Takes either because the turn loop has Card objects and the harnesses find plain dicts
        easier to assert on, and there is no reason for the bridge to care which.
        """
        value = card.to_dict() if hasattr(card, "to_dict") else dict(card)
        self.broadcast_threadsafe({"type": "card", "value": value})

    def set_route(self, route: str) -> None:
        """Which agent answered. The panel shows it as a chip, so a misroute is visible."""
        self.broadcast_threadsafe({"type": "route", "value": route})

    def set_mode(self, mode: str) -> None:
        """"normal" or "quiz". A mode you cannot see is a mode you get stuck in (D5)."""
        self.broadcast_threadsafe({"type": "mode", "value": mode})

    def ask_approval(self, pending) -> None:
        """Put a pending action up with Approve and Deny buttons.

        The buttons and a spoken "yes" are the same event: both end up as
        `{"type":"approve","value":bool}` on `inbound`, and the turn loop feeds either into
        the same `Engine.ask()`. One decision path, two ways of reaching it.
        """
        self.broadcast_threadsafe({
            "type": "pending",
            "value": {"kind": pending.kind, "spoken": pending.spoken, "shown": pending.shown},
        })

    def clear_pending(self) -> None:
        """Take the buttons away once the action is resolved, however it was resolved."""
        self.broadcast_threadsafe({"type": "pending", "value": None})

    def drain_inbound(self) -> list[dict]:
        """Everything the rig has sent since the last call. Never blocks."""
        out: list[dict] = []
        while True:
            try:
                out.append(self.inbound.get_nowait())
            except queue.Empty:
                return out
