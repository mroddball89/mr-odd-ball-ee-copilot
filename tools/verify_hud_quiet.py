#!/usr/bin/env python3
"""
Module:  verify_hud_quiet.py
Purpose: Prove a port probe no longer prints a stack trace, and that a real failure still does.
Author:  LB
Date:    2026-08-29

    python tools/verify_hud_quiet.py

Runs a real `HudBridge` on a real socket and connects to it for real. Nothing is mocked, because
the thing under test is a `logging.Filter` sitting on a path that only exists at runtime — the
record it has to recognise is built by `websockets`, four frames deep, out of an exception it
raises itself. A fake record would be a description of that, and descriptions of log records are
exactly what stops matching after a library upgrade.

## The noise

35 lines in `oddball.log` on 2026-08-29, one per turn, each a 15-line traceback at ERROR:

    ERROR  opening handshake failed
    ...
    EOFError: connection closed while reading HTTP request line
    websockets.exceptions.InvalidMessage: did not receive a valid HTTP request

**It was the assistant scanning its own port.** `tools/system_state._is_listening` opens a TCP
connection to 127.0.0.1:8765 and closes it — `connect_ex`, no bytes sent — to answer "is the
face up" for the prompt block that `tools/self_context.py` builds every turn. That is the right
way to check a port is bound, and from the server's side it is indistinguishable from a client
that connected and walked away.

## Section 2 is the one that matters

Silencing the whole message would be easy and wrong. `opening handshake failed` is also what a
REAL failure logs — LB's rig unable to connect, a bad Origin, an HTTP client that never sent an
Upgrade header. Section 2 sends a well-formed HTTP request that is not a WebSocket upgrade and
asserts the ERROR still arrives, with its traceback, because that one is a bug report.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from orchestrator.hud_bridge import HudBridge, _WS_LOG               # noqa: E402

PASSED = 0
FAILED = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"   PASS  {what}")
    else:
        FAILED += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def section(name: str) -> None:
    print(f"\n  {name}")


class Spy(logging.Handler):
    """Catches everything that would have reached the terminal."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def at(self, level: int) -> list[logging.LogRecord]:
        return [r for r in self.records if r.levelno == level]

    def tracebacks(self) -> list[logging.LogRecord]:
        return [r for r in self.records if r.exc_info]


async def probe(port: int) -> None:
    """Exactly what `system_state._is_listening` does: connect, then close. No bytes sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(("127.0.0.1", port))
    await asyncio.sleep(0.35)          # let the server finish failing the handshake


async def plain_http(port: int) -> None:
    """A well-formed HTTP request that is NOT a WebSocket upgrade — a real handshake failure."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(("127.0.0.1", port))
        s.sendall(b"GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: bogus\r\n\r\n")
        try:
            s.recv(1024)
        except OSError:
            pass
    await asyncio.sleep(0.35)


async def run() -> None:
    # Port 0 — the OS picks a free one, so this cannot collide with a running assistant.
    bridge = HudBridge("127.0.0.1", 0, "sleeping")
    server = await bridge.start()
    port = next(iter(server.sockets)).getsockname()[1]

    spy = Spy()
    # On the ROOT logger, because that is where `basicConfig` puts the terminal handler. This
    # sees what LB would have seen, rather than what one logger chose to pass on.
    logging.getLogger().addHandler(spy)
    _WS_LOG.setLevel(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)

    try:
        # =================================================================================
        section("1. a bare TCP probe — the assistant checking its own port")
        # =================================================================================
        spy.records.clear()
        for _ in range(4):
            await probe(port)

        errors = spy.at(logging.ERROR)
        check(not errors,
              "four probes produce NO error lines",
              "" if not errors else
              f"{len(errors)} ERROR(s): {[r.getMessage() for r in errors]}")
        check(not spy.tracebacks(),
              "and no stack traces at all",
              "" if not spy.tracebacks() else
              f"{len(spy.tracebacks())} traceback(s) — 15 log lines each")

        debugs = [r for r in spy.at(logging.DEBUG) if "hung up" in r.getMessage()]
        check(len(debugs) == 4,
              f"each is accounted for by exactly one DEBUG line ({len(debugs)} of 4)",
              "" if len(debugs) == 4 else
              "suppressed silently is not the same as suppressed to a DEBUG line")
        if debugs:
            check(debugs[0].exc_info is None,
                  "which carries no traceback",
                  f"said: {debugs[0].getMessage()[:70]!r}")

        # =================================================================================
        section("2. a REAL handshake failure is still loud")
        # =================================================================================
        #
        # The check that stops this fix from being a mute button. This is what LB's own rig
        # failing to connect looks like, and it must not be swallowed with the port scans.
        spy.records.clear()
        await plain_http(port)

        errors = spy.at(logging.ERROR)
        check(bool(errors),
              "a malformed upgrade still logs an ERROR",
              "" if errors else
              "SILENCED. The filter is matching too much — a rig that cannot connect would "
              "now fail invisibly, which is worse than the noise it replaced")
        check(any(r.exc_info for r in errors),
              "with the traceback kept, because that one is a bug report",
              f"records: {[r.getMessage() for r in errors]}")

        # =================================================================================
        section("3. the bridge still works — this is a filter, not a firewall")
        # =================================================================================
        spy.records.clear()
        import websockets

        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await bridge.broadcast({"type": "state", "name": "listening"})
            msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            check(bool(msg), "a genuine client still connects and receives", f"got {msg[:60]}")
        check(not spy.at(logging.ERROR),
              "and a successful connection logs no errors",
              f"{[r.getMessage() for r in spy.at(logging.ERROR)]}")
    finally:
        logging.getLogger().removeHandler(spy)
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run())

    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} RED\n")
        raise SystemExit(1)
    print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
