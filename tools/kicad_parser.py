#!/usr/bin/env python3
"""
Module:  kicad_parser.py
Purpose: Let the HARDWARE agent read LB's own KiCad designs — a schematic's bill of
         materials, and a board's layer/net/footprint census.
Author:  LB
Date:    2026-08-21

    python tools/kicad_parser.py tests/fixtures/kicad/flat.kicad_sch      # debug CLI
    python tools/kicad_parser.py tests/fixtures/kicad/two-layer.kicad_pcb

Two tools, bound to `agents/hardware_agent.py`. Both are pure functions of a path: string in,
string out, no network, no model, and they never raise — a tool that raises inside an agent
turn becomes a spoken traceback.

## Why kiutils and not a regex

A `.kicad_sch` is an S-expression, and the fields worth having are nested four deep inside
quoted strings that may themselves contain brackets. A regex over that is a parser that works
on the file you tested it against. `kiutils` is the library KiCad's own ecosystem uses; it
reads the 6/7/8 formats and gives back Python objects.

## Five things that are wrong with the obvious implementation

Each of these produces a *plausible* answer rather than a crash, which is the shape of bug this
repo cares about most (D8). All five are pinned by name in `tools/verify_kicad.py`.

**1. The attribute is `schematicSymbols`, not `symbols`.** kiutils 1.4.8 has no `.symbols` on
`Schematic`. Written inside the customary `try/except Exception`, that AttributeError comes back
as `"Failed to parse schematic: 'Schematic' object has no attribute 'symbols'"` — which reads
like a corrupt file, for every file. Measured before this module was written: 100% failure.

**2. `inBom` defaults to False in kiutils, and absence is indistinguishable from "no".** So
`if not symbol.inBom: continue` returns an **empty BOM** on any file that does not write the
`in_bom` token — and an empty BOM does not look like a failure, it looks like an empty sheet.
The exclusion rule here is therefore KiCad's own `#` reference prefix, which is stable across
every format version; `inBom` is consulted **only when the file demonstrably writes it**, which
is detected by some symbol in that file carrying `inBom=True`. See `_bom_field_is_live`.

**3. A multi-unit part is several symbol blocks with one reference.** A TL074 is one 14-pin
chip drawn as four amplifiers plus a power unit — five blocks, all `U1`. Counting blocks orders
five quad op-amps. De-duplicated by reference, and the unit carrying a real footprint wins,
because KiCad leaves the power unit's Footprint field empty.

**4. A hierarchical design keeps almost nothing on the root sheet.** The parts live in the
sub-sheet files named by each sheet's `Sheetfile` property. Reading only the file you were
handed gives a BOM of two connectors for a 90-part board. `_collect` walks them, resolving each
name against its *parent's* directory, with a visited-set so `hier-child -> hier-root` does not
recurse forever.

**5. `len(board.layers)` is not the layer count anybody means.** A two-layer board has 29
entries in that table once adhesive, paste, silkscreen, mask, courtyard, fab and the nine user
layers are counted. "This is a 29-layer board" is a confident, fluent, wrong sentence — exactly
D30's failure mode. The copper count is reported as the headline and the table size beside it.

## Paths, and why a name is accepted as well

He is voice-first. A dictated path does not survive Whisper — "slash home slash pi slash amp
dot kicad underscore sch" comes back as prose. So `_resolve` takes a real path when it is given
one (the typed channel, D6, can paste one), and otherwise treats the string as a **project
name** and searches `ODDBALL_KICAD_ROOT` (`.env`, default `~/kicad`). Matching ignores case,
spaces and punctuation, so "the amp board" finds `amp_board/amp_board.kicad_sch`. Two matches
are reported as two matches — never guessed between, because picking one silently is how you
answer a question about the wrong board.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from langchain_core.tools import tool

try:
    from kiutils.board import Board
    from kiutils.schematic import Schematic
except ImportError:                                                       # pragma: no cover
    Board = None
    Schematic = None

__all__ = ["extract_kicad_bom", "analyze_kicad_pcb", "kicad_root", "SCH_EXT", "PCB_EXT"]

SCH_EXT = ".kicad_sch"
PCB_EXT = ".kicad_pcb"

# Where a bare project NAME is looked up. Overridable from .env, matching the convention in
# engine/models.py — LB keeps his designs in different places on the Pi and on Windows.
DEFAULT_KICAD_ROOT = "~/kicad"

# A search that walks an entire home directory is a tool that appears to hang. These bound it.
_MAX_SEARCH_HITS = 400
_MAX_CANDIDATES_SHOWN = 6

# kiutils reads the whole file into memory and builds an object per token. A board this size is
# not something LB has, and refusing it with a sentence beats a two-minute silence mid-turn.
_MAX_BYTES = 50 * 1024 * 1024

_MISSING_KIUTILS = ("The kiutils library is not installed, so I cannot read KiCad files. "
                    "Install it with: pip install kiutils")


def kicad_root() -> Path:
    """The directory a bare project name is searched under.

    Falls back to the literal path rather than raising: `expanduser()` raises RuntimeError on
    Linux for a `~unknownuser` prefix, and this runs at call time on a value from the
    environment — a bad ODDBALL_KICAD_ROOT should make a lookup fail, not the module.
    """
    raw = os.environ.get("ODDBALL_KICAD_ROOT", DEFAULT_KICAD_ROOT)
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError, RuntimeError):
        return Path(raw)


# --- finding the file ------------------------------------------------------------------

def _slug(text: str) -> str:
    """Lowercase alphanumerics only, so 'the Amp Board' and 'amp_board' can be compared.

    Whisper punctuates and capitalises unpredictably and hears underscores as nothing at all,
    so any comparison that survives dictation has to throw all of that away first.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _describe_root(root: Path) -> str:
    """A sentence about where we looked, for the two error paths that need to say so."""
    if not root.exists():
        return (f"I looked under {root}, which does not exist. Set ODDBALL_KICAD_ROOT in .env "
                f"to wherever your KiCad projects live, or give me the full path to the file.")
    return (f"I looked under {root}. Set ODDBALL_KICAD_ROOT in .env if your projects are "
            f"somewhere else, or give me the full path to the file.")


