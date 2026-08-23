#!/usr/bin/env python3
"""
Module:  file_manager.py
Purpose: Empty `data/inbox/` — file what LB uploaded, and rebuild whatever index it feeds.
Author:  LB
Date:    2026-08-23

    python tools/file_manager.py --list                       # what is waiting
    python tools/file_manager.py --file syllabus.pdf --as academic
    python tools/file_manager.py --file amp.kicad_sch --as schematic --project amp_board

`engine/server.py` receives a file from the paperclip in the chat panel and drops it in
`data/inbox/`. Everything after that is these tools, bound to the agents — so the sorting is a
decision Mr Odd Ball makes out loud, and LB can disagree with it in the same sentence.

## Three destinations, and what each one costs

    academic     -> data/academic/           rebuild the vector store AND the deadline calendar
    datasheet    -> data/<folder>/           rebuild the vector store
    schematic    -> data/projects/<project>/ nothing, unless it brought a PDF with it

`tools/kicad_parser.py` reads `.kicad_sch` and `.kicad_pcb` live off the disk, so a schematic is
usable the instant it is moved. A PDF is not: nothing in this repo can read a PDF except through
the vector store, so a PDF landing anywhere under `data/` means an index rebuild.

## The rebuild does NOT run inline, and that is the load-bearing decision here

`tools/vector_db.build_vector_database()` imports `langchain_huggingface`, which imports **torch
and transformers**, and then re-embeds every page under `data/`.

**Measured on the Pi 2026-08-23: 11.4 s before a single chunk is embedded**, then 14.4 ms per
chunk — `import torch` 2.1 s, `import langchain_huggingface` 1.3 s, and 8.0 s loading
`all-MiniLM-L6-v2` off the SD card. The first rebuild after a reboot pays ~2 s more for a cold
page cache. `media/data/2026-08-23-index-rebuild-familyhub.csv`.

That fixed toll is paid however small the upload, and the per-chunk part multiplies by **the
whole corpus** rather than by the new file, because `build_vector_database()` rebuilds both
collections from scratch. So the first datasheet costs ~15 s and the fiftieth costs a great deal
more, which is why nothing here promises LB a duration.

These tools are called from inside an agent turn. Even the 11.4 s floor is a face frozen in the
`thinking` pose with the microphone shut and an LLM call waiting underneath it — and it only
grows, because it is the whole corpus being re-embedded. `run_voice.py`'s idle timer would
eventually drop him to `sleeping` mid-work, which is the exact shape of the bug `engine/turn.py`
documents at its permission gate ("he falls asleep while running a command").

Eleven seconds is not the number that was assumed when this was written — "minutes" was, and
every sentence in this repo that said so has been corrected to the measurement. It still settles
the design the same way, which is the useful thing about having measured it: an eleven-second
freeze on every upload is not acceptable either, and the alternative was never in doubt once
there was a number instead of a guess.

So `process_inbox_file` does the part that is instantaneous and always true — the move — and
hands the rebuild to a background thread. **What it returns says which of the two happened**,
and it deliberately does not say how long, because the honest answer depends on how much LB has
uploaded so far. `index_status` is how he answers "is it ready yet".

## Why the calendar extraction is now incremental

`extract_deadlines_from_syllabi()` re-reads every syllabus and spends one API call per file. D3
measured the free tier at 20 requests per model per day, so uploading one syllabus to a folder
holding five would spend a quarter of the day's quota re-extracting four files that have not
changed. It now takes a `sources` argument, and this module passes the one file that arrived.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from langchain_core.tools import tool

# The upload endpoint owns two things this module also needs: where the inbox is, and what
# counts as waiting in it. Imported rather than restated — `engine/server.py` is stdlib-only, so
# this costs nothing, and the alternative is two definitions that were already measured
# disagreeing on the Pi. `tools/academic_calendar.py` imports from `engine/` for the same reason.
#
# The fallback is for `python tools/file_manager.py`, which puts `tools/` on `sys.path` and NOT
# the repo root — so this import works when an agent imports the module and fails when the CLI
# runs it. `main()` inserts the root, but that is far too late: module-level imports have
# already run by then. Caught on the Pi, by running the CLI after the deploy.
#
# Guarded rather than an unconditional `sys.path.insert`, so importing this module from an
# agent has no side effect on the interpreter's search path.
try:
    from engine.server import INBOX_DIR, pending_uploads
except ModuleNotFoundError:                                           # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from engine.server import INBOX_DIR, pending_uploads

LOG = logging.getLogger("oddball.files")

__all__ = ["INBOX_DIR", "PROJECTS_DIR", "FILE_TOOLS", "FILE_INSTRUCTION",
           "list_inbox", "process_inbox_file", "list_project_files", "index_status",
           "run_file_calls", "file_followup_prompt"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

# Anchored to the repo, not to the working directory — a systemd unit starts from its own
# WorkingDirectory, and `Path("data")` would put these somewhere else there. Same reasoning as
# `HUD_DIR` in orchestrator/hud_bridge.py and `VAULT_DIR` in tools/knowledge_vault.py.
#
# `INBOX_DIR` is NOT redefined here — it is imported from `engine/server.py` above, because the
# endpoint that writes into it and the tool that empties it must not be able to disagree about
# where it is. These two are this module's alone: nothing else writes them.
ACADEMIC_DIR = DATA_DIR / "academic"
PROJECTS_DIR = DATA_DIR / "projects"

# Where a datasheet goes when the model does not name a folder. `data/` already holds
# `arduino/`, `espressif/`, `raspberry_pi/` and `sensors/`, and the prompt below lists them so
# the model can pick one — this is the fallback for a part that fits none of them.
DEFAULT_DATASHEET_FOLDER = "datasheets"

# What a KiCad-shaped file is, for the "this obviously belongs in a project" guess in
# `list_inbox`. Imported nowhere else — `tools/kicad_parser.py` owns the two it can PARSE, and
# this is the wider set that belongs beside them in a project folder.
_PROJECT_SUFFIXES = frozenset({".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_prl",
                               ".net", ".zip"})

# One safe path segment. Everything else becomes an underscore, so a project name chosen by a
# model cannot walk out of `data/projects/`. Same rule as tools/knowledge_vault._safe_segment.
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._ -]+")

# Zip guards. A gerber bundle is a few dozen small files; these bounds are two orders of
# magnitude above that and exist to make a zip bomb a sentence rather than a full disk.
_MAX_ZIP_MEMBERS = 2000
_MAX_ZIP_BYTES = 256 * 1024 * 1024


def _safe_segment(text: str, fallback: str) -> str:
    """One filesystem-safe path component. Never empty, never `.` or `..`, never nested."""
    cleaned = _SAFE_SEGMENT.sub("_", (text or "").strip().replace("\\", "/").split("/")[-1])
    cleaned = cleaned.strip(". ").strip()
    return cleaned or fallback


def _slug(text: str) -> str:
    """Lowercase alphanumerics only, so 'the Amp Board.pdf' and 'amp_board' compare equal.

    He is voice-first, and a filename read back by Whisper keeps none of its punctuation. Same
    function, for the same reason, as `tools/kicad_parser._slug`.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Categories the model may pass, and the one word each maps to. A family rather than one exact
