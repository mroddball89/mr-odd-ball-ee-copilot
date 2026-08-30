#!/usr/bin/env python3
"""
Module:  verify_launch.py
Purpose: Prove he opens the right application, and never claims to have opened one he didn't.
Author:  LB
Date:    2026-08-21

    python tools/verify_launch.py
    python tools/verify_launch.py --probe

**No process is started by this harness.** `tools/app_launcher.py` funnels everything that
touches the operating system through two module-level names, `_run` and `_which`, and this file
rebinds both. That is the same trick `refuse()` uses to test a destructive-command blocklist
without running destructive commands.

It is also keyless and hardware-free, per D7: no Gemini key, no Wayland, no `.desktop` tree. The
catalogue checks build real files in a `TemporaryDirectory` and parse them with the code that
ships, so what is tested is the parser and not a mock of it.

## The check that matters more than the other hundred and thirty

Section 4 asserts `FakeRunner.calls == []` on every pre-flight refusal. The bug this whole
change exists to fix was **a confident success** — he said "Done" about a window that never
appeared. Every one of those refusal paths is a place where saying "Done" would be a lie, and
the only way to prove he does not is to prove nothing ran.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# These ledgers are written from `Engine.ask` and `agents/os_agent.py`, so this harness writes
# to them the moment it drives a failure — even though it was written before they existed and
# does not mention them. Redirected to a temp directory BEFORE anything under `tools/` is
# imported, because both read their location at import time. tasks/lessons.md L22.
import os                                                             # noqa: E402
import tempfile                                                       # noqa: E402

os.environ.setdefault("ODDBALL_VAULT_DIR",
                      tempfile.mkdtemp(prefix="oddball-harness-vault-"))


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Before importing anything that reaches engine/models.py. D7: Windows has no .env by design,
# and a harness that needs one is a harness LB cannot run where he writes the code.
os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key")

import tools.app_catalogue as cat                                     # noqa: E402
import tools.app_launcher as launcher                                 # noqa: E402
import orchestrator.launch_intent as launch_intent_mod                # noqa: E402
from tools.app_catalogue import load_catalogue, resolve             # noqa: E402
from tools.os_controller import KINDS, Outcome                        # noqa: E402
from engine.split import is_speakable                                 # noqa: E402
from agents.os_agent import _SPEECH, _speech_for                      # noqa: E402

PASSED = 0
FAILED = 0
QUIET = False           # probes re-run the checks counting reds instead of printing them


def check(ok: bool, what: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        if not QUIET:
            print(f"   PASS  {what}")
    else:
        FAILED += 1
        if not QUIET:
            print(f"   FAIL  {what}")
    if detail and not QUIET:
        print(f"           {detail}")
    return ok


def section(name: str) -> None:
    if not QUIET:
        print(f"\n  {name}")


class FakeRunner:
    """Records the argv it was handed and returns a canned result. Starts nothing.

    The two asserts are the load-bearing part: they fail the harness loudly if anybody ever
    reintroduces a shell or a command string on this path.
    """

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._rc, self._err = returncode, stderr

    def __call__(self, argv, **kw):
        assert kw.get("shell") is not True, "a launch must never reach a shell"
        assert isinstance(argv, list), f"argv must be a list, never a string: {argv!r}"
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self._rc, "", self._err)


def fake_which(installed: set[str]):
    """A `shutil.which` that only knows about `installed`."""
    def _which(name, path=None):
        base = Path(name).name
        return f"/usr/bin/{base}" if base in installed else None
    return _which


# --- fixtures ----------------------------------------------------------------------------
# Mirrors of real entries from the Pi, including the two awkward ones: a reverse-DNS id
# The fixture Start Menu.
#
# This was twelve `.desktop` entries until 2026-08-26. It is now a shortcut tree, because
# `tools/app_catalogue.py` reads the Start Menu and the XDG reader was deleted — but the
# SHAPE is deliberately preserved entry for entry, so that section 2's resolution tests are
# the same tests they were. Two browsers, so "the browser" is still ambiguous. A reverse-DNS
# name. A program in a vendor subfolder, standing in for the reverse-DNS id that used to
# prove nesting. An uninstaller and an excluded folder, which are this platform's version of
# `NoDisplay=true` and `pishutdown`.
#
# Synthesized, never copied from the real Start Menu: a harness that depends on what happens
# to be installed on the box running it tests the box.
_tmp = tempfile.TemporaryDirectory()
FIXTURE_DIR = Path(_tmp.name) / "Programs"
FIXTURE_DIR.mkdir(parents=True)

# (relative path under Programs, target) — the folder is the category, the stem is the name.
SHORTCUTS = {
    "Firefox":                          r"C:\Program Files\Mozilla Firefox\firefox.exe",
    # Named in full, so that "browser" is ambiguous by NAME. On the Pi both browsers
    # carried `Categories=WebBrowser` and the role tier made them ambiguous; the
    # fixture tree has no registry behind it, so the ambiguity has to arrive the way
    # it really would on this platform — through the names themselves.
    "Chromium Web Browser":             r"C:\Program Files\Chromium\chrome.exe",
    "Waterfox Web Browser":             r"C:\Program Files\Waterfox\waterfox.exe",
    "Thonny":                           r"C:\Program Files\Thonny\thonny.exe",
    "PCMan File Manager":               r"C:\Program Files\PCManFM\pcmanfm.exe",
    "Galculator":                       r"C:\Program Files\Galculator\galculator.exe",
    "Htop":                             r"C:\Program Files\Htop\htop.exe",
    "VLC media player":                 r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    # Nested in a vendor folder, so the folder-as-category path is exercised.
    "Autodesk/Autodesk Fusion":         r"C:\Users\lb\AppData\Local\Autodesk\fusion.exe",
    # --- and the ones that must NOT become applications ---
    "Uninstall":                        r"C:\Program Files\Thing\uninstall.exe",
    "Uninstall Thonny":                 r"C:\Windows\SysWOW64\msiexec.exe",
    "Administrative Tools/Disk Cleanup": r"C:\Windows\System32\cleanmgr.exe",
    "Administrative Tools/Event Viewer": r"C:\Windows\System32\eventvwr.exe",
}

# `_make_lnk` is defined further down, beside the section that first needed it. Fixtures are
# built lazily at first use rather than at import for that reason alone.
def _build_fixture_tree() -> tuple:
    for rel, target in SHORTCUTS.items():
        path = FIXTURE_DIR / f"{rel}.lnk"
        path.parent.mkdir(parents=True, exist_ok=True)
        _make_lnk(path, target)
    return _cat_mod.load_start_menu((FIXTURE_DIR,))


import tools.app_catalogue as _cat_mod                               # noqa: E402

# A few probes below resolve a REAL application name against the REAL catalogue, to prove the
# guards fire on this machine and not only on fixtures. They are skipped where there is
# nothing installed to resolve, so the harness stays honest on a bare box rather than
# reporting a pass it did not earn.
_HAS_REAL_CATALOGUE = len(_cat_mod.load_catalogue()) > 0

# Filled in by `build()`, once `_make_lnk` is defined.
CATALOGUE: tuple = ()
BY_NAME: dict = {}

# `find_display` reads no environment on Windows — it counts monitors — so LIVE is now just
# "whatever the real environment is" and the no-display case is produced by faking
# `find_display` itself. See `run_launch(dark=True)`.
LIVE: dict = {}

# The programs whose target should EXIST. Everything else in the fixture tree gets a target
# that does not, which is the `nautilus` case: an entry promising a program the machine lacks.
INSTALLED = {"firefox.exe", "chrome.exe", "waterfox.exe", "thonny.exe", "pcmanfm.exe",
             "galculator.exe", "htop.exe", "vlc.exe", "fusion.exe"}

# Something guaranteed to exist for `with_targets` to point "installed" rows at. This
# file: it is on disk by definition, on every platform, and outlives every temp dir.
_REAL_FILE = Path(__file__).resolve()


class FakeShell:
    """Records what was handed to the shell, and starts nothing.

    The replacement for `FakeRunner`. That class faked `subprocess.run` and carried a
    returncode and a stderr, because the Pi asked systemd to launch things and read an exit
    code back. `os.startfile` has no exit code — it returns, or it raises — so failure is
    modelled the way the real thing signals it.
    """

    def __init__(self, error: Exception | None = None):
        self.started: list[str] = []
        self.error = error

    def __call__(self, path):
        self.started.append(str(path))
        if self.error is not None:
            raise self.error

    @property
    def ran(self) -> bool:
        return bool(self.started)


def with_targets(catalogue, installed):
    """Give each fixture row a `target`, so guard 2 has something to check.

    The XDG fixtures are `.desktop` rows and `parse_entry` leaves `target` empty — correct on
    the Pi, where `argv[0]` already IS the program. Guard 2 on Windows reads `target`, so
    without this every fixture would skip the check and `INSTALLED` would mean nothing: the
    harness would report a passing not-installed test that could never fail.

    `installed` names the apps whose target should exist. Everything else gets a target that
    does not, which is the `nautilus` case.
    """
    out = []
    for a in catalogue:
        program = Path(a.target).name if a.target else a.name
        target = str(_REAL_FILE) if program in installed else "C:\\nowhere\\%s" % program
        out.append(cat.DesktopApp(a.entry_id, a.name, a.argv, a.path, a.terminal,
                                  a.categories, target))
    return tuple(out)


def run_launch(name, *, installed=None, environ=None, error=None, dark=False):
    """Drive the real `launch()` with every seam faked. Returns (Outcome, FakeShell).

    `dark` replaces the old `environ=DARK`. On the Pi "no display" was expressible as an
    environment with no Wayland socket in it, so the harness could produce it by passing a
    dict. Windows counts monitors through `GetSystemMetrics`, which no environment can lie
    about — so the seam is `find_display` itself.
    """
    shell = FakeShell(error=error)
    real_start, real_load = launcher._start, cat.load_catalogue
    real_find = launcher.find_display
    fixture = with_targets(CATALOGUE, INSTALLED if installed is None else installed)
    launcher._start = shell
    cat.load_catalogue = lambda dirs=None: fixture
    if dark:
        launcher.find_display = lambda environ=None: launcher.Display(
            monitors=0, detail="monitors = 0\nsession = Remote Desktop")
    try:
        return launch_result(name, environ), shell
    finally:
        launcher._start = real_start
        launcher.find_display = real_find
        cat.load_catalogue = real_load


def launch_result(name, environ):
    return launcher.launch(name, environ=LIVE if environ is None else environ,
                           now="20260821-143012")


# =========================================================================================
def s1_catalogue() -> None:
    section("1. the catalogue is the machine's list, read correctly")

    # This section tested `exec_argv()` — the XDG field-code stripper — until 2026-08-26.
    # Seven checks about `%u`, `%F`, `%i`, `%%` and shlex quoting, all deleted with the reader.
    #
    # **Windows needs none of it, and that is the interesting part rather than a loss.** A
    # `.lnk` already holds its arguments, working directory and elevation flag as structured
    # fields, so there is no command LINE to parse and no way for a quoted path with a space
    # in it to become two arguments. The entire class of bug those seven checks guarded
    # against does not exist on this platform. What replaces them is the check that the
    # shortcut is read at all, and that the things which must not be applications are not.

    check("Firefox" in BY_NAME, "Firefox is in the catalogue", ", ".join(sorted(BY_NAME)))
    check("VLC media player" in BY_NAME, "so is VLC")
    check("Autodesk Fusion" in BY_NAME, "and one nested in a vendor folder")

    check(BY_NAME["Firefox"].target.endswith("firefox.exe"),
          "the shortcut's TARGET is read out of the .lnk binary, with no dependency",
          BY_NAME["Firefox"].target)
    check(BY_NAME["Firefox"].argv[0].endswith(".lnk"),
          "but argv[0] is the SHORTCUT — os.startfile resolves the rest",
          BY_NAME["Firefox"].argv[0])

    # The Windows equivalents of NoDisplay=true and pishutdown.
    for excluded in ("Uninstall", "Uninstall Thonny", "Disk Cleanup", "Event Viewer"):
        check(excluded not in BY_NAME, f"{excluded!r} is excluded from the catalogue")
    check(len(CATALOGUE) == 9,
          "9 of 13 fixture shortcuts are real applications", f"got {len(CATALOGUE)}")

    check(BY_NAME["Htop"].terminal is False,
          "Terminal is always False — a .lnk carries its own console decision, so the "
          "terminal-emulator wrapping the Pi needed has no counterpart")
    check("autodesk" in BY_NAME["Autodesk Fusion"].categories,
          "the containing folder becomes a category, as Categories= did on the Pi",
          str(BY_NAME["Autodesk Fusion"].categories))

    # A per-user shortcut must shadow a machine-wide one of the same name.
    user_dir = Path(_tmp.name) / "user-Programs"
    user_dir.mkdir(exist_ok=True)
    _make_lnk(user_dir / "Firefox.lnk", r"C:\Users\lb\AppData\Local\Firefox\firefox.exe")
    shadowed = {a.name: a for a in _cat_mod.load_start_menu((user_dir, FIXTURE_DIR))}
    check(shadowed["Firefox"].target.startswith(r"C:\Users"),
          "a per-user shortcut shadows the machine-wide one of the same name",
          shadowed["Firefox"].target)
    check(len([a for a in shadowed.values() if a.name == "Firefox"]) == 1,
          "and there is exactly one Firefox, not two")

    check(_cat_mod.load_start_menu((Path(_tmp.name) / "nope",)) == (),
          "a missing directory is an empty catalogue, not a crash")

    # A malformed shortcut costs its own row and nothing else.
    (FIXTURE_DIR / "Corrupt.lnk").write_bytes(b"this is not a shortcut")
    try:
        after = _cat_mod.load_start_menu((FIXTURE_DIR,))
        check(len(after) == len(CATALOGUE) + 1,
              "an unreadable .lnk is still listed — os.startfile can resolve an IDList "
              "even where the target cannot be parsed", f"{len(after)} rows")
        corrupt = next(a for a in after if a.name == "Corrupt")
        check(corrupt.target == "",
              "...with an empty target, so the launcher SKIPS the existence check "
              "rather than refusing")
    finally:
        (FIXTURE_DIR / "Corrupt.lnk").unlink(missing_ok=True)


def s2_resolution() -> None:
    section("2. resolution is exact, and ambiguity is reported rather than guessed")

    check(resolve("firefox", CATALOGUE).app is BY_NAME["Firefox"], "by desktop id")
    check(resolve("Firefox", CATALOGUE).app is BY_NAME["Firefox"], "by name, case-insensitively")
    check(resolve("autodesk fusion", CATALOGUE).app is BY_NAME["Autodesk Fusion"],
          "by the full name of a program nested in a vendor folder")
    check(resolve("thonny", CATALOGUE).app is BY_NAME["Thonny"], "and by its plain name")
    check(resolve("file manager", CATALOGUE).app is BY_NAME["PCMan File Manager"],
          "by a whole-word phrase inside the name")
    check(resolve("galculator", CATALOGUE).app is BY_NAME["Galculator"], "an exact name")
    check(resolve("media player", CATALOGUE).app is BY_NAME["VLC media player"],
          "a two-word phrase at the end of a name")

    # Found on the Pi, 2026-08-21. A raw substring test resolved "rm" to LXTe-RM-inal: two
    # letters, matched mid-word, opening a program LB never named. Whole-word phrases only.
    check(not resolve("rm", CATALOGUE).ok,
          "'rm' does NOT match mid-word inside 'LXTerminal'",
          "a raw substring match opened a terminal when LB said 'rm'")
    check(not resolve("ox", CATALOGUE).ok, "nor 'ox' inside 'Firefox'")
    check(not resolve("cal", CATALOGUE).ok, "nor 'cal' inside 'Galculator'")
    check(resolve("terminal", CATALOGUE).app is None
          or resolve("terminal", CATALOGUE).app.name != "Firefox",
          "and a role word never lands on an unrelated app")

    # A leading article is dropped, so ROLES needs one row per role, not one per phrasing.
    # The fixture tree has no registry behind it, so nothing carries the WebBrowser tag and
    # "browser" falls through to the name tiers — where it finds Chromium, because that
    # shortcut has "Browser" in its name and Firefox does not.
    for phrasing in ("browser", "the browser", "a browser"):
        m = resolve(phrasing, CATALOGUE)
        check(m.ambiguous and {a.name for a in m.candidates} ==
              {"Chromium Web Browser", "Waterfox Web Browser"},
              f"{phrasing!r} finds both browsers, article or not",
              str(sorted(a.name for a in m.candidates)))

    # ROLES lost five of its seven rows in the port. See the note above the table in
    # app_catalogue.py: Windows Start Menu folders are named after VENDORS, not roles, so a
    # `calculator` or `terminal` row could never match and would only block the name tiers
    # from finding the program. What is checked here is that the fall-through actually works.
    # --- the role map -------------------------------------------------------------------
    #
    # `PROGRAM_ROLES` is a curated table in a module that refuses curated tables, so these
    # checks are about the property that makes it the acceptable kind: it says what KIND a
    # program is, never which programs exist. Nothing here asserts that any particular tool
    # is installed, and the fixture tree contains none of LB's real stack.

    # EVERY token a program can be tagged with must be reachable by something spoken. A token
    # in PROGRAM_ROLES with no ROLES phrase pointing at it is a row that can never fire — the
    # program gets a category nobody can ask for, which is worse than no category because it
    # looks like coverage.
    _spoken = set(_cat_mod.ROLES.values())
    _tagged = {tok for toks in _cat_mod.PROGRAM_ROLES.values() for tok in toks}
    _unreachable = _tagged - _spoken
    check(not _unreachable,
          "every role token a program can carry has a spoken phrase that reaches it",
          f"UNREACHABLE: {sorted(_unreachable)}" if _unreachable else
          f"{len(_tagged)} tokens, all reachable")

    # And the other direction, which is the softer of the two: a spoken phrase pointing at a
    # token nothing can carry resolves to nothing forever. WebBrowser is the legitimate
    # exception — it is assigned from the REGISTRY in load_start_menu(), not from this table.
    _orphan = _spoken - _tagged - {"WebBrowser"}
    check(not _orphan,
          "and every spoken role phrase points at a token some program can carry",
          f"ORPHANED: {sorted(_orphan)}" if _orphan else "")

    # Keyed on the EXECUTABLE, which is what makes it durable. A Start Menu name carries a
    # version — "KiCad 8.0", "Creality Print 7.2" — and goes stale on the next update, which
    # is the exact failure mode of hardcoding a versioned install path.
    check(all(k == k.lower() and not k.endswith(".exe") for k in _cat_mod.PROGRAM_ROLES),
          "the keys are lower-case executable stems, never display names or paths",
          "so a version bump in the Start Menu name cannot break a role")
    check("kicad" in _cat_mod.PROGRAM_ROLES and "eeschema" in _cat_mod.PROGRAM_ROLES,
          "the KiCad binaries are listed individually",
          "'open the pcb editor' must not open the schematic editor")
    check("cmd" not in _cat_mod.PROGRAM_ROLES,
          "cmd.exe is deliberately NOT tagged as a terminal",
          "several Start Menu entries point at it with different arguments, so tagging it "
          "would make 'the terminal' ambiguous between things that are not terminals")

    # It claims nothing about what EXISTS — the whole distinction from the table this module
    # refuses. The fixture catalogue has no KiCad in it, and asking for one says so.
    check(not resolve("the schematic editor", CATALOGUE).ok,
          "a role for a program that is not installed resolves to NOTHING",
          "PROGRAM_ROLES supplies an adjective; the catalogue remains the only source of "
          "truth about what is on the machine")

    # The tagging itself, on a fixture whose target is a known key.
    with tempfile.TemporaryDirectory() as _rt:
        _r = Path(_rt)
        _make_lnk(_r / "KiCad 9.0.lnk", r"C:\Program Files\KiCad\9.0\bin\kicad.exe")
        _make_lnk(_r / "Schematic Editor.lnk", r"C:\Program Files\KiCad\9.0\bin\eeschema.exe")
        _roled = _cat_mod.load_start_menu((_r,))
        _by = {a.name: a for a in _roled}
        check("EDA" in _by["KiCad 9.0"].categories,
              "a program is tagged from its executable, not its display name",
              f"'KiCad 9.0' -> {_by['KiCad 9.0'].categories}")
        check(resolve("the eda tool", _roled).ambiguous,
              "and both KiCad binaries answer to 'the eda tool'")
        m = resolve("the schematic editor", _roled)
        check(m.ok and m.app.name == "Schematic Editor",
              "while 'the schematic editor' reaches exactly one of them",
              "which is the distinction the individually-named binaries buy")
    check(resolve("file manager", CATALOGUE).app is BY_NAME["PCMan File Manager"],
          "'file manager' still resolves — through the NAME tier now, not the role tier",
          "which is why deleting the dead role rows was necessary rather than merely tidy")
    check(resolve("the file manager", CATALOGUE).app is BY_NAME["PCMan File Manager"],
          "and the article is still dropped before the name tiers run")

    # The registry path, which is the half the fixtures cannot exercise. Tagged by hand here
    # exactly as `load_start_menu()` tags it from `_default_browser_target()`.
    tagged = tuple(
        cat.DesktopApp(a.entry_id, a.name, a.argv, a.path, a.terminal,
                       a.categories + (("WebBrowser",) if a.name == "Firefox" else ()),
                       a.target)
        for a in CATALOGUE)
    m = resolve("the browser", tagged)
    check(m.ok and m.app.name == "Firefox",
          "with a default browser recorded, 'the browser' resolves to it OUTRIGHT",
          "the Pi had two browsers and had to ask; Windows records a preference, so this is "
          "one place the port made the answer better rather than merely different")

    # The negatives. Edit-distance matching is refused on purpose: a threshold loose enough to
    # catch `tawny -> thonny` is loose enough to catch `shut up -> shut down`, and the failure
    # mode is not a wrong answer, it is the wrong program running.
    for miss in ("", "   ", "thonnyy", "firefx", "fire fox", "chrome", "bash", "rm", "sudo",
                 "nautilus", "emacs", "firefox; rm -rf /", "$(reboot)", "../../bin/sh"):
        m = resolve(miss, CATALOGUE)
        check(not m.ok, f"{miss!r} resolves to nothing",
              f"resolved to {m.app.name!r} — this is the wrong program running" if m.ok else "")


def s3_argv() -> None:
    section("3. the launch is one call, and the app is not our child")

    # The Pi built an 18-element `systemd-run` argv and this section proved every flag in it.
    # Windows needs none of that construction — `os.startfile` IS the disown — so what is
    # checked here is the set of guarantees those flags were BUYING, restated as claims about
    # this module. A shorter section is the correct outcome of deleting machinery; a section
    # that merely got shorter without saying what it stopped covering would not be.

    # The module WITHOUT its own docstring. Not `source.split('"""')[-1]` — that takes only
    # the file's tail, because every function docstring splits it too, so a `shell=True`
    # anywhere above the last one would sail through. `ast` knows where the docstring ends.
    import ast

    source = Path(launcher.__file__).read_text(encoding="utf-8")
    _tree = ast.parse(source)
    _doc = ast.get_docstring(_tree, clean=False) or ""
    body = source.replace(_doc, "", 1) if _doc else source

    # --- what `--collect`, `Type=exec` and the transient unit were for --------------------
    check(not hasattr(launcher, "systemd_run_argv"),
          "systemd_run_argv is GONE, not kept as dead code")
    check(not hasattr(launcher, "PINNED_PATH"),
          "so is PINNED_PATH — there is no argv to pin a PATH into any more")

    check("subprocess" not in body,
          "the module does not import subprocess at all",
          "a blocking capture with a timeout is what killed the app at 15 seconds; there is "
          "now no call that could regress to it")
    check("shell=True" not in body,
          "and contains no shell=True outside its docstring")
    check("Popen" not in body,
          "no Popen either — the launched app must not be our child in any sense")

    # --- the single OS surface, and the seam over it ---------------------------------------
    check(callable(launcher.start), "start() is the entire operating-system surface")
    check(body.count("_start(") == 1,
          "and the injection seam has exactly ONE call site, so a harness has one thing to "
          "replace", f"found {body.count('_start(')}")

    # `os.startfile` raising OSError is the honesty guarantee that `--property=Type=exec` was
    # on the Pi: it is what separates "the shell accepted it" from "nothing happened".
    calls = []
    real = launcher._start
    try:
        launcher._start = lambda path: (_ for _ in ()).throw(OSError(2, "no application"))
        out = launcher.launch("openscad") if _HAS_REAL_CATALOGUE else None
    finally:
        launcher._start = real
    if _HAS_REAL_CATALOGUE:
        check(out is not None and out.kind == "launch-failed" and not out.ok,
              "a shell refusal becomes launch-failed, never a silent success",
              f"kind={out.kind!r}" if out else "")
        check(out is not None and "no application" in out.detail,
              "and the reason reaches the card verbatim")

    # --- the shortcut, not the target -------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        lnk = Path(tmp) / "Thing.lnk"
        _make_lnk(lnk, r"C:\\Program Files\\Thing\\thing.exe")
        app = cat.parse_shortcut(lnk, Path(tmp))
        real = launcher._start
        try:
            launcher._start = calls.append
            cat_real = cat.load_catalogue
            cat.load_catalogue = lambda dirs=None: (app,)
            try:
                out = launcher.launch("thing")
            finally:
                cat.load_catalogue = cat_real
        finally:
            launcher._start = real

        check(out.kind == "not-installed",
              "a shortcut whose TARGET does not exist is not-installed — the nautilus case, "
              "which is the one guard that survived the port", out.kind)
        check(not calls, "and nothing was started")


