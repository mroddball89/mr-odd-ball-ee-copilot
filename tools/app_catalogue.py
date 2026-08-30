#!/usr/bin/env python3
r"""
Module:  app_catalogue.py
Purpose: What applications exist on this machine, and which one did LB mean.
Author:  LB
Date:    2026-08-21

    python tools/app_catalogue.py                 # list what he can open
    python tools/app_catalogue.py "the browser"   # resolve a phrase

## The machine already keeps this list. We read it; we do not curate it.

The source of truth is whatever database the operating system already draws its own menu
from. On the Pi that is the XDG desktop-entry tree — `~/.local/share/applications` and
`/usr/share/applications`. On Windows it is the Start Menu shortcut tree. Same principle,
same code below the loader, and it is the reason "every app, current and future" needs no
code change: `apt install vlc` and VLC is openable; install KiCad 9 and he opens KiCad 9.

**The alternative was tried, twice, and it was already wrong both times.**
`~/oddball/hardware/apps.py` is a hand-written table of three rows, and on 2026-08-21 a `which`
sweep of the Pi found `nautilus` **missing** — so one row in three would have failed exactly
the way that file's own comment predicted: *"he says he opened it and nothing appears."*

The Windows port asked for the same table again, in Windows spelling: KiCad, VS Code and
Firefox at hardcoded `C:\Program Files\...` paths. Measured on LB's workstation 2026-08-26,
that table would have scored one in three as well — KiCad and Firefox are not installed, and
VS Code lives under `AppData\Local\Programs`, not in Program Files. See the note above
`EXCLUDED_FOLDERS` for the full measurement.

A curated list is a second copy of the truth, and a second copy drifts. This one cannot: if
the entry is gone, so is the app.

It also gets the safety boundary for free. `rm`, `dd`, `mkfs` and `bash` have no desktop entry,
so they are not in the catalogue and were never candidates. That is not a filter anybody has to
maintain — it falls out of asking the right question, which is "what are the applications?"
rather than "what is on PATH?"

## What is excluded, and why

`NoDisplay=true` and `Hidden=true` entries are dropped. Those are not applications LB can mean —
they are portals, notification daemons, URL handlers and settings shims. On this Pi that is 36
of the 62 entries. `Type=Application` is required for the same reason: `Link` and `Directory`
entries are not runnable.

Session-ending entries are dropped too, by resolved binary. `pishutdown` ("Logout") is in the
catalogue and would end the desktop session mid-sentence. `tools/os_controller.py` refuses
`shutdown`/`poweroff`/`reboot` on the shell path for exactly this reason and the launcher
inherits none of that, so the same judgement is made here rather than nowhere.

## Matching: spellings and roles, never edit distance

Resolution is exact, in tiers: entry id, then `Name`, then a role word read off `Categories`,
then a unique substring of `Name`. **Anything matching more than one entry is reported as
ambiguous rather than guessed** — two browsers are installed here, and picking one by sort order
is how "open the browser" silently opens the wrong one forever.

**Edit-distance matching is refused on purpose**, and this is inherited reasoning, not new: a
threshold loose enough to catch `tawny -> thonny` is loose enough to catch `shut up -> shut
down`, and the failure mode is not a wrong answer, it is *the wrong program running*.

Role words come from the entry's own `Categories=` key rather than a hand-written synonym list,
so "the browser" keeps working when the browser changes. It resolves to two candidates on this
Pi (Firefox and Chromium) and he asks which — which is the honest answer, not a shortcoming.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("oddball.catalogue")

__all__ = ["DesktopApp", "Match", "load_catalogue", "cached_catalogue", "resolve", "ROLES",
           "start_menu_dirs", "load_start_menu", "parse_shortcut", "shortcut_target",
           "EXCLUDED_FOLDERS", "SESSION_ENDING_WIN", "PROGRAM_ROLES"]

# Which loader `load_catalogue()` uses, and which directories `cached_catalogue()` watches.
# Read once, here, for the same reason `os_controller._IS_WINDOWS` is: a platform test
# scattered through a module is a platform test that will one day disagree with itself.
_IS_WINDOWS = sys.platform == "win32"

if not _IS_WINDOWS:                                                    # pragma: no cover
    # LOUD, at import. The XDG reader was DELETED 2026-08-26, not disabled — see the same
    # guard in `tools/os_controller.py` for the reasoning. An empty catalogue here would make
    # `resolve()` answer "no such application" for everything LB owns, and `launch()` would
    # report `unknown-app` forever with nothing anywhere saying why.
    raise ImportError(
        f"tools/app_catalogue.py reads the Windows Start Menu and this is {sys.platform}. "
        f"The XDG desktop-entry reader was deleted, not disabled; restore `_load_xdg`, "
        f"`parse_entry`, `exec_argv` and `catalogue_dirs` from git history to run on the Pi.")


# Role words — "the schematic editor" rather than a program's name.
#
# **This table lost five of its seven rows in the Windows port, and then got a different kind
# of row back on 2026-08-26. Both halves are worth recording.**
#
# On the Pi these were read off each entry's own `Categories=` key, which is part of the XDG
# spec and genuinely describes what a program IS: `TextEditor`, `FileManager`, `Calculator`.
# The machine told us the kind of thing, and no synonym list had to be maintained.
#
# **Windows records no such thing.** The nearest field is the containing Start Menu folder, and
# folders are named after VENDORS — "Autodesk", "Git", "NVIDIA Corporation", "Python 3.12".
# Enumerated on LB's machine 2026-08-26: not one of the 70 shortcuts sits in a folder that says
# what kind of program it is. So `editor`, `terminal`, `calculator` and `file manager` were
# removed: four rows that could never match, occupying the role tier and stopping resolution
# falling through to the name tiers that WOULD have found the program.
#
# `PROGRAM_ROLES` below is what puts them back, and it is a curated table in a module whose
# docstring refuses curated tables. That is not an inconsistency, and the distinction is the
# same one already drawn for `ALIASES`:
#
#     The table this module refuses says WHICH APPS EXIST. It went stale the moment one was
#     installed — `~/oddball/hardware/apps.py` shipped without `nautilus`, and the Windows
#     version of the same idea would have hardcoded a KiCad that is not installed and a VS
#     Code at the wrong path.
#
#     PROGRAM_ROLES says WHAT KIND OF THING a program is, IF it is here. It claims nothing
#     about what exists. "the schematic editor" with KiCad uninstalled resolves to nothing,
#     exactly as "eeschema" does. The catalogue is still the only source of truth about what
#     is on the machine; this only supplies the adjective the Start Menu failed to.
#
# **Keyed on the executable, not on the display name**, and that is the part that makes it
# durable. A Start Menu name carries a version — "KiCad 8.0", "Creality Print 7.2", "Python
# 3.12 (64-bit)" — and goes stale on the next update, which is the precise failure mode of
# hardcoding `C:\Program Files\KiCad\8.0\bin\kicad.exe`. `kicad.exe` has been `kicad.exe` for
# twenty years. Matched on the stem, case-folded, so `Code.exe` and `code.exe` are one row.
#
# **What happens to a tool that is not listed:** nothing bad. It is still in the catalogue,
# still resolves by name, still launches. It is only unreachable by ROLE — "open the slicer"
# will not find it until somebody adds a row. That is a graceful degradation and the reason
# this table can stay short: it is an accelerator for the dozen tools LB actually says out
# loud, not a registry that has to be complete to be correct.
ROLES: dict[str, str] = {
    # --- the one Windows answers for itself -------------------------------------------------
    # Read from the registry's default-browser association by `_default_browser_target()` and
    # tagged in `load_start_menu()`. Better than the Pi managed: there, two browsers were
    # installed and he had to ask which; here the OS records a preference.
    "browser": "WebBrowser",
    "web browser": "WebBrowser",

    # --- LB's lab stack ---------------------------------------------------------------------
    "eda": "EDA",
    "eda tool": "EDA",
    "pcb tool": "EDA",
    "schematic editor": "SchematicEditor",
    "pcb editor": "PCBEditor",
    "layout editor": "PCBEditor",
    "gerber viewer": "GerberViewer",

    "cad": "CAD",
    "cad program": "CAD",
    "modeller": "CAD",
    "modeler": "CAD",

    "simulator": "Simulator",
    "spice": "Simulator",
    "circuit simulator": "Simulator",

    "slicer": "Slicer",
    "printer software": "Slicer",

    "editor": "TextEditor",
    "text editor": "TextEditor",
    "code editor": "TextEditor",
    "ide": "IDE",

    "terminal": "Terminal",
    "shell": "Terminal",
    "command prompt": "Terminal",

    "serial monitor": "SerialTerminal",
    "serial terminal": "SerialTerminal",

    "logic analyzer": "LogicAnalyzer",
    "logic analyser": "LogicAnalyzer",

    "calculator": "Calculator",
    "file manager": "FileManager",
    "files": "FileManager",
    "file explorer": "FileManager",
}

# Executable stem (lower-case, no extension) -> the role tokens it satisfies.
#
# One program may be several things: KiCad's own launcher is the EDA suite AND the thing LB
# means by "kicad", and Arduino IDE is both an IDE and how he talks to a board. Values are
# tuples for that reason, and every token here must appear as a VALUE in `ROLES` above or it
# is unreachable — `tools/verify_launch.py` asserts exactly that, because a token with no
# spoken phrase pointing at it is a row that can never fire.
PROGRAM_ROLES: dict[str, tuple[str, ...]] = {
    # --- EDA. The KiCad binaries are individually named because LB asks for them
    #     individually: "open the pcb editor" must not open the schematic editor.
    "kicad": ("EDA",),
    "eeschema": ("EDA", "SchematicEditor"),
    "pcbnew": ("EDA", "PCBEditor"),
    "gerbview": ("EDA", "GerberViewer"),
    "pcbcalculator": ("EDA", "Calculator"),
    "pcb_calculator": ("EDA", "Calculator"),
    "bitmap2component": ("EDA",),
    "eagle": ("EDA",),
    "altium": ("EDA", "PCBEditor"),
    "diptrace": ("EDA", "PCBEditor"),
    "fritzing": ("EDA",),

    # --- SPICE and simulation. LTspice ships as XVIIx64.exe, which nobody would guess.
    "ltspice": ("Simulator",),
    "xviix64": ("Simulator",),
    "ngspice": ("Simulator",),
    "qucs": ("Simulator",),
    "falstad": ("Simulator",),

    # --- CAD and mechanical
    "openscad": ("CAD",),
    "freecad": ("CAD",),
    "fusionlauncher": ("CAD",),
    "fusion360": ("CAD",),
    "solidworks": ("CAD",),
    "blender": ("CAD",),

    # --- slicers, for the printers
    "crealityprint": ("Slicer",),
    "anycubicslicernext": ("Slicer",),
    "prusa-slicer": ("Slicer",),
    "prusaslicer": ("Slicer",),
    "orca-slicer": ("Slicer",),
    "orcaslicer": ("Slicer",),
    "cura": ("Slicer",),
    "bambustudio": ("Slicer",),

    # --- editors and IDEs
    "code": ("TextEditor", "IDE"),
    "code - insiders": ("TextEditor", "IDE"),
    "devenv": ("IDE",),
    "pycharm64": ("TextEditor", "IDE"),
    "idea64": ("TextEditor", "IDE"),
    "sublime_text": ("TextEditor",),
    "notepad++": ("TextEditor",),
    "notepad": ("TextEditor",),
    "gvim": ("TextEditor",),
    "opencode": ("TextEditor", "IDE"),

    # --- embedded toolchains
    "arduino ide": ("IDE",),
    "arduino": ("IDE",),
    "thonny": ("TextEditor", "IDE"),
    "platformio": ("IDE",),
    "stm32cubeide": ("IDE",),
    "mplab_ide": ("IDE",),
    "energia": ("IDE",),

    # --- serial and instruments
    "putty": ("SerialTerminal", "Terminal"),
    "ttermpro": ("SerialTerminal",),          # Tera Term
    "coolterm": ("SerialTerminal",),
    "realterm": ("SerialTerminal",),
    "logic": ("LogicAnalyzer",),              # Saleae
    "pulseview": ("LogicAnalyzer",),
    "wireshark": ("LogicAnalyzer",),

    # --- shells. `cmd` is deliberately ABSENT: several Start Menu entries point at cmd.exe
    #     with different arguments ("Node.js command prompt"), so tagging it would make
    #     "the terminal" ambiguous between things that are not really terminals.
    "windowsterminal": ("Terminal",),
    "wt": ("Terminal",),
    "powershell": ("Terminal",),
    "pwsh": ("Terminal",),
    "git-bash": ("Terminal",),

    # --- the rest of the desktop
    "explorer": ("FileManager",),
    "calculatorapp": ("Calculator",),
    "win32calc": ("Calculator",),
    "matlab": ("Simulator",),
    "octave-gui": ("Simulator",),
}

# Spoken nicknames for apps whose `Name=` nobody says out loud. Applied to the QUERY before
# the tiers, so the catalogue stays the only source of truth about what is installed.
#
# **This is not the hardcoded app table this module refuses**, and the difference is worth
# being precise about. That table listed WHICH APPS EXIST and went stale the moment one was
# installed (it shipped without `nautilus`). This maps what a person SAYS to a phrase the
# catalogue already resolves on its own: if Visual Studio Code is not installed, "vscode"
# resolves to nothing, exactly as "visual studio code" does. `ROLES` above is the same shape.
#
# It is also not edit-distance matching, which is refused here and in `launch_intent` for the
# reason stated there: a threshold loose enough to catch "tawny" -> "thonny" is loose enough to
# catch "shut up" -> "shut down". These are exact keys. Nothing is guessed.
#
# Keep it to nicknames that are genuinely unreachable otherwise. "code" and "visual studio
# code" both already resolve through the entry-id and Name tiers and are deliberately absent.
ALIASES: dict[str, str] = {
    "vscode": "visual studio code",
    "vs code": "visual studio code",
}

_ARTICLES = ("the ", "a ", "an ")

@dataclass(frozen=True)
class DesktopApp:
    """One launchable application, as the machine describes it.

    Args:
        entry_id: the desktop id — the filename without `.desktop` ("org.thonny.Thonny").
        name:     the `Name=` key. What he calls it out loud ("Thonny").
        argv:     the `Exec=` line with field codes stripped and `shlex.split` applied.
                  `argv[0]` is resolved to an absolute path at launch time, not here.
        path:     the file this came from, for the card and the log.
        terminal: `Terminal=true` — the program needs a terminal emulator wrapped around it.
                  Always False on Windows: a `.lnk` carries its own console decision.
        categories: the `Categories=` key, split. Used for role words. On Windows this is the
                  containing Start Menu folder chain, plus the target's stem.
        target:   **Windows only.** The program the shortcut points at, for the "is it
                  actually installed" check. `""` when the shortcut is a shell-namespace link
                  with no local path — a normal answer, not a failure, and the launcher skips
                  the existence check rather than guessing. Always `""` on the Pi, where
                  `argv[0]` already IS the program.
    """

    entry_id: str
    name: str
    argv: tuple[str, ...]
    path: str
    terminal: bool = False
    categories: tuple[str, ...] = ()
    target: str = ""

    @property
    def program(self) -> str:
        """The binary this entry runs, with no directory part — for the exclusion check."""
        return Path(self.argv[0]).name if self.argv else ""


@dataclass(frozen=True)
class Match:
    """The result of asking "which app is this?".

    Exactly one of `app` / `candidates` is meaningful:
      - `app` set                -> resolved
      - `candidates` non-empty   -> ambiguous, and these are the ones that matched
      - both empty               -> nothing matched
    """

    app: DesktopApp | None = None
    candidates: tuple[DesktopApp, ...] = ()

    @property
    def ok(self) -> bool:
        return self.app is not None

    @property
    def ambiguous(self) -> bool:
        return self.app is None and bool(self.candidates)


def load_catalogue(dirs: tuple[Path, ...] | None = None) -> tuple[DesktopApp, ...]:
    """Every application this machine can open, sorted by name.

    The one entry point, on both platforms. It dispatches to the reader for the list this
    operating system actually keeps — `load_start_menu()` on Windows, `_load_xdg()` on the Pi
    — and both hand back the same `DesktopApp`, so everything downstream is platform-free.

    Args:
        dirs: where to look. Defaults to the right directories for this platform. The harness
              passes a temporary directory, which is what keeps both readers testable on a box
              that has neither tree.

    Returns:
        The catalogue. Empty is a legitimate answer (a machine with no desktop), not an error.
    """
    return load_start_menu(dirs)


_CACHE: tuple[DesktopApp, ...] | None = None
_CACHE_KEY: tuple | None = None


def cached_catalogue() -> tuple[DesktopApp, ...]:
    """`load_catalogue()`, re-read only when the applications directories change.

    `orchestrator/launch_intent.py` consults this on **every utterance**, and reading 60 files
    to answer "was that a launch request?" would be absurd. A directory's mtime changes when a
    `.desktop` file is added or removed, so `apt install vlc` is still picked up without a
    restart — which is the property that makes "every app, current and future" true rather
    than merely intended.

    Not `functools.lru_cache`: the cache key has to be the directories' mtimes, not the
    arguments, and there are none.
    """
    global _CACHE, _CACHE_KEY

    dirs = start_menu_dirs()
    key = []
    for d in dirs:
        try:
            key.append((str(d), d.stat().st_mtime))
        except OSError:
            key.append((str(d), 0.0))
        # Windows nests: an installer drops "Autodesk\Fusion.lnk", which changes the mtime of
        # the SUBFOLDER and not of the root. Watching only the root would miss every install
        # into a vendor folder — which is most of them — so the subfolders are in the key too.
        # One level deep: that is where installers put things, and walking the whole tree on
        # every utterance is the cost this cache exists to avoid.
        try:
            for sub in sorted(d.iterdir()):
                if sub.is_dir():
                    key.append((str(sub), sub.stat().st_mtime))
        except OSError:
            pass
    frozen = tuple(key)

    if _CACHE is None or frozen != _CACHE_KEY:
        _CACHE, _CACHE_KEY = load_catalogue(dirs), frozen
        LOG.info("catalogue loaded: %d application(s)", len(_CACHE))
    return _CACHE



# =========================================================================================
# THE WINDOWS BACKEND — the Start Menu is this platform's desktop-entry database
# =========================================================================================
#
# Everything above this line is the XDG reader and is unchanged by the Windows port. What
# follows reads the same KIND of thing from the place Windows keeps it, and hands back the
# same `DesktopApp` — so `resolve()`, `ROLES`, `ALIASES`, the ambiguity refusal and the
# whole-word phrase matching below are shared, byte for byte, by both platforms.
#
# ## Why this, and not the table of paths that was asked for
#
# The brief for the Windows port asked for `C:\Program Files\KiCad\8.0\bin\kicad.exe` and
# similar, hardcoded. That is precisely the thing this module's docstring exists to refuse,
# and the argument is not theoretical on this machine — it was re-measured on 2026-08-26,
# against LB's own three examples:
#
#     KiCad         NOT INSTALLED on this box. A hardcoded path would be a launcher for a
#                   program that is not there, failing exactly the way `nautilus` failed on
#                   the Pi: "he says he opened it and nothing appears."
#     Firefox       NOT INSTALLED either. Only Edge is. "Open the browser" must resolve to
#                   what is actually here.
#     Visual Studio Code
#                   Installed — at C:\Users\ironi\AppData\Local\Programs\Microsoft VS Code\
#                   Code.exe, which is a per-user install under AppData and NOT the
#                   C:\Program Files path anybody would have written down.
#
# One of three would have worked, which is the same score the Pi's hand-written table got.
# And `\8.0\` in the path is a version number: it goes stale on the next KiCad update, which
# is a second, slower way for the same list to become a lie.
#
# ## The mapping is close to exact
#
#     %ProgramData%\Microsoft\Windows\Start Menu\Programs   ==  /usr/share/applications
#     %AppData%\Microsoft\Windows\Start Menu\Programs       ==  ~/.local/share/applications
#
#     .desktop `Name=`         ->  the .lnk filename stem, which is what LB says out loud
#     .desktop `Exec=`         ->  the shortcut's target
#     .desktop `Categories=`   ->  the containing Start Menu folder
#     user shadows system      ->  same rule, same order
#
# ## Two things Windows does BETTER here, and one it does worse
#
# Better: `os.startfile()` on the `.lnk` makes Windows resolve the target, the arguments, the
# working directory and any elevation prompt itself. There is no `Exec=` line to parse, no
# field codes to strip, and no `Terminal=true` case needing a terminal emulator wrapped round
# it — the shortcut already knows all of that. `exec_argv()` has no Windows counterpart and
# needs none.
#
# Better: "the browser" has a real answer. The Pi found two browsers and had to ask which;
# Windows records a DEFAULT browser in the registry, so `ROLES` can name it. See
# `_default_browser_target()`.
#
# Worse: 38 of this machine's 70 shortcuts carry no parseable local target — they are shell
# namespace links (Control Panel applets, the Administrative Tools set) whose "target" is an
# IDList rather than a path. They are still launchable, because `os.startfile` resolves an
# IDList perfectly well. What is lost is the ability to check that the target EXISTS before
# launching, which is the `nautilus` check. That is a real gap and it is handled honestly:
# `DesktopApp.target` is `""` for those, and the launcher skips the existence check rather
# than guessing, because refusing to launch something merely because we could not read its
# target would break every Control Panel entry on the machine.

# Start Menu folders excluded wholesale. The XDG reader gets this for free from
# `NoDisplay=true`; Windows has no such flag, so the judgement is made by folder — which is
# the same judgement Windows itself makes when it hides these from search by default.
#
# "Administrative Tools" is the significant one. It holds Disk Cleanup, Defragment, Event
# Viewer, iSCSI Initiator, ODBC and Computer Management: none is an application LB would ask
# for out loud, several can end a session or reformat something, and every one of them would
# otherwise be a `resolve()` candidate competing with real programs.
EXCLUDED_FOLDERS: frozenset[str] = frozenset({
    "administrative tools",
    "windows administrative tools",
    "windows tools",
    "windows system",
    "accessibility",
    # NOT "windows powershell". It was excluded in the first draft alongside the system
    # folders, and that was wrong: it holds four ordinary shells LB might reasonably ask for,
    # and excluding it made "open the terminal" resolve to nothing on a machine with several.
    # The system folders above are excluded because their contents END SESSIONS or edit the
    # registry; a shell does neither. `os_controller`'s blocklist is what guards what gets
    # TYPED into one, and it is unaffected by whether the window can be opened.
    "startup",                    # things that already start themselves — including him
    "maintenance",
})

# Dropped by shortcut NAME, case-folded, matched as a whole word run. The Windows counterpart
# of `SESSION_ENDING` — same reasoning, different vocabulary — plus the uninstallers, which
# have no Linux analogue because `.desktop` files are not shipped for them.
#
# `Uninstall` is the one worth pausing on: this machine has FOUR shortcuts named exactly
# "Uninstall" (Creality Print, OBS, OrcaSlicer, and Node.js). Without this they would make
# every one of those a four-way ambiguity, and the resolution of "uninstall" would be a
# coin toss between four programs that each remove a different thing.
SESSION_ENDING_WIN: frozenset[str] = frozenset({
    "shut down", "shutdown", "restart", "sign out", "log off", "logoff",
    "uninstall", "uninstaller", "disk cleanup", "defragment and optimize drives",
    "diskpart", "recovery", "reset this pc",
})


def start_menu_dirs(environ: dict | None = None) -> tuple[Path, ...]:
    """Where Windows keeps its application list, most specific first.

    Order is load-bearing for the same reason `catalogue_dirs()`'s is: a per-user shortcut must
    shadow a machine-wide one with the same name. Windows itself merges the two trees this way
    when it draws the Start Menu.

    Args:
        environ: the environment to read. Defaults to `os.environ`; the harness passes a dict
                 so this is testable on a box with no Start Menu at all.
    """
    import os

    # CASE-FOLDED, and this is a real bug that was caught rather than a precaution.
    #
    # `os.environ` on Windows is case-INsensitive: `os.environ["AppData"]` works even though
    # the key is stored as `APPDATA`. `dict(os.environ)` throws that away — it produces a
    # plain dict whose keys are the uppercase spellings — so the obvious
    # `env.get("AppData")` returned None and this function returned an empty tuple. The
    # symptom was a catalogue of ZERO applications on a machine with 70 shortcuts, with no
    # error anywhere: `load_start_menu` iterated an empty list of directories and succeeded.
    #
    # The XDG version above does not have this problem only because XDG variable names are
    # already uppercase, so it never noticed the same conversion happening.
    src = os.environ if environ is None else environ
    env = {k.upper(): v for k, v in src.items()}

    out: list[Path] = []
    # User first — a per-user shortcut must shadow a machine-wide one. `APPDATA` is Roaming,
    # which is where the per-user Start Menu lives; it is NOT `LOCALAPPDATA`, and using the
    # wrong one silently finds nothing.
    for key in ("APPDATA", "PROGRAMDATA"):
        root = env.get(key)
        if root:
            out.append(Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return tuple(out)


def shortcut_target(path: Path) -> str:
    """The program a `.lnk` points at, or "" if it does not name a local one.

    A minimal MS-SHLLINK reader: header, then the optional LinkTargetIDList, then the LinkInfo
    block's LocalBasePath. Deliberately NOT a full parser — the only field wanted is the target
    path, for two checks (`SESSION_ENDING_WIN` by binary, and "is it actually installed").

    **No dependency, on purpose.** The obvious alternatives are `pywin32`'s
    `WScript.Shell.CreateShortcut` or shelling out to PowerShell. The first adds a 10 MB
    package to the Pi's requirements for a Windows-only field; the second spawns a PowerShell
    process per shortcut, and `cached_catalogue()` is consulted on every utterance. Sixty-eight
    subprocesses to answer "was that a launch request?" is not a trade worth making.

    Returns:
        An absolute path, or "". **"" is a normal answer, not a failure** — 38 of the 70
        shortcuts on LB's machine are shell-namespace links with an IDList and no path, and
        they launch perfectly well through `os.startfile`. Never raises: a malformed shortcut
        costs its own row, not the catalogue.
    """
    import struct

    try:
        data = path.read_bytes()
    except OSError:
        return ""

    # HeaderSize is 0x0000004C and is the file's magic in practice.
    if len(data) < 76 or data[:4] != b"\x4c\x00\x00\x00":
        return ""
    try:
        flags = struct.unpack_from("<I", data, 20)[0]
        off = 76
        if flags & 0x1:                                   # HasLinkTargetIDList
            off += 2 + struct.unpack_from("<H", data, off)[0]
        if not (flags & 0x2):                             # HasLinkInfo — no path to read
            return ""
        if off + 12 > len(data):
            return ""
        info_size, header_size = struct.unpack_from("<II", data, off)
        if info_size <= 0 or off + info_size > len(data):
            return ""
        info_flags = struct.unpack_from("<I", data, off + 8)[0]
        if not (info_flags & 0x1):                        # VolumeIDAndLocalBasePath absent
            return ""

        def _zstr(start: int, wide: bool) -> str:
            if wide:
                end = start
                while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
                    end += 2
                return data[start:end].decode("utf-16-le", "replace")
            end = data.index(b"\x00", start)
            return data[start:end].decode("mbcs", "replace")

        # The Unicode fields exist only in the larger, optional form of the LinkInfo header.
        # Preferring them matters for any install path with a non-ANSI character in it.
        if header_size >= 0x24:
            base = _zstr(off + struct.unpack_from("<I", data, off + 28)[0], True)
            suffix = _zstr(off + struct.unpack_from("<I", data, off + 32)[0], True)
        else:
            base = _zstr(off + struct.unpack_from("<I", data, off + 16)[0], False)
            suffix = _zstr(off + struct.unpack_from("<I", data, off + 24)[0], False)
        return base + suffix
    except (struct.error, ValueError, IndexError):
        # A shortcut this cannot read is a shortcut launched without the existence check, not
        # a shortcut dropped. Same judgement as a malformed .desktop file: one bad row must
        # never cost LB the whole catalogue.
        LOG.debug("unreadable shortcut: %s", path)
        return ""


def parse_shortcut(path: Path, root: Path) -> "DesktopApp | None":
    """One `.lnk` to a `DesktopApp`, or None if it is not something LB can mean.

    The Windows counterpart of `parse_entry()`. Args and return shape match it deliberately,
    so `resolve()` cannot tell which platform built the row it is matching.

    Args:
        path: the shortcut file.
        root: the Start Menu directory it was found under, so the containing folder can be
              read off as a category.

    Returns:
        A `DesktopApp`, or None when the shortcut is in an excluded folder or names a
        session-ending action. Never raises.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)

    # The folder chain becomes the categories. "Autodesk\Autodesk Fusion.lnk" gets category
    # "Autodesk", which is what `Categories=` gave the XDG reader — weaker (a vendor name, not
    # a role) but the same shape, and `ROLES` no longer depends on it. See
    # `_default_browser_target()`.
    folders = tuple(p.lower() for p in relative.parent.parts if p not in (".", ""))
    if any(f in EXCLUDED_FOLDERS for f in folders):
        return None

    name = path.stem
    flat = _norm(name)
    # Exact match, OR anything that STARTS with an excluded word.
    #
    # The prefix half is not tidiness. With an exact-match-only test, the four bare
    # "Uninstall" shortcuts on this machine were correctly dropped and "Uninstall Node.js"
    # was not — so `resolve("uninstall")` found exactly one whole-word phrase hit and
    # cheerfully offered to run an uninstaller. Removing a program is not "opening" one, and
    # it is precisely the class of action `SESSION_ENDING` exists to keep out of a catalogue
    # that a voice can reach.
    if flat in SESSION_ENDING_WIN or any(flat.startswith(w + " ") for w in SESSION_ENDING_WIN):
        LOG.info("excluding %s (%s ends the session or removes a program)", path.name, name)
        return None

    # A shortcut whose target is a FOLDER, not a program. "Administrative Tools" is one: it is
    # a link into the shell namespace that opens a directory window. Harmless, but it is not an
    # application LB can mean, and it competes in `resolve()` with ones that are.
    if flat in {f.rstrip("s") for f in EXCLUDED_FOLDERS} | EXCLUDED_FOLDERS:
        LOG.info("excluding %s (a link to an excluded folder, not an application)", path.name)
        return None

    target = shortcut_target(path)

    # argv[0] is the SHORTCUT, not the target, and that is the whole trick. `os.startfile` on
    # a .lnk makes Windows resolve the target, its arguments, its working directory and any
    # elevation — all the things `Exec=` had to be parsed for. The target is carried alongside
    # only so the launcher can check it exists.
    return DesktopApp(
        entry_id=_norm(str(relative.with_suffix(""))).replace("\\", " "),
        name=name,
        argv=(str(path),),
        path=str(path),
        terminal=False,          # Windows shortcuts carry their own console decision
        categories=folders + ((Path(target).stem,) if target else ()),
        target=target,
    )


