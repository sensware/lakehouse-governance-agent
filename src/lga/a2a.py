"""Phase 4 — Agent-to-Agent (A2A) collaboration: author + reviewer.

Two specialised agents with different system prompts share the same tool surface
and hand work to each other through a typed message (here: the contract text plus
a structured verdict). That hand-off *is* the A2A protocol in miniature:

    Author agent  --(draft contract)-->  Reviewer agent  --(verdict, findings)-->  Author
                  <--(revised draft)--

The loop stops when the reviewer approves or we exhaust MAX_REVISIONS. Every
revision is reviewed — the last word is always the reviewer's, never an unchecked
draft (a lesson from the first run, where the final fix went un-reviewed).
Each side keeps its own memory (its own transcript); only the artifact and the
verdict cross the boundary — that's the point of a protocol over a shared context
window.

Phase 5 (contract_revision) reuses the same author/reviewer pattern for *change
management*: an approved contract in contracts/ is the baseline, `contract.py`
detects drift and computes a structured diff, and the reviewer verifies only that
diff — not the whole document. Versioning is deterministic (breaking change ->
major bump), owned by the promotion step, not the agents.

  uv run lga review silver_customers          # Phase 4 — draft from scratch
  uv run lga contract-status silver_customers # Phase 5 — drift only, no LLM
  uv run lga contract-revise silver_customers # Phase 5 — drift -> revise -> promote
"""

from __future__ import annotations

import json
import re

from rich.console import Console
from rich.panel import Panel

from . import contract as C
from .agent import run_agent
from .config import ARTIFACTS_DIR

console = Console()
MAX_REVISIONS = 2  # -> up to MAX_REVISIONS + 1 reviews

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

    for round_no in range(1, MAX_REVISIONS + 2):
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
        if round_no > MAX_REVISIONS:
            break  # budget spent; the final verdict on record is a real review, not a guess

        console.rule(f"[bold]A2A · round {round_no} · author revises")
        run_agent(
            f"Your data contract for `{table}` was sent back by the reviewer. "
            f"Address every finding, verify with tools where needed, and overwrite "
            f"{contract_path.name} with write_artifact.\n\nReviewer findings:\n{review}",
            system=AUTHOR_SYSTEM,
        )

    console.print(
        f"[yellow]{MAX_REVISIONS} revisions exhausted — contract still REVISE; "
        f"escalate to a human with {table}_review_r{MAX_REVISIONS + 1}.md[/]"
    )


def _parse_verdict(text: str) -> str:
    m = re.search(r"VERDICT:\s*(APPROVE|REVISE)", text, re.I)
    return m.group(1).upper() if m else "REVISE"


# --------------------------------------------------------------------------- Phase 5

REVISION_AUTHOR_SYSTEM = """You are the Data Product Owner revising an EXISTING approved data contract.
You are given the current contract and a drift report (how the live table diverged from it).
Produce a MINIMAL revision: change only what the drift requires, keep every other line identical,
keep the same YAML shape. Investigate the live table with tools first (profile new columns,
check the new enum values).
Do NOT touch metadata.version or metadata.change_log — the promotion step owns versioning and
will bump the version from the diff classification. Leave them exactly as in the baseline.
Write the full revised contract with write_artifact to  <table>_contract_revised.yml .
Reply with a 3-line summary of what you changed and why."""

REVISION_REVIEWER_SYSTEM = """You are the Governance Reviewer. You are given the BASELINE contract,
a STRUCTURED DIFF of the proposed revision (each change pre-classified additive / breaking /
metadata), and the drift report. Your job is narrow:
  1. Does the revision actually resolve every item in the drift report?
  2. Is each change's additive/breaking classification correct? (breaking = removed column,
     type change, new NOT NULL, PII flag flip, removed/loosened quality rule.)
  3. Are new columns / new enum values accurately described? Verify against the live table with tools.
The version number and change_log are set automatically by the promotion step from the
classification (breaking -> major bump) — do NOT ask for version-number edits.
Do NOT re-review unchanged parts of the contract.

End with EXACTLY one line:  VERDICT: APPROVE   or   VERDICT: REVISE
then bulleted, actionable findings if REVISE."""