def s3b_display_free_launch() -> None:
    section("3b. the shortcut is launched, never the .exe it points at")

    calls: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        lnk = Path(tmp) / "Real.lnk"
        # Point it at something that genuinely exists, so guard 2 passes and guard 4 is reached.
        _make_lnk(lnk, sys.executable)
        app = cat.parse_shortcut(lnk, Path(tmp))

        real_start, real_cat = launcher._start, cat.load_catalogue
        try:
            launcher._start = calls.append
            cat.load_catalogue = lambda dirs=None: (app,)
            out = launcher.launch("real")
        finally:
            launcher._start, cat.load_catalogue = real_start, real_cat

    check(out.ok and out.kind == "launched", "a resolvable, present application launches",
          f"kind={out.kind!r} detail={out.detail[:60]!r}")
    check(calls == [str(lnk)],
          "and what was handed to the shell is the .LNK, not its target",
          f"got {calls!r}")
    check(str(sys.executable) not in calls,
          "the target is never executed directly — that would discard the shortcut's "
          "arguments, working directory and run-as flag")
    check(sys.executable in out.detail,
          "though the verified target IS on the card, so the check is visible")


def s4_outcomes() -> None:
    section("4. every refusal starts NOTHING, and none of them is spoken as success")

    out, shell = run_launch("firefox")
    check(out.kind == "launched" and out.ok, "the happy path launches", out.kind)
    check(len(shell.started) == 1, "and hands the shell exactly one thing")
    _fx = next(a for a in with_targets(CATALOGUE, INSTALLED) if a.name == "Firefox")
    check(shell.started[0] == _fx.argv[0],
          "and what it hands over is the catalogue's own argv[0], verbatim",
          f"started {shell.started[0]!r}; on Windows argv[0] is the .lnk, which is what "
          f"makes the shell resolve the target, arguments and working directory")
    check(shell.started[0] != _fx.target,
          "never the resolved TARGET — that would discard the shortcut's own arguments, "
          "working directory and run-as flag", f"target was {_fx.target!r}")
    check(out.subject == "Firefox", "the subject is the spoken name, for the sentence")
    check("target verified" in out.detail,
          "and the card says the existence check actually RAN — a guard that cannot run must "
          "say so, and this is how the two are told apart", out.detail.splitlines()[-2:])

    out, shell = run_launch("nautilus")
    check(out.kind == "unknown-app" and not out.ok, "an app not in the catalogue", out.kind)
    check(not shell.ran, "and NOTHING was started")
    check("Firefox" in out.detail, "the card lists what he CAN open")

    out, shell = run_launch("the browser")
    check(out.kind == "ambiguous" and not out.ok, "two matches is ambiguous", out.kind)
    check(not shell.ran, "and NOTHING was started — guessing would open the wrong browser")

    # The `nautilus` case, generalised: the entry promises a program the machine lacks.
    out, shell = run_launch("firefox", installed=set())
    check(out.kind == "not-installed" and not out.ok,
          "an entry whose program is missing is caught BEFORE launching", out.kind)
    check(not shell.ran, "and NOTHING was started")
    check("not on this machine" in out.detail, "the card says where it looked")

    out, shell = run_launch("firefox", dark=True)
    check(out.kind == "no-display" and not out.ok,
          "a session with no monitors refuses", out.kind)
    check(not shell.ran,
          "and NOTHING was started — launching into a session with no screen is "
          "indistinguishable from doing nothing, which IS the reported bug")
    check("Remote Desktop" in out.detail,
          "the card says what he looked at, not just that he failed",
          "a disconnected RDP session is the reachable case this outcome exists for")

    # `os.startfile` signals failure by RAISING, not by an exit code. This is the Windows
    # form of the `Type=exec` guarantee: it separates "the shell took it" from "nothing
    # happened", which was defect 4 of the original bug — silence reported as success.
    out, shell = run_launch("firefox", error=OSError(2, "No application is associated"))
    check(out.kind == "launch-failed" and not out.ok,
          "a shell refusal is a failure, not a success", out.kind)
    check("No application is associated" in out.detail,
          "and the reason is carried to the card verbatim")
    check(shell.ran, "the attempt really was made — this is a failure, not a refusal")

    # A shortcut with no readable target: the check is SKIPPED, and the launch proceeds.
    # 14 of the 41 applications on this machine are like this, so getting it wrong would break
    # every Control Panel entry on the box.
    skipped = tuple(cat.DesktopApp(a.entry_id, a.name, a.argv, a.path, a.terminal,
                                   a.categories, "")
                    for a in CATALOGUE)
    real_start, real_load = launcher._start, cat.load_catalogue
    shell = FakeShell()
    try:
        launcher._start = shell
        cat.load_catalogue = lambda dirs=None: skipped
        out = launcher.launch("firefox")
    finally:
        launcher._start, cat.load_catalogue = real_start, real_load

    check(out.ok and out.kind == "launched",
          "a shortcut naming no local path still launches", out.kind)
    check("not checked" in out.detail,
          "and the card SAYS the check was skipped rather than implying it passed",
          "a guard that silently passes is worse than no guard")

    # The single most important check in the file.
    check(all(o.ok is False for o in [
        run_launch("nautilus")[0], run_launch("the browser")[0],
        run_launch("firefox", installed=set())[0], run_launch("firefox", dark=True)[0],
        run_launch("firefox", error=OSError(2, "x"))[0]]),
        "NONE of the five refusal paths reports ok=True")


