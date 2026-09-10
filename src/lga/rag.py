"""Phase 2 — Retrieval-Augmented Generation over the data catalog.

Pipeline:  catalog cards -> local embeddings (fastembed/ONNX) -> FAISS index
           question -> embed -> top-k cards -> Claude synthesises a grounded answer

Why this shape:
  * The corpus (catalog cards) is *generated from live metadata*, so the knowledge
    base never drifts from the platform. Re-run `lga index` in CI after each dbt run.
  * Embeddings are local + free here. Swap `embed_texts` for Voyage / OpenAI /
    Bedrock Titan without touching the rest. FAISS -> Pinecone/Weaviate is the
    same interface: upsert(id, vector, metadata) + query(vector, k).
  * The answer is grounded: we pass only retrieved cards and ask Claude to cite
    table names and say "not in catalog" rather than guess — the core RAG contract.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass

import faiss
import numpy as np

from .catalog import build_catalog
from .config import ANTHROPIC_MODEL, EMBED_MODEL, INDEX_DIR, require_api_key

_INDEX_FILE = INDEX_DIR / "cards.faiss"
_META_FILE = INDEX_DIR / "cards.pkl"

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return _embedder


def embed_texts(texts: list[str]) -> np.ndarray:
    vecs = np.array(list(_get_embedder().embed(texts)), dtype="float32")
    faiss.normalize_L2(vecs)  # cosine similarity via inner product
    return vecs


@dataclass
class RetrievedCard:
    table: str
    text: str
    score: float


def build_index() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog()
    cards = [(t.name, t.to_card()) for t in catalog]
    vecs = embed_texts([text for _, text in cards])

    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    faiss.write_index(index, str(_INDEX_FILE))
    with open(_META_FILE, "wb") as fh:
        pickle.dump(cards, fh)
    print(f"Indexed {len(cards)} catalog cards ({vecs.shape[1]}-dim) -> {_INDEX_FILE}")


def _load_index():
    if not _INDEX_FILE.exists():
        raise SystemExit("No index yet — run: uv run lga index")
    index = faiss.read_index(str(_INDEX_FILE))
    with open(_META_FILE, "rb") as fh:
        cards = pickle.load(fh)
    return index, cards


def retrieve(query: str, k: int = 3) -> list[RetrievedCard]:
    index, cards = _load_index()
    qv = embed_texts([query])
    scores, idxs = index.search(qv, min(k, len(cards)))
    out = []
    for score, i in zip(scores[0], idxs[0]):
        if i == -1:
            continue
        table, text = cards[i]
        out.append(RetrievedCard(table=table, text=text, score=float(score)))
    return out


_SYSTEM = (
    "You are a data catalog assistant for a bank's lakehouse. Answer ONLY from the "
    "catalog cards provided. Cite the table name(s) you used. If the answer is not in "
    "the cards, say so plainly — never invent columns, types, or statistics."
)


def answer(question: str, k: int = 3) -> str:
    import anthropic

    require_api_key()
    hits = retrieve(question, k=k)
    context = "\n\n---\n\n".join(h.text for h in hits)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4000,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Catalog cards:\n\n{context}\n\n---\n\nQuestion: {question}",
            }
        ],
    )
    body = "".join(block.text for block in msg.content if block.type == "text")
    cites = ", ".join(f"{h.table} ({h.score:.2f})" for h in hits)
    return f"{body}\n\n[retrieved: {cites}]"


if __name__ == "__main__":
    build_index()
    print(json.dumps([h.__dict__ for h in retrieve("which columns contain PII?")], indent=2))
