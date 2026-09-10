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
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError

from . import tools as T

mcp = MCPServer("lakehouse-governance")


def _run(name: str, **args) -> str:
    """Call a registry tool; turn guardrail rejections into *expected* MCP errors.

    mcp treats any other exception as a crash and keeps its text server-side — the model
    would see only "Error executing tool" and couldn't fix its SQL. An MCPToolError carries
    the reason across the wire as an is_error result, same as the in-process loop.
    """
    try:
        return json.dumps(T.call_tool(name, args), default=str)
    except T.ToolError as e:
        raise MCPToolError(str(e)) from e


@mcp.tool()
def list_tables() -> str:
    """List every lakehouse table with its medallion layer, domain, and row count."""
    return _run("list_tables")


@mcp.tool()
def run_sql(sql: str) -> str:
    """Run ONE read-only SQL statement (SELECT/WITH/EXPLAIN/DESCRIBE) against the lakehouse."""
    return _run("run_sql", sql=sql)


@mcp.tool()
def profile_column(table: str, column: str) -> str:
    """Profile a column: null %, cardinality, min/max, mean/stddev, sample values, PII flag."""
    return _run("profile_column", table=table, column=column)


@mcp.tool()
def search_catalog(query: str, k: int = 3) -> str:
    """Semantic search over the catalog cards (RAG retriever)."""
    return _run("search_catalog", query=query, k=k)


@mcp.tool()
def write_artifact(filename: str, content: str) -> str:
    """Save a governance deliverable (.md/.yml/.json/.sql) into artifacts/."""
    return _run("write_artifact", filename=filename, content=content)


if __name__ == "__main__":
    mcp.run()