def s5_display() -> None:
    section("5. the display is discovered at launch time, not configured")

    # The Pi globbed $XDG_RUNTIME_DIR for a compositor socket, because labwc picks a new socket
    # number after a session restart and a value baked into a config file would be right until
    # the first restart and silently wrong forever after. Windows has no such moving part —
    # but the PRINCIPLE is the one thing worth carrying over, so it is still asked at the
    # moment it is needed rather than cached, and this section is what pins that.

    d = launcher.find_display()
    check(isinstance(d.monitors, int), "find_display counts monitors", str(d.monitors))
    check(d.usable == (d.monitors > 0),
          "usable is exactly 'there is at least one monitor'")
    check(d.describe(),
          "and it describes what it looked at — 'I couldn't find the screen' with no detail "
          "is not diagnosable", d.describe().replace("\n", " | "))

    # `no-display` is kept for a REACHABLE case, not out of sentiment: a disconnected RDP
    # session still runs processes and has nothing to draw on. Proven by construction.
    dark = launcher.Display(monitors=0, detail="monitors = 0")
    check(not dark.usable, "zero monitors is not usable")

    calls: list[str] = []
    real_find, real_start = launcher.find_display, launcher._start
    try:
        launcher.find_display = lambda environ=None: dark
        launcher._start = calls.append
        out = launcher.launch("the browser") if _HAS_REAL_CATALOGUE else None
    finally:
        launcher.find_display, launcher._start = real_find, real_start

    if _HAS_REAL_CATALOGUE:
        check(out is not None and out.kind == "no-display",
              "a session with no desktop refuses rather than launching",
              f"kind={out.kind!r}" if out else "")
    check(not calls,
          "and NOTHING was started — launching into a session with no screen starts a process "
          "nobody can see, which is indistinguishable from doing nothing")

    # A failure to ASK is not a failure to have a screen.
    import ctypes as _ctypes
    real_windll = getattr(_ctypes, "windll", None)
    if real_windll is not None:
        class _Boom:
            def __getattr__(self, _):
                raise OSError("simulated ctypes failure")
        try:
            _ctypes.windll = _Boom()
            d = launcher.find_display()
        finally:
            _ctypes.windll = real_windll
        check(d.usable and "unavailable" in d.describe(),
              "if the monitor count cannot be READ, a display is assumed present and the "
              "reason is on the card",
              "refusing every launch because a ctypes call misbehaved is a worse bug than "
              "the one this guard prevents")


