"""Attribute-based access control (ABAC) — the same policy enforced everywhere.

docs/07 named this gap independently from two Databricks articles ("ABAC across SQL
*and* vector search") and it's the same idea Snowflake's guide implements as a **row
access policy** + a **masking policy** (docs/08). All three describe the same shape:

    a caller's role determines (a) which tables it may touch at all,
                                (b) which ROWS of an allowed table it may see,
                                (c) whether PII columns come back masked or real.

This module is the *single* place that decides all three. `tools.py` calls it from
every tool that reaches the lakehouse — `run_sql`, `profile_column`, `list_tables`,
`search_catalog`, `read_contract` — so a role change here changes what every consumer
sees, in SQL and in the RAG index alike. That's the point: enforce once, not per-tool.

Role comes from `LGA_ROLE` (an environment variable an *operator* sets when launching
the agent or the MCP server), never from a tool argument — a caller cannot ask the
model to grant itself a different role.

Honest limits: `match_tables`/`apply_row_filters` work by regex against this project's
small, fixed table vocabulary — not a real SQL parser (no `sqlglot` dependency here).
Good enough for the agent-generated SQL this project actually produces; a hand-crafted
adversarial query could evade it. A real platform enforces this in the query engine
itself (Snowflake row access policies, Databricks Unity Catalog row filters), where it
cannot be bypassed by phrasing. See docs/07 and docs/08 for the honest comparison.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable


class PolicyError(RuntimeError):
    """A role tried to reach a table/row it isn't entitled to."""


@dataclass(frozen=True)
class Role:
    name: str
    # None = unrestricted table access (still subject to column masking below).
    allowed_tables: frozenset[str] | None = None
    # table -> SQL predicate; the row access policy for that table.
    row_filters: dict[str, str] = field(default_factory=dict)
    # bypass PII masking in profile_column — an audited, deliberate exception
    # (Phase 3's DQ-audit agent; mirrors a Snowflake/Databricks "unmask" privilege).
    unmask_pii: bool = False

    def can_access(self, table: str) -> bool:
        return self.allowed_tables is None or table in self.allowed_tables


ROLES: dict[str, Role] = {
    # Default: broad read access, PII stays masked in previews. Today's status quo.
    "analyst": Role("analyst"),
    # Phase 3's DQ-audit persona: needs real values to find duplicates/malformed
    # data. Full table access, PII unmasked — a deliberate, documented exception,
    # not a default (see docs/07's "mask one tool, not the other" reasoning).
    "dq_auditor": Role("dq_auditor", unmask_pii=True),
    # A branch-scoped role: can reach only the customer-domain tables, and only
    # rows belonging to London customers within them. Each filter mirrors a
    # Snowflake row access policy expression (docs/08) — a predicate on
    # CURRENT_ROLE()'s data there is a Python dict lookup here.
    "branch_ops_london": Role(
        "branch_ops_london",
        allowed_tables=frozenset(
            {"silver_customers", "silver_accounts", "silver_transactions", "gold_customer_360"}
        ),
        row_filters={
            "silver_customers": "city = 'London'",
            "gold_customer_360": "city = 'London'",
            "silver_accounts": (
                "customer_id IN (SELECT customer_id FROM silver_customers WHERE city = 'London')"
            ),
            "silver_transactions": (
                "account_id IN (SELECT account_id FROM silver_accounts WHERE customer_id IN "
                "(SELECT customer_id FROM silver_customers WHERE city = 'London'))"
            ),
        },
    ),
}
DEFAULT_ROLE = "analyst"


def current_role() -> Role:
    name = os.getenv("LGA_ROLE", DEFAULT_ROLE)
    try:
        return ROLES[name]
    except KeyError:
        raise PolicyError(f"Unknown LGA_ROLE '{name}'. Known roles: {sorted(ROLES)}") from None


def match_tables(sql: str, known_tables: Iterable[str]) -> set[str]:
    """Which known tables does this SQL text reference? Word-boundary match against
    the finite table vocabulary — see the module docstring for why that's enough here."""
    return {t for t in known_tables if re.search(rf"\b{re.escape(t)}\b", sql, re.I)}


def check_allowed(role: Role, tables: Iterable[str]) -> None:
    denied = sorted(t for t in tables if not role.can_access(t))
    if denied:
        raise PolicyError(f"role '{role.name}' is not permitted to access: {', '.join(denied)}")


# Clauses that can legally follow "FROM/JOIN <table>" — if one of these is the next
# word, it is NOT the table's alias, and the alias group below must not consume it.
_ALIAS_STOP = "|".join(
    rf"{k}\b"
    for k in (
        "WHERE ON GROUP ORDER JOIN LEFT RIGHT INNER OUTER FULL CROSS USING "
        "LIMIT HAVING UNION EXCEPT INTERSECT SEMI ANTI QUALIFY WINDOW"
    ).split()
)


def _substitute_table(sql: str, table: str, row_filter: str) -> str:
    """Replace every FROM/JOIN reference to `table` with a pre-filtered subquery, so
    the row filter applies before anything downstream (a join, an aggregate) runs —
    'policy before computation', the same principle a Snowflake row access policy or
    a Databricks row filter applies at the engine level. Handles a bare table name,
    `table alias`, and `table AS alias`; leaves other clauses (WHERE/JOIN/...) alone."""
    pattern = re.compile(
        rf'\b(FROM|JOIN)\s+"?{re.escape(table)}"?'
        rf"(?:\s+(?:AS\s+)?(?!(?:{_ALIAS_STOP}))([A-Za-z_]\w*))?",
        re.I,
    )

    def repl(m: re.Match[str]) -> str:
        alias = m.group(2) or table
        return f"{m.group(1)} (SELECT * FROM {table} WHERE {row_filter}) AS {alias}"

    return pattern.sub(repl, sql)


def apply_row_filters(sql: str, role: Role, tables: Iterable[str]) -> str:
    for table in tables:
        row_filter = role.row_filters.get(table)
        if row_filter:
            sql = _substitute_table(sql, table, row_filter)
    return sql


def scoped_relation(table: str, role: Role) -> str:
    """The FROM-clause relation `profile_column` should read: the base table, or —
    mirroring `apply_row_filters` — a pre-filtered subquery aliased to the same name."""
    row_filter = role.row_filters.get(table)
    return table if not row_filter else f"(SELECT * FROM {table} WHERE {row_filter}) AS {table}"
