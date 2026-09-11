# 09 — Snowflake's lakehouse-governance guide, mapped to this project

Source: Snowflake, [*Govern Your Lakehouse for AI*](https://www.snowflake.com/en/developers/guides/govern-your-lakehouse-for-ai/).
Read after `docs/07` (the Databricks pair) and `docs/08` (the ABAC feature it names
as still missing) — this article is the sharpest match yet: it's a step-by-step
tutorial for the *exact* row-level-security-plus-masking gap docs/07 left open, using
different vocabulary for the same mechanism.

## The article's thesis, in one paragraph

Governing a lakehouse for AI means combining an **open table format** (Iceberg — ACID,
schema evolution, time travel, so multiple engines can share the data without a fork)
with **enterprise governance primitives that apply identically to a human's SQL and an
agent's tool call**: row access policies, column masking, quality monitoring, and a
semantic layer AI agents query through rather than raw tables. Its implementation
sequence is worth reading as a checklist: storage → external volume → **row access
policy** → **masking policy** → **tags/classification** → **scheduled quality checks**
→ **semantic model** → **AI agent**. Governance comes *before* the AI layer, not after.

## Concept → Snowflake feature → where it lives here

| Article concept | Snowflake feature | This repo | Honest gap |
|---|---|---|---|
| Open table format so multiple engines share data without migrating it | **Apache Iceberg tables** + **External Volumes** + **Catalog Integration** | DuckDB reading its own native format — no external-storage or multi-engine story at all | Not modeled; this project has one engine, one file. A real answer: point DuckDB's own Iceberg reader, or swap in Spark/Snowflake, at the same S3 prefix. |
| "Dynamic filtering based on `CURRENT_ROLE()`… allows a single table to serve multiple audiences without duplication" | **Row Access Policies** | **Built in docs/08**: `policy.py::Role.row_filters` + `_substitute_table` — the same "one table, filtered per caller" idea, done by SQL text substitution instead of an engine feature | Enforced in application code, not the query engine (docs/08's honest-limits section) — a Snowflake row access policy can't be bypassed by a differently-worded query; this project's regex-based rewrite, in principle, could be. |
| "Original data remains unchanged; privileged users see real values" via role-aware column transforms | **Dynamic Data Masking** | **Built**: `catalog.py::_mask` + `Role.unmask_pii` — masked by default, real values only for `dq_auditor` | Snowflake's masking policy has multiple patterns (full, partial, tokenization) attached declaratively per column; this project has one partial-mask function, applied by one `if` check. |
| Hierarchical classification driving policy | **Object Tagging** (PII, sensitivity level, on databases/schemas/tables/columns) | `catalog.py::PII_COLUMNS` — a hand-written set, not a tag hierarchy | Same gap docs/07 named for `DOMAIN_OWNERS`: authored once, no propagation, no tag-based policy triggers. |
| "Schedule quality checks to run automatically… results stored in dedicated event tables" | **Data Metric Functions (DMFs)** — system (null count, duplicates, freshness) and custom SQL | `contract.py::detect_drift` re-runs every `quality_rules` assertion — the custom-DMF idea, exactly | **Gap named in docs/06/GUIDE §16, still open:** DMF results land in a Snowflake event table (queryable history); `contract-status` recomputes on demand and doesn't persist a run log. |
| A semantic model (dimensions, measures, verified queries) an agent queries through | **Cortex Analyst** + semantic models | `catalog.py`'s "catalog cards" — the same instinct: give the agent governed meaning, not raw tables | Cortex Analyst's semantic model is a declared, versioned artifact with verified queries; catalog cards are profiling output, not curated business definitions. |
| Natural-language access to governed data via an orchestrating agent | **Cortex Agents** (with Cortex Analyst for text-to-SQL) | `agent.py`'s ReAct loop + `rag.py`'s grounded Q&A | Directionally the same shape: an agent that answers from governed structure instead of raw inference. |
| Agents discoverable through a shared workspace | **Snowflake CoWork** | `.mcp.json` — any MCP client (Claude Code, Claude Desktop) discovers this project's tools the same way | Narrower: one tool surface via one protocol, not a workspace of many discoverable agents. |

## What changed because of this article: nothing left to fix — verify instead

Unlike docs/07 (which found a live bug), this pass didn't surface a new defect — the
row-policy/masking gap it names was *already* fixed while writing docs/07 and docs/08,
one article earlier. So the useful thing to do here is **verify the two implementations
actually agree**, not just that they use similar words:

| Snowflake's row access policy example | This project's equivalent |
|---|---|
| `CREATE ROW ACCESS POLICY … AS (region STRING) RETURNS BOOLEAN -> CURRENT_ROLE() = 'REGION_ADMIN' OR region = CURRENT_ROLE()` | `Role.row_filters["silver_customers"] = "city = 'London'"`, looked up by `LGA_ROLE`, substituted into the query text |
| Attached to a table with `ALTER TABLE … ADD ROW ACCESS POLICY …`, enforced by the engine on every query, from any tool | Substituted by `tools.run_sql`/`profile_column` before execution, enforced by application code, from every registered tool (`run_sql`, `profile_column`, `list_tables`, `search_catalog`, `read_contract` — docs/08) |
| Masking policy: `CREATE MASKING POLICY … RETURNS STRING -> CASE WHEN CURRENT_ROLE() IN ('PRIVILEGED') THEN val ELSE '***MASKED***' END` | `catalog.py::_mask`, gated by `Role.unmask_pii`, applied inside `profile_column` before the `ColumnProfile` is built |

Same two primitives (filter rows by role, mask columns by role), same trigger (a role
value), same reach (every tool a caller might use) — implemented as SQL-engine features
there, as Python + string substitution here. The gap that's real and stated in both
docs/08 and this doc: Snowflake's version can't be bypassed by how the query is phrased;
this project's can, in principle, by an adversarial query the regex doesn't recognize.
That's not a difference in ambition, just in where the enforcement point physically
sits — and it's the single sentence to have ready if asked why a Snowflake/Databricks
platform's guarantees are stronger than this demo's.

## Interview soundbite

> "Snowflake's guide implements exactly the ABAC gap two Databricks articles named
> independently — row access policies plus masking policies, both keyed off the
> caller's role, enforced identically no matter which tool reaches the table. I'd
> already built the same two primitives in this project by the time I read this
> article: a `Role.row_filters` dict substituted into the query text before it runs,
> and a masking function gated by `unmask_pii`. The honest difference is where
> enforcement sits — Snowflake's is in the query engine and can't be evaded by
> rephrasing; mine is a regex-based rewrite in application code and, in principle,
> could be. That's the one sentence I'd lead with if asked to compare a real platform's
> guarantees to a from-scratch demo's."