def s6_speech() -> None:
    section("6. what he says is total, speakable, and never a claim he did not check")

    check(set(_SPEECH) == set(KINDS), "the speech table is total over KINDS",
          f"missing: {set(KINDS) - set(_SPEECH)}")

    for kind in KINDS:
        spoken = _speech_for(Outcome(kind in ("output", "launched"), kind, "x" * 400, "Firefox"))
        check(is_speakable(spoken) is None, f"{kind!r} is speakable", spoken)
        check("x" * 20 not in spoken, f"{kind!r} never leaks .detail into speech")
        check(not re.search(r"[/\\]|--|\bsystemd\b|\bwayland\b", spoken),
              f"{kind!r} has no path, flag or plumbing word in it", spoken)

    check("Opening" in _SPEECH["launched"] and "is open" not in _SPEECH["launched"],
          "success is 'Opening Firefox now' — a claim about what he DID. "
          "'Firefox is open' would be a claim about a screen he cannot see.")
    check(_SPEECH["blocked"] != _SPEECH["error"],
          "a refusal does not share a sentence with a failure — the guard working correctly "
          "is not a malfunction")
    check("stopped it" in _SPEECH["timeout"],
          "the timeout sentence says he STOPPED it, which is what subprocess.run actually does")
    check(not any(c.isdigit() for c in _SPEECH["timeout"]),
          "and names no number, so it cannot drift from TIMEOUT_S")


