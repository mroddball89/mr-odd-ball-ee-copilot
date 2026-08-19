#!/usr/bin/env python3
"""
Module:  main.py
Purpose: Start Mr Odd Ball — by voice, or by typing.
Author:  LB
Date:    2026-08-17 (became a launcher 2026-08-19)

    python main.py                 # voice: wake word, ears, voice, face
    python main.py --text          # type at him instead. No audio, no HUD.
    python main.py --text --no-hud # same, explicitly

This file used to BE the assistant — a `while True:` around `input()`, with the router, the
mode state, the permission gates and the terminal formatting all interleaved. That last one is
what made the other three unreachable from anywhere else, so a voice turn could not have
answered `input("Allow execution? (y/n): ")` if it tried.

All of it now lives in `engine/`. What is left here is the choice of which mouth and ears to
attach, which is what an entry point should be.

**`--text` is not a lesser mode.** It is the one that runs with no microphone, no speaker and
no quota spent on speech synthesis, so it is where an agent gets debugged. It is also the mode
that still works when the audio stack does not — and on a Pi with a Bluetooth speaker, that is
worth having.
"""

from __future__ import annotations

import argparse
import sys

# Windows defaults stdout to cp1252, which cannot encode the emoji in this file's own banner —
# nor "Ω", "μ" or "τ", which is most of what an EE copilot has to print. It crashes rather than
# degrading, and it crashes on the FIRST line, so it looks like a startup failure rather than a
# console setting. The Pi is UTF-8 already; this only does anything on Windows.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# engine.models loads .env, and it must happen before `router` is imported anywhere, because
# router.py builds its LangChain chain at import time and the constructor validates the key.
from engine import models


def run_text(show_cards: bool = True) -> int:
    """The typing loop. One question in, one Response out, printed."""
    from engine.core import Engine
    from engine.response import CardKind

    print("=" * 60)
    print("⚡ MR. ODD BALL — EE COPILOT (text mode) ⚡")
    print(f"   router: {models.ROUTER_MODEL}   agents: {models.AGENT_MODEL}")
    print("=" * 60)
    print("Type 'exit' or 'quit' to close. Say 'quiz me' to be tested.\n")

    engine = Engine()

    while True:
        try:
            query = input("\n👤 You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down... Goodbye!")
            return 0

        if query.lower() in ("exit", "quit") and engine.mode != "quiz" and engine.pending is None:
            print("Shutting down... Goodbye!")
            return 0
        if not query:
            continue

        response = engine.ask(query)

        # The spoken half first and on its own line, because that is what he would have SAID —
        # seeing the two channels separated is most of the value of this mode.
        print(f"\n🧠 Says: {response.speech}")

        if show_cards:
            for card in response.cards:
                icon = {CardKind.CODE: "💻", CardKind.TABLE: "📊", CardKind.LOG: "📜",
                        CardKind.ERROR: "❌"}.get(card.kind, "📄")
                title = f" {card.title}" if card.title else ""
                print(f"\n{icon} Shows [{card.kind}]{title}:")
                print("   " + card.body.replace("\n", "\n   "))

        if response.pending is not None:
            print("\n⚠️  Waiting on your answer (yes / no).")

        print(f"\n   [{engine.last.line()}]")
        print("-" * 60)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mr Odd Ball — an EE copilot with a face and a voice.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--voice", action="store_true",
                      help="wake word, ears, voice and face. The default.")
    mode.add_argument("--text", action="store_true",
                      help="type at him. No audio hardware needed — where agents get debugged.")
    ap.add_argument("--no-cards", action="store_true",
                    help="text mode: print only what he would SAY, not what he would show")
    args, rest = ap.parse_known_args(argv)

    if args.text:
        return run_text(show_cards=not args.no_cards)

    # Voice is the default. Anything unrecognised is forwarded to run_voice's own parser, so
    # `python main.py --simulate 5 --no-speech` works without this file knowing those flags.
    from engine.run_voice import main as run_voice

    import asyncio

    return asyncio.run(run_voice(rest))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
