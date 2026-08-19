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
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

LOG = logging.getLogger("oddball.vector_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "data"          # put your PDFs here
CHROMA_PATH = REPO_ROOT / "chroma_db"   # gitignored; a build artifact, rebuildable

# Small and fast, runs locally, no key and no network. 384 dimensions.
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Loaded once and reused. Instantiating HuggingFaceEmbeddings pulls the model into memory and
# takes seconds — paying that per question would put it on the answer path, where the whole
# turn budget is two of them.
_embeddings = None
_store = None


def get_embeddings():
    """The embedding model, loaded once."""
    global _embeddings
    if _embeddings is None:
        LOG.info("loading the embedding model (%s)", EMBEDDING_MODEL)
        _embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    return _embeddings


def get_retriever(k: int = 4):
    """A retriever over the persisted store, or **None** if it was never built.

    Args:
        k: how many chunks to return. Four at 500 characters is ~2000 characters of context,
           which is a couple of register tables and still leaves room in the prompt.

    Returns:
        A LangChain retriever, or None. None is not an error — it means "no datasheets have
        been ingested", which is the normal state of a fresh clone, and the caller answers
        ungrounded and says so rather than falling over.
    """
    global _store
    if not CHROMA_PATH.exists():
        LOG.info("no vector store at %s — run `python tools/vector_db.py` to build one",
                 CHROMA_PATH)
        return None

    if _store is None:
        try:
            _store = Chroma(persist_directory=str(CHROMA_PATH),
                            embedding_function=get_embeddings())
        except Exception:                              # noqa: BLE001
            LOG.exception("could not open the vector store at %s", CHROMA_PATH)
            return None

    return _store.as_retriever(search_kwargs={"k": k})


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


def build_vector_database():
    print(f"1. Loading PDFs from {DATA_PATH}...")
    # glob is explicit and recursive: data/ has arduino/, espressif/, raspberry_pi/ and
    # sensors/ subdirectories, and the default pattern does not descend into them — so the
    # loader silently found nothing while looking like it had worked.
    loader = PyPDFDirectoryLoader(str(DATA_PATH), glob="**/*.pdf")
    documents = loader.load()
    print(f"   Loaded {len(documents)} pages.")

    if not documents:
        print(f"   No PDFs found under {DATA_PATH}. Put datasheets in there and run again.")
        return

    print("2. Chunking documents...")
    # We use small chunks (500 chars) with high overlap (150 chars)
    # so code blocks or register tables don't get cut in half.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=150,
        length_function=len,
    )
    chunks = text_splitter.split_documents(documents)
    print(f"   Split into {len(chunks)} chunks.")

    print(f"3. Embedding and saving to {CHROMA_PATH}...")
    Chroma.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        persist_directory=str(CHROMA_PATH),
    )
    print("Database built successfully!")


# Run this once to build the DB
if __name__ == "__main__":
    build_vector_database()