def _default_browser_target() -> str:
    """The user's DEFAULT browser, from the registry, or "" if it cannot be read.

    This is the one place Windows gives a better answer than the Pi did. `ROLES["browser"]`
    on the Pi resolved to two candidates (Firefox and Chromium) and he had to ask which — the
    honest answer there, because nothing on that system recorded a preference.

    Windows does record one. `UserChoice\\ProgId` under the http association is what the OS
    itself uses when something asks for "a browser", so reading it means "open the browser"
    opens the browser LB actually uses, without a synonym table and without a question.

    Returns:
        A lowercased ProgId fragment to match against a shortcut's name or target
        ("firefox", "msedge", "chrome"), or "" when the key is missing. Never raises — a
        registry this cannot read costs the shortcut, and `resolve()` falls back to asking.
    """
    try:
        import winreg
    except ImportError:
        return ""
    try:
        key = (r"Software\Microsoft\Windows\Shell\Associations"
               r"\UrlAssociations\http\UserChoice")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog_id, _ = winreg.QueryValueEx(k, "ProgId")
    except OSError:
        return ""
    # ProgIds look like "MSEdgeHTM", "FirefoxURL-308046B0AF4A39CB", "ChromeHTML". The stable
    # part is the leading vendor token, so it is matched as a prefix rather than parsed.
    prog_id = (prog_id or "").lower()
    for token in ("firefox", "msedge", "edge", "chrome", "brave", "opera", "vivaldi"):
        if prog_id.startswith(token) or token in prog_id:
            return token
    return ""