# string for the same reason `engine/core._QUIZ_EXITS` is a family: the model will say
# "syllabus" where the prompt said "academic", and refusing that is a tool that does not
# trigger for a reason nobody can see.
#
# Keys are SLUGGED at build time, because the lookup slugs what the model sent — and the
# harness caught `project_file` failing for exactly that reason: it is a word the prompt offers
# by name, `_slug` turns it into `projectfile`, and the literal key with the underscore in it
# could never be hit. A lookup table whose keys are not in the form the lookup uses is a table
# with silent holes in it.
_CATEGORIES = {_slug(word): key for word, key in {
    "academic": "academic", "syllabus": "academic", "syllabi": "academic",
    "course": "academic", "coursework": "academic", "class": "academic",
    "datasheet": "datasheet", "datasheets": "datasheet", "component": "datasheet",
    "part": "datasheet", "reference": "datasheet", "manual": "datasheet",
    "schematic": "schematic", "project_file": "schematic", "project": "schematic",
    "projectfile": "schematic", "pcb": "schematic", "board": "schematic",
    "kicad": "schematic", "gerber": "schematic",
}.items()}


# ---------------------------------------------------------------------------------------
# The background rebuild
# ---------------------------------------------------------------------------------------

@dataclass
class _IndexState:
    """What the rebuild thread is doing, readable from any thread. See `index_status`."""

    running: bool = False
    started: float = 0.0
    finished: float = 0.0
    ok: bool = True
    detail: str = ""
    jobs: set[str] = field(default_factory=set)


