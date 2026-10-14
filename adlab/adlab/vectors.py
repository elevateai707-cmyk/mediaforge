"""ChromaDB collection of teardowns, for "best hooks for home-service ads" queries.

What gets embedded is the teardown's *structure* (hook type, angles, format,
pacing, why it works), never the source ad's script, so search finds patterns,
not copy. Metadata carries the filterable fields (site, hook_type, format, score).

Embeddings default to Chroma's built-in local ONNX model (CPU, no API cost, no
VRAM; it downloads ~80 MB on first use). Pass `embedding_function` to override
(tests use a deterministic fake).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .config import settings
from .schemas import Teardown

COLLECTION = "teardowns"


def teardown_document(t: Teardown) -> str:
    """The text that represents a teardown in vector space."""
    scenes = " / ".join(s.description for s in t.timeline)
    return (
        f"Hook: {t.hook.type} ({t.hook.seconds:.1f}s): {t.hook.visual}. "
        f"Angles: {', '.join(t.angles)}. Format: {t.format}. "
        f"Pacing: {t.pacing.length_s:.0f}s, {t.pacing.cuts_per_10s:.1f} cuts/10s, "
        f"music {t.pacing.music_energy}, captions {t.pacing.caption_style or 'none'}. "
        f"Offer: {t.offer or '-'}. CTA: {t.cta or '-'}. Structure: {scenes}. "
        f"Why it works: {t.why_it_works}"
    )


def client(path: Optional[Path] = None):
    import chromadb

    p = path or settings().chroma_dir
    p.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(p))


def collection(chroma=None, embedding_function=None):
    chroma = chroma or client()
    kwargs: dict[str, Any] = {"name": COLLECTION, "metadata": {"hnsw:space": "cosine"}}
    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function
    return chroma.get_or_create_collection(**kwargs)


def add_teardown(coll, teardown_id: str, t: Teardown, site: str, score: Optional[float] = None) -> None:
    meta: dict[str, Any] = {
        "ad_id": t.ad_id, "site": site, "hook_type": t.hook.type, "format": t.format,
        "angles": ",".join(t.angles), "music_energy": t.pacing.music_energy,
    }
    if score is not None:
        meta["score"] = float(score)
    coll.upsert(ids=[teardown_id], documents=[teardown_document(t)], metadatas=[meta])


def search(coll, query: str, n: int = 5, where: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    res = coll.query(query_texts=[query], n_results=n, where=where)
    out = []
    for i, tid in enumerate(res["ids"][0]):
        out.append({"id": tid, "distance": res["distances"][0][i],
                    "metadata": res["metadatas"][0][i], "document": res["documents"][0][i]})
    return out