def _search_by_name(name: str, ext: str) -> tuple[Path | None, str]:
    """Find one file called `name` under the KiCad root. Returns (path, error-message).

    Matches the file's own stem OR its parent directory's name, because KiCad names a project
    folder and the file inside it the same thing, and LB says the folder name.
    """
    root = kicad_root()
    if not root.is_dir():
        return None, f"I could not find a {ext} file for {name!r}. {_describe_root(root)}"

    wanted = _slug(name)
    if not wanted:
        return None, f"{name!r} is not a file path or a project name I can look up."

    hits: list[Path] = []
    for i, path in enumerate(root.rglob(f"*{ext}")):
        if i >= _MAX_SEARCH_HITS:
            break
        stem, parent = _slug(path.stem), _slug(path.parent.name)
        # Either direction: "amp" matches amp_board, and "the amp board" matches amp_board.
        if wanted in stem or stem in wanted or wanted in parent or parent in wanted:
            hits.append(path)

    if not hits:
        return None, f"I could not find a {ext} file for {name!r}. {_describe_root(root)}"

    # An exact stem match settles it — "amp" beside "amp_v2" is not really ambiguous.
    exact = [p for p in hits if _slug(p.stem) == wanted]
    if len(exact) == 1:
        return exact[0], ""
    if len(hits) == 1:
        return hits[0], ""

    shown = sorted({str(p) for p in (exact or hits)})[:_MAX_CANDIDATES_SHOWN]
    if len(shown) == 1:
        return Path(shown[0]), ""
    return None, ("That name matches more than one file and I will not guess between them. "
                  "Which of these did you mean?\n  " + "\n  ".join(shown))


