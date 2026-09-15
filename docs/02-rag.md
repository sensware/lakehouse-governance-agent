# 02 — RAG over the data catalog

**File:** `src/lga/rag.py`
**Run:** `uv run lga index` then `uv run lga ask "Which tables hold PII and who owns them?"`

## The pattern

```
offline:   documents ──embed──▶ vectors ──▶ index          (lga index)
online:    question ──embed──▶ top-k docs ──▶ LLM(prompt + docs) ──▶ grounded answer
```

Retrieval-Augmented Generation is the answer to "the model doesn't know my data". Instead of
fine-tuning (slow, stale, expensive), you *retrieve* the relevant facts at query time and put
them in the prompt. The model's job shrinks to reading comprehension.

## What each piece does

| Piece | Here | Notes |
|---|---|---|
| Embedding model | `BAAI/bge-small-en-v1.5` via fastembed | 384-dim, runs on CPU via ONNX. Same text → same vector; similar meaning → nearby vectors. |
| Normalisation | `faiss.normalize_L2` | Unit vectors ⇒ inner product == cosine similarity. |
| Index | `IndexFlatIP` | Brute force, exact. Fine to ~100k docs. Beyond that: `IndexHNSWFlat` / IVF, or a hosted store. |
| Retriever | `retrieve(query, k)` | Returns cards + scores. Scores let you set a "not confident" threshold. |
| Generator | Claude with a **grounding system prompt** | "Answer only from cards, cite tables, say when not present." |

## The RAG contract (how to explain this to a client)

1. **Grounding beats memory.** The model is told to answer *only* from retrieved context.
   Hallucination risk moves from the model to the retriever, which you can measure.
2. **Retrieval quality is the product.** Bad chunks → bad answers no matter the model.
   Evaluate with a small golden set: question → expected table(s). Measure recall@k.
3. **Context management.** k=3 cards ≈ 2–3k tokens. Bigger k = more recall, more cost,
   more noise. Re-ranking (cross-encoder) is the usual next step.
4. **Freshness.** The corpus is generated from live metadata; re-index on every pipeline
   run. No drift between catalog and answers.
5. **Memory.** This phase is stateless (one question). Phase 3 adds working memory
   (the transcript); a "conversation memory" would add prior Q&A to the context.
6. **Retrieval is role-scoped too (Phase 6).** `search_catalog` filters retrieved cards
   by the caller's `Role` before returning them — a table your role can't query, it can't
   surface via RAG either. Same policy, same object, whichever tool the model reaches
   for. See docs/08.

## Try
```bash
uv run lga ask "What does the gold layer contain and what is it built from?"
uv run lga ask "Is there anything about mortgages?"        # should say: not in catalog
```

Look at the `[retrieved: ...]` trailer: those scores are your retrieval telemetry.

## Swap points
- `embed_texts` → Voyage (`voyage-3`), OpenAI (`text-embedding-3-small`), Bedrock Titan.
- FAISS → Pinecone / Weaviate / Databricks Vector Search: `upsert(id, vec, meta)` + `query`.
- Add **hybrid search**: BM25 on column names + vectors on descriptions, fused by RRF.
