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
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import duckdb

from .catalog import build_catalog, profile_column as _profile_column
from .config import ARTIFACTS_DIR, DB_PATH

MAX_ROWS = 200

_READ_ONLY_SQL = re.compile(r"^\s*(with|select|explain|pragma|describe|summarize)\b", re.I)


class ToolError(RuntimeError):
    """Raised for bad tool input — surfaced back to the model as an observation."""


def list_tables() -> list[dict[str, str]]:
    """Return every table in the lakehouse with its medallion layer and domain."""
    return [
        {"table": t.name, "layer": t.layer, "domain": t.domain, "rows": t.row_count}
        for t in build_catalog()
    ]


def run_sql(sql: str) -> dict[str, Any]:
    """Run one read-only SQL statement against the lakehouse. Returns columns + rows."""
    if ";" in sql.strip().rstrip(";"):
        raise ToolError("Only a single statement is allowed.")
    if not _READ_ONLY_SQL.match(sql):
        raise ToolError("Only read-only queries (SELECT/WITH/EXPLAIN/PRAGMA/DESCRIBE).")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
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
    """Full profile of one column: nulls, cardinality, range, mean/stddev, samples."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        match = con.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            [table, column],
        ).fetchone()
        if not match:
            raise ToolError(f"No column {table}.{column}")
        prof = _profile_column(con, table, column, match[0])
    finally:
        con.close()
    return {k: _json_safe(v) for k, v in asdict(prof).items()}


def search_catalog(query: str, k: int = 3) -> list[dict[str, Any]]:
    """Semantic search over catalog cards (the RAG retriever, exposed as a tool)."""
    from .rag import retrieve

    return [{"table": h.table, "score": round(h.score, 3), "card": h.text} for h in retrieve(query, k)]


def write_artifact(filename: str, content: str) -> dict[str, str]:
    """Persist a governance deliverable (DQ ruleset, data contract, review) to artifacts/."""
    safe = Path(filename).name
    if not safe.endswith((".md", ".yml", ".yaml", ".json", ".sql")):
        raise ToolError("filename must end in .md/.yml/.yaml/.json/.sql")
    path = ARTIFACTS_DIR / safe
    path.write_text(content)
    return {"written": str(path.relative_to(ARTIFACTS_DIR.parent)), "bytes": len(content)}


def read_contract(table: str) -> dict[str, Any]:
    """Return the currently approved data contract for a table (from contracts/<table>.yml)."""
    from .contract import contract_path

    p = contract_path(table)
    if not p.exists():
        raise ToolError(f"no approved contract for {table}")
    return {"path": str(p.relative_to(p.parents[1])), "yaml": p.read_text()}


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
