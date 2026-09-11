"""Phase 3 — a ReAct governance agent.

ReAct = interleave **Reason** (model thinks) and **Act** (model calls a tool),
feeding each observation back until the model stops calling tools and answers.
With the Anthropic API this is just: loop while `stop_reason == "tool_use"`.

Agent memory here is the running `messages` list (full transcript = working
memory). For long tasks you'd add: summarised episodic memory, a scratchpad
artifact, or a vector store of past runs — see docs/04.

Example tasks:
  uv run lga agent "Audit bronze_customers for data-quality issues and write a DQ ruleset artifact."
  uv run lga agent "Compare row counts bronze vs silver for the customer domain and explain the drop."
"""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel

from .config import ANTHROPIC_MODEL, require_api_key
from .tools import ToolError, anthropic_tool_specs, call_tool

console = Console()

SYSTEM = """You are a Senior Data Governance Architect embedded in a bank's lakehouse platform.
The lakehouse uses medallion architecture (bronze = raw landing, silver = cleaned/conformed,
gold = business aggregates / data products).

Work like an architect:
  * Investigate with tools before concluding. Prefer run_sql for evidence; profile_column for
    distributions; search_catalog for context; list_tables to orient.
  * Quantify findings (counts, percentages), reference specific tables/columns.
  * When asked for a deliverable (DQ ruleset, data contract, lineage note), write it with
    write_artifact as clean Markdown/YAML a platform team could adopt.
  * Be concise. Call out regulatory relevance (KYC, AML, GDPR/PII) where it applies.

Finish with a short executive summary of what you found and what you produced."""

MAX_STEPS = 16


def run_agent(task: str, *, system: str = SYSTEM, verbose: bool = True) -> str:
    """Drive a ReAct loop until the model answers without calling a tool."""
    import anthropic

    require_api_key()
    client = anthropic.Anthropic()
    tools = anthropic_tool_specs()
    messages: list[dict] = [{"role": "user", "content": task}]

    final_text = ""
    for step in range(1, MAX_STEPS + 1):
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=8000,
            system=system,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        text = "".join(b.text for b in resp.content if b.type == "text")
        if text:
            final_text = text
            if verbose:
                console.print(Panel(text, title=f"step {step} · reason", border_style="cyan"))

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            break

        results = []
        for tu in tool_uses:
            try:
                payload = json.dumps(call_tool(tu.name, tu.input or {}), default=str)
                is_error = False
            except Exception as e:  # noqa: BLE001 — every failure goes back as an observation
                payload = f"{type(e).__name__}: {e}"
                is_error = True
            if verbose:
                console.print(
                    Panel(
                        f"[bold]{tu.name}[/]({json.dumps(tu.input)})\n\n{payload[:1200]}",
                        title=f"step {step} · act",
                        border_style="red" if is_error else "green",
                    )
                )
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": payload, "is_error": is_error}
            )
        messages.append({"role": "user", "content": results})
    else:
        console.print("[yellow]hit MAX_STEPS without a final answer[/]")

    if verbose:
        console.print(Panel(final_text, title="summary", border_style="magenta"))
    return final_text
