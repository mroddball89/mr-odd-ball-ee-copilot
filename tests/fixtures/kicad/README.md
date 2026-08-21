# KiCad fixtures — hand-written, and each one is a trap

Read by `tools/verify_kicad.py`. **Written by hand, not exported from KiCad**, because there is
no KiCad installed on either box and no `.kicad_sch` anywhere on this machine — so there was
nothing to record. Every file below round-trips through `kiutils` before the harness trusts it;
that check is section 0.

They are deliberately tiny (a few hundred bytes) and deliberately nasty. Each one exists
because the obvious implementation gets it wrong **and returns a plausible answer** rather than
crashing — a BOM that is merely incomplete looks exactly like a BOM.

| file | what it traps | the wrong answer it produces |
|---|---|---|
| `flat.kicad_sch` | multi-unit parts, power symbols, power flags, DNP, unannotated refs | five TL074s ordered instead of one |
| `no-inbom.kicad_sch` | the `in_bom` token is **absent** (KiCad 9 shape) | an **empty BOM**, which reads as an empty sheet |
| `hier-root.kicad_sch` | parts live in the sub-sheet, plus a sheet file that is missing | a 5-part design reported as 1 part |
| `hier-child.kicad_sch` | names its own parent as a sub-sheet — a **cycle** | infinite recursion, or a silent stop |
| `repeat-root.kicad_sch` | the same sub-sheet placed **twice**, which is legal | the second channel's parts silently uncounted |
| `two-layer.kicad_pcb` | the full 29-entry KiCad layer table on a 2-layer board | "this is a 29-layer board" |
| `four-layer.kicad_pcb` | two internal **power**-type planes | internal planes not counted as copper |
| `truncated.kicad_sch` | cut off mid-property | "no components found" instead of an error |

## Three things worth knowing before editing these

**`kiutils` has no comment syntax.** A `;` line is parsed as a *token*, not skipped — the first
draft of `flat.kicad_sch` carried explanatory comments and came back with 17 symbols instead of
15, two of them fragments of prose with a `Reference` of `None`. That is why the explanation
lives in this table and not in the files. Do not add comments to them.

**`inBom` defaults to `False` in kiutils.** So "the file said no" and "the file did not say"
are the same value, and a filter written on it empties the BOM of any file in the second case.
`no-inbom.kicad_sch` is that file, and `kicad_parser._bom_field_is_live` is the thing that
tells the two apart. See D9.

**`flat.kicad_sch`'s TL074 has five blocks and the fifth has an empty `Footprint`.** That is
what KiCad really writes for a multi-unit part's power unit, and it is why "first block wins"
is not good enough — the parser has to prefer whichever unit carries a real footprint.

## Getting real ones in here

The moment LB has an actual design, copy the `.kicad_sch` and `.kicad_pcb` in beside these and
add a row above. Real files are worth more than hand-written ones for everything except the
edge cases, which real files rarely contain on purpose — so keep both, the way
`../wake/known-limits/` is kept.
