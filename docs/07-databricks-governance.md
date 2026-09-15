# 07 — Databricks' governance-for-agents argument, mapped to this project

Two companion Databricks pieces, read together:

1. Databricks, [*Data-Native AI Agents: Why Agents Must Move to Your Data*](https://www.databricks.com/blog/data-native-ai-agents-why-agents-must-move-your-data)
   — governance has to run *inside* the agent's execution path, at query-planning time.
2. Databricks, [*Governance Beyond Security: Knowledge, Context & Ontology in the Lakehouse*](https://www.databricks.com/blog/governance-beyond-security-knowledge-context-ontology-lakehouse)
   — governance isn't only access control; its metadata (glossary, lineage, contracts,
   certification) *is* the semantic layer that makes an agent's answers trustworthy —
   and lets a cheap model do what would otherwise need a frontier one.

Read this after `docs/00` and `docs/GUIDE.md` — it sharpens the Databricks column of
those mapping tables with the actual product names and, more usefully, uses both
articles' own arguments to find **real gaps in this project**, one of which got fixed
while writing this doc.

## Part 1 — "Data-native agents": governance at query-planning time

### The thesis, in one paragraph

Agents that call an LLM and a vector store that live *outside* the data platform pay for
it six ways: governance has to be re-implemented and kept in sync in a second place;
every hop (egress → external API → back) stacks latency; costs fragment across egress +
duplicate storage + per-token billing; each vendor needs separate audit trails; and the
agent can't see the business context (metric definitions, glossary, domain ownership)
that only lives inside the platform. Its sharpest point is about *when* governance has
to run:

> "A financial summary shaped by ungoverned rows cannot be redacted after the fact.
> Policy must be enforced at query planning time."

Once an aggregation or a tool call has already used a row, you cannot un-use it by
filtering the *output*. **Governance has to sit in front of the computation, not behind
it.** Their answer is "data-native agents": run the agent inside the data platform, under
one governance layer, so policy enforcement, lineage, and audit are the same mechanism
for a human's SQL query and an agent's tool call.

### Concept → Databricks product → where it lives here

| Article concept | Databricks feature | This repo | Honest gap |
|---|---|---|---|
| One governance layer for data **and** agents | **Unity Catalog** — access control, lineage, residency for tables *and* models/agents as first-class assets | `catalog.py` (PII flags, domain owners, lineage) + `contracts/` (approved, versioned) | Ours is metadata *about* governance (documentation); Unity Catalog *enforces* it — an ACL check runs before a query returns, ours doesn't. |
| A single control plane for all model/agent traffic, with ALLOW/DENY/ASK **before** execution | **Unity AI Gateway** (and **Omnigent**, which routes coding agents — Claude Code included — through that same gateway with a shared audit trail) | `tools.py`'s single registry + guardrails (`run_sql`'s read-only regex, single-statement, row cap, `read_only` connection) consumed identically by `agent.py` and `mcp_server.py` | Right shape, wrong scope: our policy is hand-coded per tool, not a platform-wide gateway every tool automatically inherits. Directionally correct, though — the check runs *before* the SQL executes, which is the article's actual point. |
| Governed, ACL-aware retrieval that tracks its own lineage | **AI Search / Vector Search** — Delta-backed indexes that respect Unity Catalog ACLs | `rag.py` — FAISS index over catalog cards | The PII-masking fix below closes the *sample-value* leak (cards embed masked values now — confirmed by re-indexing). What's still missing: our FAISS index itself has no ACL concept — every card is retrievable by every caller, table-level, full stop. Real Vector Search enforces the underlying table's ACL on every query. |
| Inference inside the security perimeter, not an external SaaS call | **Model Serving** (Foundation Model APIs — Claude is available this way on Databricks) | `agent.py` / `rag.py` call `anthropic.Anthropic()` directly | This project *is* the "external stack" the article critiques for the LLM hop. In production you'd route through Model Serving/Foundation Model APIs so the call never leaves the perimeter. |
| Governed, transactionally consistent agent state/memory | **Lakebase** — managed Postgres for conversation history, task progress, preferences | the in-memory `messages` list in `agent.py` (see docs/03, "agent memory") | Dies with the process. No persistence, no shared state across agents — exactly the "memory scattered across an external Redis" problem, just smaller. |
| Full request/tool-call tracing for audit and debugging | **MLflow 3** — captures LLM calls, tool invocations, scores, evals | the `rich` panel trace printed to stdout | Human-readable, not queryable or persisted. docs/GUIDE.md §11 already names the fix: log `(run_id, step, tool, args, result_hash, latency)` to a table. |
| Auto-derived business context (metrics, glossary, domain ownership) grounding the agent | **Genie** / **Genie Ontology** — a knowledge graph extracted automatically from tables, queries, and dashboards | `catalog.py::DOMAIN_OWNERS` — a hand-written dict | Ours is authored once and goes stale; Genie's is derived continuously from how the data is actually used. |
| Agents as first-class, governed, optimized platform workloads | **Agent Bricks** | the *idea* of `contracts/` as versioned, promotable artifacts | We promote a YAML file by convention; Agent Bricks makes the agent itself a managed, monitored workload the platform optimizes. |