def s7_free_intent() -> None:
    section("7. a launch costs NO api call, and a question about an app is not a launch")

    from orchestrator import launch_intent
    from orchestrator.instant import Query, Router as InstantRouter, normalise
    import tools.app_catalogue as _cat

    real = _cat.cached_catalogue
    _cat.cached_catalogue = lambda: CATALOGUE   # late-bound: built in build()

    def ask(utterance):
        return launch_intent.look_up(Query(raw=utterance, text=normalise(utterance)))

    try:
        # Verb + target + nothing left over.
        for utterance, expect in [
            ("open firefox", "firefox"),
            ("launch firefox", "firefox"),
            ("start thonny", "thonny"),
            ("run galculator", "galculator"),
            ("bring up the file manager", "file manager"),
            ("fire up vlc", "vlc"),
            ("open up firefox", "firefox"),
            ("can you open firefox please", "firefox"),
            ("hey open thonny for me", "thonny"),
            ("i want to open firefox", "firefox"),
        ]:
            req = ask(utterance)
            check(req is not None and req.app == expect,
                  f"{utterance!r} is a launch of {expect!r}",
                  f"got {req.app!r}" if req else "not recognised as a launch")

        # A multi-word Name is reachable by any contiguous run of it. Added 2026-08-23 (D27):
        # "fire up the schematic editor" and "pull up the pcb editor" both failed here and fell
        # through to the paid router, even though `resolve()` answers them on its tier 4. The
        # catalogue knew; `_targets` never asked. On this fixture set the rule reaches VLC.
        #
        # The MIDDLE of a name has to work, not just its tail — the Pi ships
        # "KiCad Schematic Editor (Standalone)", where a trailing run gives only "schematic
        # editor standalone" and the first version of this fix therefore missed on hardware.
        for utterance, expect in [
            ("open the media player", "media player"),
            ("open media player", "media player"),
            ("launch the web browser", "web browser"),
        ]:
            req = ask(utterance)
            check(req is not None and req.app == expect,
                  f"{utterance!r} names {expect!r} off a multi-word Name",
                  f"got {req.app!r}" if req else "not recognised as a launch")

        check(ask("open the media player") is not None
              and _cat.resolve("media player", CATALOGUE).app.name == "VLC media player",
              "...and it resolves to exactly one app, not a guess between several")

        # A SINGLE trailing word is not a target — "editor", "manager", "player" belong to
        # ROLES, which maps them to a category rather than to whichever app ends in them.
        for utterance in ["open the player", "open the manager"]:
            check(ask(utterance) is None,
                  f"{utterance!r} is NOT a launch — one trailing word is a role, not a name",
                  f"claimed {ask(utterance)}")

        # Spoken nicknames (D27). "vscode" is not the Name, not the entry id and not a role,
        # so nothing in the four resolution tiers could reach it. It is an EXACT key, not a
        # fuzzy match, and it names no app on its own — with VS Code uninstalled it resolves
        # to nothing, exactly as its full name does.
        with tempfile.TemporaryDirectory() as _t:
            _d = Path(_t)
            _make_lnk(_d / "Visual Studio Code.lnk",
                      r"C:\Users\lb\AppData\Local\Programs\Microsoft VS Code\Code.exe")
            vscode_cat = _cat.load_start_menu((_d,))
            _cat.cached_catalogue = lambda: vscode_cat

            for utterance in ["open vscode", "launch vs code", "open code",
                              "open visual studio code"]:
                req = ask(utterance)
                check(req is not None
                      and _cat.resolve(req.app, vscode_cat).app.name == "Visual Studio Code",
                      f"{utterance!r} reaches Visual Studio Code",
                      f"got {req.app!r}" if req else "not recognised as a launch")

            # The anchor still governs a nickname exactly as it governs a name.
            for utterance in ["how do i open vscode", "is vscode installed",
                              "open vscode and delete my files"]:
                check(ask(utterance) is None, f"{utterance!r} is NOT a launch",
                      f"claimed {ask(utterance)}")

            check(all(_cat.resolve(alias, CATALOGUE).app is None
                      for alias in _cat.ALIASES),
                  "an alias for an app that is NOT installed resolves to nothing",
                  "the alias table must never imply an app exists")

        # The names THIS PI ships, copied exactly. The bracketed qualifier is the whole point:
        # the distinguishing words are in the middle, so a trailing-run rule yields only
        # "schematic editor standalone" and misses. Verified on hardware 2026-08-23.
        with tempfile.TemporaryDirectory() as _t:
            _d = Path(_t)
            # Shortcut names as a Windows KiCad install writes them. The bracketed
            # qualifier is preserved verbatim from the Pi's `Name=` values, because it is the
            # point of the test rather than a platform detail: the distinguishing words sit
            # in the MIDDLE of the name, so a trailing-run rule yields only
            # "schematic editor standalone" and misses.
            (_d / "KiCad").mkdir()
            for _exe, _name in [
                ("kicad", "KiCad"),
                ("eeschema", "KiCad Schematic Editor (Standalone)"),
                ("pcbnew", "KiCad PCB Editor (Standalone)"),
                ("gerbview", "KiCad Gerber Viewer"),
                ("pcbcalculator", "KiCad PCB Calculator"),
            ]:
                _make_lnk(_d / "KiCad" / (_name + ".lnk"),
                          "C:\\Program Files\\KiCad\\9.0\\bin\\" + _exe + ".exe")
            kicad_cat = _cat.load_start_menu((_d,))
            _cat.cached_catalogue = lambda: kicad_cat

            for utterance, want in [
                ("fire up the schematic editor", "KiCad Schematic Editor (Standalone)"),
                ("pull up the pcb editor", "KiCad PCB Editor (Standalone)"),
                ("open the gerber viewer", "KiCad Gerber Viewer"),
                ("open the pcb calculator", "KiCad PCB Calculator"),
                ("open kicad", "KiCad"),
            ]:
                req = ask(utterance)
                got = _cat.resolve(req.app, kicad_cat) if req else None
                check(req is not None and got.ok and got.app.name == want,
                      f"{utterance!r} -> {want!r}",
                      f"got {req.app!r} -> {got.app.name if got and got.ok else got}"
                      if req else "not recognised as a launch")

            # "pcb editor" and "pcb calculator" are one word apart and must not be confused.
            check(_cat.resolve("pcb editor", kicad_cat).app.name.startswith("KiCad PCB Editor")
                  and _cat.resolve("pcb calculator", kicad_cat).app.name.endswith("Calculator"),
                  "two names sharing a word still resolve apart")

            # The anchor governs here too, and a bare qualifier names nothing.
            for utterance in ["how do i open the schematic editor", "what is the pcb editor",
                              "open the standalone"]:
                check(ask(utterance) is None, f"{utterance!r} is NOT a launch",
                      f"claimed {ask(utterance)}")

        _cat.cached_catalogue = lambda: CATALOGUE   # late-bound: built in build()

        # THE negatives. Every one names something launchable and none is a request to launch
        # it. D38 for the sixth time — and the first time where a false match RUNS something.
        for utterance in [
            "how do i open a file in python",
            "why did my browser crash",
            "is firefox installed",
            "what is firefox",
            "what version of firefox is on here",
            "can firefox play this video",
            "how do i open a terminal",
            "what does thonny do",
            "the browser is slow",
            "firefox",                       # a bare noun is not an imperative
            "open",                          # a bare verb has no target
            "what time is it",
            "convert 5 amps to milliamps",
            "check the cpu temperature",
        ]:
            req = ask(utterance)
            check(req is None, f"{utterance!r} is NOT a launch",
                  f"WOULD LAUNCH {req.app!r} — a question just started a program" if req else "")

        # The question he asks is built with no model, and must be speakable.
        req = ask("open firefox")
        check(req.spoken == "Want me to open Firefox?",
              "the spoken question uses the catalogue's own Name, with no LLM call", req.spoken)
        check(is_speakable(req.spoken) is None, "and it is speakable")
        req = ask("open the browser")
        check(req.spoken == "Want me to open the browser?",
              "an ambiguous role word keeps its article rather than saying 'open browser'",
              req.spoken)

        # The planner reaches Router.route() and comes back on Reply.action. This is the seam
        # instant.py was built with; its log line assumed hardware.actions.Plan and crashed the
        # whole free tier on the first planner ever injected.
        reply = InstantRouter(planners={"launch": launch_intent.look_up}).route("open firefox")
        check(isinstance(reply.action, launch_intent.LaunchRequest),
              "Router.route carries the LaunchRequest on Reply.action")
        check(reply.text == "Want me to open Firefox?",
              "and Reply.text is the question, taken from `spoken`", reply.text)
        check(InstantRouter(planners={"launch": launch_intent.look_up}
                            ).route("why did my browser crash").action is None,
              "a planner that declines leaves Reply.action None")

        check(launch_intent.only_filler("please can you") is True, "filler is filler")
        check(launch_intent.only_filler("a file in python") is False,
              "and a real remainder is not")

        # --- the transcript, as tiny.en actually writes it -------------------------------
        #
        # Measured 2026-08-22: three of LB's four Firefox attempts said "fire fox", and every
        # one of them missed the catalogue, fell through to the paid router, reached the OS
        # agent's shell path and came back with an invented excuse. Exact CONCATENATION only —
        # this is not edit distance, which this module refuses for the reason it states.
        for utterance in ["open fire fox", "can you open fire fox",
                          "open fire fox, the internet browser",
                          "open fire fox the web browser"]:
            got = ask(utterance)
            check(got is not None and got.app == "firefox",
                  f"{utterance!r} is a launch", f"got {got.app if got else None}")

        check(ask("open firefox") is not None and ask("open fire fox") is not None,
              "written together or apart, both reach the free path")

        # --- and the anchor still holds. This is the half that matters. ------------------
        #
        # DESCRIPTOR is NOUNS ONLY, and that is the whole safety argument: a leftover built
        # only from nouns cannot express a second action. Mutation-tested on the Pi
        # 2026-08-22 — adding "delete"/"shut"/"down" to DESCRIPTOR makes both of the first
        # two below LAUNCH, so these checks bite.
        for utterance in ["open firefox and delete my files",
                          "open fire fox then shut down",
                          "how do I open a file in Python",
                          "why did my browser crash",
                          "is firefox installed",
                          "what is firefox",
                          "open the fire escape"]:
            check(ask(utterance) is None, f"{utterance!r} is NOT a launch",
                  f"got {getattr(ask(utterance), 'app', None)!r}")

        check(all(w.islower() and w.isalpha() for w in launch_intent.DESCRIPTOR),
              "DESCRIPTOR holds bare lowercase words only")
        check(not (launch_intent.DESCRIPTOR & set(launch_intent.LAUNCH_VERBS)),
              "and no launch verb has leaked into it — that would let a leftover start a "
              "second program")
    finally:
        _cat.cached_catalogue = real


