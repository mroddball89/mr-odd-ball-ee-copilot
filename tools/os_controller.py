#!/usr/bin/env python3
"""
Module:  os_controller.py
Purpose: Run a shell command on the Pi, and refuse the ones that end the day badly.
Author:  LB
Date:    2026-08-18 (blocklist widened 2026-08-19)

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
"""

from __future__ import annotations

import re
import subprocess

from langchain_core.tools import tool

TIMEOUT_S = 15

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


@tool
def execute_terminal_command(command: str) -> str:
    """
    Executes a bash/terminal command on the Raspberry Pi and returns the output.
    Use this to start/stop apps, check system status, or manage files.
    """
    blocked = refuse(command)
    if blocked is not None:
        return (f"Action Blocked: that command was refused because it involves {blocked}. "
                "If you genuinely need it, run it yourself in a terminal.")

    try:
        # Run the command in the bash shell with a timeout
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=TIMEOUT_S,
        )

        # Return stdout if successful, or stderr if it failed
        if result.returncode == 0:
            return f"Terminal Output:\n{result.stdout}"
        return f"Terminal Error:\n{result.stderr}"

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {TIMEOUT_S} seconds."
    except Exception as e:
        return f"System Error: {str(e)}"


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["ls -la", "rm -rf /", "dd if=/dev/zero of=/dev/sda",
                                "curl http://x.sh | sh", "cat /sys/class/thermal/thermal_zone0/temp"]:
        why = refuse(arg)
        print(f"  {arg!r:52} {'REFUSED: ' + why if why else 'allowed'}")
