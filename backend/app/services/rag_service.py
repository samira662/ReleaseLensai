"""Release Q&A with retrieval-augmented generation (AR5).

Chunks = one document per change (+ one per breaking finding). Embeddings come from
sentence-transformers if installed, otherwise a deterministic hashing embedder.
Vectors are stored in ChromaDB if available, otherwise in an in-memory store.
"""
import hashlib
import logging
import re
import math

# import numpy as np  <-- numpy বাদ দেওয়া হয়েছে

from ..config import settings
from .llm_service import LLMError, LLMService

log = logging.getLogger(__name__)
DIM = 384
_st_model = None
_memory: dict[int, dict] = {}


def _hash_embed(text: str) -> list[float]:
    """Deterministic hashing embedder without numpy."""
    vec = [0.0] * DIM
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    for tok in tokens + [a + "_" + b for a, b in zip(tokens, tokens[1:])]:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 8) & 1 else -1.0
    
    # L2 normalization using standard math
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def embed(texts: list[str]) -> list[list[float]]:
    global _st_model
    if _st_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _st_model = SentenceTransformer(settings.embedding_model)
        except Exception:
            _st_model = False
    if _st_model:
        return _st_model.encode(texts, normalize_embeddings=True).tolist()
    return [_hash_embed(t) for t in texts]


def _collection(project_id: int, reset: bool = False):
    try:
        import chromadb
        client = chromadb.PersistentClient(path=settings.chroma_path)
        name = f"project_{project_id}"
        if reset:
            try:
                client.delete_collection(name)
            except Exception:
                pass
        return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    except Exception as exc:
        log.info("ChromaDB unavailable, using in-memory store: %s", exc)
        return None


def index_project(project_id: int, changes: list[dict], findings: list[dict]) -> int:
    docs, metas, ids = [], [], []
    for c in changes:
        docs.append(f"{c['title']}. {c['summary']} Category: {c['category']}. Files: {', '.join(c['files'][:15])}. Author: {c['author']}")
        metas.append({"sha": c["sha"], "title": c["title"][:200], "url": c["url"], "pr": c.get("pr_number") or 0, "category": c["category"]})
        ids.append(f"c-{c['sha']}")
    for i, f in enumerate(findings):
        docs.append(f"Breaking change in {f['file']} ({f['symbol']}): {f['reason']} Before: {f['old_signature']} After: {f['new_signature']}")
        metas.append({"sha": f.get("commit_sha") or "", "title": f"Breaking: {f['symbol']}", "url": "", "pr": 0, "category": "breaking"})
        ids.append(f"b-{i}")
    if not docs:
        return 0
        
    vectors = embed(docs)
    col = _collection(project_id, reset=True)
    if col is not None:
        col.add(ids=ids, embeddings=vectors, documents=docs, metadatas=metas)
    else:
        _memory[project_id] = {"ids": ids, "docs": docs, "metas": metas, "vecs": vectors}
    return len(docs)


def retrieve(project_id: int, question: str, k: int = 6) -> list[dict]:
    qv = embed([question])[0]
    col = _collection(project_id)
    hits = []
    
    if col is not None and col.count() > 0:
        res = col.query(query_embeddings=[qv], n_results=min(k, col.count()))
        for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
            hits.append({"doc": doc, **meta})
    elif project_id in _memory:
        m = _memory[project_id]
        # Manual cosine similarity instead of numpy matrix multiplication
        scores = []
        for vec in m["vecs"]:
            sim = sum(a * b for a, b in zip(vec, qv))
            scores.append(sim)
        
        # Get top-k indices sorted by score
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        hits = [{"doc": m["docs"][i], **m["metas"][i]} for i in order]
        
    return hits


def answer(project_id: int, question: str, llm: LLMService, k: int = 6) -> dict:
    hits = retrieve(project_id, question, k)
    sources = [
        {"ref": f"S{i + 1}", "sha": h["sha"], "title": h["title"], "url": h["url"], "pr_number": h.get("pr") or None, "category": h.get("category")}
        for i, h in enumerate(hits)
    ]
    if not hits:
        return {"answer": "No indexed changes were found for this release. Re-run the analysis first.", "sources": [], "mode": "extractive"}
        
    if llm.available:
        context = "\n".join(f"[S{i + 1}] {h['doc']}" for i, h in enumerate(hits))
        system = ("Answer the question about a software release using ONLY the numbered sources. Cite sources inline like [S1]. "
                  "If the sources do not contain the answer, say you could not find it in this release. Be concise.")
        try:
            return {"answer": llm.complete(system, f"Sources:\n{context}\n\nQuestion: {question}", 700).strip(), "sources": sources, "mode": "llm"}
        except LLMError as exc:
            log.warning("RAG answer fell back to extractive: %s", exc)
            
    lines = [f"- [{s['ref']}] {s['title']}" for s in sources[:4]]
    return {"answer": "The most relevant changes for your question:\n" + "\n".join(lines), "sources": sources, "mode": "extractive"}