# tenancy/rag_store.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

RAG_DIR = Path(os.getenv("CHROMA_DIR", "data/chroma")).resolve()
RAG_DIR.mkdir(parents=True, exist_ok=True)

EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
RAG_ENABLED = os.getenv("RAG_ENABLED", "1") == "1"


def rag_available() -> bool:
    if not RAG_ENABLED:
        return False
    try:
        import chromadb  # noqa
        from openai import OpenAI  # noqa
        return True
    except Exception:
        return False


def _client():
    import chromadb
    return chromadb.PersistentClient(path=str(RAG_DIR))


def _collection(tenant_id: str):
    c = _client()
    name = f"spec_chunks_{tenant_id}"
    return c.get_or_create_collection(name=name)


def _embed_texts(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing (required for embeddings)")

    client = OpenAI()
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def upsert_spec_chunks(
    tenant_id: str,
    spec_id: str,
    chunks: list[dict[str, Any]],
) -> None:
    """
    chunks: [{id, chunk_index, content, meta}]
    """
    if not rag_available():
        return

    coll = _collection(str(tenant_id))

    ids = [c["id"] for c in chunks]
    texts = [c["content"] for c in chunks]
    metas = []
    for c in chunks:
        meta = dict(c.get("meta") or {})
        meta.update({"spec_id": spec_id, "chunk_index": int(c.get("chunk_index", 0))})
        metas.append(meta)

    embs = _embed_texts(texts)
    coll.upsert(ids=ids, documents=texts, metadatas=metas, embeddings=embs)


def query_chunks(
    tenant_id: str,
    query: str,
    top_k: int = 6,
    spec_id: str | None = None,
) -> list[dict[str, Any]]:
    if not rag_available():
        return []

    coll = _collection(str(tenant_id))
    where = {"spec_id": spec_id} if spec_id else None

    res = coll.query(query_texts=[query], n_results=top_k, where=where)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    ids = (res.get("ids") or [[]])[0]
    out = []
    for i in range(len(docs)):
        out.append({"id": ids[i], "content": docs[i], "meta": metas[i]})
    return out