def _resolve(path_or_name: str, ext: str) -> tuple[Path | None, str]:
    """Turn whatever the model passed into a real file. Returns (path, error-message).

    Accepts a full path, a relative path, or a bare project name. Never raises.
    """
    if not isinstance(path_or_name, str) or not path_or_name.strip():
        return None, "I need a file path or a project name, and I was given nothing."

    # Models like to hand back quoted paths, and dictation adds trailing punctuation.
    raw = path_or_name.strip().strip("'\"").strip().rstrip(".,;:")
    if not raw:
        return None, "I need a file path or a project name, and I was given nothing."

    try:
        candidate = Path(raw).expanduser()
    except (OSError, ValueError, RuntimeError):
        # RuntimeError is the Linux-only one, and the fuzzer on the Pi is what found it:
        # `~someuser/x` makes expanduser() look up a real account, and Python 3.13 raises
        # RuntimeError("Could not determine home directory.") when there is none. On Windows
        # the same input comes back unchanged, so 162/162 passed there and 161/162 on the Pi —
        # the harness's own "never raises" claim, false on the only machine he runs on.
        return None, f"{path_or_name!r} is not a usable file path."

    other = PCB_EXT if ext == SCH_EXT else SCH_EXT
    suffix = candidate.suffix.lower()

    try:
        exists = candidate.exists()
        is_dir = exists and candidate.is_dir()
    except OSError:
        exists = is_dir = False

    # A directory is a KiCad PROJECT — look inside it for the file of the type asked for.
    if is_dir:
        inside = sorted(candidate.glob(f"*{ext}"))
        if len(inside) == 1:
            return inside[0], ""
        if not inside:
            return None, (f"{candidate} is a folder with no {ext} file in it.")
        shown = "\n  ".join(str(p) for p in inside[:_MAX_CANDIDATES_SHOWN])
        return None, ("That folder holds more than one such file and I will not guess "
                      f"between them. Which did you mean?\n  {shown}")

    if suffix == other:
        which = "schematic" if ext == SCH_EXT else "board"
        return None, (f"{candidate.name} is a {other} file, and I was asked for the {which}. "
                      f"Point me at the {ext} instead.")

    if suffix and suffix not in (SCH_EXT, PCB_EXT):
        return None, (f"{candidate.name} is not a KiCad file — I read {SCH_EXT} and {PCB_EXT}, "
                      f"and that is a {suffix} file.")

    if suffix == ext:
        if exists:
            return candidate, ""
        # A real path that is not there is a missing file, not a project name.
        return None, f"There is no file at {candidate}."

    # No recognised extension: treat it as a project name and go looking.
    return _search_by_name(raw, ext)


