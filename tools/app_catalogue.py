#!/usr/bin/env python3
"""
Module:  app_catalogue.py
Purpose: What applications exist on this machine, and which one did LB mean.
Author:  LB
Date:    2026-08-21

    python tools/app_catalogue.py                 # list what he can open
    python tools/app_catalogue.py "the browser"   # resolve a phrase

## The machine already keeps this list. We read it; we do not curate it.

The source of truth is the XDG desktop-entry database — `~/.local/share/applications` and
`/usr/share/applications`. That is the same list the menu draws from, and it is the reason
"every app, current and future" needs no code change: `apt install vlc` and VLC is openable.

**The alternative was tried and it was already wrong.** `~/oddball/hardware/apps.py` is a
hand-written table of three rows, and on 2026-08-21 a `which` sweep of the Pi found `nautilus`
**missing** — so one row in three would have failed exactly the way that file's own comment
predicted: *"he says he opened it and nothing appears."* A curated list is a second copy of the
truth, and a second copy drifts. This one cannot: if the entry is gone, so is the app.

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
import shlex
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("oddball.catalogue")

__all__ = ["DesktopApp", "Match", "catalogue_dirs", "load_catalogue", "cached_catalogue",
           "parse_entry", "exec_argv", "resolve", "ROLES", "SESSION_ENDING"]

# Field codes an Exec line may carry, per the XDG Desktop Entry Specification. Every one of
# them is about a file or URL argument, an icon, or a label — none survives into an argv we
# build ourselves, because we are launching the app with no document.
#
# `%i` is the subtle one: it expands to TWO arguments (`--icon <Icon>`), so leaving it in place
# would pass the literal string "%i" to the program as if it were a filename.
_FIELD_CODES = frozenset("fFuUdDnNickvm")

# Role words, read off the entry's own Categories key. NOT a synonym table for named apps —
# that would be the curated list this module exists to avoid. These say "the kind of thing",
# and the machine says which programs are that kind.
# A leading article is stripped before these are consulted, so "the browser" and "browser" are
# one row, not two.
ROLES: dict[str, str] = {
    "browser": "WebBrowser",
    "web browser": "WebBrowser",
    "editor": "TextEditor",
    "text editor": "TextEditor",
    "file manager": "FileManager",
    "files": "FileManager",
    "terminal": "TerminalEmulator",
    "calculator": "Calculator",
}

_ARTICLES = ("the ", "a ", "an ")

# Dropped from the catalogue by resolved binary. Opening any of these ends the session or the
# machine, which is not a launch — it is the class of action `os_controller.refuse()` blocks on
# the shell path, and the launcher does not inherit that blocklist.
SESSION_ENDING: frozenset[str] = frozenset({
    "pishutdown", "lxsession-logout", "wayfire-logout", "systemctl", "shutdown",
    "poweroff", "reboot", "halt", "gnome-session-quit", "openbox--exit",
})


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
        categories: the `Categories=` key, split. Used for role words.
    """

    entry_id: str
    name: str
    argv: tuple[str, ...]
    path: str
    terminal: bool = False
    categories: tuple[str, ...] = ()

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


def catalogue_dirs(environ: dict | None = None) -> tuple[Path, ...]:
    """Where desktop entries live, most specific first.

    Order is load-bearing: a user entry in `~/.local/share/applications` must shadow a system
    one with the same id, which is how the XDG spec says a user overrides a package.
    """
    import os

    env = dict(os.environ if environ is None else environ)
    home = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME", "~")).expanduser() / ".local/share")
    dirs = [Path(home)]
    system = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs.extend(Path(p) for p in system.split(":") if p)
    return tuple(d / "applications" for d in dirs)


