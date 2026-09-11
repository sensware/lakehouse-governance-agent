# 00 — Architecture & how this maps to the job

## What this project is

A **governance agent for a medallion lakehouse**. It applies GenAI to the *data value chain* —
the JD's phrase — rather than to a chatbot. Every AI capability in the job spec is exercised on
a problem you already understand (catalog, quality, lineage, contracts), so the new concepts
land on familiar ground.

```
                ┌──────────────────────────────────────────────────────────────┐
                │                    lakehouse.duckdb                          │
                │   bronze_*  ──▶  silver_*  ──▶  gold_*      (medallion)      │
                └──────────────┬───────────────────────────────────────────────┘
                               │ information_schema + profiling SQL
                     ┌─────────▼─────────┐
   Phase 1           │   catalog.py      │  TableProfile / ColumnProfile / "catalog cards"
                     └───┬──────────┬────┘
                         │          │ cards → embeddings → FAISS
                         │   ┌──────▼──────┐
   Phase 2               │   │   rag.py    │  retrieve(k) → Claude grounded answer
                         │   └──────┬──────┘
                         │          │ exposed as a tool
                     ┌───▼──────────▼────┐
   Phase 3 / 4       │    tools.py       │  list_tables · run_sql · profile_column
                     │  (one registry)   │  search_catalog · write_artifact
                     └───┬──────────┬────┘
              in-process │          │ over MCP (stdio)
                 ┌───────▼──────┐ ┌─▼─────────────┐
   Phase 3       │  agent.py    │ │ mcp_server.py │  Phase 4
                 │  ReAct loop  │ │ any MCP client│
                 └───────┬──────┘ └───────────────┘
                         │ two personas, one tool surface
                 ┌───────▼──────┐
   Phase 4       │   a2a.py     │  Author agent ⇄ Reviewer agent → approved contract
                 └──────────────┘
```

## Layer-by-layer: what it teaches, and where it lives in a real platform

| Layer | Here | On Databricks / Snowflake | JD bullet |
|---|---|---|---|
| Storage + medallion | DuckDB tables with `bronze_/silver_/gold_` prefixes | Delta/Iceberg tables in three schemas or catalogs; **Unity Catalog** / Snowflake DBs | Medallion, data products |
| Metadata + profiling | `catalog.py` queries `information_schema` + aggregates | **Unity Catalog** system tables + **Lakehouse Monitoring**, Snowflake `ACCOUNT_USAGE`, dbt `manifest.json`, Great Expectations / Soda | Quality, lineage, metadata standards |
| Business context (domain/owner) | hand-written `DOMAIN_OWNERS` dict | **Genie** / **Genie Ontology** — auto-derived from tables, queries, dashboards | Metadata standards |
| Lineage | Declared dict `LINEAGE` | **Unity Catalog** lineage API, OpenLineage events, Snowflake `OBJECT_DEPENDENCIES`; column-level via sqlglot/dbt | Lineage, data contracts |
| Embeddings | `fastembed` (ONNX, local, free) | Voyage / OpenAI `text-embedding-3` / Bedrock Titan / **Databricks Foundation Model APIs** | Vector DBs |
| Vector store | FAISS `IndexFlatIP` file | Pinecone, Weaviate, **Databricks AI/Vector Search** (Delta-backed, ACL-aware), Snowflake Cortex Search | FAISS/Pinecone/Weaviate |
| LLM | Anthropic Claude (Messages API, tool use) — called directly, outside any perimeter | Same, or via Bedrock/Vertex; **Databricks Model Serving** runs it *inside* the security perimeter | Claude, OpenAI APIs |
| Agent loop | Hand-written ReAct in `agent.py` | LangGraph / CrewAI / AutoGen; **Databricks Agent Bricks** / Mosaic AI Agent Framework | ReAct, LangChain, CrewAI |
| Agent memory/state | in-process `messages` list, dies with the run | **Lakebase** — managed Postgres, transactional, shared across agents | Agent memory |
| Tool protocol | `tools.py` registry + `mcp_server.py` | MCP servers in front of Unity Catalog, Snowflake, Airflow; **Unity AI Gateway** as the platform-wide control plane, **Omnigent** for coding agents specifically | MCP |
| Multi-agent | `a2a.py` author/reviewer hand-off | A2A protocol (Google), CrewAI crews, LangGraph multi-actor graphs | A2A orchestration |
| Observability | `rich` panel trace, printed, not persisted | **MLflow 3** — full request/tool-call tracing, auditable, queryable | MLOps |
| Guardrails | read-only SQL, single statement, row cap, PII masking in `profile_column`, artifact path sandbox | **Unity AI Gateway** — ALLOW/DENY/ASK enforced *before* execution, platform-wide, not per-tool | Responsible AI, guardrails |

**Deeper dive:** [docs/07-databricks-governance.md](07-databricks-governance.md) works through
Databricks' [*data-native agents*](https://www.databricks.com/blog/data-native-ai-agents-why-agents-must-move-your-data)
argument line by line against this repo — including a real PII-masking gap it found and
a fix that landed in `catalog.py`.

## The architect's questions this project prepares you to answer

1. **"How would you use GenAI in data governance?"** — RAG over live metadata for discovery;
   agents that *generate evidence-backed* DQ rules and contracts; a reviewer agent as a
   control. Human approval stays in the loop (the artifact is a *draft*).
2. **"What are the risks?"** — hallucinated columns (mitigated by grounding + tools that
   return real data), unsafe actions (read-only tool surface), PII leakage into prompts
   (mask at the tool boundary), non-determinism (log every tool call; pin models).
3. **"RAG vs fine-tuning vs long context?"** — metadata changes daily → RAG. Re-index in CI.
4. **"Why MCP?"** — decouples tool implementation from the model/harness; one governed tool
   surface for Claude Desktop, IDEs, and orchestrators; auditable boundary.
5. **"How does this scale?"** — profiling pushes down to the warehouse; index is rebuilt
   incrementally by table; agents are stateless per run; artifacts land in git for review.
