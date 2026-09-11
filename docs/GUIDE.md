# The Complete Guide — Lakehouse Governance Agent

> A build-along study document for the **Senior Cloud Data & AI Architect** role.
> It walks through everything in this repo, top to bottom, and explains every
> concept and process in detail — assuming you already know data platforms
> (lakehouse, medallion, data mesh) but are new to the LLM / agent side.

---

## 0. How to read this

The project is built in **six phases**. Each phase takes one cluster of the job
description and implements it against a problem you already understand — a bank's
medallion lakehouse — so the unfamiliar AI concepts land on familiar ground.

| Phase | You build | New concepts it teaches | JD bullets |
|---|---|---|---|
| 0 | A seeded BFSI medallion lakehouse in DuckDB | (revision) medallion, data mesh, data products, domains | Medallion, data products, lakehouse |
| 1 | Metadata catalog + column profiler | Metadata as an LLM input; "catalog cards" | Data quality, lineage, metadata standards |
| 2 | RAG pipeline over the catalog | Embeddings, vector DBs, similarity, grounding, context management | RAG, FAISS/Pinecone/Weaviate, Claude/OpenAI APIs |
| 3 | ReAct governance agent with tools | Tool calling, the agent loop, prompt engineering, agent memory, ReAct vs ToT vs AutoGPT | LLMs, prompt engineering, ReAct, Tree of Thought, tool calling, agent memory |
| 4 | MCP server + author/reviewer A2A loop | Model Context Protocol, multi-agent orchestration, separation-of-duties as a control | MCP, A2A orchestration, agent frameworks, responsible AI |
| 5 | Contract drift detection + change management | Data contracts, semantic diff, semver, deterministic versioning | Data contracts, lineage consensus, architectural guardrails |

Everything runs locally. Every command is `uv run lga <something>`. The whole
thing is ~1,500 lines of Python plus seven concept notes (`docs/00`–`06`) and this
guide, which ties them together.

---

## 1. The job, decoded

The JD is two roles fused into one:

**The data-platform architect** (your existing strength):
> Architect data lake / Lakehouse / streaming • data integration & pipeline patterns •
> data quality, lineage, metadata standards • privacy/security/regulation compliance •
> data products, data mesh, Medallion • Snowflake + Databricks on AWS/Azure • BFSI
> transformation programmes • trusted advisor to stakeholders.

**The GenAI / agent architect** (the part this project trains):
> LLMs, prompt engineering, agent frameworks (LangChain, AutoGen, CrewAI) • MCP, ReAct,
> Tree of Thought, AutoGPT-style reasoning • Python, OpenAI APIs, Anthropic Claude,
> vector DBs (FAISS, Pinecone, Weaviate) • A2A orchestration, agent memory, tool calling •
> RAG pipelines with memory + context management + tool usage • *"Design and implement
> AI and Gen AI solution for the data value chain"* • *"leverage GenAI capabilities"* for
> governance, quality, metadata, lineage • responsible AI frameworks • MLOps.

The bridge sentence — the one this whole project is built around — is:

> **"Define and implement data governance, quality, metadata, and lineage frameworks and
> should be able to leverage GenAI capabilities."**

That is exactly what a *governance agent* is: GenAI applied to the data value chain
instead of to a customer chatbot.

---

## 2. The mental model

A **large language model (LLM)** is a function: text in → text out. It has read a
large fraction of the public internet and code, compressed into weights. It cannot
look anything up, run anything, or remember anything between calls. Everything else —
"agents", "RAG", "tools", "memory" — is scaffolding built *around* that function to
make it useful on your data.

Three ways to get your data to the model:

1. **Fine-tuning** — retrain weights on your data. Expensive, slow, stale the moment
   the data changes. Wrong choice for governance (metadata changes daily).
2. **Long context** — paste everything into the prompt. Simple, but there's a size
   limit and a cost per token, and the model's attention degrades over very long inputs.
3. **Retrieval-augmented generation (RAG)** — keep the data in a searchable store,
   fetch only the relevant slice per question, put *that* in the prompt. This is what
   Phase 2 builds.

And two ways to let the model *act* on your data:

4. **Tool calling** — you describe functions to the model; it emits a structured
   request to call one; your code runs it and feeds the result back. Phase 3.
5. **A protocol** — a standard wire format for exposing those tools so any client can
   use them. That's **MCP**. Phase 4.

An **agent** is just: an LLM in a loop, with tools, that decides when to stop.

---

## 3. Setup & tooling — and why each piece

```bash
brew install uv                 # Python packaging + venv + Python-version manager
cd lakehouse-governance-agent
uv sync                         # creates .venv, installs everything from pyproject.toml
cp .env.example .env            # add ANTHROPIC_API_KEY=sk-ant-...
```

| Dependency | Role | Why this one |
|---|---|---|
| `uv` | env + deps | Fast, reproducible (`uv.lock`), manages the Python version itself. Modern standard; shows you're current. |
| `duckdb` | the "lakehouse" | In-process OLAP SQL engine. Same SQL shape as Snowflake/Databricks, zero infra. Stands in for the warehouse. |
| `anthropic` | LLM client | The Messages API: system prompt, messages, `tools=[...]`, `stop_reason`. |
| `fastembed` | embeddings | Runs a small embedding model locally via ONNX — **no API key, no torch, no cost**. Swappable for Voyage/OpenAI/Bedrock. |
| `faiss-cpu` | vector store | Facebook AI Similarity Search. A file-based index. Stands in for Pinecone/Weaviate. |
| `mcp` | tool protocol | The official Model Context Protocol SDK — server + stdio transport. |
| `pyyaml` | contracts | Data contracts are YAML; Phase 5 parses and diffs them structurally. |
| `rich` | console output | Renders the agent's reasoning/tool-call trace as readable panels. |
| `pytest` | tests | Offline tests for the Phase 5 diff engine (no API calls). |

