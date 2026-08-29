#!/usr/bin/env python3
# The module docstring is RAW: it quotes real Windows paths, and \U in C:\Users is a
# unicode escape that stops this file importing. Same rule as OS_PROMPT_TEMPLATE below.
r"""
Module:  os_agent.py
Purpose: Control this Windows PC from a spoken request — with LB's approval, never without.
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

Reading `Get-ChildItem -Path 'C:\Users\ironi\OneDrive\Desktop' -Filter *.stl` out loud
gives "get dash child item dash path c colon backslash users backslash ironi backslash one
drive backslash desktop dash filter star dot s t l", which is unusable as a question — LB
cannot judge what he is approving from it. So the model supplies a plain description for
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

from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from engine.response import Card, CardKind, Pending, Response
from engine.split import SPOKEN_INSTRUCTION, split
from tools.memory_manager import format_memory_for_llm
from tools.os_controller import (KINDS, Outcome, execute_terminal_command,
                                 folders_for_prompt, run_command)

# ## The prompt says Windows because the machine is Windows (2026-08-29)
#
# It said "You are an expert Linux System Administrator running on a Raspberry Pi" for three
# days after the Pi was retired, while `tools/os_controller.execute_terminal_command` — the
# only tool bound to this agent — told the same model, in its own docstring, that it was
# running PowerShell on Windows 11.
#
# A contradiction inside one prompt does not fail loudly. It gets **averaged**, and the average
# is a PowerShell command carrying a Unix assumption: `Get-ChildItem -Path "$Home\Desktop"` is
# the right cmdlet wrapped around `~/Desktop`, and it is the exact command that failed on
# 2026-08-29 at 07:17:19 after LB had approved it. The example underneath did not help — it
# read `cat /sys/class/thermal/thermal_zone0/temp`, a path with no meaning on this machine, and
# an example is the strongest instruction in a prompt.
#
# So: one platform, stated once, with examples from the platform it names, and the real folder
# paths handed in rather than left to be composed. See `os_controller.folders_for_prompt`.
# **A raw string, and it has to be.** The example below quotes a real Windows path, and
# `\U` in `C:\Users` is a unicode escape in a plain literal — the module stops importing
# entirely, which is how this was caught. Any path added here keeps the `r` prefix honest.
OS_PROMPT_TEMPLATE = r"""
You are an expert Windows 11 System Administrator. The shell you write for is Windows
PowerShell 5.1 (powershell.exe), reached through the `execute_terminal_command` tool.

Write PowerShell. Not bash, not cmd:
- Cmdlets, not coreutils. Get-ChildItem, not ls. Get-Content, not cat. Remove-Item, not rm.
  Get-Process, not ps. Get-CimInstance, not /proc.
- Environment variables are $env:NAME — never %NAME% and never a bare $NAME.
- Paths use backslashes, and any path containing a space must be quoted.
- PowerShell 5.1 has no && and no || operators. Separate commands with a semicolon.
- There is no /sys, no /proc and no /etc on this machine, and no sudo. Nothing that reads a
  file under those paths can work here.
- The commands run with -NoProfile, so rely only on built-in cmdlets.
{folders}
{chat_history}

EXAMPLE 1:
User: How much disk space have I got left?
AI: I will ask the drive holding the user profile how much space is free.
[Action: AI triggers execute_terminal_command with args: command="Get-PSDrive C | Select-Object Used, Free"]
Result: Terminal Output: Used Free  152.6 GB 85.2 GB
AI: You have about 85 gigabytes free on the C drive.

EXAMPLE 2:
User: What STL files are sitting on my desktop?
AI: I will list the STL files in the desktop folder given above, using its real path.
[Action: AI triggers execute_terminal_command with args: command="Get-ChildItem -Path 'C:\Users\ironi\OneDrive\Desktop' -Filter *.stl -File | Select-Object Name, Length"]
Result: Terminal Output: bracket.stl 41234  mount.stl 88210
AI: There are two STL files on your desktop, bracket and mount.

User Question: {question}
""" + SPOKEN_INSTRUCTION

# Asked separately, and asked of the model rather than built by string formatting, because the
# model is the only thing that knows what its own command was FOR. "Check the CPU temperature"
# is a description of intent; a regex over the command text could only ever produce a
# description of syntax.
DESCRIBE_PROMPT = """
You are about to ask permission to run a command on a Windows 11 PC. The person you are asking
will HEAR your question, not read it, so the command itself must not appear in it.

The command is: {command}

Write one short question asking whether to run it, describing what it does in plain spoken
English. No file paths, no flags, no command names, no symbols.

Good: "I want to check the CPU temperature. Should I?"
Good: "I want to list what's in your home folder. Go ahead?"
Bad:  "Should I run Get-CimInstance Win32_Processor?"

