#!/usr/bin/env python3
"""
Module:  server.py
Purpose: FastAPI app behind the desktop avatar — GET /ui, and the /ws/state broadcast.
Author:  LB
Date:    2026-08-21

    python -m ui.server                    # serve on 127.0.0.1:8000
    python -m ui.server --demo             # cycle the states, so the window can be watched
    python launch_ui.py                    # the frameless window that points at it

## Routes

    GET  /            -> 302 to /ui
    GET  /ui          -> ui/avatar.html
    GET  /healthz     -> {"ok": true, "state": ..., "clients": N}
    WS   /ws/state    -> the current state on connect, then every change, as a bare string

The socket sends bare strings rather than JSON on purpose: `ui/avatar.html` does
`ball.className = event.data`, and wrapping one word in an envelope only to unwrap it in four
lines of browser JavaScript is a moving part bought with nothing. The main rig on port 8765
speaks JSON because it carries cards, transcripts and mouth positions; this carries one word.

## Two servers, one state, and why that is not a fork

`orchestrator/hud_bridge.py` already serves the full character rig over websockets on 8765.
This is a **second** HTTP surface on 8000, and the reason it exists rather than being another
route on the bridge is the directive's own constraint: the overlay is a FastAPI app, and the
bridge is a raw `websockets` server whose `process_request` hook is deliberately a two-entry
table rather than a general static server.

What keeps them from disagreeing is that neither one owns the state. `HudBridge.set_state()`
mirrors into `ui/avatar_state.py`, and this app reads from there. There is exactly one writer,
in the engine, exactly as before.

If FastAPI is not installed, importing this module raises — and nothing on the assistant's
critical path imports it. `hud_bridge` imports `ui.avatar_state`, which is stdlib only.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse

from ui import avatar_state

LOG = logging.getLogger("oddball.ui")

UI_DIR = Path(__file__).resolve().parent
AVATAR_HTML = UI_DIR / "avatar.html"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

app = FastAPI(title="Mr Odd Ball — desktop avatar", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _bind() -> None:
    """Hand the running loop to the broadcaster, so the engine's threads can reach it."""
    avatar_state.bind_loop(asyncio.get_running_loop())
    LOG.info("avatar UI at http://%s:%d/ui", DEFAULT_HOST, DEFAULT_PORT)


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@app.get("/ui")
async def ui() -> FileResponse:
    """The avatar page itself.

    `no-store`, because the window is frameless and has no reload button — a cached copy of a
    page LB has just edited is a page he cannot get rid of without killing the process.
    """
    return FileResponse(AVATAR_HTML, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get("/healthz")
async def healthz() -> dict:
    """Is the UI server up, what state is it holding, and is anything watching."""
    return {"ok": True, "state": avatar_state.last_state(),
            "clients": avatar_state.subscriber_count()}


async def _until_disconnect(websocket: WebSocket) -> None:
    """Resolve when the client goes away.

    The page never sends anything, so this only ever ends in a disconnect — which is the
    point. Without a task watching the receive side, a closed window is not noticed until the
    next `send_text`, so a subscriber queue outlives its window and `/healthz` reports clients
    that are not there. Opening and closing the overlay while he rests would then count up.
    """
    try:
        while True:
            await websocket.receive_text()
    except Exception:                                                     # noqa: BLE001
        return


@app.websocket("/ws/state")
async def ws_state(websocket: WebSocket) -> None:
    """Push "idle", "thinking", "speaking" — and whatever else the engine broadcasts.

    The current state goes out immediately on connect, before any change arrives. Without
    that, a window opened mid-session sits on its default until he next does something, which
    for a resting assistant can be hours.
    """
    await websocket.accept()
    queue = avatar_state.subscribe()
    closed = asyncio.create_task(_until_disconnect(websocket))
    try:
        await websocket.send_text(avatar_state.last_state())
        while True:
            nxt = asyncio.create_task(queue.get())
            done, _pending = await asyncio.wait(
                {nxt, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                nxt.cancel()
                break
            await websocket.send_text(nxt.result())
    except WebSocketDisconnect:
        pass
    except Exception:                                                     # noqa: BLE001
        LOG.debug("avatar socket closed", exc_info=True)
    finally:
        closed.cancel()
        avatar_state.unsubscribe(queue)


def serve_in_thread(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> threading.Thread:
    """Run the UI server in a daemon thread and return it.

    This is how `engine/run_voice.py --avatar` starts it: **in the assistant's own process**,
    which is the only arrangement where the overlay sees live state. A separately launched
    server would serve the page and then show a ball that never moves, because nothing in that
    process ever calls `publish()`.

    Daemon, so ctrl-C on the voice loop takes the UI with it rather than leaving a port held.
    """
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="avatar-ui", daemon=True)
    thread.start()
    LOG.info("avatar UI serving on http://%s:%d/ui", host, port)
    return thread


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="serve the Mr Odd Ball desktop avatar")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--demo", action="store_true",
                    help="cycle idle -> thinking -> speaking every 2s, so the window can be "
                         "watched with no assistant running. Nothing else publishes state in "
                         "this process, so without it the ball is correctly motionless.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    if args.demo:
        import itertools
        import time

        def _cycle() -> None:
            for state in itertools.cycle(("idle", "thinking", "speaking")):
                time.sleep(2.0)
                avatar_state.publish(state)

        threading.Thread(target=_cycle, name="avatar-demo", daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
