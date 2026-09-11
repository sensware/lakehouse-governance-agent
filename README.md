# Lakehouse Governance Agent

A learning project that applies **GenAI to the data value chain**: a medallion-architecture
lakehouse (DuckDB) plus a governance agent that catalogs it, answers questions via RAG,
audits data quality with a ReAct tool loop, drafts data contracts, and peer-reviews them
through an agent-to-agent hand-off — with the tools also served over MCP.

Built to implement: RAG · vector DBs · prompt engineering · tool calling · ReAct · agent memory · MCP · A2A ·
responsible-AI guardrails — on top of medallion / data products / lineage / quality 

## Quick start

```bash
brew install uv                                   # once
cp .env.example .env                              # add ANTHROPIC_API_KEY
uv sync

uv run lga build-data                             # Phase 0  bronze/silver/gold in DuckDB
uv run lga catalog                                # Phase 1  profile + catalog cards
uv run lga index                                  # Phase 2  embed cards → FAISS
uv run lga ask "Which tables contain PII?"        # Phase 2  RAG answer, cites tables
uv run lga agent "Audit bronze_customers for data-quality issues and write a DQ ruleset."
                                                  # Phase 3  ReAct agent, watch it work
uv run lga review silver_customers                # Phase 4  author ⇄ reviewer contract loop
uv run python -m lga.mcp_server                   # Phase 4  same tools over MCP (stdio)

uv run lga evolve                                 # Phase 5  simulate a pipeline schema change
uv run lga contract-status silver_customers       # Phase 5  drift vs approved contract (no LLM; exit 1 on drift)
uv run lga contract-revise silver_customers       # Phase 5  drift → propose → diff → review → promote
```

Outputs land in `artifacts/`.

## Read along

| Doc | Concept |
|---|---|
| **[docs/GUIDE.md](docs/GUIDE.md)** | **Start here — the complete walkthrough: every phase, every concept, glossary, interview Q&A** |
| [docs/00-architecture.md](docs/00-architecture.md) | The whole picture, and the map to Databricks / Snowflake / JD bullets |
| [docs/01-catalog-and-profiling.md](docs/01-catalog-and-profiling.md) | Metadata + profiling, designed for LLM consumption |
| [docs/02-rag.md](docs/02-rag.md) | RAG, embeddings, FAISS, grounding, retrieval telemetry |
| [docs/03-react-agent.md](docs/03-react-agent.md) | ReAct loop, tool design, prompt engineering, memory, ToT/AutoGPT |
| [docs/04-mcp-and-a2a.md](docs/04-mcp-and-a2a.md) | MCP as a governed tool boundary; author/reviewer A2A protocol |
| [docs/05-first-run-debrief.md](docs/05-first-run-debrief.md) | What the agents found, what they missed (anchoring), a real pipeline bug they surfaced |
| [docs/06-contract-change-management.md](docs/06-contract-change-management.md) | Contract drift detection, structured diffs, deterministic version bumps, review-the-diff-not-the-doc |
| [docs/07-data-native-agents.md](docs/07-data-native-agents.md) | Two Databricks articles mapped line-by-line to this repo — and a real PII-masking bug they surfaced and fixed |

## Layout

```
data/build_lakehouse.py   seeded BFSI dataset with deliberate bronze defects
src/lga/catalog.py        Phase 1 — profiling + catalog cards
src/lga/rag.py            Phase 2 — embeddings, FAISS, grounded Q&A
src/lga/tools.py          governance tools, one registry (guardrails live here)
src/lga/agent.py          Phase 3 — ReAct loop over the tools
src/lga/a2a.py            Phase 4 — author ⇄ reviewer agents; Phase 5 — drift-triggered revision
src/lga/mcp_server.py     Phase 4 — the same tools over MCP
src/lga/contract.py       Phase 5 — drift detection + structured contract diff (no LLM)
data/evolve_lakehouse.py  Phase 5 — simulate a schema change to trigger drift
contracts/                approved data contracts, versioned (source of truth)
tests/test_contract.py    offline tests for the diff engine
docs/                     one concept note per phase
```

## Use the tools from Claude Code

The repo ships a project-scoped [`.mcp.json`](.mcp.json). Open this folder in Claude Code
(desktop app or CLI) and approve the `lakehouse-governance` server when prompted — then ask
things like *"use the lakehouse tools to find accounts with no matching customer"*.

Equivalent one-liner if you use the standalone CLI (`claude` on your PATH):
```bash
claude mcp add lakehouse-governance -- uv run python -m lga.mcp_server
```