Write only the question.
"""

_FALLBACK_QUESTION = "I want to run a command on your PC. It's on the screen. Should I?"

# What he SAYS for each way an action can end. Lives here, not in `tools/`, because the tools
# must stay free of persona — they report facts, this file decides how he puts them.
#
# It is total over `tools.os_controller.KINDS` and a harness asserts that, because the failure
# mode of a missing row is the one this table exists to fix: falling through to a generic
# sentence that happens to sound like success.
#
# Two rules every row obeys:
#   1. It is a claim about what HE DID, never about what is now true of the screen. He cannot
#      see the screen. "Opening Firefox now" is checked; "Firefox is open" is not.
#   2. It is speakable — no paths, no flags, no numbers that drift. The old timeout sentence
#      named fifteen seconds; TIMEOUT_S is a constant and the sentence would have started
#      lying the moment anybody tuned it.
_SPEECH: dict[str, str] = {
    "output":        "Done. The output's on the screen.",
    "error":         "That didn't work — the error is on the screen.",
    # NOT "that didn't work". The blocklist refusing something is the system working, and
    # os_controller's own docstring says reporting it as a fault is how a guard gets disabled.
    "blocked":       "I won't run that one. The reason's on the screen.",
    # subprocess.run kills the child on timeout, so "I stopped it" is a fact, not a euphemism.
    "timeout":       "It was taking too long, so I stopped it.",
    "crash":         "Something went wrong on my end. It's on the screen.",
    "launched":      "Opening {subject} now.",
    "no-display":    "I couldn't find the screen to open it on, so I've left it alone.",
    "not-installed": "{subject} isn't installed on here, so there's nothing to open.",
    "unknown-app":   "I don't know how to open that one. What I can open is on the screen.",
    "ambiguous":     "There's more than one of those. The list is on the screen.",
    "launch-failed": "{subject} wouldn't start. The reason's on the screen.",
    "unknown-tool":  "I'm not sure how to do that one. The details are on the screen.",
}

assert set(_SPEECH) == set(KINDS), f"_SPEECH is not total over KINDS: {set(KINDS) ^ set(_SPEECH)}"


def _speech_for(outcome: Outcome) -> str:
    """The sentence for `outcome`. Never raises, never leaks the detail into speech."""
    template = _SPEECH.get(outcome.kind, _SPEECH["crash"])
    return template.format(subject=outcome.subject or "That")


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
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1, max_retries=LLM_MAX_RETRIES)
    llm_with_tools = llm.bind_tools([execute_terminal_command])

    history = format_memory_for_llm()
    prompt_template = ChatPromptTemplate.from_template(OS_PROMPT_TEMPLATE)
    # Resolved per turn rather than at import. OneDrive can redirect a folder while the process
    # is running, and a path frozen at start-up is the same class of stale fact this prompt was
    # just fixed for.
    prompt = prompt_template.format(chat_history=history, question=query,
                                    folders=folders_for_prompt())

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


def propose_launch(app: str, spoken: str) -> Response:
    r"""Ask whether to open `app`. **Costs no model call at all.**

    `_describe()` exists because a shell string is unspeakable — reading
    `Get-ChildItem -Path 'C:\Users\ironi\OneDrive\Desktop'` aloud is unusable as a question. But "Want me
    to open Firefox?" is already a sentence, so the second Gemini call that produced it is pure
    waste. Together with the free intent in `orchestrator/launch_intent.py`, that takes a
    launch from three API calls to zero.

    D4's property is unchanged and this is the point: the card still carries the exact argv,
    rendered before the question is asked. It is in fact *stronger* here than on the shell
    path, because the thing approved, the thing spoken and the thing shown all come from one
    reviewed desktop entry rather than from a paraphrase of model output.

    Args:
        app:    the phrase to resolve — "firefox", "the browser".
        spoken: the question to ask, from `launch_intent`.

    Returns:
        A gated `Response`. **Nothing has run.**
    """
    from tools.app_catalogue import cached_catalogue, resolve

    shown = app
    try:
        match = resolve(app, cached_catalogue())
        if match.ok:
            shown = f"{' '.join(match.app.argv)}\n\n{match.app.path}"
    except Exception:                                                  # noqa: BLE001
        pass    # the card degrades to the app name; resolution happens again at launch time

    return Response(
        speech=spoken,
        cards=[Card(CardKind.CODE, "Wants to open", shown, "bash")],
        route="os",
        pending=Pending(kind="os", tool_args={"app": app}, spoken=spoken, shown=shown,
                        tool="launch_app"),
        raw=f"Proposed launch:\n{app}")


# Which tool resumes a Pending. `engine/core.py` dispatches on `Pending.kind` to pick the AGENT;
# this picks the tool within it. Dispatch is on the NAME, never on the shape of `tool_args`:
# models routinely emit extra keys, and the day one arrives as {"app": ..., "command": ...} an
# `if "command" in args` would pick the shell and run something. An authority boundary decided
# by a dict key is not a boundary.
_RESUME = {
    "execute_terminal_command": lambda args: run_command(args.get("command", "")),
    "launch_app": lambda args: _launch(args.get("app", "")),
}


def _launch(app: str):
    """Imported late so a broken catalogue costs the launch feature, not the whole OS route."""
    from tools.app_launcher import launch
    return launch(app)


# Which outcome kinds are worth remembering, and what he should take from each. An outcome that
# is not in here is not written down — `output` and `launched` are successes, and a ledger that
# records those is a log rather than a lesson.
#
# `blocked` IS in here and that deserves saying plainly: the blocklist refusing something is the
# system working, not a fault, and `tools/os_controller.py` warns that reporting it as a fault
# is how a guard gets switched off. It is recorded anyway because the *composition* was the
# mistake — a model wrote a command that must never run — and the lesson is about not writing it
# again, not about the guard. The wording below keeps that distinction.
_WORTH_REMEMBERING: dict[str, str] = {
    "error":         "check the command works before proposing it",
    "timeout":       "this one does not finish quickly; do not propose it on a spoken turn",
    "blocked":       "never propose this again — the safety list refuses it, and correctly",
    "crash":         "",
    "no-display":    "there is no screen up; do not offer to open applications until there is",
    "not-installed": "this is not installed on this PC; say so instead of offering to open it",
    "unknown-app":   "this app is not in the catalogue; do not claim you can open it",
    "ambiguous":     "this name matches several apps; ask which one rather than guessing",
    "launch-failed": "",
    "unknown-tool":  "",
}


def _reflect(pending: Pending, outcome: Outcome) -> None:
    """Write a failed OS action to `vault/reflections.md`. **Never raises.**

    The narrow hook that `engine/core._reflect_on_failure` cannot cover: none of these RAISE.
    `run_command` catches everything and reports it as an `Outcome`, on purpose — that is the
    whole point of `os_controller`'s "what happened is STATED, not re-parsed" section — so a
    command that failed is a perfectly successful Python call, and the broad exception hook
    never sees it. This is where the OS route's mistakes actually are.
    """
    lesson = _WORTH_REMEMBERING.get(outcome.kind)
    if outcome.ok or lesson is None:
        return
    try:
        from tools import reflections
        subject = pending.shown.splitlines()[0] if pending.shown else outcome.subject
        reflections.note(
            kind=f"os/{outcome.kind}",
            what=("open " if pending.tool == "launch_app" else "run ") + f"`{subject}`",
            why=" ".join(outcome.detail.split())[:200] or outcome.kind,
            lesson=lesson)
    except Exception:                                                     # noqa: BLE001
        # A ledger failure must not cost the report of what actually happened.
        pass


def resume_os_action(pending: Pending) -> Response:
    """Run the approved action and report what happened.

    Only ever called after `orchestrator.classify_yes.is_yes` returned True.
    """
    resume = _RESUME.get(pending.tool)
    if resume is None:
        # An unrecognised tool refuses. Distinct from a LEGACY Pending with no tool at all,
        # which takes the field's default and reaches the shell exactly as it always did.
        outcome = Outcome(ok=False, kind="unknown-tool", detail=pending.tool)
    else:
        outcome = resume(pending.tool_args)

    _reflect(pending, outcome)

    launched = pending.tool == "launch_app"
    return Response(
        speech=_speech_for(outcome),
        cards=[
            Card(CardKind.CODE, "Opened" if launched else "Ran", pending.shown, "bash"),
            # ERROR styling for anything that did not happen, refusals included. The card being
            # obvious is right either way; the SENTENCE is what distinguishes "I refused" from
            # "it broke", and that distinction now lives in `_SPEECH` where it can be tested.
            Card(CardKind.ERROR if not outcome.ok else CardKind.LOG, "Output", outcome.text),
        ],
        route="os",
        raw=f"OS Execution Result:\n{outcome.text}")


def run_os_agent(query: str) -> str:
    """The old blocking entry point, kept for `main.py --text` and the existing tests.

    Deliberately NOT the path the voice loop takes. It still reads stdin, which is correct for
    a terminal session and impossible everywhere else.

    ## The camera, added 2026-08-21

    A thumbs up at the camera counts as the `y`. `tools/gesture_control.py` owns that decision
    and only a thumbs up returns True — no camera, no hand, an open palm and any exception all
    fall through to `input()`, so the worst a broken camera can do is make LB type the letter
    he was already typing. It never *declines* on his behalf either; the keyboard still gets
    asked.

    What it does not change: the blocklist in `tools/os_controller.py` runs regardless of how
    approval arrived, and the exact command is on screen before the question. A gesture
    replaces the keystroke, not the review. Set `ODDBALL_GESTURE=0` to keep the camera shut.
    """
    proposed = propose_os_action(query)
    if proposed.pending is None:
        return proposed.raw or proposed.speech

    # The exact command is printed BEFORE approval is asked for, whichever way it arrives.
    # That ordering is the property D4 is about and the camera does not change it.
    print("\n⚠️ SECURITY CHECK: The AI wants to execute:")
    print(f"   > {proposed.pending.shown}")
    print("   👍 thumbs up at the camera to approve, or answer below.")

    from tools.gesture_control import approve_by_gesture_or_keyboard

    if approve_by_gesture_or_keyboard("   Allow execution? (y/n): "):
        return resume_os_action(proposed.pending).raw
    return "Action aborted by the user. No terminal commands were executed."