def exec_argv(exec_line: str) -> tuple[str, ...]:
    """Turn an `Exec=` value into an argv, with field codes removed.

    `shlex.split` runs FIRST and the codes are stripped per-token afterwards, because the Exec
    value is quoted by the spec's own rules — splitting a stripped string would mis-handle
    `Exec="/opt/My App/run" %f`, where the quotes are the only thing holding the path together.

    Args:
        exec_line: the raw value of the `Exec=` key.

    Returns:
        The argv, or `()` if the line is unusable. Never raises — a malformed entry is skipped,
        not fatal, because one bad file must not cost LB the whole catalogue.

    >>> exec_argv("/usr/bin/firefox %u")
    ('/usr/bin/firefox',)
    >>> exec_argv("thonny %F")
    ('thonny',)
    >>> exec_argv("env FOO=1 galculator --icon %i")
    ('env', 'FOO=1', 'galculator')
    >>> exec_argv("foo %%f bar")
    ('foo', '%f', 'bar')
    """
    try:
        tokens = shlex.split(exec_line)
    except ValueError:                      # an unbalanced quote is a refusal, not a guess
        LOG.warning("unparseable Exec line: %r", exec_line)
        return ()

    out: list[str] = []
    for token in tokens:
        # ONE pass, not a regex substitution followed by an unescape. `%%` is an escaped
        # percent and has to be consumed atomically: a regex that strips `%f` first turns
        # `%%f` into a bare `%`, losing the f. Scanning left to right is the only order in
        # which `%%f` (a literal percent, then the letter f) and `%f` (a filename slot) stay
        # distinguishable. The harness caught this; the first version was wrong.
        buf: list[str] = []
        i = 0
        while i < len(token):
            if token[i] == "%" and i + 1 < len(token):
                nxt = token[i + 1]
                if nxt == "%":
                    buf.append("%")         # an escaped percent survives
                    i += 2
                    continue
                if nxt in _FIELD_CODES:
                    i += 2                  # a field code is dropped
                    continue
            buf.append(token[i])
            i += 1
        stripped = "".join(buf)
        if stripped:                        # a token that was ONLY a field code disappears
            out.append(stripped)
    return tuple(out)


def parse_entry(text: str, path: str) -> DesktopApp | None:
    """One `.desktop` file to a `DesktopApp`, or None if it is not a launchable application.

    Only the `[Desktop Entry]` group is read. Later groups are actions ("Open a New Window"),
    which are a feature this does not offer and whose Exec lines would otherwise be mistaken
    for the main one.

    Args:
        text: the file's contents.
        path: where it came from, for the log and the card.

    Returns:
        A `DesktopApp`, or None when the entry is hidden, not an application, or has no usable
        Exec line. Never raises.
    """
    keys: dict[str, str] = {}
    in_group = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            # The main group is the only one read. Everything after it is skipped.
            if in_group:
                break
            in_group = line == "[Desktop Entry]"
            continue
        if not in_group or not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Localised keys look like `Name[de]`. The unlocalised one is what he speaks.
        if "[" not in key:
            keys.setdefault(key, value.strip())

    if keys.get("Type", "Application") != "Application":
        return None
    if keys.get("NoDisplay", "").lower() == "true":
        return None
    if keys.get("Hidden", "").lower() == "true":
        return None

    argv = exec_argv(keys.get("Exec", ""))
    if not argv:
        return None

    name = keys.get("Name") or Path(path).stem
    return DesktopApp(
        entry_id=Path(path).stem,
        name=name,
        argv=argv,
        path=path,
        terminal=keys.get("Terminal", "").lower() == "true",
        categories=tuple(c for c in keys.get("Categories", "").split(";") if c),
    )


def load_catalogue(dirs: tuple[Path, ...] | None = None) -> tuple[DesktopApp, ...]:
    """Every application this machine can open, sorted by name.

    Args:
        dirs: where to look. Defaults to `catalogue_dirs()`. The harness passes a temporary
              directory, which is what keeps this testable on a Windows box with no XDG tree.

    Returns:
        The catalogue. Empty is a legitimate answer (a machine with no desktop), not an error.
    """
    seen: dict[str, DesktopApp] = {}
    for directory in (dirs if dirs is not None else catalogue_dirs()):
        try:
            files = sorted(Path(directory).glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            if f.stem in seen:              # first directory wins — user shadows system
                continue
            try:
                app = parse_entry(f.read_text(encoding="utf-8", errors="replace"), str(f))
            except OSError:
                continue
            if app is None:
                continue
            if app.program in SESSION_ENDING:
                LOG.info("excluding %s (%s ends the session)", app.entry_id, app.program)
                continue
            seen[f.stem] = app
    return tuple(sorted(seen.values(), key=lambda a: a.name.lower()))


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

    dirs = catalogue_dirs()
    key = []
    for d in dirs:
        try:
            key.append((str(d), d.stat().st_mtime))
        except OSError:
            key.append((str(d), 0.0))
    frozen = tuple(key)

    if _CACHE is None or frozen != _CACHE_KEY:
        _CACHE, _CACHE_KEY = load_catalogue(dirs), frozen
        LOG.info("catalogue loaded: %d application(s)", len(_CACHE))
    return _CACHE


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
            print(f"    {app.name:<28} {app.entry_id:<30} {' '.join(app.argv)}{marker}")
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
