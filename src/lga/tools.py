"""Governance tools — defined once, exposed two ways.

  * Phase 3 (agent.py): fed to Claude as `tools=[...]` for an in-process ReAct loop.
  * Phase 4 (mcp_server.py): served over the Model Context Protocol so *any* MCP
    client (Claude Desktop, another agent, an IDE) can call them.

Each tool is a pure-ish function over the lakehouse + a JSON schema. Keeping the
registry transport-agnostic is the point: the "protocol to chain reasoning,
retrieval, and action models" (the JD's MCP bullet) is just a well-typed tool
surface plus a driver loop.

Safety: `run_sql` is read-only (DuckDB opened read_only, single statement,
SELECT/WITH/EXPLAIN/PRAGMA only, hard row cap). This is the kind of guardrail a
platform team would enforce centrally.

Access control: every tool that reaches the lakehouse consults `policy.py` for the
caller's role (`LGA_ROLE`, set by whoever launches the agent/MCP server — never by
the model). One role definition drives table access, row filtering, and PII masking
identically across `run_sql`, `profile_column`, `list_tables`, `search_catalog`, and
`read_contract` — see docs/07 and docs/08 for why that "identically" matters.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import duckdb

from . import policy as P
from .catalog import build_catalog, profile_column as _profile_column
from .config import ARTIFACTS_DIR, DB_PATH

MAX_ROWS = 200

_READ_ONLY_SQL = re.compile(r"^\s*(with|select|explain|pragma|describe|summarize)\b", re.I)


class ToolError(RuntimeError):
    """Raised for bad tool input — surfaced back to the model as an observation."""


def _role() -> P.Role:
    """current_role(), normalizing an unknown LGA_ROLE to ToolError at the same point
    every other policy violation is normalized — callers below never see PolicyError."""
    try:
        return P.current_role()
    except P.PolicyError as e:
        raise ToolError(str(e)) from e


def list_tables() -> list[dict[str, str]]:
    """Return every table in the lakehouse with its medallion layer and domain.
    Restricted to the caller's role — a role with limited allowed_tables can't even
    discover a table it isn't entitled to query (policy.py)."""
    role = _role()
    return [
        {"table": t.name, "layer": t.layer, "domain": t.domain, "rows": t.row_count}
        for t in build_catalog()
        if role.can_access(t.name)
    ]


def run_sql(sql: str) -> dict[str, Any]:
    """Run one read-only SQL statement against the lakehouse. Returns columns + rows.
    Enforces the caller's role (policy.py): rejects any referenced table it can't
    access, and rewrites the query to pre-filter every table it has a row policy on —
    before the query runs, not after."""
    if ";" in sql.strip().rstrip(";"):
        raise ToolError("Only a single statement is allowed.")
    if not _READ_ONLY_SQL.match(sql):
        raise ToolError("Only read-only queries (SELECT/WITH/EXPLAIN/PRAGMA/DESCRIBE).")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        role = _role()
        try:
            referenced = P.match_tables(sql, _known_tables(con))
            P.check_allowed(role, referenced)
            sql = P.apply_row_filters(sql, role, referenced)
        except P.PolicyError as e:
            raise ToolError(str(e)) from e
        rel = con.sql(sql)
        cols = rel.columns
        rows = rel.fetchmany(MAX_ROWS)
    except duckdb.Error as e:
        raise ToolError(f"SQL error: {e}") from e
    finally:
        con.close()
    return {
        "columns": cols,
        "rows": [[_json_safe(v) for v in r] for r in rows],
        "truncated": len(rows) == MAX_ROWS,
    }


def profile_column(table: str, column: str) -> dict[str, Any]:
    """Full profile of one column: nulls, cardinality, range, mean/stddev, samples.
    Enforced by the caller's role (policy.py): rejects a disallowed table, profiles
    only the role's allowed rows if it has a row policy on this table, and masks PII
    unless the role is explicitly granted unmask_pii (e.g. the DQ-audit persona)."""
    role = _role()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        try:
            P.check_allowed(role, {table})
        except P.PolicyError as e:
            raise ToolError(str(e)) from e
        match = con.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            [table, column],
        ).fetchone()
        if not match:
            raise ToolError(f"No column {table}.{column}")
        relation = P.scoped_relation(table, role)
        prof = _profile_column(con, table, column, match[0], relation=relation, mask=not role.unmask_pii)
    finally:
        con.close()
    return {k: _json_safe(v) for k, v in asdict(prof).items()}


def search_catalog(query: str, k: int = 3) -> list[dict[str, Any]]:
    """Semantic search over catalog cards (the RAG retriever, exposed as a tool).
    Enforces the same role-based table access as run_sql/profile_column (policy.py)
    — a role that can't query a table can't retrieve its card via RAG either."""
    from .rag import retrieve

    role = _role()
    # over-fetch before filtering so a restricted role still gets up to k results
    hits = [h for h in retrieve(query, k=max(k, 8)) if role.can_access(h.table)][:k]
    return [{"table": h.table, "score": round(h.score, 3), "card": h.text} for h in hits]


def write_artifact(filename: str, content: str) -> dict[str, str]:
    """Persist a governance deliverable (DQ ruleset, data contract, review) to artifacts/."""
    safe = Path(filename).name
    if not safe.endswith((".md", ".yml", ".yaml", ".json", ".sql")):
        raise ToolError("filename must end in .md/.yml/.yaml/.json/.sql")
    path = ARTIFACTS_DIR / safe
    path.write_text(content)
    return {"written": str(path.relative_to(ARTIFACTS_DIR.parent)), "bytes": len(content)}


def read_contract(table: str) -> dict[str, Any]:
    """Return the currently approved data contract for a table (from contracts/<table>.yml).
    Gated by the same role check as the other tools (policy.py) — a role that can't
    query a table can't read its contract either."""
    from .contract import contract_path

    try:
        P.check_allowed(_role(), {table})
    except P.PolicyError as e:
        raise ToolError(str(e)) from e
    p = contract_path(table)
    if not p.exists():
        raise ToolError(f"no approved contract for {table}")
    return {"path": str(p.relative_to(p.parents[1])), "yaml": p.read_text()}


def _known_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    ]


def _json_safe(v: Any) -> Any:
    import datetime as _dt

    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, float) and (v != v):  # NaN
        return None
    return v


# ---- registry: name -> (callable, JSON schema for the model) -----------------

TOOLS: dict[str, dict[str, Any]] = {
    "list_tables": {
        "fn": list_tables,
        "description": list_tables.__doc__,
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "search_catalog": {
        "fn": search_catalog,
        "description": search_catalog.__doc__,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 8},
            },
            "required": ["query"],
        },
    },
    "run_sql": {
        "fn": run_sql,
        "description": run_sql.__doc__,
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "single read-only statement"}},
            "required": ["sql"],
        },
    },
    "profile_column": {
        "fn": profile_column,
        "description": profile_column.__doc__,
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "column": {"type": "string"},
            },
            "required": ["table", "column"],
        },
    },
    "read_contract": {
        "fn": read_contract,
        "description": read_contract.__doc__,
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    },
    "write_artifact": {
        "fn": write_artifact,
        "description": write_artifact.__doc__,
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
}


def anthropic_tool_specs() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": t["description"], "input_schema": t["input_schema"]}
        for name, t in TOOLS.items()
    ]


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name}")
    fn: Callable = TOOLS[name]["fn"]
    return fn(**args)