def contract_revision(table: str) -> None:
    """Drift-triggered contract change management: detect -> propose -> diff -> review -> promote."""
    base = C.load(table)
    drifts = C.detect_drift(table, base)

    console.rule(f"[bold]Phase 5 · drift check · {table} (contract v{base['metadata']['version']})")
    console.print(Panel(C.render_drift(drifts), border_style="yellow", title="drift report"))
    if not drifts:
        console.print("[green]No drift — approved contract still holds. Nothing to revise.[/]")
        return

    revised_path = ARTIFACTS_DIR / f"{table}_contract_revised.yml"
    revised_path.unlink(missing_ok=True)

    console.rule(f"[bold]Phase 5 · author proposes a revision")
    run_agent(
        f"Current approved contract for `{table}`:\n\n```yaml\n{C.contract_path(table).read_text()}\n```\n\n"
        f"Drift report (what changed in the live table):\n\n{C.render_drift(drifts)}\n\n"
        f"Produce the minimal revision and write it to {revised_path.name}.",
        system=REVISION_AUTHOR_SYSTEM,
    )

    for round_no in range(1, MAX_REVISIONS + 2):
        if not revised_path.exists():
            console.print(f"[red]author did not write {revised_path.name}[/]")
            return
        proposed = yaml_safe_load(revised_path.read_text())
        if proposed is None:
            console.print(f"[red]{revised_path.name} is not valid YAML[/]")
            return
        changes = C.diff(base, proposed)
        summary = C.summarize(changes)

        console.rule(f"[bold]Phase 5 · round {round_no} · reviewer verifies the diff")
        console.print(Panel(C.render_diff(changes), border_style="cyan", title="structured diff"))
        review = run_agent(
            f"Baseline contract for `{table}`:\n\n```yaml\n{C.contract_path(table).read_text()}\n```\n\n"
            f"Drift report:\n\n{C.render_drift(drifts)}\n\n"
            f"Structured diff of the proposed revision "
            f"({summary['additive']} additive, {summary['breaking']} breaking, {summary['metadata']} metadata):\n\n"
            f"{C.render_diff(changes)}\n\n"
            f"Full proposed contract:\n\n```yaml\n{revised_path.read_text()}\n```",
            system=REVISION_REVIEWER_SYSTEM,
        )
        verdict = _parse_verdict(review)
        console.print(Panel(f"verdict: [bold]{verdict}[/]", border_style="magenta"))
        (ARTIFACTS_DIR / f"{table}_revision_review_r{round_no}.md").write_text(review)

        if verdict == "APPROVE":
            level = "major" if summary["breaking"] else ("minor" if summary["additive"] else "patch")
            note = f"{summary['additive']} additive, {summary['breaking']} breaking, {summary['metadata']} metadata change(s); drift resolved."
            new_version = C.save(table, proposed, base=base, level=level, note=note)
            console.print(
                Panel(
                    f"promoted [bold]{table}[/] v{base['metadata']['version']} -> "
                    f"[bold green]v{new_version}[/] ({level} bump)\n{C.render_diff(changes)}",
                    border_style="green",
                    title="contract promoted",
                )
            )
            return
        if round_no > MAX_REVISIONS:
            break

        console.rule(f"[bold]Phase 5 · round {round_no} · author revises")
        run_agent(
            f"Your proposed revision of the `{table}` contract was sent back. Address every finding, "
            f"verify with tools, and overwrite {revised_path.name}.\n\nReviewer findings:\n{review}",
            system=REVISION_AUTHOR_SYSTEM,
        )

    console.print(
        f"[yellow]{MAX_REVISIONS} revisions exhausted — proposed change NOT promoted; "
        f"escalate with {table}_revision_review_r{MAX_REVISIONS + 1}.md[/]"
    )


def yaml_safe_load(text: str):
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


if __name__ == "__main__":
    print(json.dumps({"hint": "uv run lga review silver_customers"}))