### The gap this exposed in *our own* code — found, and fixed

Re-reading the article's line — "cannot be redacted after the fact" — against this
project surfaced a specific defect: **`profile_column` and every catalog card returned
unmasked PII.** `catalog.py` correctly *flagged* `first_name`, `last_name`, `email`,
`date_of_birth` as `is_pii: true` — but nothing checked that flag before putting real
sample values (and, for `date_of_birth`, the exact min/max birth dates) into the
`ColumnProfile`, the Markdown catalog card, the RAG index, and every tool result built
from it. The catalog *documented* the column as sensitive; nothing *enforced* it. That's
the article's failure mode one layer down: policy metadata that exists but isn't
consulted at execution time.

**Fixed** in `catalog.py::profile_column` (`_mask()`), mirroring Unity Catalog
column-level masking: when `is_pii` is set, `sample_values`/`min`/`max` are redacted
*before* the `ColumnProfile` is built — one point of enforcement, inherited by every
consumer (the catalog card, the RAG corpus, the ReAct agent's `profile_column` tool,
and MCP) because they all read the same object:

```
first_name -> ['N***', 'G****', 'L***', ...]
email      -> ['k***@example.com', 'm***@example.com', ...]
date_of_birth -> min '1*********'  max '2*********'   (format visible, value is not)
```

**Deliberately left alone: `run_sql`.** It still returns raw PII —
`SELECT email FROM silver_customers LIMIT 2` comes back unmasked. That's not an
oversight; Phase 3's whole DQ-audit use case (finding one email shared by three
customer IDs, malformed addresses, duplicate rows) needs the *real* values — a masked
view can't detect a duplicate. This is the same distinction Unity Catalog draws between
a **masked preview grant** (`profile_column`, safe for broad discovery) and **full,
audited data access** (`run_sql`, needed for investigation, and the reason a real
platform gates it by role and logs every use). Masking one tool and not the other,
deliberately, is the honest version of "enforce governance" — not "hide all the data."

### What's still not real enforcement

- The mask and the row filter (docs/08) live in application code, not a platform
  policy engine — a second tool that reads the raw DuckDB file directly bypasses all
  of it. Unity Catalog's ACLs and Snowflake's row access/masking policies (docs/09)
  are enforced by the engine itself, so nothing downstream can bypass them.
- `run_sql` and `profile_column` **now do** have role-aware table, row, and column
  policy (docs/08) — the "any caller sees everything" state this bullet used to
  describe is fixed. What's still not modeled is a real authenticated identity behind
  the role: `LGA_ROLE` is one coarse setting per process, not a per-call, per-user
  lookup.

### Where this project already agrees with the thesis

- **One tool registry, two consumers.** `tools.py` is defined once and used identically
  by the in-process ReAct agent and the MCP server — the same "single control plane"
  instinct as Unity AI Gateway, just not yet a platform-level one.
- **Guardrails before execution, not after.** `run_sql` rejects a write *before* running
  it. The SQL never executes — there's no output to redact, because there's no
  computation to redact it from. That is the article's point, correctly applied, at tool
  scale.
- **Contracts as governed artifacts.** `contracts/<table>.yml` treats a data contract as
  a versioned, promotable thing with an owner and an approval step — the same instinct
  Unity Catalog and Agent Bricks apply to tables and agents respectively.

## Part 2 — "Governance beyond security": metadata as the semantic layer

### The thesis, in one paragraph

The companion article's move is to stop treating governance artifacts — tags, glossary
terms, lineage, contracts, certifications — as compliance paperwork and start treating
them as **the raw material of an AI semantic layer**. Access control only answers *who*
can touch a column; it says nothing about *what the column means*, *whether its values
are trustworthy*, or *which definition of "risk rating" is the official one*. Without
that, an agent has to either guess (hallucination risk) or have the definition pasted
into every prompt (expensive). With it:

> "Because meaning and context live in the catalog instead of the more expensive LLM
> tokens, cheaper models can serve most needs with more trust."

Governance metadata, done right, is what lets a *small* model answer most questions
correctly — you reserve the expensive, frontier-model reasoning for the genuinely hard
cases, because the easy cases don't need reasoning at all; they need a certified
definition looked up.

### Concept → Databricks feature → where it lives here