def s8_gate_plumbing() -> None:
    section("8. the gate routes to the right tool, and an unknown one runs nothing")

    from engine.response import Pending

    legacy = Pending("os", {"command": "ls"}, "spoken", "ls")
    check(legacy.tool == "execute_terminal_command",
          "a 4-argument Pending still means the shell — every existing construction in the "
          "repo is positional and none of them was touched")

    launch_pending = Pending("os", {"app": "firefox"}, "q", "shown", "launch_app")
    check(launch_pending.tool == "launch_app", "a launch Pending names the launcher")

    import agents.os_agent as osa
    check(set(osa._RESUME) == {"execute_terminal_command", "launch_app"},
          "both tools are dispatchable", str(sorted(osa._RESUME)))

    shell = FakeShell()
    real_start, real_cat = launcher._start, cat.load_catalogue
    launcher._start = shell
    cat.load_catalogue = lambda dirs=None: with_targets(CATALOGUE, INSTALLED)
    try:
        r = osa.resume_os_action(Pending("os", {"tool": "nope"}, "q", "s", "not-a-real-tool"))
        check("unknown-tool" in r.raw, "an unrecognised tool name refuses", r.raw[:60])
        check(not shell.ran, "and starts NOTHING — dispatch is on the name, never on the "
                             "shape of tool_args, so an extra key cannot pick the shell")
    finally:
        launcher._start = real_start
        cat.load_catalogue = real_cat