def load_start_menu(dirs: tuple[Path, ...] | None = None) -> tuple[DesktopApp, ...]:
    """Every application this Windows machine can open, sorted by name.

    The Windows counterpart of `load_catalogue()`'s body. Same shadowing rule: the first
    directory that supplies a given name wins, so a per-user shortcut beats a machine-wide one.

    Args:
        dirs: where to look. Defaults to `start_menu_dirs()`. The harness passes a temporary
              directory, which is what keeps this testable on a box with no Start Menu.

    Returns:
        The catalogue. Empty is a legitimate answer, not an error.
    """
    seen: dict[str, DesktopApp] = {}
    for root in (dirs if dirs is not None else start_menu_dirs()):
        try:
            files = sorted(Path(root).rglob("*.lnk"))
        except OSError:
            continue
        for f in files:
            app = parse_shortcut(f, Path(root))
            if app is None:
                continue
            if _norm(app.name) in seen:          # first directory wins — user shadows machine
                continue
            seen[_norm(app.name)] = app

    apps = tuple(sorted(seen.values(), key=lambda a: a.name.lower()))

    # Fill in the `categories` the Start Menu could not supply, so that `resolve()`'s role tier
    # works here the way `Categories=` made it work on the Pi. Done at LOAD time rather than in
    # `resolve()` because the tiers must stay free of platform knowledge: they match on
    # `categories`, and this is the one place that field gets its value.
    #
    # Two sources, and they are different in kind:
    #   * the registry, for the default browser — the machine's own answer
    #   * PROGRAM_ROLES, for LB's lab stack — a curated adjective, never a claim about what
    #     exists. See the note above that table.
    browser = _default_browser_target()
    tagged: list[DesktopApp] = []
    for a in apps:
        extra: tuple[str, ...] = ()

        # Keyed on the executable stem: stable across the version numbers that Start Menu
        # names carry ("KiCad 8.0", "Creality Print 7.2"). Falls back to the shortcut's own
        # name for the 38-of-68 entries with no readable target — a Store app or a shell link
        # still deserves a role if we know one for it.
        key = _norm(Path(a.target).stem if a.target else a.name)
        extra += PROGRAM_ROLES.get(key, ())

        if browser and (browser in _norm(a.name) or browser in (a.target or "").lower()):
            extra += ("WebBrowser",)

        # Deduplicated, and only rebuilt when something was actually added — a frozen dataclass
        # copied for every row would be churn for no reason.
        new_cats = a.categories + tuple(c for c in dict.fromkeys(extra)
                                        if c not in a.categories)
        tagged.append(a if new_cats == a.categories
                      else DesktopApp(a.entry_id, a.name, a.argv, a.path, a.terminal,
                                      new_cats, a.target))
    apps = tuple(tagged)

    if browser:
        LOG.info("default browser role assigned to %r", browser)
    LOG.info("%d application(s), %d carrying a role word",
             len(apps), sum(1 for a in apps if set(a.categories) & set(ROLES.values())))
    return apps

