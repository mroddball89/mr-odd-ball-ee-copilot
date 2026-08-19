#!/usr/bin/env python3
"""
Module:  demo_chat.py
Purpose: Serve the rig with a realistic conversation already in it, so the layout can be seen.
Author:  LB
Date:    2026-08-19

    python tools/demo_chat.py
    # then open  http://127.0.0.1:8765/?chat=1

**No API key, no model, no microphone.** Every message is canned, so this costs nothing against
the 20/day free tier (D3) and works with the network unplugged. It exists because the chat
panel is the one part of this project that cannot be judged from a harness: `verify_chat.py`
proves the messages arrive and the rig dispatches them, and proves nothing at all about whether
the thing is legible at 50% opacity over a wallpaper.

The conversation is chosen to exercise every card kind in one screen — prose, a C snippet, a
markdown table, terminal output, a sources list, and a permission gate with its buttons. If any
of those look wrong, they look wrong here first.

    --solo      the old look: him alone, no panel
    --state X   park his face in one state instead of cycling
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from engine.response import Card, CardKind, Pending          # noqa: E402
from orchestrator.hud_bridge import HudBridge                # noqa: E402

# (delay before, action). Delays are what make it read as a conversation rather than a dump —
# and they are what shows whether the panel auto-scrolls sensibly as content arrives.
SCRIPT = [
    (0.4, ("state", "listening")),
    (0.8, ("you", "how wide does a trace need to be for three amps")),
    (0.5, ("state", "thinking")),
    (1.1, ("route", "hardware")),
    (0.1, ("oddball", "You need a trace at least ninety three mils wide, or two point four "
                      "millimetres.")),
    (0.1, ("card", Card(CardKind.MARKDOWN, "",
                        "For 3.0 A on 1.0 oz external copper with a 10 °C rise, the required "
                        "trace width is 92.99 mils (2.362 mm)."))),
    (0.1, ("card", Card(CardKind.TABLE, "IPC-2221",
                        "| Parameter | Value |\n"
                        "|---|---|\n"
                        "| Current | 3.0 A |\n"
                        "| Temp rise | 10 °C |\n"
                        "| Copper | 1.0 oz, external |\n"
                        "| Cross section | 128.1 sq mils |\n"
                        "| **Trace width** | **92.99 mils (2.362 mm)** |"))),

    (2.2, ("you", "how do i set gpio thirteen as an output on the esp32")),
    (0.6, ("route", "firmware")),
    (0.1, ("oddball", "Set bit thirteen of the GPIO enable register to make that pin an "
                      "output.")),
    (0.1, ("card", Card(CardKind.CODE, "C", "// Set GPIO 13 as output\n"
                                            "REG_WRITE(GPIO_ENABLE_REG, BIT13);\n"
                                            "\n"
                                            "// ...then drive it high\n"
                                            "REG_WRITE(GPIO_OUT_W1TS_REG, BIT13);", "c"))),
    (0.1, ("card", Card(CardKind.MARKDOWN, "Sources",
                        "[1] esp32_technical_reference_manual.pdf, page 62\n"
                        "[2] esp32_datasheet_en.pdf, page 14"))),

    (2.4, ("you", "check the cpu temperature")),
    (0.6, ("route", "os")),
    (0.1, ("card", Card(CardKind.CODE, "Wants to run",
                        "cat /sys/class/thermal/thermal_zone0/temp", "bash"))),
    (0.1, ("oddball", "I want to check the CPU temperature. Should I?")),
    (0.1, ("pending", Pending("os", {}, "I want to check the CPU temperature. Should I?",
                              "cat /sys/class/thermal/thermal_zone0/temp"))),
    (2.6, ("clear_pending", None)),
    (0.1, ("oddball", "Done. The output's on the screen.")),
    (0.1, ("card", Card(CardKind.LOG, "Output", "Terminal Output:\n48312"))),
    (0.1, ("state", "idle")),
]


async def run(loop_forever: bool) -> None:
    bridge = HudBridge(host="127.0.0.1", port=8765, resting_state="idle")
    server = await bridge.start()

    print("\n  Open one of these:\n")
    print("    http://127.0.0.1:8765/?chat=1     <- him, with the chat box under him")
    print("    http://127.0.0.1:8765/?solo=1     <- him alone, the old look")
    print("    http://127.0.0.1:8765/            <- the development rig, all its buttons")
    print("\n  Ctrl-C to stop.\n")

    async with server:
        while True:
            # Wait for a rig to attach before playing, or the whole conversation happens to
            # nobody and the panel opens empty. The replay buffer would cover it, but watching
            # it arrive is the entire point of this script.
            while bridge.client_count == 0:
                await asyncio.sleep(0.3)

            for delay, (kind, value) in SCRIPT:
                await asyncio.sleep(delay)
                if kind == "state":
                    bridge.set_state(value)
                elif kind == "route":
                    bridge.set_route(value)
                elif kind in ("you", "oddball"):
                    bridge.say_line(kind, value)
                elif kind == "card":
                    bridge.show_card(value)
                elif kind == "pending":
                    bridge.ask_approval(value)
                elif kind == "clear_pending":
                    bridge.clear_pending()

            if not loop_forever:
                print("  ...conversation played. Ctrl-C to stop, or pass --loop to replay.")
                while True:
                    await asyncio.sleep(3600)
            await asyncio.sleep(4.0)


def main() -> int:
    ap = argparse.ArgumentParser(description="serve the rig with a canned conversation")
    ap.add_argument("--loop", action="store_true", help="replay the conversation forever")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.loop))
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
