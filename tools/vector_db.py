#!/usr/bin/env python3
"""
Module:  vector_db.py
Purpose: Turn datasheet PDFs into a searchable local vector store, and read from it.
Author:  LB
Date:    2026-08-18 (retriever added, paths fixed 2026-08-19)

    python tools/vector_db.py          # build (or rebuild) the store from data/

## Two bugs fixed on 2026-08-19, both about paths

`CHROMA_PATH` was `"../chroma_db"` and `DATA_PATH` was `"data"` — both relative to the
**current working directory**, not to the repo. That meant the database was written one level
ABOVE the project, outside anything git or the `tar`-over-ssh deploy would carry, and it moved
depending on where you happened to run Python from. Under a systemd unit the working directory
is not the repo at all, so on the Pi it would have built somewhere else again.

Both are now absolute, derived from this file's own location, the same way
`orchestrator/settings.py` does it.

## And one that mattered more: nothing read from this

The store was built and then never queried. `agents/firmware_agent.py` prompted Gemini with
chat history alone, so datasheet questions were answered from model memory — which is exactly
the failure D30 documents, models stating electronics relationships fluently and wrongly. A
RAG pipeline nobody retrieves from is a slow way of doing nothing.

`get_retriever()` is the missing half. It returns **None** rather than raising when the store
has never been built, so a fresh clone with no PDFs in it still runs and simply answers without
grounding — and says so.

## One collection: datasheets (2026-08-23)

There were briefly two. `data/academic/` was embedded into an `academic` collection for the
ACADEMIC route, kept apart from the datasheets because a semantic search for "GPIO
configuration" ranks by similarity alone and cannot tell that a chunk came from a course
outline — so a firmware answer could be grounded in a syllabus and cite it as a datasheet.

**The academic collection is gone** (D23). That route reads LB's Canvas calendar now and does no
retrieval at all, so the store is back to the one job it started with:

    datasheets    everything under data/ EXCEPT data/academic/   -> firmware_agent

`data/academic/` is **still excluded from the walk**, and that exclusion is the load-bearing half
of what survives. The directory did not go away — it holds `academic_calendar.json`, and LB may
still keep syllabus PDFs there for his own reference. Dropping the exclusion along with the
collection would sweep those into the datasheets pool and reintroduce exactly the cross-grounding
the split existed to prevent, with no collection boundary left to catch it.

**A store built before 2026-08-21 will look empty.** Chroma's default collection name is
`langchain`; this one is `datasheets`. An older store opens fine under the new name and yields
nothing rather than failing, so anyone upgrading must rebuild. The same is true for anyone whose
store still holds the retired `academic` collection — it is simply never opened now:

    python tools/vector_db.py

That is already the documented step after adding PDFs, and `chroma_db/` is gitignored precisely
because it is a rebuildable artifact.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

# NOTHING heavy is imported at module scope, and that is a deployment decision.
#
# `langchain_huggingface` pulls **torch and transformers** the moment it is imported, and
# `agents/firmware_agent.py` imports this module — so a plain `import` up here would drag
# multiple gigabytes of wheels onto the Pi and several seconds onto the first firmware
# question, to support a retriever that returns None until datasheets have been ingested.
#
# So the embedding stack is imported inside `get_embeddings()`, which only runs when a store
# actually exists. A Pi with no PDFs in `data/` never loads torch, and does not need it
# installed — see the optional block in requirements.txt.
#
# langchain_chroma and the loaders are cheap and stay lazy only for consistency with that.

LOG = logging.getLogger("oddball.vector_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "data"          # put your PDFs here
# Not a datasheet folder, and skipped by the walk. It holds `academic_calendar.json` (synced
# from Canvas) and any syllabus PDFs LB keeps for his own reference — neither is something the
# FIRMWARE agent should retrieve. With the academic collection gone, this exclusion is now the
# ONLY thing keeping a course outline out of a register-level answer.
EXCLUDED_FROM_DATASHEETS = DATA_PATH / "academic"
CHROMA_PATH = REPO_ROOT / "chroma_db"   # gitignored; a build artifact, rebuildable

# The one collection. A named constant rather than a literal at the call sites, because a
# typo in a collection name does not raise — Chroma happily opens a new, empty one, and the
# agent then answers ungrounded while reporting that no documents cover the question.
DATASHEET_COLLECTION = "datasheets"

# Small and fast, runs locally, no key and no network. 384 dimensions.
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Loaded once and reused. Instantiating HuggingFaceEmbeddings pulls the model into memory and
# takes seconds — paying that per question would put it on the answer path, where the whole
# turn budget is two of them.
_embeddings = None

# Keyed by collection name. There is one collection again, so this holds a single entry in
# practice — kept as a dict because `get_retriever` still takes a `collection` argument and
# opening a second one should cache rather than clobber.
_stores: dict[str, object] = {}


# The repo id, as HuggingFace knows it. `EMBEDDING_MODEL` is the short name
# `HuggingFaceEmbeddings` takes; this is the same model spelled the way the cache indexes it,
# and the two have to agree or the cache check below silently answers "not cached" forever.
EMBEDDING_REPO = f"sentence-transformers/{EMBEDDING_MODEL}"


def _local_only() -> bool:
    """Should the model be loaded WITHOUT contacting huggingface.co?

    True when the files are already cached, which is the only time it is safe.

    ## Why this is a per-model argument and not `HF_HUB_OFFLINE`

    The env var was tried first and does not work in this process. `huggingface_hub` reads it
    into a module constant **when it is imported**, and `faster_whisper` imports it while
    loading `base.en` — on the main thread, at start-up, racing the warm-up thread that would
    have set it. The result was a log line saying "from the local cache" above thirty-two HTTP
    requests to huggingface.co. The harness passed, because in a small test process nothing
    had imported the library yet.

    `local_files_only` is passed to the model constructor instead. It is scoped to this one
    load, it works whatever was imported first, and — the part that matters most — it CANNOT
    stop `faster_whisper` downloading a Whisper model it does not have. A global flag would
    have to be correct for every HuggingFace consumer in the process; this one only has to be
    correct for this model.

    ## What it saves, measured 2026-08-29

        contacting the hub   construct 3.81s
        local_files_only     construct 2.61s, zero network calls

    One second per cold start, and — the real reason — no dependency on a network that may not
    be there. Those are HEAD requests on the path in front of the free corpus tier, and a HEAD
    to a host that does not answer does not fail in a second.

    **Conditional, and that is the whole design.** Passing it unconditionally would make a
    fresh clone fail to download the model at all, with an error about local files on a machine
    that is perfectly online — the bare NO_SUCHFILE shape `audio/wake.ensure_feature_models`
    exists to prevent.
    """
    return _cache_dir() is not None


def _cache_dir() -> "Path | None":
    """The cached snapshot directory for `EMBEDDING_REPO`, or None if it is not there.

    **By path, and NOT with `huggingface_hub.try_to_load_from_cache`, which is the obvious
    way and does not work here.** `huggingface_hub` reads `HF_HUB_OFFLINE` into a module
    constant when it is IMPORTED, so importing it in order to ask about the cache freezes the
    flag as False a moment before we set it. The first version of this function did exactly
    that: it reported "cached", set the variable, and the HEAD requests went out anyway.

    The layout it walks is the hub cache's own and has been stable for years:

        <cache>/models--<org>--<name>/snapshots/<commit sha>/config.json

    Strict on purpose. A wrong answer in one direction costs a second; in the other it makes a
    fresh clone refuse to download the model while insisting it is offline.
    """
    root = (os.environ.get("HF_HUB_CACHE")
            or (Path(os.environ["HF_HOME"]) / "hub" if os.environ.get("HF_HOME") else None)
            or Path.home() / ".cache" / "huggingface" / "hub")
    folder = Path(root) / f"models--{EMBEDDING_REPO.replace('/', '--')}"
    if not folder.is_dir():
        return None
    # `config.json` rather than the weights: the small file every snapshot has, so its
    # presence means a real snapshot rather than a half-written one.
    return next((c.parent for c in folder.glob("snapshots/*/config.json")), None)


def get_embeddings():
    """The embedding model, loaded once."""
    global _embeddings
    if _embeddings is None:
        # Imported here, not at module scope: this line is what pulls torch.
        from langchain_huggingface import HuggingFaceEmbeddings      # noqa: PLC0415

        cached = _local_only()
        LOG.info("loading the embedding model (%s)%s", EMBEDDING_MODEL,
                 " from the local cache" if cached else " — fetching from huggingface.co")
        began = time.monotonic()
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"local_files_only": True} if cached else {})
        LOG.info("embedding model ready in %.2fs", time.monotonic() - began)
    return _embeddings


def warm() -> None:
    """Load the embedding model NOW, so the first question does not pay for it.

    ## The nine seconds this moves

    `get_embeddings` is lazy, which is right — a session that never asks a datasheet question
    should not pay for torch. But the first one that does pays all of it, **on the answer
    path**, and `engine/core._corpus_route` sits in the FREE tier in front of the router:

        12:03:23  loading the embedding model (all-MiniLM-L6-v2)
        12:03:34  route 'Nothing go to sleep.' -> persona
        12:04:09  turn: ... => answered in 48.89s

    Nine of those seconds were this, on a turn whose actual answer was a canned dismissal.

    Called from a background thread at start-up by `engine/run_voice.py`. Never raises: a
    machine that cannot load it must still start, and `get_retriever` already returns None for
    "nothing ingested" — a warm-up that fails simply leaves the first question paying what it
    used to pay.
    """
    try:
        get_embeddings()
    except Exception:                                                 # noqa: BLE001
        LOG.warning("could not warm the embedding model; the first corpus question will "
                    "load it instead", exc_info=True)


def get_retriever(k: int = 4, collection: str = DATASHEET_COLLECTION):
    """A retriever over one collection of the persisted store, or **None** if never built.

    Args:
        k:          how many chunks to return. Four at 500 characters is ~2000 characters of
                    context, which is a couple of register tables and still leaves room in the
                    prompt.
        collection: which collection to search. There is only one, and the default is it — the
                    argument survives so that a caller who forgets it gets the datasheets rather
                    than a silently empty collection named after their typo.

    Returns:
        A LangChain retriever, or None. None is not an error — it means "nothing has been
        ingested", which is the normal state of a fresh clone, and the caller answers
        ungrounded and says so rather than falling over.

        A collection that exists but is EMPTY returns a working retriever that yields zero
        chunks. That is deliberately not special-cased here: the firmware agent already handles
        "no chunks came back" with an explicit sentence, and the alternative is a document count
        via Chroma's private `_collection`, which is a fragile thing to make the answer path
        depend on.
    """
    if not CHROMA_PATH.exists():
        LOG.info("no vector store at %s — run `python tools/vector_db.py` to build one",
                 CHROMA_PATH)
        return None

    if collection not in _stores:
        try:
            from langchain_chroma import Chroma                      # noqa: PLC0415

            _stores[collection] = Chroma(persist_directory=str(CHROMA_PATH),
                                         collection_name=collection,
                                         embedding_function=get_embeddings())
        except Exception:                              # noqa: BLE001
            LOG.exception("could not open collection %r at %s", collection, CHROMA_PATH)
            return None

    return _stores[collection].as_retriever(search_kwargs={"k": k})


def format_chunks(docs) -> tuple[str, list[dict]]:
    """Render retrieved chunks for a prompt, and list where each came from.

    Returns:
        (context, sources) — the text to inject, and one dict per chunk with `source` and
        `page`. The sources go on a card: a register value read aloud needs to be traceable to
        a page, or it is just another confident sentence.
    """
    if not docs:
        return "", []

    lines, sources = [], []
    for i, doc in enumerate(docs, 1):
        meta = getattr(doc, "metadata", {}) or {}
        name = Path(str(meta.get("source", "unknown"))).name
        page = meta.get("page")
        # PyPDFDirectoryLoader pages are 0-indexed; humans are not.
        page_label = f" p.{int(page) + 1}" if isinstance(page, int) else ""
        lines.append(f"[{i}] from {name}{page_label}:\n{doc.page_content.strip()}")
        sources.append({"source": name, "page": (int(page) + 1) if isinstance(page, int) else None})

    return "\n\n".join(lines), sources


def load_pdfs(root: Path, exclude: Path | None = None) -> list:
    """Every PDF page under `root`, minus anything under `exclude`.

    Kept public, and kept taking `exclude`, though `build_vector_database` is now its only
    caller: the exclusion of `data/academic/` is the thing standing between a syllabus and a
    firmware answer, and burying it inside the builder would make it easy to drop by accident.

    Args:
        root:    directory to walk.
        exclude: a subdirectory of `root` to leave out, or None.

    Returns:
        A list of LangChain documents, one per page. Empty when `root` does not exist — a
        missing directory is "no PDFs yet", not a failure.
    """
    from langchain_community.document_loaders import PyPDFDirectoryLoader  # noqa: PLC0415

    if not root.exists():
        return []

    # glob is explicit and recursive: data/ has arduino/, espressif/, raspberry_pi/ and
    # sensors/ subdirectories, and the default pattern does not descend into them — so the
    # loader silently found nothing while looking like it had worked.
    documents = PyPDFDirectoryLoader(str(root), glob="**/*.pdf").load()
    if exclude is None:
        return documents

    # Filtered on the resolved path rather than on a substring of it. `"academic" in source`
    # would also drop `data/sensors/academic_press_sensor.pdf`, and dropping a datasheet
    # because of its filename is the kind of bug that only shows up as a worse answer.
    exclude = exclude.resolve()
    kept = []
    for doc in documents:
        source = Path(str((getattr(doc, "metadata", {}) or {}).get("source", "")))
        try:
            if source.resolve().is_relative_to(exclude):
                continue
        except (OSError, ValueError):       # unresolvable path — keep it, do not lose a page
            pass
        kept.append(doc)
    return kept


def _build_collection(documents: list, collection_name: str, label: str) -> int:
    """Chunk, embed and persist `documents` into one named collection.

    Args:
        documents:       pages from `_load_pdfs`.
        collection_name: which Chroma collection to write.
        label:           what to call these in the printed output.

    Returns:
        How many chunks were written. Zero means there was nothing to ingest.

    ## A page is not text, and that had to be learned on the Pi

    `documents` being non-empty does NOT mean there is anything to embed. A PDF with no text
    layer — a scan, or a datasheet exported as pure vector art — loads as a perfectly good page
    object whose `page_content` is the empty string. Both of LB's Pi camera PDFs are exactly
    that: 2 pages, **0 extractable characters**.

    Guarding only on `not documents` therefore reached `Chroma.from_documents([])`, which dies
    with `ValueError: Expected Embeddings to be non-empty list or numpy array, got []` — a
    message pointing at Chroma's internals for a problem that is entirely about the input file.
    Worse is the version that does not crash: an empty collection looks identical to a working
    one from the outside, and the firmware agent would answer ungrounded forever while a store
    sat on disk saying it had been built. Same shape as D9's empty BOM.

    So textless pages are counted and named, not silently dropped.
    """
    from langchain_chroma import Chroma                              # noqa: PLC0415
    from langchain_text_splitters import RecursiveCharacterTextSplitter    # noqa: PLC0415

    if not documents:
        return 0

    # Drop pages with no extractable text BEFORE splitting, and keep the count — a file that
    # contributed nothing is the single most useful thing to report here.
    usable = [d for d in documents if (d.page_content or "").strip()]
    empty = len(documents) - len(usable)

    if empty:
        blank_files = sorted({Path(str((getattr(d, "metadata", {}) or {}).get(
            "source", "?"))).name for d in documents if not (d.page_content or "").strip()})
        print(f"   {label}: {empty} page(s) carried NO extractable text — {', '.join(blank_files)}")
        print(f"          These are image-only PDFs. Nothing can be retrieved from them until "
              f"they are OCR'd or replaced with text-bearing files.")

    if not usable:
        print(f"   {label}: nothing to embed — every page was empty.")
        return 0

    # We use small chunks (500 chars) with high overlap (150 chars)
    # so code blocks or register tables don't get cut in half.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=150,
        length_function=len,
    )
    chunks = text_splitter.split_documents(usable)
    print(f"   {label}: {len(usable)} usable page(s) -> {len(chunks)} chunks")

    if not chunks:
        # Belt and braces: text that is all whitespace-ish can still split to nothing.
        print(f"   {label}: the splitter produced no chunks; nothing written.")
        return 0

    Chroma.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        persist_directory=str(CHROMA_PATH),
        collection_name=collection_name,
    )
    return len(chunks)


def build_vector_database():
    """Build the datasheet collection from `data/`. Safe to re-run; that is how you update it."""
    print(f"1. Loading PDFs from {DATA_PATH}...")
    datasheets = load_pdfs(DATA_PATH, exclude=EXCLUDED_FROM_DATASHEETS)
    print(f"   Loaded {len(datasheets)} datasheet page(s).")

    if not datasheets:
        print(f"   No PDFs found under {DATA_PATH}. Put datasheets in there and run again.")
        print(f"   ({EXCLUDED_FROM_DATASHEETS} is skipped on purpose — it is not a datasheet "
              f"folder.)")
        return

    print(f"2. Chunking and embedding into {CHROMA_PATH}...")
    written = _build_collection(datasheets, DATASHEET_COLLECTION, "datasheets")

    if not written:
        # Loud, because this is the state that most looks like success from the outside: the
        # install worked, the build "ran", and every grounded answer will still be ungrounded.
        print("\nNOTHING WAS WRITTEN. The store is empty, so `get_retriever()` still returns "
              "None\nand the firmware agent will keep answering without documents.")
        return

    print(f"Database built successfully — {written} chunks.")


# Run this once to build the DB
if __name__ == "__main__":
    build_vector_database()
