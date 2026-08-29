#!/usr/bin/env python3
"""
Module:  corpus_hint.py
Purpose: Let LB's own datasheets decide a question is FIRMWARE, without paying the router.
Author:  LB
Date:    2026-08-28

    python -m orchestrator.corpus_hint "what sensor does the camera module 3 use"
    python -m orchestrator.corpus_hint --fit      # re-fit the threshold to the current corpus

## Why this exists

D3 measured the free tier at **20 requests per model per day**, and `engine/models.py` resolves
its four model constants to only **two** names — `ROUTER_MODEL` and `PERSONA_MODEL` are both
`gemini-3.5-flash-lite`. So routing and chit-chat share one bucket of 20, and every paid turn
spends one of them before it spends anything else.

A datasheet question costs 1 flash-lite (route) + 1 flash (answer). **This module removes the
first.** The vector store is already on disk, and searching it is free: `tools/vector_db.py`
embeds locally with `all-MiniLM-L6-v2` through `HuggingFaceEmbeddings`, so nothing here touches
a network or a quota. If the question lands close to LB's own documents, it is a question about
LB's own documents, and no model is needed to notice that.

**The retrieved chunks come back with the hint**, so `agents/firmware_agent.py` does not search
a second time for the same query. One search, one call saved.

## Why this is not the keyword list `route_hint.py` refused

`orchestrator/route_hint.py` specified, costed and **refused** a keyword dictionary — `voltage`
to HARDWARE, `esp32` to FIRMWARE — because every one of those words is ambiguous in LB's own
vocabulary, and "semantic routing is exactly what D1 bought the paid router for".

This is the opposite construction. There is no list. The evidence is **LB's actual corpus**,
read off the filesystem, exactly as `route_hint.known_courses()` derives course codes from
`vault/courses/*.md` rather than from a hardcoded table. Add an ESP32 datasheet and ESP32
questions start routing here; delete it and they stop. Nothing to maintain, and nothing to be
wrong about.

## The threshold is fitted, and it is corpus-dependent

Chroma returns an L2 distance — **lower is closer**. Measured 2026-08-28 against the 34-chunk
store built from the two Raspberry Pi camera briefs.

### The first fit was wrong, and `tools/verify_engine.py` caught it

It measured positives at 0.447–1.205 against negatives at 1.409–1.905 — a clean gap of +0.204
— and set the threshold at the midpoint, 1.30. Then the existing engine harness went red:

    check("free questions" in r.speech, "a failed turn still SPEAKS something safe")

That test asks **"check the temperature"** expecting it to reach the router. At 1.30 this module
claimed it, because the sensor brief has a SAFETY INSTRUCTIONS page about operating temperature
— so the router was never called, the injected 429 never happened, and two more checks fell
over behind it.

**The gap was an artefact of negatives I had chosen myself.** Every positive named "camera
module 3" or "IMX708"; every negative was about something else entirely. The hard cases — short,
generic questions that share vocabulary with the corpus without being about it — were not in the
set, and they land *below* the worst positive:

    check the temperature       1.049      <- BELOW the worst positive (1.205)
    how do I focus the camera   1.102      <- also below it

**No top-1 threshold can separate those**, and pretending otherwise is how a routing band starts
quietly answering OS questions out of a camera datasheet. That is L26 arriving inside my own
measurement: an invented corpus agrees with whoever invented it.

### The fit that survives contact

Re-measured against 73 negatives — the hand-written ones, seventeen deliberately hard ones, and
**all 42 of LB's real recordings from `captures/`**:

    threshold   positives kept   false positives   margin under worst kept
       0.90          8/10               0                   0.8%
       0.95          9/10               0                   4.0%
       1.00          9/10               0                   8.8%     <- chosen
       1.05          9/10               1                  13.1%
       1.30         10/10               5                   7.3%

`THRESHOLD` is **1.00**: nine of ten positives, **zero** false positives, 8.8% clear of the
worst positive it keeps and 4.9% clear of the nearest negative (1.049).

The positive it gives up — the one at 1.205 — falls through to the paid router and is answered
exactly as it was before this module existed. **That is the correct direction to fail in.** A
missed hint costs one `flash-lite` call; a wrong hint answers a question about his CPU out of a
camera datasheet.

**This number is a property of the corpus, not of the code**, and that is the one thing to
remember about this file. A store with forty datasheets in it is denser, and an unrelated
question will find a closer neighbour than it does today. So:

  * `tools/verify_corpus_hint.py` **re-measures the separation every run and fails if the
    current corpus no longer separates**, rather than asserting a number someone fitted once.
  * `python -m orchestrator.corpus_hint --fit` prints the separation and the midpoint for
    whatever is on disk now. Run it after adding documents.

If they ever stop separating, the honest answer is to raise the bar or switch this off — not to
nudge the constant until the harness goes quiet. That is what the `[wake].threshold` block in
`config/oddball.toml` had to learn the hard way.

## What it deliberately does NOT do

**It only ever claims FIRMWARE, and only when nothing cheaper has claimed the turn.**
`engine/core.py` runs it after `_free_turn` and after `route_hint`, so the time, a conversion,
a note, a launch, "sync my schedule" and "cpu temp" are all long since answered. This sees only
what would otherwise have gone to the paid router.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

LOG = logging.getLogger("oddball.corpus")

__all__ = ["CorpusHit", "look_up", "THRESHOLD", "FIRMWARE", "separation"]

FIRMWARE = "firmware"

# Fitted 2026-08-28. See the module docstring, and re-fit with `--fit` after adding documents.
THRESHOLD = 1.00

# How many chunks to hand on. Matches `firmware_agent`'s own `get_retriever(k=4)`, so routing
# this way and routing through the paid router put the SAME context in front of the model —
# a hint that quietly changed the answer would be a much worse bargain than a saved call.
K = 4


@dataclass(frozen=True)
class CorpusHit:
    """A question the datasheet corpus recognises as its own.

    **Carries no authority to answer.** `engine/core.py` decides, exactly as it does with
    `LaunchRequest` and `NoteRequest`.

    Args:
        route:    always `FIRMWARE`. A field rather than a constant so the caller reads it the
                  same way it reads `route_hint.look_up`'s return.
        distance: the top chunk's L2 distance. Lower is closer; kept for the log, because the
                  first question anyone asks about a mis-route is "how close was it".
        context:  the chunks, already formatted by `vector_db.format_chunks` — handed to the
                  agent so it does not search again for the same query.
        sources:  what `format_chunks` returned, for the Sources card.
    """

    route: str
    distance: float
    context: str = ""
    sources: list[dict] = field(default_factory=list)


def _store():
    """The datasheet collection, or None. Imported lazily and never at module scope.

    `tools/vector_db` pulls in `langchain_chroma`, `langchain_huggingface` and **torch** —
    measured at 11.4 s of imports in `tools/file_manager.py`'s docstring. `route_hint.py` is a
    pure function of a string and must stay one; this module is imported from the same place
    and would otherwise put that cost on every turn, including the free ones.

    After the first call the store is cached by `vector_db._stores`, so the cost is paid once.
    """
    from tools.vector_db import DATASHEET_COLLECTION, get_embeddings  # noqa: PLC0415
    from tools.vector_db import CHROMA_PATH, _stores                  # noqa: PLC0415

    if not CHROMA_PATH.exists():
        return None
    key = f"{DATASHEET_COLLECTION}:raw"
    if key not in _stores:
        from langchain_chroma import Chroma                           # noqa: PLC0415
        _stores[key] = Chroma(persist_directory=str(CHROMA_PATH),
                              collection_name=DATASHEET_COLLECTION,
                              embedding_function=get_embeddings())
    return _stores[key]


def look_up(text: str) -> "CorpusHit | None":
    """Do LB's own documents answer this? Returns the hint and its chunks, or None.

    Args:
        text: the raw utterance. Not normalised — the embedding model was trained on ordinary
              text, and `normalise()` would strip the punctuation and capitals it expects.

    Returns:
        A `CorpusHit`, or None. **None is the right answer for everything that is not about a
        document LB has actually filed**, including every question when no store has been
        built — which is the normal state of a fresh clone.

    Never raises. A broken store degrades to the paid router, which is exactly what happened
    before this module existed.
    """
    query = (text or "").strip()
    if not query:
        return None

    try:
        store = _store()
        if store is None:
            return None

        hits = store.similarity_search_with_score(query, k=K)
        if not hits:
            return None

        best = hits[0][1]
        if best >= THRESHOLD:
            LOG.debug("corpus miss for %r (best %.3f >= %.3f)", query, best, THRESHOLD)
            return None

        from tools.vector_db import format_chunks                     # noqa: PLC0415
        context, sources = format_chunks([doc for doc, _ in hits])
        LOG.info("corpus hint firmware: %r (distance %.3f, %d chunk(s), no api call)",
                 query, best, len(sources))
        return CorpusHit(route=FIRMWARE, distance=best, context=context, sources=sources)
    except Exception:                                                 # noqa: BLE001
        # Loud, per D10's lesson: a silent fall-through to the paid path is how the free tier
        # died for a day without anyone noticing.
        LOG.exception("corpus hint failed; falling back to the router")
        return None


def separation(positives: "list[str]", negatives: "list[str]") -> dict:
    """Measure how well the current corpus separates the two sets. For `--fit` and the harness.

    Returns:
        A dict describing the OPERATING POINT at the current `THRESHOLD`: how many positives it
        keeps and how many negatives it wrongly takes.

    **`gap` is reported but is NOT the property to assert**, and that is the correction this
    function needed. The first version returned only `worst_positive`, `best_negative` and the
    gap between them, which assumes the two sets are perfectly separable. Once the hard
    negatives went in they stopped being separable — "check the temperature" sits at 1.049,
    below the worst positive at 1.205 — and a harness asserting `gap > 0` would have demanded
    a separation that no threshold can deliver.

    What actually matters is `false_positives == 0` at the threshold in force. A positive lost
    below the threshold costs one `flash-lite` call; a negative taken above it answers a
    question about his CPU out of a camera datasheet.
    """
    store = _store()
    if store is None:
        return {"built": False}

    def best(q: str) -> float:
        hits = store.similarity_search_with_score(q, k=1)
        return hits[0][1] if hits else 99.0

    pos = sorted(best(q) for q in positives)
    neg = sorted(best(q) for q in negatives)
    if not pos or not neg:
        return {"built": True, "gap": None}

    kept = [d for d in pos if d < THRESHOLD]
    taken = [d for d in neg if d < THRESHOLD]
    return {"built": True, "worst_positive": pos[-1], "best_negative": neg[0],
            "gap": neg[0] - pos[-1], "midpoint": (pos[-1] + neg[0]) / 2,
            "kept": len(kept), "of": len(pos),
            "false_positives": len(taken), "negatives_tried": len(neg),
            "margin_pct": ((THRESHOLD - max(kept)) / THRESHOLD * 100) if kept else 0.0,
            "headroom_pct": ((min(taken + neg) - THRESHOLD) / THRESHOLD * 100)
                            if not taken else 0.0,
            "positives": pos, "negatives": neg}


# The corpus the CLI and the harness fit against. Positives are datasheet questions; negatives
# are every other kind of thing said to him — including, deliberately, LB's own recordings,
# which is L26: the invented negatives can argue this is safe and the real ones prove it.
FIT_POSITIVES: tuple[str, ...] = (
    "what sensor does the camera module 3 use",
    "what is the resolution of the camera module 3",
    "does the camera module 3 have autofocus",
    "what is the focal length of the camera module",
    "what voltage does the camera module need",
    "what is the IMX708 pixel size",
    "how many megapixels is the pi camera",
    "what is the field of view of the wide camera",
    "what is the focus range on the wide variant",
    "what part number is the noir camera",
)

FIT_NEGATIVES: tuple[str, ...] = (
    "what time is it", "whats the trace width for 5 amps", "quiz me on filters",
    "open firefox", "sync my schedule", "take a note that the reg is an LM317",
    "how hot is the cpu", "whats on my screen", "what does ohms law say",
    "convert 5 volts to millivolts", "tell me a joke", "whats due tomorrow",
    "read me my regulator note", "delete my scratch note",
    # The hard ones, added after the first fit shipped a false positive. Every one is SHORT and
    # GENERIC and shares vocabulary with the corpus without being about it — which is exactly
    # the shape the first negative set had none of. `check the temperature` is the one
    # `tools/verify_engine.py` caught: it is an OS question, and the sensor brief has a page
    # about operating temperature.
    "check the temperature", "whats the temperature", "how hot is it", "check the cpu",
    "whats the voltage", "how many volts", "what resolution should I use",
    "how do I focus the camera", "take a picture", "what size is it", "whats the part number",
    "how wide is it", "check the power", "whats the current draw", "how do I install it",
    "what cable does it need", "is it compatible",
)


def real_captures() -> "list[str]":
    """LB's own recorded speech, as negatives. Empty when `captures/` is absent.

    Gitignored, so this is empty on a fresh clone and the harness says so rather than silently
    testing less — the same trap `tools/verify_notes.py` avoids by copying its transcripts in.
    """
    import re                                                          # noqa: PLC0415
    from pathlib import Path                                           # noqa: PLC0415

    root = Path(__file__).resolve().parents[1] / "captures"
    if not root.is_dir():
        return []
    out = []
    for wav in sorted(root.glob("*.wav")):
        text = re.sub(r"^\d+_", "", wav.stem).replace("-", " ").strip()
        if text and text != "empty":
            out.append(text)
    return out


def main(argv: "list[str] | None" = None) -> int:
    import argparse                                                    # noqa: PLC0415
    import sys                                                         # noqa: PLC0415

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="does LB's own corpus claim this question?")
    ap.add_argument("query", nargs="*")
    ap.add_argument("--fit", action="store_true",
                    help="measure the separation against the corpus on disk right now")
    args = ap.parse_args(argv)

    if args.fit:
        negatives = list(FIT_NEGATIVES) + real_captures()
        result = separation(list(FIT_POSITIVES), negatives)
        if not result.get("built"):
            print("  no vector store — run `python tools/vector_db.py` first")
            return 1
        print(f"  positives {len(FIT_POSITIVES)}, negatives {len(negatives)} "
              f"({len(real_captures())} of them real recordings)\n")
        print(f"    positives        {result['positives'][0]:.3f} .. "
              f"{result['worst_positive']:.3f}")
        print(f"    negatives        {result['best_negative']:.3f} .. "
              f"{result['negatives'][-1]:.3f}")
        if result["gap"] < 0:
            print(f"    the sets OVERLAP by {-result['gap']:.3f} — expected, and the reason "
                  f"the operating point below is what matters")
        else:
            print(f"    gap              {result['gap']:+.3f}")

        print(f"\n  AT THRESHOLD {THRESHOLD}:")
        print(f"    keeps          {result['kept']}/{result['of']} positives"
              f"  ({result['margin_pct']:.1f}% margin under the worst it keeps)")
        print(f"    wrongly takes  {result['false_positives']}/{result['negatives_tried']} "
              f"negatives" + (f"  ({result['headroom_pct']:.1f}% clear of the nearest)"
                              if not result["false_positives"] else ""))
        print()
        if result["false_positives"]:
            print("  RED — it is claiming questions that are not about the corpus. Lower "
                  "THRESHOLD until this is zero, or switch the band off.")
            return 1
        if not result["kept"]:
            print("  RED — it claims nothing at all; the threshold is below every positive.")
            return 1
        print(f"  OK — no false positives. The {result['of'] - result['kept']} positive(s) it "
              f"gives up fall through to the paid router, which is the safe direction.")
        return 0

    for query in (args.query or list(FIT_POSITIVES[:3]) + list(FIT_NEGATIVES[:3])):
        hit = look_up(query)
        if hit is None:
            print(f"  {query!r:56} -> (router decides)")
        else:
            print(f"  {query!r:56} -> {hit.route}  d={hit.distance:.3f}  "
                  f"{len(hit.sources)} chunk(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