`ANTHROPIC_MODEL` defaults to `claude-opus-5` (in `src/lga/config.py`); set it to
`claude-sonnet-5` in `.env` for cheaper, faster iteration.

---

## 4. Phase 0 — the medallion lakehouse

**File:** `data/build_lakehouse.py` · **Command:** `uv run lga build-data`

### Concepts

**Medallion architecture** is a three-tier refinement pattern:

| Layer | Contains | Guarantees | In this project |
|---|---|---|---|
| **Bronze** | Raw ingested data, as landed | None. Faithful copy of the source, warts and all. | `bronze_customers`, `bronze_accounts`, `bronze_transactions` — with **deliberate defects**: duplicate rows, `99` risk-rating sentinels, mixed-case enums (`VERIFIED`/`verified`), malformed emails, orphan foreign keys, future-dated timestamps, implausible dates of birth. |
| **Silver** | Cleaned, conformed, deduplicated, typed | Referential integrity, standard enums, one row per business key. | `silver_*` — built by SQL that trims/normalises, drops exact duplicates, filters impossible values, and drops orphan rows via `SEMI JOIN` / `ANTI JOIN`. |
| **Gold** | Business-level aggregates and data products | Ready for analytics / ML / reporting. | `gold_customer_360` (a customer data product), `gold_monthly_channel_volume`. |

**Data mesh** is the organisational counterpart: instead of one central team owning
all data, each **domain** (Payments, Deposits, Customer) owns its data end-to-end and
publishes **data products** — well-described, quality-guaranteed, discoverable datasets
with an owner and an SLA. In the code, `catalog.py`'s `DOMAIN_OWNERS` dict assigns each
table to a domain; `gold_customer_360` is explicitly labelled a data product.

**Why DuckDB and not Databricks?** The point is the *patterns*, not the infra. Every
piece of SQL here (`information_schema` queries, window functions, `ANTI JOIN`) runs
unchanged on Snowflake or Spark SQL. `docs/00` has the full mapping table.

### The seeded defects (and why they matter)

The bronze layer's defects are not decoration — they are the **workload** for the
agents in later phases. When the Phase 3 agent audits `bronze_customers` it *discovers*
these by running SQL; when the Phase 4 reviewer checks a contract, it catches the
pipeline dropping rows because of them. A clean dataset would teach nothing.

Some defects were **planted**; a few **emerged** from the random generator by accident
(one email shared by three customer IDs; a customer born after their account opened).
The agent found both kinds — see §10.

---

## 5. Phase 1 — metadata catalog & profiling

**File:** `src/lga/catalog.py` · **Command:** `uv run lga catalog`

### Concepts

A **data catalog** is the index of what data exists: for every table and column, its
type, meaning, owner, freshness, sensitivity, and lineage. It is the thing analysts
search and governance teams audit. Real ones: Unity Catalog, Snowflake Horizon,
Collibra, DataHub, Alation.

**Column profiling** is measuring the actual contents: null rate, distinct count,
min/max, mean/stddev, sample values. It's how you find out that a column *documented*
as "1–5 risk rating" actually contains `99` in 15% of rows.

**Lineage** is the dependency graph: which tables/columns a table is derived from.
- *Table-level* lineage ("silver_customers comes from bronze_customers") is declared
  here in the `LINEAGE` dict, because we own the build and know it.
- *Column-level* lineage ("silver_customers.city is `initcap(trim(bronze_customers.city))`")
  requires parsing the transformation SQL — a real platform uses `sqlglot`, dbt's
  `manifest.json`, or OpenLineage. Noted as a gap; not built.