| Article concept | Databricks feature | This repo | Honest gap |
|---|---|---|---|
| Governance metadata as executable semantics, not documentation | **Data contracts** embedded as runtime logic; **Unity Catalog** as the metadata repo agents read *and execute against* | `contracts/*.yml`'s `quality_rules` are literal SQL, re-run by `contract.detect_drift` — not prose. This is the one place this project already matches the article exactly. | Contracts aren't wired into the pipeline itself (silver build doesn't consult them); a real system would generate the DLT/dbt expectation *from* the contract, not check it after the fact. |
| A dynamic, query-able certification that **auto-revokes** when the underlying data changes | **AI Certification scorecard** in Unity Catalog | `lga contract-status` — re-runs every rule and reports drift live; a passing contract with fresh drift is, functionally, a revoked certification | We don't persist the scorecard as its own artifact — it's recomputed on demand, not stored/queryable history. |
| A single named owner accountable for fixing a wrong definition | **Data Product Owner accountability model** | `contracts/*.yml: metadata.owner` — a team alias (`customer-domain@bank.internal`), not a named individual with a defined correction workflow | Coarser than the article's model: no named person, no "agent got it wrong, here's who fixes the catalog" loop. |
| An agent bound to one governed data product, answering from certified definitions rather than inferring | **Genie Agent** | `rag.py::answer()` — grounded strictly to retrieved catalog cards, told to say "not in the cards" rather than infer | Ours is one general RAG tool over every table; Genie binds one agent per certified data product specifically. |
| Row-level policy enforced identically across SQL **and** vector search | **Attribute-Based Access Control (ABAC)** | **Fixed in docs/08**: `policy.py`'s `Role` (table access + row filters + PII unmasking) is consulted by every tool — `run_sql`, `profile_column`, `list_tables`, `search_catalog`, `read_contract` — so a restricted role sees the same rows and the same tables whichever one it uses. | Enforced in application code (regex-based SQL rewriting), not the query engine — see docs/08's honest-limits section, and docs/09 for the same gap as Snowflake implements it natively. |
| Column-level lineage captured automatically, not maintained by hand | Unity Catalog automatic lineage | `catalog.py::LINEAGE` — a hand-written dict, correct today, silently stale the moment someone adds a transformation and forgets to update it | Unchanged from docs/01's original gap; this article gives it a sharper reason to matter: lineage is *provenance for trust*, not just a diagram. |
| Glossary/taxonomy → ontology → AI semantic layer | Business Glossary, taxonomy, Genie Ontology | `catalog.py`'s "catalog cards" *are* a small, hand-built semantic layer — domain, owner, PII flags, lineage, all in one LLM-readable document | Static and manually authored, where Genie's ontology is continuously derived from how the data is actually queried and dashboarded. |

### The reframe this article gives the whole project

Phase 1's "catalog cards" were originally framed as an engineering convenience — a
retrieval unit for RAG. This article gives them a better name: they're a **hand-built
semantic layer**, and that's *why* Phase 2's RAG answers stay grounded on a small local
embedding model instead of needing a frontier model to reason about raw table dumps.
Phase 5's contracts are the sharpest match in the whole project: the article's insistence
that governance be "executable runtime logic, not documentation" is *exactly* what
`quality_rules: - assertion: "SELECT count(*) FROM ... WHERE ..."` already is — every
rule is real SQL, re-run, not a sentence a human has to remember to check.

## Client takeaways

> Databricks' "data-native agents" argument is that governance has to run at query
> planning time, before computation, because you can't redact an aggregate after the
> fact. Building this PoC surfaced exactly that failure in our own catalog: the `is_pii`
> flag was documentation, not an enforced check — `profile_column` was returning real
> names and emails. Fixed at the one place every consumer reads from, the same shape as
> Unity Catalog column masking, and *deliberately* left `run_sql` unmasked, because the
> DQ-audit agent genuinely needs raw values to find duplicates — masking has to be
> scoped to the use case, not applied blindly to every tool.

> The companion article's point is that governance metadata is the semantic layer, not
> compliance paperwork — it's what lets a cheap model answer most questions correctly
> instead of needing a frontier model to infer meaning from raw tables every time. This
> PoC's `contracts/*.yml` quality rules are already literal SQL that gets re-run, not
> documentation — that's the "executable governance" idea done right. What's still
> missing for production is the sharper certification point: Databricks' scorecard
> auto-revokes and is queryable history; `contract-status` here recomputes on demand and
> doesn't persist — a natural next investment for a client adopting this pattern.

---

Both parts pointed at the same unresolved item from two angles: **row-level policy that
applies identically no matter which tool (SQL, vector search, a future one) an agent
uses to reach the data.** That's now built — `policy.py`, wired into every tool — see
**docs/08**. Snowflake's own governance guide turns out to describe exactly this same
mechanism (row access policies + masking policies); that comparison is **docs/09**.
