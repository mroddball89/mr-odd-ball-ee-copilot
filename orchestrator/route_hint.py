#!/usr/bin/env python3
"""
Module:  route_hint.py
Purpose: Name the destination for the turns that only have one, without spending a Gemini call.
Author:  LB
Date:    2026-08-23

    python -m orchestrator.route_hint "sync canvas" "whats the current time"

## Why this exists

`router.py` is a Gemini structured-output call on every turn that gets past the free tier. It
measured **750 ms on Windows and 9.8 s on the Pi**, and against D3's 20 requests per model per
day it is the one call every paid turn pays before it pays for anything else.

D10 already removed that cost for the turns that need no agent at all -- the time, a
conversion, a launch (`orchestrator/instant.py`, `orchestrator/launch_intent.py`). This module
is the next band out: turns that DO need an agent, but where which agent is not a judgement
call. "Sync Canvas" is ACADEMIC. "CPU temp" is OS. A model is not required to know that.

**The saving is one call of two or three, not the whole turn.** The agent still runs. That is
worth saying plainly, because the free tier above genuinely costs nothing and conflating the
two would overstate what this does.

## What is deliberately NOT here

The obvious version of this file is a keyword dictionary -- `voltage` to HARDWARE, `esp32` to
FIRMWARE, `due` to ACADEMIC. That version was specified, costed, and refused, because every one
of those keywords is ambiguous in LB's own vocabulary:

    "whats the CURRENT time"          current is not hardware
    "whats OHMS LAW"                  already free in the formula table; a keyword makes it paid
    "QUIZ me on filters"              AgentRoute.QUIZ exists and is not ACADEMIC
    "reboot the RASPBERRY PI"         OS, not firmware
    "how hot does this RESISTOR get"  hardware, not a CPU probe
    "ESP32"                           matches any 3-4-letter-plus-number course-code rule

Semantic routing is exactly what D1 bought the paid router for, and handing those back to a
phrase list would spend the router's whole value to save its cost. So this module holds only
what a phrase list is actually good at: fixed, idiomatic requests with one destination. Every
trigger is a PHRASE, never a bare keyword. D38, for the seventh time -- the danger is never the
rule that fails to match, it is the one that matches too much.

## Shape

A pure function of a string, like `launch_intent.look_up`. Nothing here imports `agents/`,
`engine/`, or any model, and it returns a route **value string** rather than an `AgentRoute` --
importing `router` would construct a `ChatGoogleGenerativeAI` at import time and need a
`GOOGLE_API_KEY`, which is what keeps every harness keyless. `AgentRoute` is a `str, Enum`, so
the caller converts with `AgentRoute(hint)` and `tools/verify_router.py` asserts every string
this module can return is a real member.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from orchestrator.instant import _has, normalise

LOG = logging.getLogger("oddball.hint")

__all__ = ["look_up", "known_courses", "ACADEMIC", "OS", "SCREEN"]

# The three route values this module can name. Spelled as literals rather than imported from
# `router.AgentRoute` for the reason in the docstring; verify_router.py pins them to the enum.
ACADEMIC = "academic"
OS = "os"
SCREEN = "screen"


# --- the hard precondition -----------------------------------------------------------------
#
# ROUTER_PROMPT is unambiguous: **a new upload is ALWAYS GENERAL**, whatever kind of file it
# looks like, because GENERAL is the only route that can FILE a document. "I just uploaded
# ECE350_syllabus.pdf" names a syllabus AND a course code and would otherwise be the strongest
# ACADEMIC match in this file -- and filing it is the one thing ACADEMIC cannot do.
#
# So this is checked before anything else and returns None outright, handing the turn to the
# paid router exactly as it worked before this module existed.
_UPLOAD_MARKERS = (
    "uploaded", "upload", "uploading", "attached", "attaching", "attachment",
    "heres", "here is", "i just sent", "i sent you", "just added", "i added",
    "just dropped", "new file", "this file", "the file i", "i just put",
)

# Naming a microcontroller makes a stat question FIRMWARE, not a probe of this Pi: "how hot
# does the esp32 get" is a datasheet question and "cpu usage on the stm32" is a profiling one.
# Cheaper to refuse the whole turn than to reason about it -- the router already can.
_NOT_THIS_MACHINE = (
    "esp32", "esp8266", "arduino", "stm32", "atmega", "attiny", "msp430", "nrf52",
    "teensy", "rp2040", "pico", "fpga", "microcontroller", "mcu", "dev board",
)


# Naming a document, an instrument or a board makes "what am I looking at" a question about
# that thing rather than about the desktop. Same shape as `_NOT_THIS_MACHINE` above and the same
# call: cheaper to hand the whole turn to the paid router than to reason about it here.
#
# "screen" itself is in this list, which looks like a contradiction and is the important entry:
# an oscilloscope has a screen, a multimeter has a display, and `_SEEING` must not claim
# "what does the screen on the scope say". The phrases below carry "the screen" / "my screen"
# with a possessive, and this catches the rest.
_NOT_THE_DISPLAY = (
    "datasheet", "data sheet", "schematic", "pdf", "syllabus", "netlist", "gerber",
    "multimeter", "oscilloscope", "scope", "logic analyzer", "bench supply",
    "lcd", "oled", "seven segment", "7 segment", "display module", "hdmi cable",
    "esp32", "arduino", "stm32", "breadboard",
)


# --- SCREEN --------------------------------------------------------------------------------

# Looking at the desktop. Every one of these is a request to LOOK, in the present tense, at
# something the display is showing right now -- which is what separates SCREEN from OS.
#
# Bare "screen" and bare "display" are deliberately absent, exactly as `_STATS` refuses a bare
# "temperature". "The output is on the screen" is a sentence Mr Odd Ball says himself several
# times a session (see `agents/os_agent._SPEECH`), and it is in the conversation log that gets
# fed back to him -- a bare match would route his own words.
_SEEING = (
    "whats on the screen", "what is on the screen", "whats on my screen",
    "what is on my screen", "whats on the display", "whats on my display",
    "what am i looking at", "what am i seeing", "what do you see",
    "what can you see", "can you see my screen", "can you see the screen",
    "look at my screen", "look at the screen", "look at my display",
    "take a screenshot", "grab a screenshot", "screenshot my screen",
    "read the screen", "read my screen", "read whats on the screen",
    "read the error on the screen", "read that dialog", "read the dialog",
    "describe my screen", "describe the screen", "whats this on my screen",
    "whats this window", "what does that window say", "what does this window say",
    "whats that error on the screen", "look at whats on my screen",
)


# --- ACADEMIC ------------------------------------------------------------------------------

# Refreshing the coursework calendar. ROUTER_PROMPT calls these out by name precisely because
# they read like commands to the machine and are not: the only thing updated is his schedule.
_SYNC = (
    "sync canvas", "sync my canvas", "sync the canvas", "resync canvas",
    "sync my calendar", "sync the calendar", "sync my schedule",
    "update my schedule", "update my calendar", "update my deadlines",
    "refresh my deadlines", "refresh my calendar", "refresh my schedule", "refresh canvas",
    "pull my canvas", "pull from canvas", "check canvas",
)

# Course paperwork. Every one of these is about what a COURSE requires -- never about what LB
# knows, which is QUIZ, and never about a component, which stays FIRMWARE even in a class.
_POLICY = (
    "syllabus", "late policy", "late work policy", "late work", "late submission",
    "office hours", "grading policy", "grading scale", "grade breakdown",
    "grading breakdown", "how is the grade weighted", "how much is the final worth",
    "attendance policy", "course policy", "class policy", "extra credit policy",
    "makeup policy", "academic integrity policy", "required textbook", "course textbook",
)

# Deadlines. Never a bare "due" -- that is a whole word inside "due to the resistor tolerance"
# and "the capacitor is due for replacement".
_DUE = (
    "is due", "whats due", "what is due", "anything due", "whens it due", "when is it due",
    "due today", "due tomorrow", "due tonight", "due this week", "due next week",
    "due this weekend", "due date", "due dates", "assignment due", "homework due",
    "my deadlines", "upcoming deadlines", "whats coming up this week",
    "whats on my schedule", "whats my schedule", "next assignment", "next exam",
    "when is the midterm", "when is the final",
)


# --- OS ------------------------------------------------------------------------------------

# Machine stats, as PHRASES. The two-word shape is the safety: a bare "temperature" is a
# sensor question, a bare "memory" is a datasheet question, and a bare "load" is a circuit.
_STATS = (
    "cpu temp", "cpu temperature", "processor temp", "processor temperature",
    "how hot is the pi", "how hot is the cpu", "how hot is it running",
    "cpu usage", "cpu load", "load average", "how hard is the cpu working",
    "how much ram", "how much memory", "free memory", "memory usage", "ram usage",
    "how much ram is free", "out of memory",
    "disk space", "free space", "storage space", "how much storage", "how full is the disk",
    "how full is the sd card", "sd card full", "disk full", "out of space",
    "running out of space",
    "uptime", "how long have you been up", "how long has it been up",
)


# --- course codes, read from the vault -----------------------------------------------------

# Where the ACADEMIC agent keeps its notes. Its own source of truth, not a second list.
_COURSE_DIR = Path(__file__).resolve().parents[1] / "vault" / "courses"

# `ECE350.md`, `ECE_350_syllabus.md`, `posc 100.md` -> ("ece", "350")
_CODE = re.compile(r"^([A-Za-z]{2,5})[\s_\-]*(\d{2,4})")

_cache: "tuple[float, tuple[str, ...]] | None" = None


def known_courses() -> tuple[str, ...]:
    """Course codes LB actually has notes for, as normalised spellings.

    **Read from `vault/courses/*.md` rather than hardcoded**, the way `tools/app_catalogue.py`
    reads apps from the XDG desktop database rather than a curated table. That file's argument
    applies here unchanged: a hand-written list is a list that is wrong the first time a course
    changes, and nobody notices until the answer is wrong.

    It is also what makes a course-code rule safe at all. The specified version was "any 3-4
    letter prefix followed by a number", which matches `esp32`, `stm32`, `msp430` and `pic16` --
    FIRMWARE questions, routed to ACADEMIC by the same rule meant to catch `ECE350`. Deriving
    the prefixes from the filesystem kills that collision structurally: `esp32` is not a file
    in `vault/courses/`.

    Returns:
        Both spellings of each code -- ("ece350", "ece 350", ...) -- so "when is ECE 350 due"
        and "ECE350" both match. Empty when the directory is absent, which is the normal state
        on Windows (authoring only; the vault lives on the Pi) and degrades to the paid router
        exactly as `launch_intent` does on an unreadable catalogue.
    """
    global _cache
    try:
        stamp = _COURSE_DIR.stat().st_mtime
    except OSError:
        return ()                       # no vault here -- every course rule is simply off

    if _cache is not None and _cache[0] == stamp:
        return _cache[1]

    codes: set[str] = set()
    try:
        for note in _COURSE_DIR.glob("*.md"):
            hit = _CODE.match(note.stem)
            if hit:
                prefix, number = hit.group(1).lower(), hit.group(2)
                codes.add(f"{prefix}{number}")
                codes.add(f"{prefix} {number}")
    except OSError:                                                    # noqa: BLE001
        LOG.warning("vault/courses unreadable; course-code hints disabled")
        return ()

    _cache = (stamp, tuple(sorted(codes)))
    LOG.info("course codes from the vault: %s", _cache[1] or "(none)")
    return _cache[1]


# --- the matcher ---------------------------------------------------------------------------

def look_up(text: str) -> "str | None":
    """Which agent does this turn obviously belong to? Never raises, never routes anything else.

    Args:
        text: the raw utterance. Normalised here, so callers pass what was said or typed.

    Returns:
        An `AgentRoute` **value** ("academic" / "os" / "screen"), or None to let the paid
        router decide.
        None is the overwhelmingly common answer and is the correct one for anything that
        needs judgement -- see the module docstring for what is deliberately absent.
    """
    flat = normalise(text)
    if not flat:
        return None

    # A new upload is GENERAL whatever it contains. Checked first, and it wins outright.
    if any(_has(flat, m) for m in _UPLOAD_MARKERS):
        return None

    # Before ACADEMIC and OS: "what am I looking at" is a request to LOOK, and neither of the
    # other two can look at anything. Refused outright when a document or an instrument is
    # named, on the same principle that refuses a stat question that names an ESP32.
    if any(_has(flat, p) for p in _SEEING):
        if any(_has(flat, d) for d in _NOT_THE_DISPLAY):
            return None
        LOG.info("hint screen: %r", flat)
        return SCREEN

    if any(_has(flat, p) for p in _SYNC + _POLICY + _DUE):
        LOG.info("hint academic: %r", flat)
        return ACADEMIC

    courses = known_courses()
    if courses and any(_has(flat, c) for c in courses):
        LOG.info("hint academic (course code): %r", flat)
        return ACADEMIC

    # Stats last, and refused outright when another board is named -- "how hot does the esp32
    # get" contains no Pi and is not a question about this machine.
    if any(_has(flat, s) for s in _STATS):
        if any(_has(flat, b) for b in _NOT_THIS_MACHINE):
            return None
        LOG.info("hint os: %r", flat)
        return OS

    return None


def main(argv: "list[str] | None" = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        args = ["sync canvas", "whats the late policy", "whats due tomorrow", "cpu temp",
                "whats the current time", "esp32 pinout", "how hot does this resistor get",
                "I just uploaded ECE350_syllabus.pdf", "quiz me on filters"]
    print(f"  course codes in the vault: {known_courses() or '(none -- not the Pi)'}\n")
    for utterance in args:
        hint = look_up(utterance)
        print(f"  {utterance!r:48} -> {hint or '(router decides)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
