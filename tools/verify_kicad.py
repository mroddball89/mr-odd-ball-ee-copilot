#!/usr/bin/env python3
"""
Module:  verify_kicad.py
Purpose: Prove the KiCad tools read a real design correctly, refuse everything else with a
         sentence, and never put a bill of materials into the speech channel.
Author:  LB
Date:    2026-08-21

    python tools/verify_kicad.py

Thirteenth harness, same contract as the other twelve: exit 0 = all passed, no microphone, no
model, no network, no API key, and every claim measured rather than asserted.

## What earns its place here

Every section below exists because the obvious implementation gets it wrong **and returns a
plausible answer** rather than crashing. That is the failure shape this repo keeps meeting
(D8, D30): a number said fluently and confidently, wrong, with nothing in the logs.

**The attribute name.** `Schematic.symbols` does not exist in kiutils — it is
`schematicSymbols`. Inside the customary `try/except Exception` that becomes
`"Failed to parse schematic: ... has no attribute 'symbols'"`, which reads like a corrupt file,
on every file. Pinned by asserting the real attribute exists AND that the tool returns parts,
so a kiutils rename goes red here rather than in front of LB.

**The empty BOM.** kiutils defaults `inBom` to False, so a filter written as
`if not symbol.inBom: continue` returns nothing at all for a file that omits the `in_bom`
token. `no-inbom.kicad_sch` is exactly that file, and the check is stated as a refusal of the
wrong answer: the BOM must NOT be empty.

**The multi-unit part.** A TL074 is five symbol blocks all called U1. The check is that the
quantity is 1 — and separately that the footprint survives, because the block KiCad writes for
the power unit has an empty Footprint field and a first-one-wins parser keeps that one.

**The hierarchy.** The parts of a hierarchical design are not in the file you open. Stated
both ways: the child's parts must be present, and the root-only count must NOT be the answer.

**The layer count.** A two-layer board has 29 entries in its layer table. "29 layers" is the
single most likely wrong sentence this tool could produce, so it is pinned by name.

**The speech channel.** The BOM must reach the HUD and never the voice. Rather than asserting
something about the tool's own text, this runs the real `engine.split.split()` over a reply
shaped exactly as `hardware_agent` builds one, and checks the listing is in a card and the
speech is clean.
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "kicad"

# Set BEFORE importing the parser, because kicad_root() reads it per call but the name-search
# section below depends on it pointing at the fixtures rather than at LB's real ~/kicad.
os.environ["ODDBALL_KICAD_ROOT"] = str(FIXTURES)

from tools import kicad_parser as K                                       # noqa: E402
from tools.kicad_parser import analyze_kicad_pcb, extract_kicad_bom       # noqa: E402

RESULTS: list[tuple[bool, str, str, str]] = []
_section = ""


def section(name: str) -> None:
    global _section
    _section = name


def check(ok: bool, msg: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), _section, msg, detail))
    return bool(ok)


def bom(name: str) -> str:
    return extract_kicad_bom.invoke({"file_path": str(FIXTURES / name)})


def pcb(name: str) -> str:
    return analyze_kicad_pcb.invoke({"file_path": str(FIXTURES / name)})


# ============================================================ 0. the library is really there

section("kiutils")

check(K.Schematic is not None and K.Board is not None,
      "kiutils imported — Schematic and Board are available",
      "pip install kiutils" if K.Schematic is None else "")

if K.Schematic is None:
    print("\n  kiutils is not installed; nothing below can be measured.\n"
          "  pip install kiutils\n")
    raise SystemExit(1)

# THE ATTRIBUTE THAT DOES NOT EXIST. `.symbols` is the name everybody reaches for and it is
# wrong; a bare except turns that into "your file is corrupt". Pinned so a kiutils rename
# fails HERE and not in front of LB.
import dataclasses                                                        # noqa: E402

_sch_fields = {f.name for f in dataclasses.fields(K.Schematic)}
check("schematicSymbols" in _sch_fields,
      "Schematic.schematicSymbols exists (the field the BOM is actually built from)",
      f"fields: {sorted(_sch_fields)}")
check("symbols" not in _sch_fields,
      "Schematic.symbols does NOT exist — the obvious name is the wrong one, still",
      "if this went green, kiutils added it and the docstring needs revisiting")

check(FIXTURES.is_dir(), f"the fixture directory exists", str(FIXTURES))


# ============================================================ 1. a flat schematic

section("flat schematic")

flat = bom("flat.kicad_sch")

check("could not be read" not in flat and "no components" not in flat.lower(),
      "flat.kicad_sch parses and yields parts", flat.splitlines()[0] if flat else "")

# Every part that should be there, by reference.
for ref in ("R1", "R2", "R3", "R4", "C1", "C2", "U1", "D1"):
    check(ref in flat, f"{ref} is in the BOM")

check("8 parts" in flat, "eight distinct parts counted",
      next((l for l in flat.splitlines() if "parts in" in l), flat[:60]))

# Values and footprints came off the file, not out of thin air.
check("10k" in flat and "R_0805_2012Metric" in flat,
      "values and footprints are read from the symbol properties")
check("100k" in flat and "R_0603_1608Metric" in flat,
      "a second footprint size is distinguished from the first")

# Grouping: three 10k resistors on ONE line naming all three references.
tenk = next((l for l in flat.splitlines() if "10k" in l), "")
check("3x" in tenk, "the three 10k resistors are grouped as a quantity of 3", tenk.strip())
for ref in ("R1", "R2", "R4"):
    check(ref in tenk, f"...and {ref} is named on that line", tenk.strip())
check("R3" not in tenk, "...while the 100k is NOT on it (different value, different line)",
      tenk.strip())

# The library prefix is stripped for width; the footprint is still identifiable.
check("Resistor_SMD:" not in flat,
      "the footprint library prefix is stripped from the listing")


# ============================================================ 2. THE MULTI-UNIT PART

section("multi-unit")

# A TL074 is FIVE symbol blocks, every one of them "U1". Counting blocks orders five chips.
u1 = next((l for l in flat.splitlines() if "TL074" in l), "")
check(bool(u1), "the TL074 appears in the BOM", flat)
check(u1.strip().startswith("1x"),
      "a five-unit TL074 is ONE part, not five",
      f"line reads: {u1.strip()!r}")
check("5x" not in u1 and "4x" not in u1,
      "and specifically not 4 or 5 — the wrong answers are refused by name", u1.strip())
check(flat.count("U1") == 1, "U1 is listed exactly once", f"counted {flat.count('U1')}")

# The power unit's Footprint field is EMPTY. A first-one-wins parser can keep that emptiness
# and lose a real footprint — so assert the real one survived.
check("SOIC-14_3.9x8.7mm_P1.27mm" in u1,
      "the footprint survives even though one unit of the part has none", u1.strip())
check("(no footprint)" not in u1, "...and it is not reported as having no footprint",
      u1.strip())

check("extra unit" in flat,
      "the four extra units are accounted for in the excluded line, not silently dropped",
      next((l for l in flat.splitlines() if "Excluded" in l), ""))


# ============================================================ 3. power, flags, DNP, unannotated

section("what is not a part")

check("#PWR" not in flat and "#FLG" not in flat,
      "power symbols and power flags are not parts and are not listed")
check("GND" not in flat and "PWR_FLAG" not in flat,
      "...and neither are their values")
check("3 power symbol" in flat, "three of them were seen and excluded",
      next((l for l in flat.splitlines() if "Excluded" in l), ""))

# The PWR_FLAG has in_bom YES in real KiCad output — the "#" prefix is the ONLY thing that
# excludes it. If the "#" rule is ever dropped for an inBom-only rule, this goes red.
check("PWR_FLAG" not in flat,
      "the power flag is excluded by its '#' prefix, which is the only signal it carries")

# DNP is fitted-but-not-populated. It must be VISIBLE and MARKED — dropping it silently is how
# a part goes missing between design and assembly.
d1 = next((l for l in flat.splitlines() if l.strip().startswith("1x") and "LED" in l), "")
check(bool(d1), "the DNP part is still listed", flat)
check("DNP" in d1, "...and is marked DNP rather than quietly counted as fitted", d1.strip())

check("R?" not in flat, "an unannotated 'R?' is not an orderable part and is not listed")
check("unannotated" in flat, "...but it is reported as excluded, so it is not lost",
      next((l for l in flat.splitlines() if "Excluded" in l), ""))


# ============================================================ 4. THE in_bom DEFAULT TRAP

section("in_bom default")

# kiutils sets inBom=False when the token is absent, so `if not sym.inBom: continue` empties
# the BOM of any file that does not write it — and an empty BOM looks like an empty sheet.
# Stated as a refusal of the wrong answer.
noinbom = bom("no-inbom.kicad_sch")

check("No components found" not in noinbom,
      "a file with NO in_bom token still yields a BOM (the kiutils default is False)",
      noinbom.splitlines()[0] if noinbom else "")
check("3 parts" in noinbom, "all three real parts are found", noinbom[:120])
for ref in ("R1", "R2", "U1"):
    check(ref in noinbom, f"{ref} survives the missing in_bom token")
check("#PWR01" not in noinbom,
      "...and the power symbol is still excluded, by its '#' prefix rather than by in_bom")

# The mechanism, directly: the field is only trusted when the file demonstrably writes it.
from kiutils.schematic import Schematic                                   # noqa: E402

flat_syms = Schematic.from_file(str(FIXTURES / "flat.kicad_sch")).schematicSymbols
none_syms = Schematic.from_file(str(FIXTURES / "no-inbom.kicad_sch")).schematicSymbols
check(K._bom_field_is_live(flat_syms) is True,
      "in_bom is judged live in a file that writes it")
check(K._bom_field_is_live(none_syms) is False,
      "in_bom is judged absent in a file that does not — so False is not read as 'excluded'")
check(all(getattr(s, "inBom", None) is False for s in none_syms),
      "(and kiutils really does report False for every symbol in that file — the trap is real)")


# ============================================================ 5. THE HIERARCHY

section("hierarchy")

root = bom("hier-root.kicad_sch")

# The root sheet holds ONE part. Everything else is one level down. Both directions stated:
# the child's parts are present, and the root-only answer is refused by name.
check("J1" in root, "the root sheet's own part is present")
for ref in ("U2", "C10", "C11", "R10"):
    check(ref in root, f"{ref} comes from the SUB-SHEET and is present")

check("5 parts" in root, "five parts across the hierarchy", root[:140])
check("1 parts" not in root and "1 part in" not in root,
      "the root-only count is NOT the answer — the sub-sheet was actually walked", root[:140])
check("2 sheets" in root, "two sheet files were read", root[:140])

# A sheet named but not on disk is reported, not silently skipped. A BOM missing a tenth of
# the board with no warning is the worst possible output.
check("no-such-sheet.kicad_sch" in root,
      "a missing sub-sheet file is named in a warning", root)
check("NOT in this list" in root,
      "...and the warning says plainly that those parts are absent")

# THE CYCLE. hier-child names hier-root as a sub-sheet. The walk must terminate, and must call
# it what it is.
child = bom("hier-child.kicad_sch")
check("could not be read" not in child, "the cycle terminates instead of recursing forever")
check("is its own parent sheet" in child,
      "...and is reported as a malformed hierarchy", child)

# A REPEATED sheet is a different thing and must read differently — it is legal, deliberate,
# and it means the parts really are on the board twice.
repeat = bom("repeat-root.kicad_sch")
check("placed more than once" in repeat,
      "the same sheet placed twice is reported as a repeat, not as a cycle", repeat)
check("is its own parent sheet" not in repeat,
      "...and specifically NOT as a malformed hierarchy", repeat)


# ============================================================ 6. THE BOARD

section("board")

two = pcb("two-layer.kicad_pcb")

# THE HEADLINE WRONG ANSWER. len(board.layers) is 29 for this board.
check("Copper layers: 2" in two, "a two-layer board is reported as two COPPER layers",
      two.splitlines()[1] if len(two.splitlines()) > 1 else two)
check("Copper layers: 29" not in two,
      "...and NOT as 29, which is the size of the whole layer table", two)
check("29" in two, "the table size is still reported, beside the copper count and labelled",
      next((l for l in two.splitlines() if "Total layers" in l), ""))
check("F.Cu" in two and "B.Cu" in two, "the copper layers are named")

four = pcb("four-layer.kicad_pcb")
check("Copper layers: 4" in four, "a four-layer board is four", four)
check("In1.Cu" in four and "In2.Cu" in four, "the internal planes are counted as copper", four)
check("Copper layers: 20" not in four, "...and not the 20-entry table size", four)

# Net 0 is KiCad's unassigned net and is on every board including an empty one. Reporting it
# as a net inflates the count by one, forever.
check("5 named" in two, "the five named nets are counted", two)
check("6 including" in two, "...and the raw count including net 0 is given, labelled", two)

check("Footprints placed: 5" in two, "footprints are counted", two)
check("1.6 mm" in two, "board thickness is read from the general section", two)
check("R_0805_2012Metric x2" in two, "the most-used footprint is identified", two)


# ============================================================ 7. IT REFUSES, WITH A SENTENCE

section("refusals")

REFUSALS = [
    # (argument, tool, a phrase the answer must contain)
    (str(FIXTURES / "nope.kicad_sch"),      extract_kicad_bom, "no file at"),
    (str(FIXTURES / "two-layer.kicad_pcb"), extract_kicad_bom, "asked for the schematic"),
    (str(FIXTURES / "flat.kicad_sch"),      analyze_kicad_pcb, "asked for the board"),
    (str(REPO_ROOT / "README.md"),          extract_kicad_bom, "not a KiCad file"),
    (str(FIXTURES / "truncated.kicad_sch"), extract_kicad_bom, "could not be read"),
    ("",                                    extract_kicad_bom, "given nothing"),
    ("   ",                                 analyze_kicad_pcb, "given nothing"),
]
for arg, fn, phrase in REFUSALS:
    try:
        out = fn.invoke({"file_path": arg})
        raised = ""
    except Exception as exc:                                              # noqa: BLE001
        out, raised = "", f"{type(exc).__name__}: {exc}"
    check(not raised, f"{Path(arg).name or arg!r}: returns rather than raising", raised)
    check(phrase in out, f"{Path(arg).name or arg!r}: says {phrase!r}", out[:100])

# A folder is a KiCad PROJECT. With one file of the right kind inside it, use it; with several,
# say so rather than picking.
folder = extract_kicad_bom.invoke({"file_path": str(FIXTURES)})
check("will not guess" in folder,
      "a folder holding several schematics is not guessed between", folder[:120])

# And the corrupt file must NOT come back as an empty BOM, which would read as a valid answer.
truncated = extract_kicad_bom.invoke({"file_path": str(FIXTURES / "truncated.kicad_sch")})
check("No components found" not in truncated,
      "a corrupt file is an ERROR, never an empty parts list", truncated[:100])


# ============================================================ 8. FINDING IT BY NAME

section("name resolution")

# He is voice-first: a dictated path does not survive Whisper, so a bare name has to work.
for spoken, expect in [("flat", "flat.kicad_sch"),
                       ("Flat", "flat.kicad_sch"),
                       ("the flat board", "flat.kicad_sch"),
                       ("no-inbom", "no-inbom.kicad_sch"),
                       ("no inbom", "no-inbom.kicad_sch")]:
    out = extract_kicad_bom.invoke({"file_path": spoken})
    check(expect in out, f"{spoken!r} resolves to {expect}", out.splitlines()[0] if out else "")

for spoken, expect in [("two-layer", "two-layer.kicad_pcb"),
                       ("two layer", "two-layer.kicad_pcb"),
                       ("four layer", "four-layer.kicad_pcb")]:
    out = analyze_kicad_pcb.invoke({"file_path": spoken})
    check(expect in out, f"{spoken!r} resolves to {expect}", out.splitlines()[0] if out else "")

# AMBIGUITY IS REPORTED, NEVER GUESSED. Answering confidently about the wrong board is worse
# than asking which one — the answer is right, about something LB did not ask about.
ambiguous = extract_kicad_bom.invoke({"file_path": "hier"})
check("will not guess" in ambiguous, "'hier' matches two files and is not guessed between",
      ambiguous[:110])
check("hier-root" in ambiguous and "hier-child" in ambiguous,
      "...and both candidates are named so he can choose", ambiguous[:200])

missing = extract_kicad_bom.invoke({"file_path": "a board that does not exist"})
check("could not find" in missing, "an unknown name is refused", missing[:90])
check("ODDBALL_KICAD_ROOT" in missing,
      "...and the message names the setting that would fix it", missing[:200])

# The root is configurable, which is the whole reason a name can be looked up on two machines.
check(str(K.kicad_root()) == str(FIXTURES),
      "ODDBALL_KICAD_ROOT is honoured", f"{K.kicad_root()}")


# ============================================================ 9. IT NEVER REACHES THE VOICE

section("speech channel")

from engine.split import is_speakable, split                              # noqa: E402

# The exact shape agents/hardware_agent.py builds: a spoken sentence, then the raw tool string
# under the header engine/split.py already knows how to card.
reply = ("Your amp board has eight parts, and three of them are ten kilohm resistors.\n"
         "SPOKEN: Your amp board has eight parts, and three of them are ten kilohm resistors.\n"
         f"\nTool Execution Result: {flat}")
got = split(reply, route="hardware")

check(is_speakable(got.speech) is None, "the spoken half passes the speech filter",
      f"{is_speakable(got.speech)} :: {got.speech!r}")
check("R_0805_2012Metric" not in got.speech,
      "no footprint name is ever said out loud", got.speech)
check("R1, R2" not in got.speech, "no reference list is said out loud", got.speech)
check("kilohm" in got.speech, "the sentence that IS spoken answers the question", got.speech)

card_text = "\n".join(c.body for c in got.cards)
check("R_0805_2012Metric" in card_text, "the listing reaches a card instead")
check("D1" in card_text and "TL074" in card_text, "the whole BOM is on the card, not a summary")
check(any(c.kind == "log" for c in got.cards), "and it is a log card",
      f"kinds: {[c.kind for c in got.cards]}")

# The board summary too — it carries "F.Cu" and "1.6 mm", both awkward aloud.
pcb_reply = ("That board is two layers with five nets.\n"
             "SPOKEN: That board is two layers with five nets.\n"
             f"\nTool Execution Result: {two}")
pcb_got = split(pcb_reply, route="hardware")
check(is_speakable(pcb_got.speech) is None, "the board answer's spoken half is clean",
      f"{is_speakable(pcb_got.speech)} :: {pcb_got.speech!r}")
check("F.Cu" not in pcb_got.speech, "layer identifiers do not reach the voice", pcb_got.speech)


# ============================================================ 10. the agent is really wired up

section("agent wiring")

# The tools exist on the agent, addressable by the name the model emits. A tool bound but not
# reachable by name is the NameError branch the old if/elif chain had.
os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key-but-long-enough-to-pass")
try:
    from agents import hardware_agent as HA                               # noqa: E402

    imported = True
    detail = ""
except Exception as exc:                                                  # noqa: BLE001
    imported = False
    HA = None
    detail = f"{type(exc).__name__}: {exc}"

check(imported, "agents/hardware_agent.py imports with the new tools", detail)

if imported:
    # The three KiCad/IPC tools must all be present. Asserted as a SUBSET rather than as an
    # exact set, and that is the fix for a real false alarm: this check was written when the
    # hardware agent had exactly three tools, and binding the two vault tools to it turned the
    # harness red without anything being broken.
    #
    # An exact-set assertion on a list designed to grow reports every ADDITION as a
    # regression — which trains you to ignore it, and the day it means something you already
    # are. The property that actually matters is that nothing was LOST, so that is what is
    # checked, and the full list is printed either way.
    names = {t.name for t in HA.TOOLS}
    required = {"calculate_ipc2221_trace_width", "extract_kicad_bom", "analyze_kicad_pcb"}
    check(required <= names, "all three KiCad/IPC tools are bound",
          f"bound: {sorted(names)}"
          + (f" — MISSING {sorted(required - names)}" if required - names else ""))
    check(set(HA._BY_NAME) == names, "every bound tool is reachable by the name it emits")
    for name in names:
        check(callable(getattr(HA._BY_NAME[name], "invoke", None)),
              f"{name} can be invoked from the dispatch table")

    # The prompt has to TELL the model the tools exist, or they are never called.
    check("extract_kicad_bom" in HA.HARDWARE_PROMPT_TEMPLATE
          and "analyze_kicad_pcb" in HA.HARDWARE_PROMPT_TEMPLATE,
          "the prompt names both KiCad tools, so the model knows it has them")
    check("SPOKEN:" in HA.HARDWARE_PROMPT_TEMPLATE,
          "the prompt still ends with the SPOKEN instruction (section 9 depends on it)")
    check("gemini" not in HA.HARDWARE_PROMPT_TEMPLATE.lower()
          and "AGENT_MODEL" in Path(HA.__file__).read_text(encoding="utf-8"),
          "the model name comes from engine/models.py, not hardcoded (D3: quota is per model)")

    # The truncation helper is on the summary path; a mangled f-string here silently drops the
    # instruction not to guess at the hidden lines.
    long_result = "\n".join(f"line {i}" for i in range(200))
    trimmed = HA._for_summary(long_result)
    check("do not guess" in trimmed, "the summary truncation keeps its warning intact",
          trimmed[-120:])
    check(trimmed.count("\n") < 200, "...and actually truncates",
          f"{trimmed.count(chr(10))} newlines")
    check(HA._for_summary(flat) == flat, "a short result is passed through untouched")

# The route the router must pick has to describe the capability, or nothing reaches the agent.
router_src = (REPO_ROOT / "router.py").read_text(encoding="utf-8")
check("KiCad" in router_src, "router.py's prompt mentions KiCad, so HARDWARE can be chosen")
check("not OS" in router_src,
      "...and says a file question is not automatically an OS question")


# ============================================================ 11. it never raises

section("never raises")

random.seed(20260821)
alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 .,-/\\:_()'\"~*?"
crashes = []
for _ in range(300):
    junk = "".join(random.choice(alphabet) for _ in range(random.randint(0, 70)))
    for fn in (extract_kicad_bom, analyze_kicad_pcb):
        try:
            fn.invoke({"file_path": junk})
        except Exception as exc:                                          # noqa: BLE001
            crashes.append((junk, f"{type(exc).__name__}: {exc}"))
check(not crashes, "300 fuzzed paths through both tools, none raised",
      "; ".join(f"{j!r} -> {e}" for j, e in crashes[:3]))

EDGE = ["", " ", "\n", "?" * 300, "/" * 40, "C:\\", "~", "..", "../" * 30,
        "con", "nul", "*.kicad_sch", "a\x00b", str(FIXTURES) + "/", "'quoted.kicad_sch'",
        "the amp board.", "  spaced name  "]
for junk in EDGE:
    for fn in (extract_kicad_bom, analyze_kicad_pcb):
        try:
            out = fn.invoke({"file_path": junk})
            ok, detail = isinstance(out, str) and bool(out), ""
        except Exception as exc:                                          # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(ok, f"{fn.name} survives {junk[:24]!r} and answers with a sentence", detail)


# ============================================================ 12. it is cheap enough

section("latency")

start = time.perf_counter()
for _ in range(20):
    bom("flat.kicad_sch")
flat_ms = (time.perf_counter() - start) * 1000 / 20
check(flat_ms < 250.0, "a schematic parses fast enough to sit inside a turn",
      f"{flat_ms:.1f} ms per call, 16 symbols, over 20 calls")

start = time.perf_counter()
for _ in range(20):
    pcb("two-layer.kicad_pcb")
pcb_ms = (time.perf_counter() - start) * 1000 / 20
check(pcb_ms < 250.0, "so does a board",
      f"{pcb_ms:.1f} ms per call, 29 layers / 5 footprints, over 20 calls")

start = time.perf_counter()
for _ in range(20):
    extract_kicad_bom.invoke({"file_path": "flat"})
name_ms = (time.perf_counter() - start) * 1000 / 20
check(name_ms < 400.0, "and so does resolving a spoken name before parsing",
      f"{name_ms:.1f} ms per call, including the directory search")


# ============================================================ report

passed = sum(1 for ok, *_ in RESULTS if ok)
failed = len(RESULTS) - passed

width = 76
last_section = None
for ok, sec, msg, detail in RESULTS:
    if sec != last_section:
        print(f"\n-- {sec} " + "-" * max(3, width - len(sec) - 4))
        last_section = sec
    if not ok:
        print(f"  FAIL  {msg}")
        if detail:
            print(f"        {detail}")

print("\n" + "=" * width)
print(f"{passed}/{len(RESULTS)} checks passed"
      + (f"  ({failed} FAILED)" if failed else "  — all green"))
print(f"{len(list(FIXTURES.glob('*.kicad_*')))} fixtures, "
      f"{len(REFUSALS)} refusals, {len(EDGE) * 2} edge paths, 600 fuzzed calls; "
      f"schematic {flat_ms:.0f}ms, board {pcb_ms:.0f}ms, by-name {name_ms:.0f}ms")
raise SystemExit(1 if failed else 0)
