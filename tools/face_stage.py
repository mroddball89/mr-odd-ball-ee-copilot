#!/usr/bin/env python3
"""
Module:  face_stage.py
Purpose: Serve the rig on its own port, pinned to one state, so it can be measured or looked
         at without the orchestrator running.
Author:  LB
Date:    2026-08-13

`HudBridge` already serves the page over HTTP and broadcasts state on the same port, and it
replays its last state to every client that connects. That is exactly what a measurement rig
needs, so this adds nothing to it — it starts one, pins a state, and waits.

Why a pinned state matters: his states have genuinely different costs. `idle` blinks, breathes
and drifts his gaze; `speaking` drives the jaw at 30fps; `sleeping` animates three Zs and
almost nothing else. Comparing a browser rendering `speaking` against a native window
rendering `idle` would measure the states, not the renderers.

A different port from the live orchestrator (8765) on purpose, so this can run while the real
thing is up without either of them failing to bind.

    python tools/face_stage.py --state idle --port 8766
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.hud_bridge import HudBridge  # noqa: E402

LOG = logging.getLogger("oddball.stage")


async def run(state: str, host: str, port: int, seconds: float) -> None:
    bridge = HudBridge(host=host, port=port)
    async with await bridge.start():
        # Straight to the coroutine rather than the threadsafe wrapper: we are already on the
        # loop here, and set_state()'s run_coroutine_threadsafe would deadlock on it.
        await bridge.broadcast({"type": "state", "value": state})
        LOG.info("stage pinned to %r at http://%s:%d/", state, host, port)
        if seconds > 0:
            await asyncio.sleep(seconds)
        else:
            await asyncio.Event().wait()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="sleeping",
                    help="the state to pin (sleeping, idle, speaking, local, ...); defaults "
                         "to how he actually boots, so a stage with no argument is honest")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 means run until killed")
    args = ap.parse_args(argv[1:])

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        asyncio.run(run(args.state, args.host, args.port, args.seconds))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