def _make_lnk(path: Path, target: str) -> None:
    """Write a minimal but SPEC-CORRECT .lnk pointing at `target`.

    Synthesized rather than copied from the real Start Menu, for the reason every fixture in
    this file is synthesized: a harness that depends on what happens to be installed on the
    box running it tests the box, not the code. This one also has to run on the Pi, where
    there are no shortcuts to copy.

    The layout is MS-SHLLINK: a 76-byte ShellLinkHeader with LinkFlags = HasLinkInfo, then a
    LinkInfo block in its basic (0x1C header) form holding a VolumeID and a null-terminated
    ANSI LocalBasePath. Deliberately NOT the Unicode form — `shortcut_target()` reads both,
    and the ANSI branch is the one a hand-written fixture can exercise without ambiguity.
    """
    import struct

    header = bytearray(76)
    header[0:4] = struct.pack("<I", 0x4C)                       # HeaderSize, the magic
    header[4:20] = bytes.fromhex("01140200000000000000000000000046")   # CLSID
    header[20:24] = struct.pack("<I", 0x2)                      # LinkFlags = HasLinkInfo

    # VolumeID: size, drive type, serial, label offset, then a null label. 17 bytes.
    volume = struct.pack("<IIII", 0x11, 3, 0x12345678, 0x10) + b"\x00"

    base = target.encode("mbcs") + b"\x00"
    header_size = 0x1C
    volume_off = header_size
    base_off = volume_off + len(volume)
    suffix_off = base_off + len(base)
    info_size = suffix_off + 1                                  # + the empty suffix

    info = struct.pack("<IIIIIII", info_size, header_size, 0x1,
                       volume_off, base_off, 0, suffix_off)
    path.write_bytes(bytes(header) + info + volume + base + b"\x00")


def s7_start_menu() -> None:
    """The Windows backend: the Start Menu read as this platform's desktop-entry database.

    Runs on BOTH platforms and deliberately so. Every function under test is pure file
    parsing — no registry, no COM, no Windows API — so the Pi can prove the Windows reader
    still works, which is what stops it rotting the moment LB stops testing on the Pi.
    """
    import tempfile

    from tools.app_catalogue import (EXCLUDED_FOLDERS, SESSION_ENDING_WIN, load_start_menu,
                                     parse_shortcut, shortcut_target, start_menu_dirs)

    section("7. the Windows backend — the Start Menu is the desktop-entry database")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Autodesk").mkdir()
        (root / "Administrative Tools").mkdir()

        _make_lnk(root / "OpenSCAD.lnk", r"C:\Program Files\OpenSCAD\openscad.exe")
        _make_lnk(root / "Autodesk" / "Autodesk Fusion.lnk",
                  r"C:\Users\lb\AppData\Local\Autodesk\FusionLauncher.exe")
        _make_lnk(root / "Administrative Tools" / "Disk Cleanup.lnk",
                  r"C:\Windows\System32\cleanmgr.exe")
        _make_lnk(root / "Uninstall.lnk", r"C:\Program Files\Thing\uninstall.exe")
        _make_lnk(root / "Uninstall Node.js.lnk", r"C:\Windows\SysWOW64\msiexec.exe")

        # --- the parser ---
        got = shortcut_target(root / "OpenSCAD.lnk")
        check(got == r"C:\Program Files\OpenSCAD\openscad.exe",
              "shortcut_target reads the target out of a .lnk with no dependency", repr(got))

        (root / "broken.lnk").write_bytes(b"not a shortcut at all")
        check(shortcut_target(root / "broken.lnk") == "",
              "a malformed shortcut returns '' rather than raising",
              "one bad file must never cost LB the whole catalogue")
        check(shortcut_target(root / "nope.lnk") == "",
              "so does a missing one")

        # --- the loader ---
        apps = load_start_menu((root,))
        by_name = {a.name: a for a in apps}

        check("OpenSCAD" in by_name, "a top-level shortcut becomes an application",
              ", ".join(sorted(by_name)))
        check("Autodesk Fusion" in by_name, "so does one nested in a vendor folder")
        check("Disk Cleanup" not in by_name,
              "Administrative Tools is excluded wholesale — none of it is an app LB can mean")
        check("Uninstall" not in by_name,
              "a bare 'Uninstall' is excluded: removing a program is not opening one")
        check("Uninstall Node.js" not in by_name,
              "and so is a PREFIXED one — exact-match alone let this through, and "
              "resolve('uninstall') then offered to run it")

        # --- argv is the SHORTCUT, which is the whole trick ---
        openscad = by_name["OpenSCAD"]
        check(openscad.argv == (str(root / "OpenSCAD.lnk"),),
              "argv[0] is the .lnk, not the target — os.startfile makes Windows resolve it",
              str(openscad.argv))
        check(openscad.target == r"C:\Program Files\OpenSCAD\openscad.exe",
              "the target is carried alongside, for the 'is it installed' check")
        check(openscad.terminal is False,
              "Terminal is always False on Windows — a .lnk carries its own console decision")

        # --- the category, which is the folder ---
        fusion = by_name["Autodesk Fusion"]
        check("autodesk" in fusion.categories,
              "the containing folder becomes a category, as Categories= did on the Pi",
              str(fusion.categories))

        # --- resolution runs on these rows unchanged, which is the point of the port ---
        check(resolve("openscad", apps).ok, "resolve() works on Start Menu rows unchanged")
        check(resolve("fusion", apps).ok, "including a whole-word phrase match")
        check(not resolve("kicad", apps).ok,
              "and an application that is NOT installed resolves to nothing — which is the "
              "honest answer a hardcoded path could not give")

        # --- shadowing ---
        with tempfile.TemporaryDirectory() as tmp2:
            user = Path(tmp2)
            _make_lnk(user / "OpenSCAD.lnk", r"C:\Users\lb\OpenSCAD\openscad.exe")
            merged = {a.name: a for a in load_start_menu((user, root))}
            check(merged["OpenSCAD"].target.startswith(r"C:\Users"),
                  "a per-user shortcut shadows a machine-wide one of the same name",
                  merged["OpenSCAD"].target)

    check(load_start_menu((Path(tempfile.gettempdir()) / "definitely-not-here",)) == (),
          "a missing Start Menu directory is empty, not an error")

    # --- the directories, which is where the case-folding bug lived ---
    dirs = start_menu_dirs({"APPDATA": r"C:\U\R", "PROGRAMDATA": r"C:\PD"})
    check(len(dirs) == 2 and str(dirs[0]).startswith(r"C:\U\R"),
          "start_menu_dirs puts the USER directory first, so a user shortcut can shadow",
          str(dirs))
    check(start_menu_dirs({"AppData": r"C:\U\R", "ProgramData": r"C:\PD"}) == dirs,
          "and it is CASE-INSENSITIVE — dict(os.environ) loses Windows' case folding, "
          "which silently produced a catalogue of ZERO applications")
    check(start_menu_dirs({}) == (),
          "an environment naming neither directory yields none, rather than guessing a path")

    check(bool(EXCLUDED_FOLDERS) and bool(SESSION_ENDING_WIN),
          "both exclusion sets are non-empty on every platform",
          f"{len(EXCLUDED_FOLDERS)} folders, {len(SESSION_ENDING_WIN)} names")

