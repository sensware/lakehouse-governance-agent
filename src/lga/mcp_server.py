"""Phase 4 — expose the governance tools over the Model Context Protocol.

The SAME registry from tools.py, served over stdio. Any MCP client can now call
list_tables / run_sql / profile_column / search_catalog / write_artifact — including
Claude Desktop, Claude Code, or another agent process. This is the "MCP to chain
reasoning, retrieval and action" idea made concrete: the model's reasoning lives in
the client; retrieval + action live here behind a typed, auditable boundary.

Run standalone:      uv run python -m lga.mcp_server
Register in Claude Code (from the repo root):
    claude mcp add lakehouse-governance -- uv run python -m lga.mcp_server
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from . import tools as T

mcp = MCPServer("lakehouse-governance")


@mcp.tool()
def list_tables() -> str:
    """List every lakehouse table with its medallion layer, domain, and row count."""
    return json.dumps(T.list_tables(), default=str)


@mcp.tool()
def run_sql(sql: str) -> str:
    """Run ONE read-only SQL statement (SELECT/WITH/EXPLAIN/DESCRIBE) against the lakehouse."""
    return json.dumps(T.run_sql(sql), default=str)


@mcp.tool()
def profile_column(table: str, column: str) -> str:
    """Profile a column: null %, cardinality, min/max, mean/stddev, sample values, PII flag."""
    return json.dumps(T.profile_column(table, column), default=str)


@mcp.tool()
def search_catalog(query: str, k: int = 3) -> str:
    """Semantic search over the catalog cards (RAG retriever)."""
    return json.dumps(T.search_catalog(query, k), default=str)


@mcp.tool()
def write_artifact(filename: str, content: str) -> str:
    """Save a governance deliverable (.md/.yml/.json/.sql) into artifacts/."""
    return json.dumps(T.write_artifact(filename, content))


if __name__ == "__main__":
    mcp.run()
