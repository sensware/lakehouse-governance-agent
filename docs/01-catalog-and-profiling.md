# 01 — Catalog & profiling (your home turf, framed for AI)

**Files:** `data/build_lakehouse.py`, `src/lga/catalog.py`
**Run:** `uv run lga build-data && uv run lga catalog`

## What happens

1. `build_lakehouse.py` generates a retail-bank dataset with **deliberate bronze defects**:
   enum chaos (`CURRENT/current/CUR`), whitespace/case noise, malformed emails, implausible
   DOBs, sentinel `risk_rating = 99`, orphan accounts, future-dated and double-posted
   transactions, balance outliers. Silver fixes each one with an explicit SQL rule; gold
   builds two *data products* (`customer_360`, `monthly_channel_volume`).
2. `catalog.py` walks `information_schema`, profiles every column (null %, cardinality,
   min/max, mean/stddev, samples), tags PII, attaches domain owner + lineage, and renders each
   table as a **catalog card** in Markdown.

## Why it matters for the AI half

The card is the unit of retrieval in Phase 2 and the context the agent reads in Phase 3.
Design choices that make LLM consumption work:

- **One card per table, self-contained.** Chunking by table keeps every column's stats in
  the same retrieval unit — the model never sees half a schema.
- **Numbers, not adjectives.** "null 17.5%, max 99" lets the model *reason*; "some nulls"
  does not.
- **Metadata as first-class content.** Owner, layer, lineage, PII flags are in the card
  text, so semantic search over "who owns customer PII" works with no extra code.
- **The PII flag is enforced, not just documented.** `_mask()` redacts a flagged column's
  sample values and min/max *before* the `ColumnProfile` is built — the one place every
  consumer (this card, the RAG index, `profile_column`) reads from. It wasn't always: see
  docs/07 for the gap this project found in its own catalog and the fix.

## Try

```bash
uv run lga catalog | grep -A3 "risk_rating"
```
Spot the `99` sentinel and the 17.5% nulls — the agent will find the same thing in Phase 3,
but *with evidence it gathered itself*.

## Interview-grade extensions
- Swap the declared `LINEAGE` dict for column-level lineage parsed with `sqlglot`.
- Emit the catalog as [OpenMetadata](https://open-metadata.org) / DataHub JSON.
- Add a `freshness` field (max `updated_at`) and a `sla_hours` from the data contract.
