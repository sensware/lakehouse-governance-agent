# 00 — Architecture & how this maps to the job

## What this project is

A **governance agent for a medallion lakehouse**. It applies GenAI to the *data value chain* —
the JD's phrase — rather than to a chatbot. Every AI capability in the job spec is exercised on
a problem you already understand (catalog, quality, lineage, contracts, access control), so
the new concepts land on familiar ground.

```
                ┌──────────────────────────────────────────────────────────────┐
                │                    lakehouse.duckdb                          │
   Phase 0      │   bronze_*  ──▶  silver_*  ──▶  gold_*      (medallion)      │
                │              (+ a Null Member sentinel row — docs/10)        │
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
   Phase 6           │     policy.py     │  ABAC: role → allowed_tables, row_filters,
   (the guardrail)   │    (every call)   │  unmask_pii — consulted before every tool below (docs/08)
                     └───┬──────────┬────┘
                         │          │
                     ┌───▼──────────▼────┐
   Phase 3 / 4       │      tools.py     │  list_tables · run_sql · profile_column ·
                     │   (one registry)  │  search_catalog · read_contract · write_artifact
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
                         │ approved contract, versioned
                 ┌───────▼──────┐
   Phase 5       │ contract.py  │  detect_drift → diff → author/reviewer → save (version bump)
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
| Data contracts + change mgmt | `contract.py` — drift detection, structured diff, deterministic version bump | Unity Catalog + DLT expectations + CI; Snowflake Data Metric Functions + CI | Data contracts, architectural guardrails |
| Tool-execution guardrails | read-only SQL, single statement, row cap, artifact path sandbox | central tool gateway enforcing this per call | Responsible AI, guardrails |
| **Access control (ABAC)** | **`policy.py` — a `Role` (allowed tables, row filters, PII unmask) every tool consults before touching the lakehouse; enforced identically in `run_sql`, `profile_column`, `list_tables`, `search_catalog`, `read_contract`** | **Unity Catalog row filters + column masking** (engine-enforced); **Snowflake Row Access Policies + Dynamic Data Masking** | Privacy/security/regulation compliance, responsible AI |
| Observability | `rich` panel trace, printed, not persisted | **MLflow 3** — full request/tool-call tracing, auditable, queryable | MLOps |

**Deeper dive:** [docs/07-databricks-governance.md](07-databricks-governance.md) and
[docs/09-snowflake-governance.md](09-snowflake-governance.md) work through three vendor
articles (two Databricks, one Snowflake) line by line against this repo — including a
real PII-masking gap they found (fixed in `catalog.py`) and the ABAC gap that became
Phase 6 ([docs/08](08-abac-row-level-policy.md)). [docs/10](10-null-member-pattern.md)
covers the Kimball Null/Unknown Member pattern, built alongside the six AI phases rather
than inside them — the JD's data-platform half meeting its AI half in the same repo.

## The architect's questions this project prepares you to answer

1. **"How would you use GenAI in data governance?"** — RAG over live metadata for discovery;
   agents that *generate evidence-backed* DQ rules and contracts; a reviewer agent as a
   control. Human approval stays in the loop (the artifact is a *draft*).
2. **"What are the risks?"** — hallucinated columns (mitigated by grounding + tools that
   return real data), unsafe actions (read-only tool surface), PII leakage into prompts
   (mask at the tool boundary), non-determinism (log every tool call; pin models),
   over-privileged agents (ABAC, §"Access control" above — a role can't discover, query,
   or RAG-retrieve outside its scope, regardless of what the prompt asks for).
3. **"RAG vs fine-tuning vs long context?"** — metadata changes daily → RAG. Re-index in CI.
4. **"Why MCP?"** — decouples tool implementation from the model/harness; one governed tool
   surface for Claude Desktop, IDEs, and orchestrators; auditable boundary.
5. **"How does this scale?"** — profiling pushes down to the warehouse; index is rebuilt
   incrementally by table; agents are stateless per run; artifacts land in git for review.
6. **"How do you keep a contract from drifting out of sync with the pipeline?"** — a
   no-LLM drift detector re-runs every quality-rule assertion and diffs the schema;
   version bumps are a deterministic function of the diff classification, not a human
   guess (`contract.py`, docs/06).
7. **"How would you enforce row-level security and column masking for an agent?"** — one
   `Role`, consulted by every tool, before the query runs — not after (`policy.py`,
   docs/08). Named honestly: it's application-enforced here, not engine-enforced like a
   real platform's row access/masking policies — a second tool bypassing it is the stated
   limit, not a hidden one.
