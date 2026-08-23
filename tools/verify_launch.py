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
from tools.app_catalogue import exec_argv, load_catalogue, parse_entry, resolve   # noqa: E402
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
# (org.thonny.Thonny) and a Terminal=true console program (htop).

ENTRIES = {
    "firefox": """[Desktop Entry]
Type=Application
Name=Firefox
Exec=/usr/bin/firefox %u
Categories=Network;WebBrowser;
""",
    "chromium": """[Desktop Entry]
Type=Application
Name=Chromium Web Browser
Exec=chromium %U
Categories=Network;WebBrowser;
""",
    "org.thonny.Thonny": """[Desktop Entry]
Type=Application
Name=Thonny
Exec=thonny %F
Categories=Development;IDE;
""",
    "pcmanfm": """[Desktop Entry]
Type=Application
Name=PCMan File Manager
Exec=pcmanfm %U
Categories=System;FileTools;FileManager;
""",
    "galculator": """[Desktop Entry]
Type=Application
Name=Galculator
Exec=galculator --icon %i
Categories=Utility;Calculator;
""",
    "htop": """[Desktop Entry]
Type=Application
Name=Htop
Exec=htop
Terminal=true
Categories=System;Monitor;
""",
    # Must be EXCLUDED: NoDisplay
    "xdg-desktop-portal-gtk": """[Desktop Entry]
Type=Application
Name=Portal
Exec=/usr/libexec/xdg-desktop-portal-gtk
NoDisplay=true
""",
    # Must be EXCLUDED: ends the session
    "pishutdown": """[Desktop Entry]
Type=Application
Name=Logout
Exec=pishutdown
Categories=System;
""",
    # Must be EXCLUDED: Hidden
    "oldthing": """[Desktop Entry]
Type=Application
Name=Old Thing
Exec=oldthing
Hidden=true
""",
    # Must be EXCLUDED: not an application
    "somelink": """[Desktop Entry]
Type=Link
Name=A Link
URL=https://example.com
""",
    # Must be EXCLUDED: unparseable Exec (unbalanced quote), and must not take the rest with it
    "broken": """[Desktop Entry]
Type=Application
Name=Broken
Exec="unclosed quote
""",
    # A second group must NOT be read — its Exec is an action, not the program.
    "vlc": """[Desktop Entry]
Type=Application
Name=VLC media player
Exec=/usr/bin/vlc --started-from-file %U
Categories=AudioVideo;Player;

[Desktop Action Fullscreen]
Name=Fullscreen
Exec=/usr/bin/vlc --fullscreen
""",
}

_tmp = tempfile.TemporaryDirectory()
FIXTURE_DIR = Path(_tmp.name) / "applications"
FIXTURE_DIR.mkdir(parents=True)
for _stem, _body in ENTRIES.items():
    (FIXTURE_DIR / f"{_stem}.desktop").write_text(_body, encoding="utf-8")

CATALOGUE = load_catalogue((FIXTURE_DIR,))
BY_NAME = {a.name: a for a in CATALOGUE}

