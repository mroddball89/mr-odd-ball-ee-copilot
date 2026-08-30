#!/usr/bin/env python3
"""Run the shipped KiCad tools against every KiCad 9.0.2 demo project on the Pi.

Written by KiCad itself, at a format version the parser has never seen — every fixture in
tests/fixtures/kicad/ is hand-written. First time it meets files it did not help shape.

NOTE the bug in v1 of this script: it flagged "empty" with `"0 parts" in out`, which matches
inside "820 parts". That is L11 in this repo's own lessons file, committed yesterday. The
parser was fine; the check was not.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, "/home/ironi/mr-odd-ball")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.kicad_parser import analyze_kicad_pcb, extract_kicad_bom

DEMOS = Path("/usr/share/kicad/demos")
COUNT = re.compile(r"^\s*([\d,]+)\s+parts\s+in\s+([\d,]+)\s+lines\s+across\s+([\d,]+)\s+sheets",
                   re.MULTILINE)
ZERO = re.compile(r"\b0 parts\b")

print("=" * 78)
print("  SCHEMATICS — extract_kicad_bom on each project's ROOT sheet")
print("=" * 78)

ok = err = empty = 0
total_parts = 0
for proj in sorted(p for p in DEMOS.iterdir() if p.is_dir()):
    root = proj / f"{proj.name}.kicad_sch"
    if not root.exists():
        cands = sorted(proj.glob("*.kicad_sch"))
        if not cands:
            continue
        root = cands[0]
    try:
        out = extract_kicad_bom.invoke({"file_path": str(root)})
    except Exception as exc:
        print(f"  RAISED  {proj.name:30} {type(exc).__name__}: {exc}")
        err += 1
        continue

    low = out.lower()
    if low.startswith("failed") or "could not" in low[:90]:
        print(f"  ERROR   {proj.name:30} {out.splitlines()[0][:60]}")
        err += 1
    elif ZERO.search(out) or not out.strip():
        print(f"  EMPTY   {proj.name:30} {out.splitlines()[0][:60]}")
        empty += 1
    else:
        m = COUNT.search(out)
        summary = (f"{m.group(1):>5} parts  {m.group(3):>3} sheets" if m
                   else out.splitlines()[1][:40] if len(out.splitlines()) > 1 else "?")
        if m:
            total_parts += int(m.group(1).replace(",", ""))
        print(f"  ok      {proj.name:30} {summary}")
        ok += 1

print(f"\n  {ok} parsed ({total_parts:,} parts total), {empty} genuinely empty, {err} failed\n")

print("=" * 78)
print("  BOARDS — analyze_kicad_pcb")
print("=" * 78)

bok = berr = refused = 0
for pcb in sorted(DEMOS.rglob("*.kicad_pcb")):
    try:
        out = analyze_kicad_pcb.invoke({"file_path": str(pcb)})
    except Exception as exc:
        print(f"  RAISED  {pcb.parent.name:26} {type(exc).__name__}: {exc}")
        berr += 1
        continue
    low = out.lower()
    if "megabytes" in low and "larger than" in low:
        # The 50 MB guard in kicad_parser._too_big. A refusal, by design, and a speakable one.
        print(f"  refused {pcb.parent.name:26} {out.strip()[:56]}")
        refused += 1
    elif low.startswith("failed") or "could not" in low[:90]:
        print(f"  ERROR   {pcb.parent.name:26} {out.splitlines()[0][:56]}")
        berr += 1
    else:
        lay = next((l.strip() for l in out.splitlines() if "copper layer" in l.lower()), "?")
        net = next((l.strip() for l in out.splitlines() if l.lower().startswith("nets")), "?")
        print(f"  ok      {pcb.parent.name:26} {lay[:30]:32} {net[:34]}")
        bok += 1

print(f"\n  {bok} parsed, {refused} refused by the size guard, {berr} failed\n")
print("=" * 78)
print(f"  TOTAL: {ok + bok} parsed cleanly, {err + berr} FAILED, "
      f"{refused} refused by design, {empty} empty")
print("=" * 78)