def _norm(text: str) -> str:
    """Fold for comparison only. Never used to build an argv."""
    return " ".join((text or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _drop_article(text: str) -> str:
    """"the browser" -> "browser". Applied to the QUERY only, never to an app's own name."""
    for article in _ARTICLES:
        if text.startswith(article):
            return text[len(article):]
    return text


def _has_phrase(name: str, query: str) -> bool:
    """Does `query` appear in `name` as a run of WHOLE words?

    Whole words, not a raw substring, and this is a correctness fix rather than a refinement.
    A plain `in` test resolved `"rm"` to **LXTe-rm-inal** on the Pi: two letters, matched mid
    word, selecting a program LB never named. That is the exact failure this module refuses
    edit-distance matching to avoid, arriving through a different door.

    >>> _has_phrase("PCMan File Manager", "file manager")
    True
    >>> _has_phrase("LXTerminal", "rm")
    False
    """
    words, q = _norm(name).split(), _norm(query).split()
    if not q or len(q) > len(words):
        return False
    return any(words[i:i + len(q)] == q for i in range(len(words) - len(q) + 1))


def resolve(query: str, catalogue: tuple[DesktopApp, ...]) -> Match:
    """Which application did LB mean?

    Tiers, in order, first non-empty wins. Within a tier, more than one hit is **ambiguous** and
    is reported as such — never resolved by picking the first.

        1. the desktop id          "org.thonny.Thonny", "firefox"
        2. the Name                "Firefox", "PCMan File Manager"
        3. a role word             "the browser" -> every entry with Categories=WebBrowser
        4. a whole-word phrase     "file manager" -> "PCMan File Manager"

    A leading article is dropped before tiers 3 and 4, so "the browser" and "browser" behave
    identically without needing two rows in `ROLES`.

    Args:
        query:     what the model passed as the app name.
        catalogue: from `load_catalogue()`.

    Returns:
        A `Match`. Never raises.
    """
    q = _norm(query)
    if not q:
        return Match()
    bare = _drop_article(q)
    # A nickname stands in for the name it abbreviates, before any tier runs. See ALIASES.
    q = bare = ALIASES.get(bare, bare)

    tiers: list[list[DesktopApp]] = [
        [a for a in catalogue if _norm(a.entry_id) in (q, bare)],
        [a for a in catalogue if _norm(a.name) in (q, bare)],
    ]

    role = ROLES.get(bare)
    if role:
        tiers.append([a for a in catalogue if role in a.categories])

    # Phrase last, and only on the Name. Deliberately not on entry_id: ids carry reverse-DNS
    # noise ("org.gnome.", "com.raspberrypi.") that would make "pi" match half the menu.
    tiers.append([a for a in catalogue if _has_phrase(a.name, bare)])

    for hits in tiers:
        if len(hits) == 1:
            return Match(app=hits[0])
        if len(hits) > 1:
            return Match(candidates=tuple(hits))
    return Match()


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    catalogue = load_catalogue()
    if not args:
        print(f"  {len(catalogue)} application(s) he can open:\n")
        for app in catalogue:
            marker = " [terminal]" if app.terminal else ""
            # On Windows the interesting column is the TARGET, not argv — argv is always the
            # shortcut. "(no local target)" is a normal shell-namespace link, not a fault.
            what = app.target or "(no local target)"
            roles = ", ".join(c for c in app.categories if c in set(ROLES.values()))
            print(f"    {app.name:<30} {what}{marker}")
            if roles:
                print(f"    {'':<30} roles: {roles}")
        return 0

    for phrase in args:
        m = resolve(phrase, catalogue)
        if m.ok:
            print(f"  {phrase!r:28} -> {m.app.name}  ({' '.join(m.app.argv)})")
        elif m.ambiguous:
            names = ", ".join(a.name for a in m.candidates)
            print(f"  {phrase!r:28} -> AMBIGUOUS: {names}")
        else:
            print(f"  {phrase!r:28} -> (no such application)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
