"""Phase 4 — Agent-to-Agent (A2A) collaboration: author + reviewer.

Two specialised agents with different system prompts share the same tool surface
and hand work to each other through a typed message (here: the contract text plus
a structured verdict). That hand-off *is* the A2A protocol in miniature:

    Author agent  --(draft contract)-->  Reviewer agent  --(verdict, findings)-->  Author
                  <--(revised draft)--

The loop stops when the reviewer approves or we hit MAX_ROUNDS. Each side keeps
its own memory (its own transcript); only the artifact and the verdict cross the
boundary — that's the point of a protocol over a shared context window.

  uv run lga review silver_customers
"""

from __future__ import annotations

import json
import re

from rich.console import Console
from rich.panel import Panel

from .agent import run_agent
from .config import ARTIFACTS_DIR

console = Console()
MAX_ROUNDS = 2

AUTHOR_SYSTEM = """You are the Data Product Owner for a table in a bank's lakehouse.
Draft a **data contract** for the table you're given. Investigate it with tools first.
The contract must cover: owner/domain, schema (column, type, nullability, PII flag,
description), semantics (grain, primary key, freshness/SLA), quality rules (as
testable assertions), lineage (upstreams), and consumer guarantees.
Write it as YAML with write_artifact to  <table>_contract.yml . Reply with a 3-line summary."""

REVIEWER_SYSTEM = """You are an independent Governance Reviewer at the bank's Data Architecture Board.
You receive a draft data contract. Verify its claims against the lakehouse with tools
(profile columns, run SQL) — do not trust the draft. Check: are PII columns flagged,
is the PK truly unique, are the quality rules actually satisfied by the data, is the
lineage correct, does it meet GDPR/KYC expectations.

End your reply with EXACTLY one line:  VERDICT: APPROVE   or   VERDICT: REVISE
followed by a bulleted list of specific, actionable findings if REVISE."""


def contract_review(table: str) -> None:
    contract_path = ARTIFACTS_DIR / f"{table}_contract.yml"

    console.rule(f"[bold]A2A · round 1 · author drafts {table}")
    run_agent(f"Draft the data contract for `{table}`.", system=AUTHOR_SYSTEM)

    for round_no in range(1, MAX_ROUNDS + 1):
        if not contract_path.exists():
            console.print(f"[red]author did not write {contract_path.name}[/]")
            return
        draft = contract_path.read_text()

        console.rule(f"[bold]A2A · round {round_no} · reviewer verifies")
        review = run_agent(
            f"Review this draft data contract for `{table}`:\n\n```yaml\n{draft}\n```",
            system=REVIEWER_SYSTEM,
        )
        verdict = _parse_verdict(review)
        console.print(Panel(f"verdict: [bold]{verdict}[/]", border_style="magenta"))
        (ARTIFACTS_DIR / f"{table}_review_r{round_no}.md").write_text(review)

        if verdict == "APPROVE":
            console.print(f"[green]approved after {round_no} review round(s)[/] -> {contract_path}")
            return

        console.rule(f"[bold]A2A · round {round_no} · author revises")
        run_agent(
            f"Your data contract for `{table}` was sent back by the reviewer. "
            f"Address every finding, verify with tools where needed, and overwrite "
            f"{contract_path.name} with write_artifact.\n\nReviewer findings:\n{review}",
            system=AUTHOR_SYSTEM,
        )

    console.print("[yellow]max review rounds reached — contract still marked REVISE[/]")


def _parse_verdict(text: str) -> str:
    m = re.search(r"VERDICT:\s*(APPROVE|REVISE)", text, re.I)
    return m.group(1).upper() if m else "REVISE"


if __name__ == "__main__":
    print(json.dumps({"hint": "uv run lga review silver_customers"}))
