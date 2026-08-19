#!/usr/bin/env python3
"""
Module:  os_agent.py
Purpose: Control the Pi from a spoken request — with LB's approval, never without.
Author:  LB
Date:    2026-08-18 (split into propose/resume 2026-08-19)

## Why this is two functions now

It used to be one, and it blocked on `input("Allow execution? (y/n): ")`. That works in a
terminal and nowhere else: a voice turn has no stdin to read, and a chat panel cannot answer a
prompt printed to a console nobody is looking at.

So the gate is now a **suspended state** instead of a blocking call:

    propose_os_action(query) -> Response with .pending set, and NOTHING has run
    resume_os_action(pending) -> Response, after approval was given

`engine/core.py` holds the `Pending` between the two and reads the next thing LB says as the
answer. Silence, a mumble, a timeout and a refusal all decline — see `orchestrator/classify_yes.py`.

## The two strings

`Pending.spoken` and `Pending.shown` are different on purpose, and this is the part worth
being careful about.

Reading `cat /sys/class/thermal/thermal_zone0/temp` out loud gives "cat slash sys slash class
slash thermal slash thermal underscore zone zero slash temp", which is unusable as a question —
LB cannot judge what he is approving from it. So the model supplies a plain description for
the ear, and the **exact** command goes on a card for the eye, rendered BEFORE the question is
asked.

That is the honest version of the risk, not a fix for it: approving from a paraphrase means
trusting the paraphrase. What makes it acceptable is that the exact text is on screen at the
moment of asking, the blocklist in `tools/os_controller.py` runs regardless, and nothing at
all executes without a clear yes.
"""

from __future__ import annotations

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from engine.models import AGENT_MODEL
from engine.llm_text import extract_text_content
from engine.response import Card, CardKind, Pending, Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools.memory_manager import format_memory_for_llm
from tools.os_controller import execute_terminal_command

OS_PROMPT_TEMPLATE = """
You are an expert Linux System Administrator running on a Raspberry Pi.
You have access to the system terminal via the `execute_terminal_command` tool.

{chat_history}

EXAMPLE 1:
User: What is the current CPU temperature of the Pi?
AI: I will check the thermal zone file to get the CPU temperature.
[Action: AI triggers execute_terminal_command with args: command="cat /sys/class/thermal/thermal_zone0/temp"]
Result: Terminal Output: 45000
AI: The current CPU temperature of the Raspberry Pi is 45.0°C.

User Question: {question}
""" + SPOKEN_INSTRUCTION

# Asked separately, and asked of the model rather than built by string formatting, because the
# model is the only thing that knows what its own command was FOR. "Check the CPU temperature"
# is a description of intent; a regex over the command text could only ever produce a
# description of syntax.
DESCRIBE_PROMPT = """
You are about to ask permission to run a command on a Raspberry Pi. The person you are asking
will HEAR your question, not read it, so the command itself must not appear in it.

The command is: {command}

Write one short question asking whether to run it, describing what it does in plain spoken
English. No file paths, no flags, no command names, no symbols.

Good: "I want to check the CPU temperature. Should I?"
Good: "I want to list what's in your home folder. Go ahead?"
Bad:  "Should I run cat /sys/class/thermal/thermal_zone0/temp?"

Write only the question.
"""

_FALLBACK_QUESTION = "I want to run a command on the Pi. It's on the screen. Should I?"


def _describe(llm, command: str) -> str:
    """A speakable question for `command`. Falls back rather than failing.

    A failure here must not become a failure to ask — the gate is the safety property, and
    losing the description is survivable in a way that skipping the question is not.
    """
    try:
        spoken = extract_text_content(llm.invoke(DESCRIBE_PROMPT.format(command=command)).content)
        spoken = " ".join(spoken.split())
        # If the model put the command in anyway, the question is unusable aloud. Do not try
        # to clean it up — take the fallback, because the card carries the exact text and the
        # fallback points at it.
        from engine.split import is_speakable
        if spoken and is_speakable(spoken) is None:
            return spoken
    except Exception:                                  # noqa: BLE001
        pass
    return _FALLBACK_QUESTION


def propose_os_action(query: str) -> Response:
    """Work out what to run, and ask. **Nothing is executed here.**

    Args:
        query: what LB asked for.

    Returns:
        A Response with `.pending` set when a command is wanted, or a plain answer when the
        model chose to reply without touching the machine.
    """
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1)
    llm_with_tools = llm.bind_tools([execute_terminal_command])

    history = format_memory_for_llm()
    prompt_template = ChatPromptTemplate.from_template(OS_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)

    response = llm_with_tools.invoke(prompt)

    if response.tool_calls:
        tool_args = response.tool_calls[0]["args"]
        command = tool_args.get("command", "")
        spoken = _describe(llm, command)

        return Response(
            speech=spoken,
            # The card is the whole point of the gate over voice: the exact text, in front of
            # LB, at the moment he is asked. It leads the card list so it is what he sees.
            cards=[Card(CardKind.CODE, "Wants to run", command, "bash")],
            route="os",
            pending=Pending(kind="os", tool_args=tool_args, spoken=spoken, shown=command),
            raw=f"Proposed command:\n{command}")

    return split(extract_text_content(response.content), route="os")


def resume_os_action(pending: Pending) -> Response:
    """Run the approved command and report what happened.

    Only ever called after `orchestrator.classify_yes.is_yes` returned True.
    """
    result = execute_terminal_command.invoke(pending.tool_args)

    failed = result.startswith("Terminal Error:") or result.startswith("Action Blocked:")
    speech = ("That didn't work — the error is on the screen." if failed
              else "Done. The output's on the screen.")

    return Response(
        speech=speech,
        cards=[
            Card(CardKind.CODE, "Ran", pending.shown, "bash"),
            Card(CardKind.ERROR if failed else CardKind.LOG, "Output", result),
        ],
        route="os",
        raw=f"OS Execution Result:\n{result}")


def run_os_agent(query: str) -> str:
    """The old blocking entry point, kept for `main.py --text` and the existing tests.

    Deliberately NOT the path the voice loop takes. It still reads stdin, which is correct for
    a terminal session and impossible everywhere else.
    """
    proposed = propose_os_action(query)
    if proposed.pending is None:
        return proposed.raw or proposed.speech

    print("\n⚠️ SECURITY CHECK: The AI wants to execute:")
    print(f"   > {proposed.pending.shown}")
    if input("   Allow execution? (y/n): ").strip().lower() == "y":
        return resume_os_action(proposed.pending).raw
    return "Action aborted by the user. No terminal commands were executed."