def _too_big(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size > _MAX_BYTES:
        return (f"{path.name} is {size / 1e6:.0f} megabytes, which is larger than I will read "
                f"in the middle of a conversation.")
    return ""


# --- reading a schematic ---------------------------------------------------------------

def _props(symbol) -> dict[str, str]:
    """The symbol's properties as a plain dict. Missing fields become empty strings."""
    out: dict[str, str] = {}
    for prop in getattr(symbol, "properties", None) or []:
        key, value = getattr(prop, "key", None), getattr(prop, "value", None)
        if isinstance(key, str):
            out[key] = value if isinstance(value, str) else ""
    return out


def _bom_field_is_live(symbols) -> bool:
    """Whether this file actually writes `in_bom`, or kiutils is showing us its default.

    See defect 2 in the module docstring. kiutils sets `inBom = False` when the token is
    absent, so False means either "excluded" or "this format version does not have the field",
    and acting on it blind empties the BOM of any file in the second case. If ANY symbol says
    True, the token is being written and False means what it says.
    """
    return any(getattr(s, "inBom", False) is True for s in symbols)


def _footprint_name(raw: str) -> str:
    """`Resistor_SMD:R_0805_2012Metric` -> `R_0805_2012Metric`.

    The library half is almost never what you want to read back: the footprint name alone
    already identifies the part, and the prefix doubles the width of every BOM line.
    """
    return raw.split(":", 1)[1] if ":" in raw else raw


def _collect(path: Path, visited: set[Path], parts: dict[str, dict],
             stats: dict[str, int], missing: list[str], reused: list[str],
             cycles: list[str], stack: tuple[Path, ...] = ()) -> str:
    """Walk one sheet and everything below it, filling `parts` in place.

    Returns an error string if THIS file could not be read at all; sub-sheets that fail are
    recorded in `missing` and do not abort the walk — a BOM of nine sheets out of ten, clearly
    labelled, beats no BOM.

    Two different things stop the walk revisiting a file, and they mean opposite things:

        in `stack`    a CYCLE — this sheet is its own ancestor. KiCad does not allow it, so
                      the file is malformed and the user wants to hear that.
        in `visited`  a REPEATED SHEET — the same sub-sheet placed twice, which is normal and
                      deliberate (two identical channels). Its parts really are on the board
                      twice, and counting the file once undercounts them.
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved in stack:
        cycles.append(path.name)
        return ""
    if resolved in visited:
        reused.append(path.name)
        return ""
    visited.add(resolved)
    stack = stack + (resolved,)

    try:
        schematic = Schematic.from_file(str(path))
    except Exception as exc:                                              # noqa: BLE001
        return f"{path.name} could not be read: {type(exc).__name__}: {exc}"

    symbols = getattr(schematic, "schematicSymbols", None) or []
    bom_field_live = _bom_field_is_live(symbols)
    stats["sheets"] += 1

    for symbol in symbols:
        fields = _props(symbol)
        ref = (fields.get("Reference") or "").strip()

        if not ref:
            stats["no_reference"] += 1
            continue
        # KiCad's own marker for a pseudo-symbol: power ports (#PWR) and flags (#FLG). Stable
        # across every format version, which is why it and not `inBom` is the primary rule.
        if ref.startswith("#"):
            stats["power"] += 1
            continue
        if "?" in ref:
            stats["unannotated"] += 1
            continue
        if bom_field_live and getattr(symbol, "inBom", True) is False:
            stats["excluded"] += 1
            continue

        value = (fields.get("Value") or "").strip() or "?"
        footprint = _footprint_name((fields.get("Footprint") or "").strip())
        dnp = bool(getattr(symbol, "dnp", False))

        # Defect 3: several blocks, one part. First block wins, EXCEPT that a later unit
        # carrying a real footprint beats an earlier one that has none.
        seen = parts.get(ref)
        if seen is None:
            parts[ref] = {"value": value, "footprint": footprint, "dnp": dnp}
        else:
            stats["extra_units"] += 1
            if footprint and not seen["footprint"]:
                seen["footprint"] = footprint
            if value != "?" and seen["value"] == "?":
                seen["value"] = value

    # Defect 4: down into the sub-sheets, each name relative to THIS file's directory.
    for sheet in getattr(schematic, "sheets", None) or []:
        name_prop = getattr(sheet, "fileName", None)
        child = getattr(name_prop, "value", None)
        if not isinstance(child, str) or not child.strip():
            continue
        # Same RuntimeError trap as `_resolve`, and this one is reachable from a FILE rather
        # than from LB: a sheet whose fileName begins `~someuser` is a sub-sheet reference in
        # somebody else's project, not an attack, and it must read as a missing child.
        try:
            child_path = (path.parent / child.strip()).expanduser()
            exists = child_path.exists()
        except (OSError, ValueError, RuntimeError):
            missing.append(child.strip())
            continue
        if not exists:
            missing.append(child.strip())
            continue
        _collect(child_path, visited, parts, stats, missing, reused, cycles, stack)

    return ""


def _ref_sort_key(ref: str) -> tuple[str, int, str]:
    """R1, R2, R10 — not R1, R10, R2. Splits the prefix from the number."""
    match = re.match(r"^(\D*)(\d*)(.*)$", ref)
    if not match:
        return (ref, 0, "")
    return (match.group(1), int(match.group(2) or 0), match.group(3))


def _format_bom(path: Path, parts: dict[str, dict], stats: dict[str, int],
                missing: list[str], reused: list[str], cycles: list[str]) -> str:
    """Render the grouped BOM. Plain ASCII, and wide enough to read on the HUD card."""
    if not parts:
        empty = f"KiCad BOM - {path.name}\nNo components found."
        excluded = _excluded_line(stats)
        return f"{empty}\n{excluded}" if excluded else empty

    groups: dict[tuple[str, str, bool], list[str]] = defaultdict(list)
    for ref, part in parts.items():
        groups[(part["value"], part["footprint"], part["dnp"])].append(ref)

    rows = []
    for (value, footprint, dnp), refs in groups.items():
        refs.sort(key=_ref_sort_key)
        rows.append((len(refs), value, footprint or "(no footprint)", dnp, refs))
    # Most-used part first; ties broken by value so the order is stable between runs.
    rows.sort(key=lambda r: (-r[0], r[1].lower()))

    qty_w = max(len(str(r[0])) for r in rows) + 1
    val_w = min(max(len(r[1]) for r in rows), 18)
    fp_w = min(max(len(r[2]) for r in rows), 34)

    lines = [f"KiCad BOM - {path.name}",
             f"{len(parts)} parts in {len(rows)} lines across "
             f"{stats['sheets']} sheet{'s' if stats['sheets'] != 1 else ''}",
             ""]
    for qty, value, footprint, dnp, refs in rows:
        shown = ", ".join(refs)
        flag = "  [DNP - fitted but not populated]" if dnp else ""
        lines.append(f"  {str(qty) + 'x':<{qty_w + 1}} {value:<{val_w}}  "
                     f"{footprint:<{fp_w}}  {shown}{flag}")

    excluded = _excluded_line(stats)
    if excluded:
        lines += ["", excluded]
    if missing:
        lines.append(f"WARNING: {len(missing)} sub-sheet file(s) named but not found: "
                     f"{', '.join(sorted(set(missing))[:4])}. Those parts are NOT in this list.")
    if reused:
        lines.append(f"NOTE: {', '.join(sorted(set(reused))[:4])} is placed more than once. "
                     f"A repeated sheet's parts are counted ONCE here; on the real board there "
                     f"is one set per instance.")
    if cycles:
        lines.append(f"WARNING: {', '.join(sorted(set(cycles))[:4])} is its own parent sheet. "
                     f"That is a malformed hierarchy and KiCad will reject it.")
    return "\n".join(lines)


def _excluded_line(stats: dict[str, int]) -> str:
    """What was left out and why. Silence here is how a part goes missing before assembly."""
    bits = []
    if stats["power"]:
        bits.append(f"{stats['power']} power symbol(s) and flag(s)")
    if stats["unannotated"]:
        bits.append(f"{stats['unannotated']} unannotated (reference still has a '?')")
    if stats["excluded"]:
        bits.append(f"{stats['excluded']} marked excluded from the BOM")
    if stats["no_reference"]:
        bits.append(f"{stats['no_reference']} with no reference at all")
    if stats["extra_units"]:
        bits.append(f"{stats['extra_units']} extra unit(s) of multi-unit parts, "
                    f"counted once each")
    return "Excluded: " + "; ".join(bits) + "." if bits else ""


# --- the tools -------------------------------------------------------------------------

@tool
def extract_kicad_bom(file_path: str) -> str:
    """Read a KiCad schematic and return its Bill of Materials.

    Use this whenever the user asks what is on a schematic, what parts a design uses, how many
    of some component there are, or for a BOM or parts list. Identical parts are grouped with a
    quantity and the references that make it up. Sub-sheets of a hierarchical design are
    included automatically.

    Args:
        file_path: the path to a .kicad_sch file, the folder of a KiCad project, or just the
            project's name — a name is searched for under the ODDBALL_KICAD_ROOT directory.
    """
    if Schematic is None:
        return _MISSING_KIUTILS

    path, problem = _resolve(file_path, SCH_EXT)
    if problem:
        return problem
    oversize = _too_big(path)
    if oversize:
        return oversize

    parts: dict[str, dict] = {}
    stats = defaultdict(int)
    missing: list[str] = []
    reused: list[str] = []
    cycles: list[str] = []
    try:
        failure = _collect(path, set(), parts, stats, missing, reused, cycles)
    except RecursionError:
        return (f"{path.name} nests sub-sheets too deeply to follow. That usually means the "
                f"hierarchy refers back to itself.")
    except Exception as exc:                                              # noqa: BLE001
        return f"I could not read {path.name}: {type(exc).__name__}: {exc}"
    if failure:
        return failure
    return _format_bom(path, parts, stats, missing, reused, cycles)


@tool
def analyze_kicad_pcb(file_path: str) -> str:
    """Read a KiCad PCB layout and report its layer stack, nets and placed footprints.

    Use this when the user asks how many layers a board has, how many nets or components are on
    it, how thick it is, or for a general summary of a layout.

    Args:
        file_path: the path to a .kicad_pcb file, the folder of a KiCad project, or just the
            project's name — a name is searched for under the ODDBALL_KICAD_ROOT directory.
    """
    if Board is None:
        return _MISSING_KIUTILS

    path, problem = _resolve(file_path, PCB_EXT)
    if problem:
        return problem
    oversize = _too_big(path)
    if oversize:
        return oversize

    try:
        board = Board.from_file(str(path))
    except Exception as exc:                                              # noqa: BLE001
        return f"I could not read {path.name}: {type(exc).__name__}: {exc}"

    layers = list(getattr(board, "layers", None) or [])
    nets = list(getattr(board, "nets", None) or [])
    footprints = list(getattr(board, "footprints", None) or [])

    # Defect 5. Copper is what "a four-layer board" means; the table also holds silkscreen,
    # mask, paste, courtyard, fab and nine user layers, and reporting THAT number as the layer
    # count is a wrong answer that sounds authoritative.
    copper = [L for L in layers if str(getattr(L, "name", "")).endswith(".Cu")]
    # Net 0 is KiCad's unassigned net and exists on every board, including an empty one.
    named_nets = [n for n in nets if str(getattr(n, "name", "") or "").strip()]

    thickness = getattr(getattr(board, "general", None), "thickness", None)

    packages: dict[str, int] = defaultdict(int)
    for fp in footprints:
        packages[str(getattr(fp, "entryName", "") or "?")] += 1
    top = sorted(packages.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    lines = [
        f"KiCad PCB - {path.name}",
        f"Copper layers: {len(copper)}"
        + (f"  ({', '.join(str(L.name) for L in copper)})" if copper else ""),
        f"Total layers defined in the file: {len(layers)}  "
        f"(copper plus silkscreen, mask, paste, courtyard, fab and user layers)",
        f"Nets: {len(named_nets)} named, {len(nets)} including KiCad's unassigned net 0",
        f"Footprints placed: {len(footprints)}",
    ]
    if thickness is not None:
        lines.append(f"Board thickness: {thickness} mm")
    if top:
        lines.append("Most-used footprints: "
                     + "; ".join(f"{name} x{count}" for name, count in top))
    return "\n".join(lines)


# --- debug CLI -------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="read a KiCad schematic or board (debug CLI)")
    ap.add_argument("path", help=f"a {SCH_EXT} or {PCB_EXT} file, a project folder, or a name")
    args = ap.parse_args(argv)

    wants_pcb = args.path.lower().endswith(PCB_EXT)
    fn = analyze_kicad_pcb if wants_pcb else extract_kicad_bom
    print(fn.invoke({"file_path": args.path}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