class _Indexer:
    """Runs `vector_db` and `academic_calendar` off the turn path, one at a time.

    Two properties matter and neither is obvious:

    **Coalescing.** Uploading three datasheets in ten seconds must not queue three full
    re-embeddings of `data/`. A request that arrives while a rebuild is running sets a flag; the
    running thread notices at the end and goes round once more. Three uploads therefore cost at
    most two passes, and the second one sees all three files.

    **Never raising.** This runs on a thread nobody joins. An exception here would be swallowed
    by the interpreter and leave `running` stuck True forever, so every path sets `finished`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._sources: set[str] = set()
        self._thread: threading.Thread | None = None
        self.state = _IndexState()

    def request(self, jobs: set[str], sources: set[str] | None = None) -> None:
        """Ask for `jobs` ("vectors", "calendar"), starting the worker if it is not running."""
        with self._lock:
            self._pending |= jobs
            self._sources |= (sources or set())
            if self._thread is not None and self._thread.is_alive():
                LOG.info("a rebuild is already running — %s will be picked up by it",
                         ", ".join(sorted(jobs)))
                return
            self._thread = threading.Thread(target=self._run, name="reindex", daemon=True)
            self.state = _IndexState(running=True, started=time.monotonic(), jobs=set(jobs))
            self._thread.start()

    def _take(self) -> tuple[set[str], set[str]]:
        with self._lock:
            jobs, sources = self._pending, self._sources
            self._pending, self._sources = set(), set()
            return jobs, sources

    def _run(self) -> None:
        notes: list[str] = []
        ok = True
        try:
            while True:
                jobs, sources = self._take()
                if not jobs:
                    break
                with self._lock:
                    self.state.jobs |= jobs
                if "vectors" in jobs:
                    good, note = self._rebuild_vectors()
                    ok &= good
                    notes.append(note)
                if "calendar" in jobs:
                    good, note = self._rebuild_calendar(sources)
                    ok &= good
                    notes.append(note)
        except BaseException as exc:                                   # noqa: BLE001
            # BaseException, not Exception: this thread is the only place these run, and a
            # MemoryError from embedding a 400-page datasheet on a Pi must still clear
            # `running` or `index_status` reports a rebuild that stopped an hour ago.
            LOG.exception("the rebuild thread died")
            ok = False
            notes.append(f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self.state.running = False
                self.state.finished = time.monotonic()
                self.state.ok = ok
                self.state.detail = " ".join(n for n in notes if n)
            LOG.info("rebuild finished (ok=%s): %s", ok, self.state.detail)

    @staticmethod
    def _capture(fn, *args, **kwargs) -> str:
        """Run a build function, returning what it printed instead of letting it print.

        Both build scripts are `print()`-based CLIs. Left alone, their output interleaves with
        the per-turn timing lines in `oddball.log`, which is a measurement file — and the useful
        half of what they say ("3 page(s) carried NO extractable text") is worth keeping where
        `index_status` can hand it back rather than scrolling past in a journal.
        """
        import contextlib                                             # noqa: PLC0415

        buffer = StringIO()
        with contextlib.redirect_stdout(buffer):
            fn(*args, **kwargs)
        text = buffer.getvalue().strip()
        for line in text.splitlines():
            LOG.info("  %s", line)
        return text

    def _rebuild_vectors(self) -> tuple[bool, str]:
        try:
            from tools.vector_db import build_vector_database         # noqa: PLC0415

            t0 = time.monotonic()
            self._capture(build_vector_database)
            return True, f"Vector store rebuilt in {time.monotonic() - t0:.0f}s."
        except Exception as exc:                                      # noqa: BLE001
            LOG.exception("rebuilding the vector store failed")
            return False, f"The vector store rebuild failed: {type(exc).__name__}: {exc}"

    def _rebuild_calendar(self, sources: set[str]) -> tuple[bool, str]:
        try:
            from tools.academic_calendar import extract_deadlines_from_syllabi  # noqa: PLC0415

            t0 = time.monotonic()
            self._capture(extract_deadlines_from_syllabi, sources=sources or None)
            which = ", ".join(sorted(sources)) if sources else "every syllabus"
            return True, f"Deadlines extracted from {which} in {time.monotonic() - t0:.0f}s."
        except Exception as exc:                                      # noqa: BLE001
            LOG.exception("extracting deadlines failed")
            return False, f"The deadline extraction failed: {type(exc).__name__}: {exc}"

    def status(self) -> _IndexState:
        with self._lock:
            return _IndexState(running=self.state.running, started=self.state.started,
                               finished=self.state.finished, ok=self.state.ok,
                               detail=self.state.detail, jobs=set(self.state.jobs))


_INDEXER = _Indexer()


# ---------------------------------------------------------------------------------------
# Finding the file the model named
# ---------------------------------------------------------------------------------------

def inbox_files() -> list[Path]:
    """Every file waiting in the inbox, oldest first. Empty when there is nothing there.

    Delegates to `engine.server.pending_uploads`, which owns the definition — including the
    dotfile rule that the first live upload and then the first Pi deploy each caught half of.
    `INBOX_DIR` is passed rather than defaulted so a harness pointing this module at a temporary
    tree still works.
    """
    return pending_uploads(INBOX_DIR)


def _find_in_inbox(filename: str) -> tuple[Path | None, str]:
    """Resolve whatever the model called the file. Returns (path, error-message).

    An exact name wins. Failing that the name is slugged and compared, because the model is
    working from a sentence LB spoke or a filename it half-remembers — and because the exact
    string it was given may have been `data/inbox/foo.pdf` rather than `foo.pdf`.

    Two matches are reported as two matches and never guessed between, for the same reason
    `tools/kicad_parser._search_by_name` refuses to: filing the wrong document is a mistake that
    surfaces days later as an answer about the wrong course.
    """
    waiting = inbox_files()
    if not waiting:
        return None, ("There is nothing in the inbox. Nothing has been uploaded, or it has "
                      "already been filed.")

    raw = (filename or "").strip().strip("'\"").rstrip(".,;:")
    name = raw.replace("\\", "/").split("/")[-1]
    if not name:
        return None, "I need the name of a file in the inbox, and I was given nothing."

    for path in waiting:
        if path.name == name:
            return path, ""

    wanted = _slug(name)
    if not wanted:
        return None, f"{filename!r} is not a filename I can look up."

    hits = [p for p in waiting if wanted == _slug(p.name) or wanted == _slug(p.stem)]
    if not hits:
        hits = [p for p in waiting if wanted in _slug(p.name) or _slug(p.stem) in wanted]

    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, (f"There is no file called {name!r} in the inbox. What is waiting: "
                      + ", ".join(p.name for p in waiting))
    return None, ("That name matches more than one file in the inbox and I will not guess "
                  "between them: " + ", ".join(p.name for p in hits))


def guess_category(path: Path) -> str:
    """The category a filename suggests, or "" when it suggests nothing.

    A hint for the model and for `list_inbox`, never a decision. Filenames are the weakest
    evidence in this repo — `data/sensors/academic_press_sensor.pdf` is the case
    `tools/vector_db.load_pdfs` already had to be fixed for — so this suggests and the model
    (or LB) chooses.
    """
    if path.suffix.lower() in _PROJECT_SUFFIXES:
        return "schematic"
    flat = _slug(path.stem)
    if any(word in flat for word in ("syllabus", "syllabi", "coursesyllabus", "coursepolicy",
                                     "courseoutline", "gradingpolicy")):
        return "academic"
    if re.search(r"(?<![a-z])(ece|eee|phys|math|cs|ee)\d{2,4}(?![a-z])", flat):
        return "academic"
    if any(word in flat for word in ("datasheet", "reference manual", "referencemanual",
                                     "usermanual", "appnote", "applicationnote")):
        return "datasheet"
    if any(word in flat for word in ("schematic", "gerber", "pcb", "layout", "bom")):
        return "schematic"
    return ""


# ---------------------------------------------------------------------------------------
# Moving it
# ---------------------------------------------------------------------------------------

def _unique(directory: Path, filename: str) -> Path:
    """A path in `directory` that does not exist yet. Never overwrites — see `engine/server.py`."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, dot, suffix = filename.rpartition(".")
    stem, suffix = (stem, "." + suffix) if dot else (filename, "")
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"a thousand files are already called {filename}")