def build() -> None:
    global CATALOGUE, BY_NAME
    CATALOGUE = _build_fixture_tree()
    BY_NAME = {a.name: a for a in CATALOGUE}

    s1_catalogue(); s2_resolution(); s3_argv(); s3b_display_free_launch()
    s4_outcomes(); s5_display(); s6_speech()
    s7_start_menu()
    s7_free_intent(); s8_gate_plumbing()


# =========================================================================================


def _rerun(groups) -> tuple[int, int]:
    """Re-run check groups with output suppressed. Returns (passed, failed)."""
    global PASSED, FAILED, QUIET
    p0, f0, QUIET = PASSED, FAILED, True
    try:
        for g in groups:
            try:
                g()
            except Exception as e:                                     # noqa: BLE001
                print(f"   (group {g.__name__} raised {type(e).__name__}: {e})")
    finally:
        QUIET = False
    passed, failed = PASSED - p0, FAILED - f0
    PASSED, FAILED = p0, f0
    return passed, failed


def probe() -> int:
    """Break each guard in turn and confirm the checks that cover it go red."""
    print("\n  PROBE: five mutations, each removing one guard\n")
    results = []

    # --- M1: fuzzy matching, the thing apps.py refuses in prose ---------------------------
    import difflib
    real_resolve = cat.resolve

    def fuzzy(query, catalogue):
        names = {a.name.lower(): a for a in catalogue}
        hit = difflib.get_close_matches((query or "").lower(), list(names), 1, 0.6)
        return cat.Match(app=names[hit[0]]) if hit else real_resolve(query, catalogue)

    cat.resolve = fuzzy
    globals()["resolve"] = fuzzy
    _, failed = _rerun([s2_resolution])
    cat.resolve = real_resolve
    globals()["resolve"] = real_resolve
    results.append(("M1 edit-distance matching", failed))
    print(f"   M1  fuzzy resolution           -> {failed} check(s) red "
          f"({'thonnyy/firefx now open the wrong program' if failed else 'NOT COVERED'})")

    # --- M2: pretend a display is always there --------------------------------------------
    real_find = launcher.find_display
    launcher.find_display = lambda environ=None: launcher.Display(
        monitors=1, detail="monitors = 1")
    _, failed = _rerun([s4_outcomes, s5_display])
    launcher.find_display = real_find
    results.append(("M2 display always assumed", failed))
    print(f"   M2  display assumed present    -> {failed} check(s) red "
          f"({'he launches into nothing and calls it success' if failed else 'NOT COVERED'})")

    # --- M3: the shipped bug, restored ----------------------------------------------------
    # This is the mutation that makes the harness evidence rather than decoration: it puts the
    # ACTUAL defect back and confirms section 6 catches it.
    real_speech = dict(_SPEECH)
    for kind in KINDS:
        legacy = Outcome(False, kind, "", "Firefox").text
        failed_by_prefix = legacy.startswith("Terminal Error:") or legacy.startswith("Action Blocked:")
        _SPEECH[kind] = ("That didn't work — the error is on the screen." if failed_by_prefix
                         else "Done. The output's on the screen.")
    _, failed = _rerun([s6_speech])
    _SPEECH.clear(); _SPEECH.update(real_speech)
    results.append(("M3 the old prefix check", failed))
    print(f"   M3  old startswith() reporting -> {failed} check(s) red "
          f"({'timeout and blocked are spoken as Done' if failed else 'NOT COVERED'})")

    # --- M4: the existence pre-flight removed ----------------------------------------------
    # `Type=exec` was the Pi's half of this and no longer exists to drop; `os.startfile`
    # raising is what replaced it, and M4b below mutates that. What is left to remove here is
    # the OTHER half, which survived the port unchanged: the check that the program named by
    # the entry is actually on the machine. That is the `nautilus` case.
    real_exists = launcher._exists
    launcher._exists = lambda path: True                 # everything is "installed"
    _, failed = _rerun([s4_outcomes])
    launcher._exists = real_exists
    results.append(("M4 existence pre-flight removed", failed))
    print(f"   M4  nautilus check removed     -> {failed} check(s) red "
          f"({'a missing program reports success' if failed else 'NOT COVERED'})")

    # --- M4b: a shell that fails silently ---------------------------------------------------
    # The Windows form of "silence reported as success", which is defect 4 of the original
    # bug. If `start()` swallowed its OSError, `launch()` would return `launched` for a
    # program that never started — and only section 4 stands between that and LB.
    real_start = launcher.start
    launcher.start = lambda path: None                   # never raises, never starts
    _, failed = _rerun([s3_argv, s3b_display_free_launch])
    launcher.start = real_start
    results.append(("M4b shell errors swallowed", failed))
    print(f"   M4b shell failure swallowed    -> {failed} check(s) red "
          f"({'a refused launch is spoken as Done' if failed else 'NOT COVERED'})")

    # --- M5: drop the end-anchor rule from the free launch intent -------------------------
    # The mutation that matters most in this file: without it, a QUESTION starts a program.
    real_filler = launch_intent_mod.only_filler
    launch_intent_mod.only_filler = lambda text: True
    _, failed = _rerun([s7_free_intent])
    launch_intent_mod.only_filler = real_filler
    results.append(("M5 end-anchor removed", failed))
    print(f"   M5  end-anchor removed         -> {failed} check(s) red "
          f"({'\"how do i open a file in python\" launches something' if failed else 'NOT COVERED'})")

    bitten = sum(1 for _, f in results if f > 0)
    print(f"\n  {bitten}/{len(results)} mutations bite")
    if bitten == len(results):
        print("\n  The harness BITES.\n")
        return 0
    for name, f in results:
        if f == 0:
            print(f"    NOT COVERED: {name}")
    print()
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="verify the desktop app launcher")
    ap.add_argument("--probe", action="store_true",
                    help="break each guard and confirm the checks go red")
    args = ap.parse_args()

    if args.probe:
        build()                          # populate, then discard the counts
        PASSED = FAILED = 0
        raise SystemExit(probe())

    build()
    print("\n" + "=" * 78)
    print(f"  {PASSED + FAILED} checks, {PASSED} passed, {FAILED} failed")
    print("=" * 78)
    if FAILED:
        print(f"\n  {FAILED} FAILED\n")
    else:
        print(f"\n  {PASSED}/{PASSED} checks passed — all green\n")
    raise SystemExit(1 if FAILED else 0)
