#!/usr/bin/env python3
"""
Module:  avatar_state.py
Purpose: One place the desktop avatar's state comes from, whichever thread produced it.
Author:  LB
Date:    2026-08-21

## Why this is a separate file from ui/server.py

`orchestrator/hud_bridge.py` mirrors every `set_state()` into here, so that the small overlay
and the full character rig cannot disagree about what he is doing. If the fan-out lived in
`ui/server.py`, that mirror would drag **FastAPI and uvicorn into the import path of the voice
loop** — and then a box without them installed would fail to start the assistant at all,
rather than merely lacking a floating ball.

So this module is **stdlib only**. `ui/server.py` imports it; it imports nothing back.

## Threads

The engine sets state from the audio thread, the speech thread and the event loop. FastAPI's
websocket handlers live on uvicorn's loop, which is a different loop in a different thread
again. `publish()` is therefore callable from anywhere and does the hop itself, exactly the
way `HudBridge.broadcast_threadsafe` does — `call_soon_threadsafe` onto the bound loop, and a
quiet no-op when nothing is bound yet, because the assistant must run with no UI attached.
"""

from __future__ import annotations

import asyncio
import logging
import threading

LOG = logging.getLogger("oddball.avatar")

__all__ = ["bind_loop", "publish", "last_state", "subscribe", "unsubscribe", "subscriber_count"]

# Where a newly opened window is told he is. Matches `hud.resting_state`'s default in
# config/oddball.toml — a window that connects before the first broadcast must not be told he
# is awake, which is the bug HudBridge._last_state exists to record.
_DEFAULT_STATE = "sleeping"

_lock = threading.Lock()
_subscribers: "set[asyncio.Queue[str]]" = set()
_loop: asyncio.AbstractEventLoop | None = None
_last: str = _DEFAULT_STATE

# One state per client in flight. If a window is so wedged it cannot drain a single string,
# the newest state is the only one worth keeping anyway.
_QUEUE_DEPTH = 8


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Tell the module which event loop the websocket handlers live on.

    Called by `ui/server.py` on startup. Until it is called, `publish()` records the state and
    delivers nothing, which is the correct behaviour with no UI running.
    """
    global _loop
    _loop = loop


def last_state() -> str:
    """The most recent state, replayed to each window as it connects."""
    return _last


def subscribe() -> "asyncio.Queue[str]":
    """A queue that will receive every subsequent state. Call from the server's loop."""
    q: "asyncio.Queue[str]" = asyncio.Queue(maxsize=_QUEUE_DEPTH)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "asyncio.Queue[str]") -> None:
    """Stop delivering to `q`. Safe to call twice."""
    with _lock:
        _subscribers.discard(q)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def _fanout(state: str) -> None:
    """Deliver to every subscriber. Server-loop thread only."""
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(state)
        except asyncio.QueueFull:
            # Drop the OLDEST, keep the newest: a state is a snapshot, and the stale one is
            # never the one worth showing. Same call the HUD bridge makes for its inbound
            # queue, for the same reason.
            try:
                q.get_nowait()
                q.put_nowait(state)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


def publish(state: str) -> None:
    """Set the avatar's state. **Callable from any thread.** Never raises.

    Args:
        state: a state name — "sleeping", "idle", "listening", "thinking", "speaking".
               Unknown names are passed through; `ui/avatar.html` draws anything it has no
               animation for as a still ball rather than erroring.
    """
    global _last
    if not state:
        return
    _last = state

    loop = _loop
    if loop is None:
        return                     # no UI server running; the state is still recorded above
    try:
        loop.call_soon_threadsafe(_fanout, state)
    except RuntimeError:
        # The server's loop closed out from under us. Not worth a warning on every state
        # change after the UI is shut down.
        LOG.debug("avatar loop is gone — dropping state %r", state)