def _where(directory: Path) -> str:
    """A directory as a short repo-relative posix path, for saying out loud.

    `relative_to` RAISES when the path is not under the repo, and these tools are called from
    inside an agent turn where a raised exception becomes a spoken traceback. That is not
    hypothetical — `tools/verify_upload.py` points the module at a temp tree, and the first
    version of this line took the whole harness down with a `ValueError` from `pathlib`.
    """
    try:
        return directory.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(directory)


def _move(source: Path, destination_dir: Path) -> Path:
    """Move one file, returning where it landed.

    `shutil.move`, not `Path.rename`: on the Pi `data/` and `/tmp` can be different filesystems,
    and `rename` across one raises `OSError: [Errno 18] Invalid cross-device link` — which would
    surface to LB as "I could not file that" for a reason that has nothing to do with him.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = _unique(destination_dir, source.name)
    shutil.move(str(source), str(target))
    LOG.info("filed %s -> %s", source.name, target)
    return target


def _extract_zip(archive: Path, destination_dir: Path) -> tuple[list[Path], str]:
    """Unpack a zip into `destination_dir`, refusing anything that tries to leave it.

    A gerber bundle or a zipped KiCad project is useless to `tools/kicad_parser.py` while it is
    still a zip, so this is what makes "upload the board as a zip" work at all.

    `ZipFile.extractall` is not used, and that is the whole reason this function exists. It will
    happily write a member called `../../.ssh/authorized_keys`, and the archive here came off
    the network from a browser. Every member is checked before a byte is written:

    - the name is normalised, and anything absolute or containing `..` is skipped
    - symlink members are skipped, because a symlink is a traversal that passes a name check
    - the member count and the total UNCOMPRESSED size are capped, so a 40 KB zip cannot
      become a full SD card

    Returns:
        (written_paths, note) — the note names anything skipped, and is empty when nothing was.
    """
    written: list[Path] = []
    skipped: list[str] = []
    total = 0

    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        if len(members) > _MAX_ZIP_MEMBERS:
            return [], (f"{archive.name} holds {len(members)} entries, and I unpack at most "
                        f"{_MAX_ZIP_MEMBERS}. I have left it as a zip.")

        for member in members:
            if member.is_dir():
                continue
            # The high 16 bits of external_attr are the Unix mode; 0xA000 is S_IFLNK.
            if (member.external_attr >> 16) & 0xF000 == 0xA000:
                skipped.append(f"{member.filename} (a symlink)")
                continue

            parts = [p for p in member.filename.replace("\\", "/").split("/")
                     if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts) or member.filename.startswith("/"):
                skipped.append(f"{member.filename} (points outside the folder)")
                continue

            total += member.file_size
            if total > _MAX_ZIP_BYTES:
                skipped.append(f"everything after {member.filename} (over "
                               f"{_MAX_ZIP_BYTES // (1024 * 1024)} MB unpacked)")
                break

            safe = [_safe_segment(p, "part") for p in parts]
            target = destination_dir.joinpath(*safe)
            # Third check, after normalising and after sanitising each segment. Two guards that
            # both have to fail is the standard this repo already holds itself to in
            # `knowledge_vault._resolve` and `engine/server.save_upload`.
            if destination_dir.resolve() not in target.resolve().parents:
                skipped.append(f"{member.filename} (resolved outside the folder)")
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(target)

    note = ""
    if skipped:
        note = " Skipped " + "; ".join(skipped[:5]) + ("." if len(skipped) <= 5
                                                       else f" and {len(skipped) - 5} more.")
    return written, note


# ---------------------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------------------

@tool
def list_inbox() -> str:
    """
    Lists the files LB has uploaded that have NOT been filed yet (`data/inbox/`).
    Call this when he mentions uploading, attaching or adding a file and you do not know its
    exact name, or when he asks what you have not dealt with yet.
    Takes no arguments.
    """
    waiting = inbox_files()
    if not waiting:
        return "The inbox is empty — nothing is waiting to be filed."

    lines = [f"{len(waiting)} file(s) waiting in data/inbox/:"]
    for path in waiting:
        size_kb = max(1, path.stat().st_size // 1024)
        hint = guess_category(path)
        lines.append(f"  {path.name}  ({size_kb} KB)"
                     + (f"  — looks like a {hint}" if hint else "  — category unclear"))
    return "\n".join(lines)


@tool
def process_inbox_file(filename: str, category: str, project: str = "",
                       folder: str = "") -> str:
    """
    Files ONE uploaded document out of `data/inbox/` and rebuilds whatever index it feeds.
    Use this whenever LB says he has uploaded, attached or added a file.

    `filename`: the file's name as shown by `list_inbox`, e.g. 'ECE350_syllabus.pdf'.
    `category`: one of
        'academic'  — a course syllabus, schedule or policy document. Goes to data/academic/,
                      and its deadlines are extracted into the calendar.
        'datasheet' — a component datasheet, reference manual or application note. Goes to a
                      folder under data/ and becomes searchable by the firmware agent.
        'schematic' — a KiCad file (.kicad_sch, .kicad_pcb, .kicad_pro), a gerber or project
                      zip, or a PDF of a board. Goes to data/projects/, where the hardware and
                      firmware agents can read it.
    `project`: for 'schematic' only — the project folder to put it in, e.g. 'amp_board'.
               Leave empty to name the folder after the file.
    `folder`: for 'datasheet' only — which folder under data/ to use. The ones that already
              exist are 'arduino', 'espressif', 'raspberry_pi' and 'sensors'. Leave empty for
              'datasheets'.

    If you are not sure which category a file is, ASK LB rather than guessing. Filing a
    document in the wrong place gives him a wrong answer days later.
    """
    source, error = _find_in_inbox(filename)
    if source is None:
        return error

    key = _CATEGORIES.get(_slug(category), "")
    if not key:
        return (f"I do not have a category called {category!r}. It has to be 'academic', "
                f"'datasheet' or 'schematic'. {source.name} is still in the inbox.")

    suffix = source.suffix.lower()
    try:
        if key == "academic":
            return _file_academic(source, suffix)
        if key == "datasheet":
            return _file_datasheet(source, suffix, folder)
        return _file_schematic(source, suffix, project)
    except OSError as exc:
        LOG.exception("could not file %s", source)
        return (f"I could not move {source.name}: {type(exc).__name__}: {exc}. It is still in "
                f"the inbox.")


def _file_academic(source: Path, suffix: str) -> str:
    if suffix != ".pdf":
        return (f"{source.name} is a {suffix} file. Syllabus text is only readable from a PDF — "
                f"nothing in the academic pipeline reads {suffix}, so filing it there would "
                f"make it invisible. It is still in the inbox.")

    target = _move(source, ACADEMIC_DIR)
    _INDEXER.request({"vectors", "calendar"}, sources={target.name})
    return (f"Filed {target.name} to data/academic/. I am now rebuilding the syllabus index and "
            f"reading its dates into the deadline calendar, in the background. It is NOT "
            f"searchable until that finishes. Use index_status to check.")


def _file_datasheet(source: Path, suffix: str, folder: str) -> str:
    destination = DATA_DIR / _safe_segment(folder, DEFAULT_DATASHEET_FOLDER)
    target = _move(source, destination)
    where = _where(destination)

    if suffix != ".pdf":
        # Moved anyway rather than refused: a .txt pinout next to the datasheets is a
        # reasonable thing to keep, and saying so is better than putting it somewhere else.
        return (f"Filed {target.name} to {where}/. Only PDFs are indexed, so a {suffix} file "
                f"sits there as a reference for LB rather than something I can search.")

    _INDEXER.request({"vectors"})
    return (f"Filed {target.name} to {where}/. I am rebuilding the datasheet index in the "
            f"background, and it is NOT searchable until that finishes. Use index_status to "
            f"check.")


def _file_schematic(source: Path, suffix: str, project: str) -> str:
    # The project folder is named after the file when the model does not name one. `_safe_segment`
    # of "" would give the fallback, so the stem is passed as the fallback rather than tested for.
    name = _safe_segment(project, _safe_segment(source.stem, "project"))
    destination = PROJECTS_DIR / name
    where = _where(destination)

    if suffix == ".zip":
        destination.mkdir(parents=True, exist_ok=True)
        try:
            written, note = _extract_zip(source, destination)
        except (zipfile.BadZipFile, OSError) as exc:
            target = _move(source, destination)
            return (f"{source.name} would not unpack ({type(exc).__name__}: {exc}), so I have "
                    f"put the zip itself in {where}/ as {target.name}.")

        if not written:
            target = _move(source, destination)
            return (f"Nothing in {source.name} could be unpacked safely.{note} I have left the "
                    f"zip in {where}/.")

        source.unlink(missing_ok=True)
        kicad = [p.name for p in written if p.suffix.lower() in (".kicad_sch", ".kicad_pcb")]
        pdfs = [p for p in written if p.suffix.lower() == ".pdf"]
        if pdfs:
            _INDEXER.request({"vectors"})

        headline = (f"Unpacked {source.name} into {where}/ — {len(written)} file(s).{note}")
        if kicad:
            return (f"{headline} It has {', '.join(kicad[:4])} in it, so I can read that board "
                    f"by name now — just call it {name}.")
        return (f"{headline} There is no KiCad schematic or board in it, so I can only tell LB "
                f"what files are there, not what is on them.")

    target = _move(source, destination)

    if suffix in (".kicad_sch", ".kicad_pcb"):
        kind = "schematic" if suffix == ".kicad_sch" else "board"
        return (f"Filed {target.name} to {where}/. I can read that {kind} now — ask me for its "
                f"bill of materials or its layers by calling it {name}.")

    if suffix == ".pdf":
        _INDEXER.request({"vectors"})
        return (f"Filed {target.name} to {where}/. It is a PDF, so I cannot pull a parts list "
                f"out of it the way I can from a .kicad_sch — I am indexing its text in the "
                f"background instead, and that is not finished yet.")

    return (f"Filed {target.name} to {where}/. It sits beside the project for LB to open; I "
            f"read .kicad_sch and .kicad_pcb, and index PDFs, but not {suffix} files.")


@tool
def list_project_files(project: str = "") -> str:
    """
    Lists the design files LB has uploaded to `data/projects/` — his schematics, boards,
    gerbers and board PDFs. Call this when he asks what projects or boards you have, or when
    you need a project's exact name before reading its schematic.
    `project`: one project's name to look inside it, or leave empty to list every project.
    """
    if not PROJECTS_DIR.exists():
        return ("There are no uploaded projects yet. data/projects/ is where a schematic goes "
                "when LB uploads one.")

    # Dotfiles skipped for the same reason as in `inbox_files`: `.gitkeep` is committed so the
    # directory survives a fresh clone, and listing it back to LB as an uploaded design file is
    # a lie about what he has.
    folders = sorted(p for p in PROJECTS_DIR.iterdir()
                     if p.is_dir() and not p.name.startswith("."))
    loose = sorted(p for p in PROJECTS_DIR.iterdir()
                   if p.is_file() and not p.name.startswith("."))
    if not folders and not loose:
        return ("There are no uploaded projects yet. data/projects/ is where a schematic goes "
                "when LB uploads one.")

    wanted = _slug(project)
    if wanted:
        matches = [f for f in folders if wanted in _slug(f.name) or _slug(f.name) in wanted]
        if not matches:
            return (f"I have no project called {project!r}. What I do have: "
                    + ", ".join(f.name for f in folders))
        folders, loose = matches, []

    lines: list[str] = []
    for folder in folders:
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        lines.append(f"{folder.name}/  ({len(files)} file(s))")
        for path in files[:25]:
            lines.append(f"    {path.relative_to(folder).as_posix()}")
        if len(files) > 25:
            lines.append(f"    ... and {len(files) - 25} more")
    for path in loose:
        lines.append(f"{path.name}  (not in a project folder)")

    return "Uploaded design files, under data/projects/:\n" + "\n".join(lines)


@tool
def index_status() -> str:
    """
    Reports whether the background rebuild of the document index has finished.
    Call this when LB asks whether an uploaded file is ready, searchable or indexed yet.
    Takes no arguments.
    """
    state = _INDEXER.status()
    if state.running:
        elapsed = time.monotonic() - state.started
        jobs = " and ".join(sorted(state.jobs)) or "the index"
        return (f"Still rebuilding {jobs} — {elapsed:.0f} seconds so far. Anything uploaded "
                f"since the last rebuild is NOT searchable yet.")
    if not state.finished:
        return ("Nothing has been rebuilt this session. Whatever was indexed before he started "
                "me is still indexed.")

    ago = time.monotonic() - state.finished
    when = f"{ago:.0f} seconds ago" if ago < 120 else f"{ago / 60:.0f} minutes ago"
    verdict = "finished" if state.ok else "FAILED"
    return f"The rebuild {verdict} {when}. {state.detail}".strip()


# ---------------------------------------------------------------------------------------
# Binding these into an agent
#
# Same two names as `tools/knowledge_vault.py` — a tool list and a block of prompt text — so an
# agent picks up the whole capability in two lines and the three agents that have it cannot end
# up describing it three different ways.
# ---------------------------------------------------------------------------------------

FILE_TOOLS = [list_inbox, process_inbox_file, list_project_files, index_status]
_BY_NAME = {t.name: t for t in FILE_TOOLS}

FILE_INSTRUCTION = """