**Data classification** tags columns by sensitivity. Here, `PII_COLUMNS` marks
`first_name, last_name, email, date_of_birth` as personally identifiable. This drives
access control and masking downstream, and the agents reason about it ("this table
carries PII, so the contract needs a retention clause").

### The key design idea: "catalog cards"

`TableProfile.to_card()` renders each table's profile as a compact **Markdown
document** — layer, domain, owner, lineage, and a column table with null %, cardinality,
PII flag, samples. These cards are:

- human-readable (they *are* the `lga catalog` output), and
- the **retrieval unit** for Phase 2 — one card per table becomes one entry in the
  vector store.

Designing your metadata to be *LLM-consumable from the start* — self-contained,
Markdown, one concept per chunk — is a real architectural choice. `docs/01` expands.

---

## 6. Phase 2 — RAG (retrieval-augmented generation)

**File:** `src/lga/rag.py` · **Commands:** `uv run lga index`, `uv run lga ask "..."`

### The pipeline

```
catalog cards ──embed──▶ vectors ──▶ FAISS index          (build once, per lga index)

question ──embed──▶ vector ──search FAISS──▶ top-k cards ──▶ Claude ──▶ grounded answer
```

### Concepts, in order

**Token / context window.** The model reads and writes **tokens** (~¾ of a word). The
**context window** is the maximum tokens per call (prompt + response). You pay per
token. RAG exists to keep the prompt small and relevant.

**Embedding.** A function: text → a fixed-length vector of floats (here, 384
dimensions) such that texts with similar *meaning* land near each other in that space.
"Which columns hold personal data?" and "PII fields" produce nearby vectors even
though they share no words. Computed here by `fastembed` running `BAAI/bge-small-en-v1.5`
locally.

**Vector database.** A store that, given a query vector, returns the nearest stored
vectors fast. FAISS here (`IndexFlatIP` — exact inner-product search, fine for 10
cards). At scale: Pinecone, Weaviate, pgvector, Databricks Vector Search, Snowflake
Cortex Search. The interface is always the same: `upsert(id, vector, metadata)` +
`query(vector, k)`.

**Similarity metric.** We L2-normalise every vector, so **inner product = cosine
similarity** — the cosine of the angle between two vectors, 1.0 = identical direction.
The `[retrieved: bronze_customers (0.77), ...]` line in the output is these scores.

**Chunking.** Splitting the corpus into retrievable units. Here it's trivial — one
card per table. For prose documents you'd split by heading or paragraph, with overlap,
sized to a few hundred tokens.

**Retrieval + grounding.** `retrieve(query, k)` returns the top-k cards. `answer()`
puts *only those cards* in the prompt with a system instruction:

> "Answer ONLY from the catalog cards provided. Cite the table name(s). If the answer
> is not in the cards, say so — never invent columns, types, or statistics."

This is the **RAG contract**. It converts a *hallucination risk* (model makes up a
column) into a *retrieval metric* (did we fetch the right card?) — a failure you can
measure and fix. In the live run the retriever missed `silver_customers` at `k=3`;
the model correctly said "not in the cards" instead of guessing. That's the contract
working.

**Context management** is the umbrella term for all of this: deciding what goes in the
window, in what order, within budget. Retrieval is one lever; summarising history,
dropping stale turns, and re-ranking are others.

**Re-indexing.** The cards are generated *from live metadata*, so the knowledge base
never drifts from the platform — as long as you re-run `lga index` after each pipeline
change. In production that's a CI step after every dbt run.

`docs/02` covers hybrid search (keyword + vector), re-ranking, and eval.

---

## 7. Phase 3 — the ReAct agent

**File:** `src/lga/agent.py` · **Command:** `uv run lga agent "<task>"`

### Tool calling

You pass the model a list of tool specs — name, description, JSON-Schema for the
arguments (`tools.py::anthropic_tool_specs()`). The model, instead of answering, can
emit a **`tool_use`** block: `{name: "run_sql", input: {sql: "SELECT ..."}}`. Your
code runs the real function and returns a **`tool_result`**. The model reads the result
and continues. The model never touches your database — it only *asks* your code to.

### ReAct = Reason + Act

The **ReAct** pattern interleaves the model's reasoning ("I should check the null rate
of `risk_rating`") with actions (call `profile_column`), feeding each observation back.
With the Anthropic API the loop is just:

```python
while response.stop_reason == "tool_use":
    run the requested tools
    append their results to messages
    response = client.messages.create(..., messages=messages)
```

`agent.py` is that loop in ~50 lines, with a `MAX_STEPS` cap and every tool call
printed as a panel — that trace *is* your audit log.

### The tools (all read-only except the last)

| Tool | Does | Guardrail |
|---|---|---|
| `list_tables` | catalog overview | — |
| `search_catalog` | the RAG retriever, as a tool | — |
| `run_sql` | one read-only statement on the lakehouse | regex-gated to `SELECT/WITH/EXPLAIN/…`, single statement, 200-row cap, DB opened `read_only`; **intentionally still returns raw PII** — the DQ-audit use case needs real values (§10, §11, docs/07) |
| `profile_column` | full column profile | **PII columns masked at the source** (`catalog.py::_mask`) — a name/email preview, not the value |
| `read_contract` | the approved contract for a table | — |
| `write_artifact` | save a deliverable to `artifacts/` | extension whitelist, filename sandboxed |

**One registry, defined once** (`tools.py::TOOLS`), consumed two ways: by the
in-process loop here, and by the MCP server in Phase 4.

### Prompt engineering

The **system prompt** sets the persona and the working method: *"You are a Senior Data
Governance Architect… investigate with tools before concluding… quantify findings…
call out KYC/AML/GDPR relevance… finish with an executive summary."* Small wording
changes here measurably change behaviour — that's "prompt engineering". The task
message is the specific job ("Audit `bronze_customers` and write a DQ ruleset").

### Agent memory

Here, memory = the growing `messages` list (the full transcript is the working
memory). That's enough for a single bounded task. For longer-running agents you'd add:
- **episodic memory** — a summary of what happened, so old turns can be dropped;
- **a scratchpad** — an artifact the agent reads and writes across steps;
- **long-term memory** — a vector store of past runs, retrieved when relevant.

### How ReAct relates to the other patterns in the JD

| Pattern | Idea | Relationship |
|---|---|---|
| **ReAct** | reason ↔ act ↔ observe, linearly | what this project uses |
| **Tree of Thought (ToT)** | branch several reasoning paths, evaluate, keep the best | ReAct with search over reasoning steps; use when one wrong turn is costly |
| **Reflexion** | after a failure, write a self-critique into memory and retry | a feedback loop bolted onto ReAct |
| **AutoGPT-style** | give a goal, let the agent generate and pursue its own sub-tasks with little supervision | ReAct + self-directed task decomposition; powerful, less predictable, weaker guardrails |

`docs/03` has worked examples and where each is appropriate.

---

## 8. Phase 4 — MCP and Agent-to-Agent

**Files:** `src/lga/mcp_server.py`, `src/lga/a2a.py`
**Commands:** `uv run python -m lga.mcp_server`, `uv run lga review <table>`

### Model Context Protocol (MCP)

MCP is an **open standard for connecting LLM applications to tools and data**. Think
"USB-C for AI tools": the model/host on one side, your capabilities on the other, a
standard plug between them.

- An **MCP server** exposes *tools* (functions), *resources* (readable data), and
  *prompts* (templates).
- An **MCP client / host** (Claude Desktop, an IDE, Claude Code, an orchestrator)
  discovers and calls them.
- **Transport** is stdio (a subprocess, used here) or HTTP/SSE (a service).

`mcp_server.py` wraps the *same* `tools.py` registry — no logic duplicated. So the
identical governed tool surface is available to the in-process agent *and* to any MCP
client. The repo's `.mcp.json` registers it, so opening this folder in Claude Code
gives Claude your `run_sql` / `profile_column` / … tools directly.

**Why bother?** It decouples the tool implementation from the model and the harness.
One team owns and audits the tool boundary (with RBAC, PII masking, an audit log); every
consumer — Desktop, IDE, a CrewAI crew — goes through it. That boundary is where
governance for AI *lives*. This is the JD's *"Define Model Context Protocol (MCPs) to
chain reasoning, retrieval, and action models"* — the tools here are exactly retrieval
(`search_catalog`), reasoning inputs (`profile_column`), and action (`write_artifact`).

**One real bug this surfaced:** the MCP SDK treats an unexpected exception as a *crash*
and hides its text from the model. So a rejected `DELETE` showed up as a blank "Error
executing tool" with no reason. Fix (`mcp_server.py::_run`): translate the registry's
`ToolError` into the SDK's `ToolError` so the reason ("Only read-only queries…") crosses
the wire and the model can self-correct.

### Agent-to-Agent (A2A)

**A2A** is multiple specialised agents collaborating by passing typed messages, rather
than one agent doing everything in one context window. Here: two personas, one tool
surface.

```
Author agent  ──(draft contract YAML)──▶  Reviewer agent  ──(VERDICT + findings)──▶  Author
              ◀──────(revised draft)──────
```

- **Author** (`AUTHOR_SYSTEM`): "You are the Data Product Owner. Draft a data contract.
  Investigate with tools first."
- **Reviewer** (`REVIEWER_SYSTEM`): "You are an independent Governance Reviewer. Verify
  every claim against the lakehouse with tools — *do not trust the draft*. End with
  exactly `VERDICT: APPROVE` or `VERDICT: REVISE`."

Each agent keeps its **own** transcript/memory; only the artifact and the verdict cross
the boundary. That separation is the point — it's a **separation-of-duties control**,
the same reason a bank doesn't let the person who books a trade also approve it. It's a
concrete answer to *"drives adoption of responsible AI frameworks"*.

**Loop discipline** (`MAX_REVISIONS`): the loop is bounded, and it **always ends on a
review** — the last word on record is a verdict, never an unchecked draft. On exhaustion
it names the review file to escalate to a human. (The first version stopped after a
revision, leaving the final fix unverified — fixed.)

### Data contract

A **data contract** is a machine-readable agreement between a dataset's producer and its
consumers: schema, semantics (grain, primary key), quality rules as testable assertions,
lineage, freshness SLA, and consumer guarantees. It's how domains in a data mesh promise
things to each other without a meeting. Phase 4 *drafts* one; Phase 5 *manages changes*
to it.

`docs/04` covers transports, resources/prompts, and CrewAI/LangGraph equivalents.

---

## 9. Phase 5 — contract change management

**Files:** `src/lga/contract.py`, `data/evolve_lakehouse.py`
**Commands:** `uv run lga evolve`, `uv run lga contract-status <table>`, `uv run lga contract-revise <table>`

### The problem it fixes

Phase 4 drafts a contract from scratch every run — no memory, nothing cumulative.
Phase 5 makes the **approved contract a versioned artifact** in `contracts/<table>.yml`
and puts every change through a pipeline.

### The flow

```
                approved contract (contracts/silver_customers.yml, v2.0.0)
                                    │
   pipeline changes  ──▶  detect_drift()  ──▶  drift report
                                    │
                        author agent proposes a MINIMAL revision
                                    │
                             diff(old, new)  ──▶  structured, classified changes
                                    │
              reviewer agent verifies ONLY the diff  ──▶  VERDICT
                                    │
                     save(): bump version from classification, promote
```

### Concepts

**Drift** = the live table no longer matches its contract. `contract.detect_drift()`
(no LLM) checks:
- **schema** — added / removed / retyped columns (vs `information_schema`);
- **nullability** — a column declared `NOT NULL` that now has nulls;
- **enum expansion** — a value in the column that isn't in the contract's enum rule;
- **null-rate** — a column's null rate moved outside the declared tolerance;
- **rules** — it **re-runs every quality-rule assertion**; each is written to return 0
  when the rule holds, so non-zero = a rule that's now failing.

`lga contract-status` prints this and **exits 1 on any drift** — the CI hook: block the
merge until the contract is revised or the change reverted.

**Semantic diff.** `contract.diff(old, new)` compares the two contracts *structurally*
(schema by column name, rules by rule name) and classifies each change:

| Classification | Meaning | Examples |
|---|---|---|
| **additive** | backward-compatible; consumers unaffected | new nullable column; new quality rule; `NOT NULL` → nullable |
| **breaking** | consumers must change | removed column; type change; nullable → `NOT NULL`; PII flag flip; removed or *loosened* quality rule |
| **metadata** | documentation only | description, owner, domain text |

**Semantic versioning (semver).** `MAJOR.MINOR.PATCH`. The bump level is **derived from
the classification, not chosen by a human**: any breaking change → **major**; only
additive → minor; only metadata → patch. `contract.save()` computes it from the diff
summary and owns `version` + `change_log` entirely — the author agent is explicitly
told *not* to touch them.

> **Design bug caught in the first live run:** the author set its own version *and*
> `save()` bumped again → double bump (v2 → v4). Fix: versioning is a pure function of
> the diff; the agents never touch it. This is a good interview point — *"version bumps
> are a function of the change classification, not a judgement call."*

### "Review the diff, not the document"

Phase 4's reviewer re-read the whole contract every round and its findings wandered
(a wrong statistic here, a missing clause there). Phase 5's reviewer gets a **bounded**
task: *N changes, each with a proposed classification — verify them against live data,
confirm the classification, check the drift is resolved.* Result: 7 tool calls instead
of 40+, reproducible, and a small enough disagreement surface that consensus is actually
reachable — which is the entire point of a contract.

### The live run

`lga evolve` adds two columns and relabels some `kyc_status` values to `EXPIRED`.
Drift detector flags: 2 `SCHEMA_ADDED`, 1 `ENUM_EXPANDED`, 1 `RULE_FAILING`
(`kyc_status_enum` returned 25). Author profiled all three columns, proposed: two new
column entries, the enum rule widened, a new `customer_segment_enum` rule,
`profile_expectations` for the new columns. Diff classified it **2 breaking + 1
additive** (`customer_segment` as `NOT NULL`; `kyc_status_enum` *loosened*). Reviewer
verified each against live data, **APPROVE on the first round**, promoted **v2.0.0 →
v3.0.0** (major).

`docs/06` has the full detail and the known gaps (no SQL-parsing lineage, no
notification side-effect).

---

## 10. What the agents got right, and wrong — the four review runs

The `silver_customers` contract was reviewed four times as the underlying pipeline and
data improved. The convergence curve is the story:

| Run | Pipeline state | Result | What moved |
|---|---|---|---|
| 1 | age filter bug present | never converged | reviewer kept finding the same silent row drop |
| 2 | age filter fixed | APPROVE after **3 reviews** | reviewer caught the author misstating its own evidence ("14 of 15" → actually 15 of 15) |
| 3 | `silver_customers_rejected` quarantine table added | APPROVE after **1 revision** | the "silent undocumented filter" finding *vanished* — reviewer now verifies a reconciliation identity |
| 4 | `DUPLICATE_ROW` reason code added | APPROVE **first review, 0 revisions** | row conservation `309 = 285 + 24` holds exactly |

### Things the agents got right

- **Found emergent defects nobody planted.** One email shared by three `customer_id`s;
  a customer born *after* their account opened; a customer under 18 at onboarding. The
  agent found these only because it *queried the data* rather than reading the catalog
  card.
- **Caught a real bug in a pipeline the author (me) wrote.** The silver age filter used
  `current_date - 18 YEAR` when the KYC rule is `created_at - 18 YEAR`. Someone born in
  2004 and onboarded in 2020 was 16 then, is 22 now, and slipped through. The reviewer
  agent flagged it; it's now fixed with a comment pointing back at the review.
- **The reviewer caught the author's arithmetic — twice.** Run 1: "all 9 dropped rows
  have irregular `kyc_status`" (only 6 did). Run 2: "14 of 15" (15 of 15). Both times
  the reviewer re-queried and corrected it. **This is the strongest argument for the
  author/reviewer pattern: an LLM's stated number is a claim until a tool re-derives it.**

### Things the agents got wrong

- **Anchoring.** In runs 1–2 both agents fixated on a `kyc_status` explanation for the
  dropped rows. The real cause was one query away (all 9 had implausible dates of birth
  → the age filter). The author formed the hypothesis early, the reviewer *disproved the
  evidence for it* but adopted the same frame, and neither pivoted to "check every
  column of the dropped rows". Mitigations, as architecture choices: prompt the
  investigator to enumerate candidate causes *before* querying (lightweight ToT); give
  the reviewer an explicit "propose an alternative root cause" instruction; add a
  `diff_rows(a, b, key)` tool so the right query is cheap; keep a human in the loop for
  anything marked `OPEN_CRITICAL`.
- **No cross-run memory.** Each `lga review` regenerates the contract from zero, so a
  GDPR clause added in run 3 silently disappeared in run 4, and whether a concern
  resurfaces depends on reviewer sampling. Phase 5 fixes this for *revisions* (baseline
  + diff); a full fix would load the prior contract as the author's starting point.

### The pattern worth internalising

**Quarantine, don't filter.** Runs 1–2 kept flagging rows vanishing in a `SEMI JOIN`.
The fix was `silver_customers_rejected` / `silver_accounts_rejected` — tables that
capture every dropped row with a **reason code** (`DUPLICATE_ROW`, `MINOR_AT_ONBOARDING`,
`ORPHAN_CUSTOMER`, …) and a testable invariant: `count(bronze) = count(silver) +
count(rejected)`. This converts an *un-auditable pipeline side-effect* into a *declared,
reconcilable contract term*. The reviewer stopped arguing about whether rows were lost
and started verifying arithmetic. First number it surfaced: **£4.8M** of account
balances sitting in quarantine — invisible before.

`docs/05` is the full debrief.

---

## 11. Cross-cutting concerns

### Responsible AI (JD: "drives adoption of responsible AI frameworks")

| Risk | Mitigation in this project |
|---|---|
| Hallucinated columns / stats | Grounding contract in RAG; tools return real data; reviewer re-derives every number |
| Unsafe actions | Read-only SQL tool (regex gate, single statement, row cap, `read_only` connection); write tool sandboxed to `artifacts/` |
| PII in prompts | Flagged at the catalog layer AND enforced: `profile_column`/catalog cards mask PII at the source (`catalog.py::_mask`) — found missing, fixed, see docs/07. `run_sql` stays unmasked on purpose (the DQ-audit use case needs real values); no row-level policy anywhere yet. |
| Unchecked output | Every deliverable is a **draft**; the reviewer agent is a control; humans approve promotion to `contracts/` |
| Non-determinism | Every tool call logged with its result; models pinned via `ANTHROPIC_MODEL`; Phase 5 versioning is deterministic |
| One agent, no oversight | Separation of duties: author ≠ reviewer, different prompts, different memory |

### Security & privacy

- Secrets in `.env` (git-ignored), never in code or prompts.
- The SQL guardrail is defence-in-depth: even a prompt-injected "DROP TABLE" is rejected
  by the regex *and* the read-only connection.
- MCP gives you *one* place to enforce RBAC, masking, and audit for every AI consumer.

### Observability & cost

- The `rich` panel trace is the human-readable audit log; a production version persists
  `(run_id, step, tool, args, result_hash, latency)` + the final artifact hash.
- Opus 5 with adaptive thinking: an agent audit ≈ 5 model calls; an A2A review run ≈
  25–40. Switch to `claude-sonnet-5` for iteration.

### MLOps (JD: "MLOps pipelines")

The pieces that would be CI/CD jobs:
- `lga build-data` → the pipeline (dbt / DLT / Spark job).
- `lga index` → re-embed the catalog after every pipeline run.
- `lga contract-status` → a required check; **exit 1 blocks the merge**.
- `lga contract-revise` → run on drift; a human approves the promotion PR.
- Model + prompt versions pinned; eval suite on the RAG and the diff engine (`pytest`).

---

## 12. Mapping to a real cloud platform

| This repo | Databricks | Snowflake | AWS / Azure / GCP |
|---|---|---|---|
| `bronze/silver/gold` DuckDB tables | Delta tables in 3 schemas; Unity Catalog | 3 databases/schemas; Iceberg or native | Lake Formation / Synapse / BigQuery datasets |
| `catalog.py` profiling | Unity Catalog system tables; Lakehouse Monitoring | `ACCOUNT_USAGE`, `INFORMATION_SCHEMA`; Horizon | Glue Data Catalog / Purview / Dataplex |
| `LINEAGE` dict | Unity Catalog lineage API; column-level automatic | `OBJECT_DEPENDENCIES`; Horizon lineage | OpenLineage / Purview / Dataplex lineage |
| Quality rules | Lakehouse Monitoring, DLT expectations, Great Expectations | Data Metric Functions; Great Expectations / Soda | Deequ / Glue DQ / Dataplex DQ |
| `fastembed` | Foundation Model APIs (BGE, GTE) | Cortex `EMBED_TEXT` | Bedrock Titan / Azure OpenAI / Vertex embeddings |
| FAISS | Databricks Vector Search | Cortex Search | OpenSearch k-NN / AI Search / Vertex Vector Search |
| Claude via `anthropic` (called directly — outside any perimeter, see docs/07) | **Model Serving** / Foundation Model APIs run inference *inside* the security perimeter | Cortex `COMPLETE` (Claude) | Bedrock / Azure AI Foundry / Vertex |
| `agent.py` ReAct loop | **Agent Bricks** / Mosaic AI Agent Framework | Cortex Agents | Bedrock Agents / Azure AI Agent Service / Vertex Agent Builder |
| in-process agent memory (`messages` list, ephemeral) | **Lakebase** — managed Postgres, transactional, shared across agents | Snowflake tables + Cortex | Bedrock Agents memory / DynamoDB |
| `mcp_server.py` + `.mcp.json` (one tool surface, no central policy layer) | **Unity AI Gateway** — platform-wide ALLOW/DENY/ASK before execution; **Omnigent** routes coding agents (incl. Claude Code) through it | Snowflake ships an MCP server | Bedrock AgentCore Gateway (MCP) |
| `a2a.py` | LangGraph / CrewAI on Databricks | — | Bedrock multi-agent collaboration; Google A2A |
| `contracts/` + drift | Unity Catalog + DLT expectations + CI | Horizon + DMFs + CI | data contract tooling + CI |
| `rich` panel trace (printed, not persisted) | **MLflow 3** — full request/tool-call tracing, auditable | Cortex observability | CloudWatch/App Insights/Cloud Logging + eval tooling |
| hand-written `DOMAIN_OWNERS` dict | **Genie** / Genie Ontology — auto-derived business context | Horizon semantic views | Dataplex business glossary |

The interview answer to *"we use Snowflake and Databricks, not DuckDB"*: **"The
patterns are identical — swap the connection string and push the profiling SQL down to
the warehouse. Here's the mapping table."** For the deeper argument behind several of
these rows — why Databricks says agents belong *inside* the platform, and the real gap
it exposed in this project's own tool layer — see
[docs/07-data-native-agents.md](07-data-native-agents.md).

---

## 13. Glossary

**A2A (Agent-to-Agent)** — multiple specialised agents collaborating via typed
messages instead of one agent in one context. Here: author ⇄ reviewer.

**Agent** — an LLM in a loop with tools that decides when to stop.

**Agent memory** — what an agent carries across steps/runs: the transcript (working),
a rolling summary (episodic), a vector store of past runs (long-term).

**Anthropic Messages API** — the API used here: `system`, `messages[]`, `tools[]`,
returns content blocks and a `stop_reason`.

**Bronze / Silver / Gold** — medallion layers: raw / cleaned-conformed / business
aggregates.

**Chunking** — splitting a corpus into retrievable units for RAG.

**Context window** — the max tokens (prompt + response) per model call.

**Cosine similarity** — cosine of the angle between two vectors; the closeness metric
for embeddings. Equals inner product when vectors are normalised.

**Data catalog** — the searchable index of what data exists, with types, owners,
lineage, sensitivity.

**Data contract** — a machine-readable producer↔consumer agreement: schema, semantics,
quality rules, lineage, guarantees.

**Data mesh** — domains own their data end-to-end and publish data products;
federated governance instead of a central team.

**Data product** — a dataset treated as a product: owner, SLA, docs, quality
guarantees, discoverable. (`gold_customer_360`.)

**Drift** — the live table no longer matches its contract (schema, profile, or a
failing rule).

**Embedding** — text → fixed-length vector such that similar meaning → nearby vectors.

**FAISS** — Facebook AI Similarity Search; a file-based vector index.

**Fine-tuning** — retraining model weights on your data. Not used here (metadata
changes too often).

**Grounding** — constraining the model to answer only from provided context, and to
say "not found" otherwise.

**Hallucination** — the model producing fluent, plausible, false output (a column
that doesn't exist).

**KYC / AML** — Know Your Customer / Anti-Money-Laundering: BFSI regulatory controls
the agents reason about (verified status, risk rating, large transactions).

**Lineage** — the derivation graph of tables/columns. Table-level here; column-level
needs SQL parsing.

**LLM** — large language model; text in → text out, no memory or I/O of its own.

**MCP (Model Context Protocol)** — open standard for exposing tools/resources/prompts
to LLM apps; stdio or HTTP transport. "USB-C for AI tools."

**Medallion architecture** — the bronze→silver→gold refinement pattern.

**MLOps** — CI/CD, versioning, monitoring, and evaluation for ML/LLM systems.

**PII** — personally identifiable information; drives masking and access control.

**Profiling** — measuring a column's actual contents: nulls, cardinality, range,
distribution, samples.

**Prompt engineering** — deliberately wording the system/task prompts to shape
behaviour.

**Quarantine table** — captures every row a pipeline drops, with a reason code, so
the drop is auditable and the row counts reconcile.

**RAG (retrieval-augmented generation)** — retrieve the relevant slice of a corpus,
put it in the prompt, generate a grounded answer.

**ReAct** — Reason + Act: interleave model reasoning with tool calls, feeding
observations back.

**Reflexion** — after a failure, the agent writes a self-critique to memory and
retries.

**Responsible AI** — the framework of controls (grounding, guardrails, separation of
duties, human approval, audit) that makes an AI system safe to deploy.

**Semantic versioning (semver)** — `MAJOR.MINOR.PATCH`; here the bump is derived from
the change classification.

**Separation of duties** — the author cannot approve their own work; a distinct
reviewer (agent or human) must.

**System prompt** — the standing instruction that sets an agent's persona and method.

**Token** — the unit an LLM reads/writes (~¾ word); the unit you're billed in.

**Tool calling (function calling)** — the model emits a structured request to run a
named function; your code runs it and returns the result.

**Tree of Thought (ToT)** — branch multiple reasoning paths, evaluate, keep the best;
ReAct with search.

**Vector database** — stores vectors and returns nearest neighbours to a query vector
fast (FAISS, Pinecone, Weaviate, pgvector).

---

## 14. Interview question bank

**"How would you apply GenAI to data governance?"**
RAG over live catalog metadata for discovery; a ReAct agent that generates
evidence-backed DQ rules and data contracts by querying the warehouse; an independent
reviewer agent as a separation-of-duties control; deterministic contract change
management with drift detection. Humans approve; the AI drafts and checks.

**"RAG vs fine-tuning vs long context for this?"**
RAG. Metadata changes daily; fine-tuning would be stale immediately and long context
is expensive and lossy. Re-index in CI after each pipeline run.

**"What are the failure modes and how do you mitigate them?"**
Hallucination → grounding contract + tools that return real data + a reviewer that
re-derives numbers. Unsafe actions → read-only tool surface, sandboxed writes.
PII leakage → mask at the tool boundary, at the one place every consumer reads from —
found a real instance of this missing in `profile_column`, fixed it in `catalog.py`,
and *deliberately* left the raw-access tool (`run_sql`) unmasked because the DQ-audit
use case genuinely needs real values (docs/07). Non-determinism → log every tool call,
pin models, make versioning deterministic. Anchoring → enumerate hypotheses before
querying, HITL on critical findings.

**"Databricks argues agents must move to the data, not the other way round — thoughts?"**
Agreed, and this project is a working demonstration of the exact failure it describes:
by design, the LLM calls sit outside any perimeter (straight to Anthropic's API), and
writing up the mapping surfaced a real bug — a catalog `is_pii` flag that was metadata
only, never enforced, so `profile_column` leaked raw names and emails. Fixed at the one
place every consumer reads from. The deeper point stands, though: that's an
application-level patch, not platform enforcement — a second tool reading the same
DuckDB file bypasses it entirely, which is exactly why Unity Catalog enforces ACLs in
the engine instead of in each caller. Full mapping in docs/07.

**"Why MCP instead of just calling functions?"**
It decouples the tool implementation from the model and the harness. One governed,
audited tool boundary (RBAC, masking, logging) serves every consumer — Claude Desktop,
IDEs, orchestrators. That boundary is where AI governance is enforced.

**"What's the value of the multi-agent setup?"**
Separation of duties. The author agent optimises for a complete contract; the reviewer
optimises for catching errors. In our runs the reviewer caught the author misstating
its own evidence twice, and caught a real pipeline bug. One agent doing both would have
shipped both.

**"How does contract change management work?"**
Approved contracts are versioned YAML. On any pipeline change, a no-LLM drift detector
re-runs every quality assertion and diffs the schema; if there's drift, an author agent
proposes a minimal revision, a structured diff classifies each change additive/breaking,
a reviewer verifies only the diff, and the version bump is derived from the
classification — breaking changes can't ship as a patch. `contract-status` exits
non-zero to block the CI merge.

**"This is DuckDB, not our stack."**
The patterns are identical. `information_schema` queries, window functions, and
`ANTI JOIN` run unchanged on Snowflake and Spark SQL; push profiling down to the
warehouse. The embedding model, vector store, and LLM are each one swap. [Show the
mapping table in §12.]

---

## 15. Command reference

```bash
# Phase 0 — data
uv run lga build-data                 # (re)build the medallion lakehouse in DuckDB

# Phase 1 — catalog
uv run lga catalog                    # print catalog cards (profile of every table)

# Phase 2 — RAG
uv run lga index                      # embed catalog cards → FAISS
uv run lga ask "which tables hold PII and who owns them?"

# Phase 3 — ReAct agent
uv run lga agent "Audit bronze_customers for data-quality issues and write a DQ ruleset artifact."

# Phase 4 — MCP + A2A
uv run python -m lga.mcp_server        # serve the tools over MCP (stdio)
uv run lga review silver_customers     # author drafts a contract, reviewer verifies it

# Phase 5 — contract change management
uv run lga evolve                      # simulate a pipeline schema change
uv run lga contract-status silver_customers   # drift vs approved contract (exit 1 on drift)
uv run lga contract-revise silver_customers   # drift → propose → diff → review → promote

# tests (offline, no API)
uv run pytest -q

# reset Phase 5 demo
git checkout contracts/ && uv run lga build-data
```

Outputs land in `artifacts/`. Approved contracts live in `contracts/`.

---

## 16. What's deliberately not built (and what you'd add)

| Gap | What a real platform does |
|---|---|
| Column-level lineage | Parse transformation SQL with `sqlglot`, or read dbt `manifest.json` / OpenLineage |
| Streaming | The JD mentions streaming systems; this is all batch. Add Kafka → a bronze stream + windowed silver. |
| Hybrid retrieval | Vector + BM25 keyword + a re-ranker; the run showed pure-vector missing an obvious table |
| Agent memory across runs | Persist episodic summaries; load the prior contract as the author's starting point |
| Notification side-effects | On a breaking change, open a ticket / post to the domain channel / start the 30-day clock |
| Real drift source | A scheduled profiler writing snapshots to a table, not a toy `evolve` script |
| Eval harness | Golden Q&A for the RAG; labelled drift cases for the diff engine; track regression |
| Cost/latency budgets | Per-run token accounting; a cheaper model for retrieval synthesis |
| **Row-level policy (ABAC)** | Named by *both* Databricks articles in docs/07 independently: `run_sql` and the FAISS index enforce no row-level policy at all today. A real platform applies the same ABAC to SQL and vector search — "can this caller see this row" answered once, enforced everywhere. |
| Named data-product ownership | `contracts/*.yml`'s `owner` is a team alias, not an accountable individual with a "you fix the catalog" workflow (docs/07, Part 2) |
| Persisted certification history | `contract-status` recomputes drift live; it doesn't store a queryable scorecard over time the way Unity Catalog's AI Certification does |

---

## 17. Repo map

```
data/
  build_lakehouse.py      seeded BFSI medallion dataset (deliberate bronze defects)
  evolve_lakehouse.py     Phase 5 — simulate a schema change to trigger drift
contracts/
  silver_customers.yml    approved data contract — versioned source of truth
src/lga/
  config.py               paths, model id, .env loading
  catalog.py              Phase 1 — profiling + "catalog cards" + declared lineage
  rag.py                  Phase 2 — fastembed + FAISS + grounded Q&A
  tools.py                the governance tool registry + guardrails (one definition)
  agent.py                Phase 3 — the ReAct loop
  a2a.py                  Phase 4 — author ⇄ reviewer; Phase 5 — drift-triggered revision
  mcp_server.py           Phase 4 — the same tools over MCP (stdio)
  contract.py             Phase 5 — drift detection + structured contract diff (no LLM)
  cli.py                  `lga` subcommands
tests/
  test_contract.py        offline tests for the diff engine
docs/
  00-architecture.md      the picture + the cloud-platform mapping
  01..06                  one concept note per phase
  05-first-run-debrief.md what the agents found / missed across four review runs
  GUIDE.md                this document
.mcp.json                 registers the MCP server for Claude Code in this folder
```

---

*Built as a training exercise. Every AI capability in the JD is exercised on a real
governance problem, locally, for the price of a few Claude API calls.*
