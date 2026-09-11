# 04 — MCP and Agent-to-Agent collaboration

**Files:** `src/lga/mcp_server.py`, `src/lga/a2a.py`
**Run:**
```bash
uv run lga review silver_customers          # author ⇄ reviewer loop
uv run python -m lga.mcp_server             # serve tools over MCP (stdio)
```

## Model Context Protocol

MCP standardises how a model-facing host (Claude Desktop, Claude Code, an orchestrator)
discovers and calls **tools, resources, and prompts** exposed by a server. Think "USB-C for
tools": the server owns the implementation and its guardrails; any client can plug in.

```
   host / agent  ── JSON-RPC over stdio or HTTP ──▶  MCP server  ──▶  lakehouse
   (reasoning)                                      (retrieval + action, governed)
```

`mcp_server.py` is ~40 lines because the tools already exist in `tools.py`. That separation
is the architectural point: **define the tool surface once, expose it through whichever
transport the consumer needs.**

### Register it with Claude Code and use it interactively
The repo's `.mcp.json` declares the server; Claude Code picks it up when you open the folder
(approve it once when prompted). With the standalone CLI you can also do:
```bash
claude mcp add lakehouse-governance -- uv run python -m lga.mcp_server
```
Then in a Claude Code session: *"Use the lakehouse tools to find orphan accounts."*
You've just chained an external reasoning model to your governed retrieval/action layer —
the JD's "define MCPs to chain reasoning, retrieval, and action models".

### Enterprise framing
- **Governance boundary.** RBAC, PII masking, audit logging, and rate limits live in the
  server, applied identically to every client.
- **Discoverability.** `list_tools` is the contract; version it like an API.
- **Vendors ship these.** Databricks, Snowflake, dbt, Atlan all expose MCP servers — your
  platform's tool surface becomes composable across agents.

## Agent-to-Agent (A2A)

`a2a.py` runs two agents with **different roles, separate memories, one shared tool surface**:

```
Author (Data Product Owner)          Reviewer (Architecture Board)
  investigate table ──▶ draft YAML ──▶ verify claims with tools
  revise ◀── VERDICT: REVISE + findings ◀── ┘
  done   ◀── VERDICT: APPROVE
```

What crosses the boundary is *typed and minimal*: the contract artifact and a parsed verdict.
Neither agent sees the other's transcript. That is the essence of an A2A protocol —
Google's A2A spec formalises the same idea with agent cards, tasks, and artifacts over HTTP.

### Why a reviewer agent is a responsible-AI control
- **Separation of duties**: the drafter cannot approve its own work.
- **Evidence-based**: the reviewer is instructed *not* to trust the draft and to re-verify
  with tools (PK uniqueness, PII flags, rule satisfaction).
- **Bounded**: `MAX_REVISIONS` stops infinite ping-pong, and the loop *always ends on a
  review* — the last word on record is a verdict, never an unchecked draft. On exhaustion it
  escalates to a human with the final review file.

### Orchestration frameworks (JD: LangChain, AutoGen, CrewAI)
| Framework | Equivalent of this file |
|---|---|
| CrewAI | two `Agent`s with roles, a sequential `Crew`, tools shared |
| AutoGen | `AssistantAgent` + `UserProxyAgent` conversation with a termination condition |
| LangGraph | a graph: author → reviewer → conditional edge on verdict → author / END |

The concepts transfer 1:1; the frameworks add persistence, streaming, and UI.

## Try
1. Run `lga review silver_customers`. Read `artifacts/silver_customers_contract.yml` and
   `artifacts/silver_customers_review_r1.md`. Did the reviewer catch anything real?
2. Set `MAX_REVISIONS = 0` and observe a single review with no revision — this is why loops
   need budgets, and why the budget should count *revisions*, not reviews.
3. Add a third agent: a **Compliance** persona that only checks GDPR/KYC items.