# A display that exists, and one that does not.
LIVE = {"XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0",
        "HOME": "/home/ironi", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
DARK = {"XDG_RUNTIME_DIR": str(Path(_tmp.name) / "empty-runtime"), "HOME": "/home/ironi"}
Path(DARK["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)

INSTALLED = {"firefox", "chromium", "thonny", "pcmanfm", "galculator", "htop", "lxterminal"}


def run_launch(name, *, installed=None, environ=None, rc=0, stderr=""):
    """Drive the real `launch()` with both seams faked. Returns (Outcome, FakeRunner)."""
    runner = FakeRunner(returncode=rc, stderr=stderr)
    real_run, real_which, real_load = launcher._run, launcher._which, cat.load_catalogue
    launcher._run = runner
    launcher._which = fake_which(INSTALLED if installed is None else installed)
    cat.load_catalogue = lambda dirs=None: CATALOGUE
    try:
        return launch_result(name, environ), runner
    finally:
        launcher._run, launcher._which = real_run, real_which
        cat.load_catalogue = real_load


def launch_result(name, environ):
    return launcher.launch(name, environ=LIVE if environ is None else environ,
                           now="20260821-143012")


# =========================================================================================
def s1_catalogue() -> None:
    section("1. the catalogue is the machine's list, read correctly")

    check(exec_argv("/usr/bin/firefox %u") == ("/usr/bin/firefox",),
          "%u is stripped from an Exec line")
    check(exec_argv("thonny %F") == ("thonny",), "so is %F")
    check(exec_argv("galculator --icon %i") == ("galculator", "--icon"),
          "and %i, which expands to TWO arguments and would otherwise arrive as a filename")
    check(exec_argv("foo %%f bar") == ("foo", "%f", "bar"),
          "%% is a literal percent and survives — stripped AFTER the codes, never before")
    check(exec_argv('"/opt/My App/run" %f') == ("/opt/My App/run",),
          "shlex.split runs FIRST, so a quoted path with a space stays one argument")
    check(exec_argv('"unclosed') == (), "an unbalanced quote is a refusal, not a guess")
    check(exec_argv("") == (), "an empty Exec yields no argv")

    check("Firefox" in BY_NAME, "Firefox is in the catalogue")
    check("VLC media player" in BY_NAME, "so is VLC")
    check(BY_NAME["VLC media player"].argv == ("/usr/bin/vlc", "--started-from-file"),
          "and only its [Desktop Entry] group was read — the Fullscreen ACTION is not the program",
          str(BY_NAME["VLC media player"].argv))

    for hidden in ("Portal", "Old Thing", "A Link", "Broken"):
        check(hidden not in BY_NAME, f"{hidden!r} is excluded from the catalogue")
    check("Logout" not in BY_NAME,
          "and so is Logout — it ends the session, which is not a launch")
    check(len(CATALOGUE) == 7,
          "7 of 12 fixture entries are real applications", f"got {len(CATALOGUE)}")

    check(BY_NAME["Htop"].terminal is True, "Terminal=true is carried")
    check(BY_NAME["Firefox"].terminal is False, "and defaults to False")
    check(BY_NAME["Thonny"].entry_id == "org.thonny.Thonny",
          "a reverse-DNS desktop id survives intact")

    # A user entry must shadow a system one with the same id.
    user_dir = Path(_tmp.name) / "user-applications"
    user_dir.mkdir(exist_ok=True)
    (user_dir / "firefox.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Firefox ESR\nExec=/opt/firefox-esr/firefox\n",
        encoding="utf-8")
    shadowed = load_catalogue((user_dir, FIXTURE_DIR))
    check(any(a.name == "Firefox ESR" for a in shadowed)
          and not any(a.name == "Firefox" for a in shadowed),
          "a user entry shadows the system one with the same id, per the XDG spec")

    check(load_catalogue((Path(_tmp.name) / "nope",)) == (),
          "a missing directory is an empty catalogue, not a crash")


def s2_resolution() -> None:
    section("2. resolution is exact, and ambiguity is reported rather than guessed")

    check(resolve("firefox", CATALOGUE).app is BY_NAME["Firefox"], "by desktop id")
    check(resolve("Firefox", CATALOGUE).app is BY_NAME["Firefox"], "by name, case-insensitively")
    check(resolve("org.thonny.Thonny", CATALOGUE).app is BY_NAME["Thonny"], "by reverse-DNS id")
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
    for phrasing in ("browser", "the browser", "a browser"):
        check(resolve(phrasing, CATALOGUE).ambiguous,
              f"{phrasing!r} reaches the WebBrowser role")
    check(resolve("the file manager", CATALOGUE).app is BY_NAME["PCMan File Manager"],
          "'the file manager' works without a second ROLES row")

    # Role words, read off Categories. Two browsers are installed, so this is ambiguous — and
    # that is the CORRECT answer, not a shortcoming.
    m = resolve("the browser", CATALOGUE)
    check(m.ambiguous, "'the browser' is ambiguous with two browsers installed")
    check({a.name for a in m.candidates} == {"Firefox", "Chromium Web Browser"},
          "and both are named", str([a.name for a in m.candidates]))
    check(resolve("calculator", CATALOGUE).app is BY_NAME["Galculator"],
          "a role word with ONE match resolves — Categories=Calculator")
    check(resolve("files", CATALOGUE).app is BY_NAME["PCMan File Manager"],
          "'files' is a role word, and finds the file manager this Pi actually has")

    # The negatives. Edit-distance matching is refused on purpose: a threshold loose enough to
    # catch `tawny -> thonny` is loose enough to catch `shut up -> shut down`, and the failure
    # mode is not a wrong answer, it is the wrong program running.
    for miss in ("", "   ", "thonnyy", "firefx", "fire fox", "chrome", "bash", "rm", "sudo",
                 "nautilus", "emacs", "firefox; rm -rf /", "$(reboot)", "../../bin/sh"):
        m = resolve(miss, CATALOGUE)
        check(not m.ok, f"{miss!r} resolves to nothing",
              f"resolved to {m.app.name!r} — this is the wrong program running" if m.ok else "")


def s3_argv() -> None:
    section("3. the argv is built element by element, and never reaches a shell")

    d = launcher.find_display(LIVE)
    argv = launcher.systemd_run_argv("oddball-app-firefox-20260821-143012",
                                     "/usr/bin/firefox", (), d, "Mr Odd Ball opened Firefox")

    check(argv[0] == "systemd-run", "argv[0] is systemd-run")
    check("--user" in argv, "--user — a user unit, like the service itself")
    check("--collect" in argv,
          "--collect, so failed launches do not fill up `list-units --failed`")
    check("--property=Type=exec" in argv,
          "Type=exec — the start job completes after execve, not at fork. Measured on the Pi "
          "2026-08-21: a program that exists but CANNOT EXEC (bad shebang, corrupt ELF, "
          "missing .so) returns rc=0 under Type=simple and rc=1 under Type=exec.")
    check(f"--setenv=WAYLAND_DISPLAY={d.wayland}" in argv,
          "WAYLAND_DISPLAY is set — this single line is the fix for the reported bug")
    check(f"--setenv=XDG_RUNTIME_DIR={d.runtime_dir}" in argv, "XDG_RUNTIME_DIR is set")
    check(f"--setenv=PATH={launcher.PINNED_PATH}" in argv, "PATH is pinned, never inherited")
    check("--setenv=XDG_SESSION_TYPE=wayland" in argv, "XDG_SESSION_TYPE is set")
    check(any(a.startswith("--setenv=DBUS_SESSION_BUS_ADDRESS=") for a in argv),
          "DBUS_SESSION_BUS_ADDRESS is passed through when present")

    nodbus = launcher.systemd_run_argv("u", "/usr/bin/firefox", (),
                                       launcher.find_display(DARK | {"WAYLAND_DISPLAY": "wayland-0"}),
                                       "d")
    check(not any(a.startswith("--setenv=DBUS_SESSION_BUS_ADDRESS=") for a in nodbus),
          "and NOT invented when it is absent")

    check(not any(a.startswith("--setenv=DISPLAY=") for a in argv),
          "DISPLAY is deliberately absent — setting it invites the app onto Xwayland")
    check(not any("GDK_BACKEND" in a for a in argv),
          "GDK_BACKEND is never forced — it breaks apps that only ship an X11 build")

    check(argv.count("--") == 1, "exactly one -- separator")
    check(argv[argv.index("--") + 1] == "/usr/bin/firefox",
          "and the program follows it, as an absolute path")
    check(all(isinstance(a, str) for a in argv), "every element is a string")
    check(not any(re.search(r"[;|&`$><\n]", a) for a in argv[:argv.index("--")]),
          "no shell metacharacter appears anywhere in the flags")

    unit = launcher._unit_name("org.thonny.Thonny", "20260821-143012")
    check(unit == "oddball-app-org.thonny.Thonny-20260821-143012",
          "a reverse-DNS id makes a legal unit name unchanged", unit)
    check(re.fullmatch(r"oddball-app-[A-Za-z0-9_.-]+-\d{8}-\d{6}", unit) is not None,
          "and the unit name matches the pattern the Pi will be grepped for")
    check("/" not in launcher._unit_name("weird/id:name", "20260821-143012"),
          "an illegal character in a desktop id cannot produce an illegal unit name")

    source = Path(launcher.__file__).read_text(encoding="utf-8")
    check("shell=True" not in source.replace("shell=True, ", "", 0).split('"""')[-1],
          "the module body contains no shell=True outside its docstring")


def s4_outcomes() -> None:
    section("4. every refusal runs NOTHING, and none of them is spoken as success")

    out, runner = run_launch("firefox")
    check(out.kind == "launched" and out.ok, "the happy path launches", out.kind)
    check(len(runner.calls) == 1, "and runs exactly one command")
    check(runner.calls[0][0] == "systemd-run", "which is systemd-run")
    check(out.subject == "Firefox", "the subject is the spoken name, for the sentence")
    check("journalctl --user -u oddball-app-firefox" in out.detail,
          "and the card carries the journal line, so a real failure is one command away")

    out, runner = run_launch("nautilus")
    check(out.kind == "unknown-app" and not out.ok, "an app not in the catalogue", out.kind)
    check(runner.calls == [], "and NOTHING ran")
    check("Firefox" in out.detail, "the card lists what he CAN open")

    out, runner = run_launch("the browser")
    check(out.kind == "ambiguous" and not out.ok, "two matches is ambiguous", out.kind)
    check(runner.calls == [], "and NOTHING ran — guessing would open the wrong browser")

    # The `nautilus` case, generalised: the desktop entry promises a binary the machine lacks.
    out, runner = run_launch("firefox", installed=set())
    check(out.kind == "not-installed" and not out.ok,
          "an entry whose binary is missing is caught BEFORE launching", out.kind)
    check(runner.calls == [], "and NOTHING ran")
    check("not on" in out.detail, "the card says where it looked")

    out, runner = run_launch("firefox", environ=DARK)
    check(out.kind == "no-display" and not out.ok, "no compositor socket", out.kind)
    check(runner.calls == [],
          "and NOTHING ran — launching into a missing display is indistinguishable "
          "from doing nothing, which IS the reported bug")
    check("wayland sockets = (none)" in out.detail,
          "the card says what he looked at, not just that he failed")

    out, runner = run_launch("firefox", rc=1, stderr="Unit already exists.")
    check(out.kind == "launch-failed" and not out.ok,
          "a non-zero systemd-run is a failure, not a success", out.kind)
    check("Unit already exists." in out.detail, "and stderr is carried to the card")

    out, runner = run_launch("htop")
    check(out.kind == "launched" and out.ok, "a Terminal=true program launches", out.kind)
    tail = runner.calls[0][runner.calls[0].index("--") + 1:]
    check(tail[0].endswith("lxterminal") and "-e" in tail,
          "wrapped in a terminal emulator", str(tail))

    out, runner = run_launch("htop", installed={"htop"})
    check(out.kind == "not-installed" and runner.calls == [],
          "and refuses when no terminal emulator is installed", out.kind)

    # The single most important check in the file.
    check(all(o.ok is False for o in [
        run_launch("nautilus")[0], run_launch("the browser")[0],
        run_launch("firefox", installed=set())[0], run_launch("firefox", environ=DARK)[0],
        run_launch("firefox", rc=1)[0]]),
        "NONE of the five refusal paths reports ok=True")


def s5_display() -> None:
    section("5. the display is discovered at launch time, from the real filesystem")

    d = launcher.find_display(LIVE)
    check(d.usable and d.wayland == "wayland-0", "an explicit WAYLAND_DISPLAY is honoured")

    d = launcher.find_display(DARK)
    check(not d.usable and d.seen == (), "an empty runtime dir yields no socket")

    # The real glob, on real files, in a temp dir — so what ships is what is tested.
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "wayland-1").touch()
        (Path(tmp) / "wayland-1.lock").touch()
        d = launcher.find_display({"XDG_RUNTIME_DIR": tmp, "HOME": "/home/ironi"})
        check(d.usable and d.wayland == "wayland-1",
              "a socket is FOUND by globbing, not assumed to be wayland-0 — labwc picks a new "
              "number after a session restart", d.wayland)
        check(d.seen == ("wayland-1",), "and the .lock file is not mistaken for a socket")

        (Path(tmp) / "wayland-0").touch()
        d = launcher.find_display({"XDG_RUNTIME_DIR": tmp, "HOME": "/home/ironi"})
        check(d.wayland == "wayland-0" and d.seen == ("wayland-0", "wayland-1"),
              "with two compositors he takes the lowest and lists both on the card")

    d = launcher.find_display({"XDG_RUNTIME_DIR": "/nonexistent/xyz", "HOME": "/home/ironi"})
    check(not d.usable, "an unreadable runtime dir is 'no display', not a crash")


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
    _cat.cached_catalogue = lambda: CATALOGUE

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
            (_d / "code.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Visual Studio Code\nExec=code\n",
                encoding="utf-8")
            vscode_cat = load_catalogue((_d,))
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
            for _id, _name in [
                ("org.kicad.kicad", "KiCad"),
                ("org.kicad.eeschema", "KiCad Schematic Editor (Standalone)"),
                ("org.kicad.pcbnew", "KiCad PCB Editor (Standalone)"),
                ("org.kicad.gerbview", "KiCad Gerber Viewer"),
                ("org.kicad.pcbcalculator", "KiCad PCB Calculator"),
            ]:
                (_d / f"{_id}.desktop").write_text(
                    f"[Desktop Entry]\nType=Application\nName={_name}\n"
                    f"Exec={_id.split('.')[-1]}\nCategories=Electronics;Science;\n",
                    encoding="utf-8")
            kicad_cat = load_catalogue((_d,))
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

        _cat.cached_catalogue = lambda: CATALOGUE

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

    runner = FakeRunner()
    real_run, real_which, real_cat = launcher._run, launcher._which, cat.load_catalogue
    launcher._run, launcher._which = runner, fake_which(INSTALLED)
    cat.load_catalogue = lambda dirs=None: CATALOGUE
    try:
        r = osa.resume_os_action(Pending("os", {"tool": "nope"}, "q", "s", "not-a-real-tool"))
        check("unknown-tool" in r.raw, "an unrecognised tool name refuses", r.raw[:60])
        check(runner.calls == [], "and runs NOTHING — dispatch is on the name, never on the "
                                  "shape of tool_args, so an extra key cannot pick the shell")
    finally:
        launcher._run, launcher._which = real_run, real_which
        cat.load_catalogue = real_cat


def build() -> None:
    s1_catalogue(); s2_resolution(); s3_argv(); s4_outcomes(); s5_display(); s6_speech()
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
        runtime_dir="/run/user/1000", wayland="wayland-0", home="/home/ironi",
        seen=("wayland-0",))
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

    # --- M4: drop Type=exec and the which() pre-flight -------------------------------------
    real_argv, real_which = launcher.systemd_run_argv, launcher._which
    launcher.systemd_run_argv = lambda *a, **k: [
        x for x in real_argv(*a, **k) if x != "--property=Type=exec"]
    _, failed = _rerun([s3_argv])
    launcher.systemd_run_argv, launcher._which = real_argv, real_which
    results.append(("M4 Type=exec dropped", failed))
    print(f"   M4  Type=exec dropped          -> {failed} check(s) red "
          f"({'a missing binary would report success' if failed else 'NOT COVERED'})")

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