UPLOADED FILES (the paperclip):
LB can upload a file straight into the chat with the paperclip button. It lands in an inbox and
stays there until you file it, so an upload he never hears about is an upload that does nothing.
- When he says he has uploaded, attached or added a file, call `process_inbox_file` with its
  name and a category: 'academic' for a syllabus, 'datasheet' for a component document,
  'schematic' for a KiCad file, a gerber zip or a board PDF.
- Use `list_inbox` first if you do not have the exact filename.
- Choose the category from the filename when it is obvious. When it is NOT obvious, ask him
  which it is in one short question and file it on his next answer — a syllabus filed as a
  datasheet is a wrong answer about his coursework three weeks later.
- For a schematic, pass a `project` name when he gives you one.
- `list_project_files` is how you find out which boards he has uploaded.
- Indexing runs in the BACKGROUND and is not instant. Never tell him a document is searchable,
  and do NOT promise him how long it will take — say it is being indexed, and use
  `index_status` when he asks whether it is ready.
- NEVER claim you have filed something unless the tool actually ran and said so.
"""


def run_file_calls(tool_calls: list[dict]) -> list[tuple[str, str]]:
    """Execute whichever file tools a model asked for.

    Args:
        tool_calls: LangChain's `response.tool_calls` — dicts with "name" and "args".

    Returns:
        One `(tool_name, result_text)` per call that named a file tool, in order. Calls naming
        anything else are skipped, so an agent with its own tools can pass the whole list
        through and handle the remainder itself — exactly as `run_vault_calls` does.
    """
    out: list[tuple[str, str]] = []
    for call in tool_calls or []:
        chosen = _BY_NAME.get(call.get("name", ""))
        if chosen is None:
            continue
        try:
            out.append((chosen.name, str(chosen.invoke(call.get("args", {})))))
        except Exception as exc:                                          # noqa: BLE001
            # The tools themselves never raise; binding the arguments does, when the model
            # invents a field or omits a required one. Logged WITH the arguments the model
            # chose, because "the tool did not trigger" is usually a tool called with a field
            # it does not have, and the sentence below never says which.
            LOG.exception("file tool %s failed to run with args=%r",
                          chosen.name, call.get("args", {}))
            out.append((chosen.name, f"That file tool could not be run: "
                                     f"{type(exc).__name__}: {exc}"))
    return out


def file_followup_prompt(base_prompt: str, results: list[tuple[str, str]]) -> str:
    """The second pass, after `run_file_calls` executed something.

    `knowledge_vault.followup_prompt` does the same job for the vault, and this is deliberately
    not that function with a flag: its closing paragraph is written entirely around notes being
    saved and read back, and pointing it at a filing operation would ask the model to "confirm
    where the note went" about a datasheet. Two short functions that each say the right thing,
    rather than one that says an approximate thing twice.

    The rule about indexing is repeated here because this is the prompt that produces the
    sentence LB actually hears, and "it's indexed" when it is not is the failure that matters.
    """
    block = "\n\n".join(f"`{name}` returned:\n{text}" for name, text in results)
    return (
        f"{base_prompt}\n\n"
        f"FILE TOOL RESULTS — these have ALREADY RUN. Do not call any tool again.\n{block}\n\n"
        "Answer LB now, using only what is above. Say plainly where the file went. If the "
        "result says indexing is running in the background, say it is being indexed and is not "
        "searchable yet — never that it is ready. If the result says the file is still in the "
        "inbox, say so and say what you need from him to file it."
    )


# ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="what is waiting in the inbox")
    ap.add_argument("--projects", action="store_true", help="what is in data/projects/")
    ap.add_argument("--file", metavar="NAME", help="a file in the inbox to process")
    ap.add_argument("--as", dest="category", metavar="CATEGORY",
                    help="academic | datasheet | schematic")
    ap.add_argument("--project", default="", help="project folder, for a schematic")
    ap.add_argument("--folder", default="", help="folder under data/, for a datasheet")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    # Windows defaults stdout to cp1252, and every sentence this module returns has an em dash
    # in it. Without this the CLI prints mojibake on the box the repo is authored on — the same
    # reconfigure main.py does, and for the same reason.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.projects:
        print(list_project_files.invoke({"project": ""}))
        return 0
    if not args.file:
        print(list_inbox.invoke({}))
        return 0
    if not args.category:
        print("--file needs --as academic|datasheet|schematic", file=sys.stderr)
        return 2

    print(process_inbox_file.invoke({
        "filename": args.file, "category": args.category,
        "project": args.project, "folder": args.folder}))

    # The CLI is synchronous even though the tool is not: a build kicked off on a daemon thread
    # would die with the interpreter the moment this function returned, and the operator would
    # be told a rebuild had started that never ran a single line.
    while _INDEXER.status().running:
        time.sleep(1.0)
    print(index_status.invoke({}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
