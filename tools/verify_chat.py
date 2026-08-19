#!/usr/bin/env python3
"""
Module:  verify_chat.py
Purpose: Prove the chat channel — both directions, and that the rig speaks the same protocol.
Author:  LB
Date:    2026-08-19

    python tools/verify_chat.py

No model, no audio, no key. A real WebSocket on a real ephemeral port, because the thing being
tested IS the transport and a mocked socket would test the mock.

## The check that has caught this before

`verify_speech.py` has a check that reads: *"the rig actually handles the message type the
bridge sends"*. It exists because the two halves are a Python file and an HTML file with a
JSON contract between them and no compiler in the middle — the bridge can broadcast
`{"type": "card"}` forever while the rig quietly ignores it, and everything looks fine from
both sides in isolation.

Section 3 does that for every new message type at once, in both directions: every type the
bridge can send must appear in the rig's dispatch, and every type the rig can send must be one
the turn loop knows how to drain.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import websockets                                                    # noqa: E402

from engine.response import Card, CardKind, Pending                  # noqa: E402
from orchestrator.hud_bridge import HISTORY, HudBridge, _REPLAYABLE   # noqa: E402

PASSED = 0
FAILED = 0
RIG = Path(__file__).resolve().parents[1] / "hud" / "face-preview.html"


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


async def collect(ws, n: int, timeout: float = 2.0) -> list[dict]:
    """Read up to n messages, giving up rather than hanging a harness."""
    out = []
    for _ in range(n):
        try:
            out.append(json.loads(await asyncio.wait_for(ws.recv(), timeout)))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            break
    return out


async def run() -> None:
    bridge = HudBridge(host="127.0.0.1", port=0)
    server = await bridge.start()
    port = next(iter(server.sockets)).getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    # =====================================================================================
    section("1. outbound — the panel is told what happened")
    # =====================================================================================

    async with websockets.connect(url) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
        check(first.get("type") == "state",
              "a new client is told his state before anything else", f"{first}")

        bridge.say_line("you", "how wide for 3 amps")
        bridge.say_line("oddball", "You need about ninety three mils.")
        bridge.set_route("hardware")
        bridge.show_card(Card(CardKind.CODE, "Wants to run", "ls -la", "bash"))
        bridge.set_mode("quiz")
        bridge.ask_approval(Pending("os", {"command": "ls"}, "Should I list them?", "ls -la"))

        got = await collect(ws, 6)
        kinds = [m["type"] for m in got]
        check(kinds == ["transcript", "transcript", "route", "card", "mode", "pending"],
              "every chat message reaches the panel, in order", f"{kinds}")

        you = [m for m in got if m["type"] == "transcript" and m["role"] == "you"]
        check(you and you[0]["value"] == "how wide for 3 amps",
              "a transcript line carries its role and text")

        card = [m for m in got if m["type"] == "card"][0]["value"]
        check(card["kind"] == "code" and card["lang"] == "bash" and card["body"] == "ls -la",
              "a Card serialises with kind, lang and body intact", f"{card}")

        pending = [m for m in got if m["type"] == "pending"][0]["value"]
        check(pending["shown"] == "ls -la" and pending["spoken"] == "Should I list them?",
              "a Pending crosses with BOTH strings — the paraphrase and the exact command",
              f"{pending}")
        check(pending["spoken"] != pending["shown"],
              "and they are different, which is the whole point of asking by voice (D4)")

        bridge.clear_pending()
        cleared = await collect(ws, 1)
        check(cleared and cleared[0]["value"] is None,
              "clear_pending takes the buttons away", f"{cleared}")

        # An empty line is not a line. Piper says nothing for it and a blank bubble is noise.
        bridge.say_line("oddball", "   ")
        bridge.set_route("math")
        after = await collect(ws, 2)
        check([m["type"] for m in after] == ["route"],
              "a blank transcript line is dropped rather than shown",
              f"{[m['type'] for m in after]}")

    # =====================================================================================
    section("2. inbound — the panel talks back")
    # =====================================================================================

    async with websockets.connect(url) as ws:
        await collect(ws, HISTORY + 1, timeout=0.4)          # drain the replay
        await ws.send(json.dumps({"type": "text", "value": "what is ohms law"}))
        await ws.send(json.dumps({"type": "approve", "value": True}))
        await ws.send("not json at all")
        await ws.send(json.dumps({"no_type": 1}))
        await ws.send(json.dumps(["a list, not an object"]))
        await asyncio.sleep(0.35)

        drained = bridge.drain_inbound()
        kinds = [m["type"] for m in drained]
        check(kinds == ["text", "approve"],
              "typed text and an approve click arrive; malformed input is discarded",
              f"{kinds}")
        check(drained[0]["value"] == "what is ohms law", "the typed question survives intact")
        check(drained[1]["value"] is True, "the approve click carries its boolean")
        check(bridge.drain_inbound() == [],
              "draining twice does not replay — the queue is consumed")

    # =====================================================================================
    section("3. the rig speaks the same protocol")
    # =====================================================================================

    rig = RIG.read_text(encoding="utf-8")

    # Every type the bridge can broadcast must be dispatched by the rig. This is the check
    # that catches the two halves drifting, because nothing else can: it is a Python file and
    # an HTML file with a JSON contract and no compiler between them.
    for kind in ("state", "gesture", "mouth", "transcript", "card", "route", "mode", "pending"):
        check(f'"{kind}"' in rig, f"the rig handles {kind!r} messages")

    for element in ("chatLog", "chatRoute", "chatMode", "chatPending", "pendingQ",
                    "pendingCmd", "btnApprove", "btnDeny", "chatForm", "chatInput"):
        check(f'id="{element}"' in rig, f"the rig has the {element!r} element the script writes")

    # ...and every type the rig can SEND must be one the turn loop knows what to do with.
    check('"type": "text"' in rig or '{ type: "text"' in rig or 'type: "text"' in rig,
          "the rig sends typed questions as 'text'")
    check('type: "approve"' in rig or '"type": "approve"' in rig,
          "the rig sends gate decisions as 'approve'")

    turn_src = (Path(__file__).resolve().parents[1] / "engine" / "turn.py").read_text(encoding="utf-8")
    check('"approve"' in turn_src,
          "the turn loop reads 'approve' off the inbound queue — a button IS a spoken yes")

    # Scanned with COMMENTS STRIPPED, and that is not fussiness — the first version of this
    # check went red on the comment that promises never to use innerHTML. That is the third
    # time this project has hit the same shape (STATE.md's grep-vs-AST on `shell=True`, and
    # the script-tag literal in this same file), so the rule is worth stating once: a check
    # that punishes documenting the rule is a check that gets deleted. Scan the code.
    code = re.sub(r"//[^\n]*", "", rig)
    check("textContent" in code and "innerHTML" not in code,
          "cards render through textContent, never innerHTML",
          "this panel shows model output and shell output verbatim, so markup in a datasheet "
          "must render AS TEXT")

    check('data-chat="on"' in rig, "the rig has a ?chat=1 mode distinct from ?solo=1")

    # =====================================================================================
    section("4. a late joiner sees the conversation, not an empty column")
    # =====================================================================================

    async with websockets.connect(url) as ws:
        replayed = await collect(ws, 20, timeout=0.5)
        kinds = {m["type"] for m in replayed}
        check("transcript" in kinds and "card" in kinds,
              "a client connecting mid-session is replayed the chat so far", f"{sorted(kinds)}")
        check("mouth" not in kinds and "gesture" not in kinds,
              "but NOT momentary face messages — a replayed mouth position freezes his jaw",
              f"{sorted(kinds)}")
        check(_REPLAYABLE == {"transcript", "card", "route", "mode"},
              "and the replayable set is exactly the chat types", f"{_REPLAYABLE}")

    # The cap is what stops a long session turning the bridge into a memory leak.
    #
    # The sleep is required, not cosmetic: say_line goes through broadcast_threadsafe, which
    # SCHEDULES the coroutine on the loop rather than running it. Without yielding, only the
    # handful that happened to get scheduled are counted — the first version of this check
    # asserted 40 and measured 6, and the bug was entirely in the test.
    for i in range(HISTORY + 25):
        bridge.say_line("you", f"line {i}")
    await asyncio.sleep(0.4)
    check(len(bridge._history) == HISTORY,
          f"the replay buffer is capped at {HISTORY}", f"{len(bridge._history)}")

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
    raise SystemExit(0)
