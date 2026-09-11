# 03 — ReAct agent with tool calling

**Files:** `src/lga/tools.py`, `src/lga/agent.py`
**Run:**
```bash
uv run lga agent "Audit bronze_customers for data-quality issues and write a DQ ruleset artifact."
uv run lga agent "Why does silver_transactions have fewer rows than bronze? Quantify each cause."
```

## ReAct in one loop

```python
messages = [user_task]
while True:
    resp = claude(messages, tools=TOOLS)         # Reason: model thinks, may emit tool_use
    messages.append(resp)                        #   (thinking + text + tool_use blocks)
    if no tool_use in resp: break                # Done: final answer
    for call in tool_uses:                       # Act: we execute the tools
        result = run(call)                       #   (errors go back as observations too)
    messages.append(tool_results)                # Observe: results become the next input
```

That's the entire pattern. "ReAct" (Reason + Act, Yao et al. 2022) just names the
interleaving. Frameworks (LangChain `AgentExecutor`, LangGraph, CrewAI) wrap this loop with
routing, retries, and state — useful, but the loop is the thing to understand.

## Tool design — the part that makes or breaks agents

| Principle | How `tools.py` does it |
|---|---|
| Few, orthogonal tools | orient (`list_tables`), read (`run_sql`), inspect (`profile_column`), recall (`search_catalog`), check (`read_contract`), act (`write_artifact`) |
| Descriptions are prompts | Each `description` tells the model *when* to use it, not just what it does |
| Typed inputs | JSON schema → the model produces valid args; add `strict: true` for guaranteed validation |
| Errors are observations | `ToolError` text is returned with `is_error=True`; the model self-corrects (e.g. fixes SQL) |
| Guardrails at the boundary | read-only connection, single statement, allow-listed verbs, row cap, artifact extension allow-list |
| **Role is a guardrail too** | every tool above also consults `policy.py`'s `Role` (allowed tables, row filters, PII unmask) before touching the lakehouse — the model can ask for anything, but what it *reaches* is bounded by who launched it, not by what it asks (Phase 6, docs/08) |
| Transport-agnostic | Same registry powers the in-process loop *and* the MCP server |

## Prompt engineering that matters here

- **Role + operating principles** in the system prompt ("investigate before concluding",
  "quantify", "reference table.column", "note regulatory relevance").
- **Deliverable format** stated up front (Markdown/YAML via `write_artifact`).
- **Stop condition** ("finish with an executive summary") so the loop terminates cleanly.
- Adaptive thinking is on by default on Opus 5; you don't need "think step by step".

## Memory strategies (JD: "agent memory strategies")

| Type | This project | Production |
|---|---|---|
| Working memory | the `messages` transcript | same, plus server-side compaction for long runs |
| Episodic | `artifacts/*.md` written by the agent | store run summaries in a vector DB; retrieve on similar tasks |
| Semantic | catalog cards via `search_catalog` | the RAG index *is* long-term memory over the platform |
| Procedural | system prompt | skills / playbooks loaded per task type |

## Reasoning styles (JD: "ReAct, Tree of Thought, AutoGPT-style")

- **ReAct** — what you just ran. Linear: think → act → observe.
- **Tree of Thought** — branch several candidate plans, score them, expand the best.
  Costly; useful for puzzles/planning, rarely for tool-driven data work.
- **AutoGPT-style** — fully autonomous goal decomposition with self-generated subtasks and
  long-term memory. High variance; modern practice bounds it with explicit plans, budgets
  (`MAX_STEPS`), and human checkpoints — which is what `a2a.py` does with a reviewer.

## Watch the run
Each cyan panel is a *reason* step, each green panel an *act* with the raw observation.
Notice the model writing SQL, hitting an error (red), and fixing it — that's the loop working.
