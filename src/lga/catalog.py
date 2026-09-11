"""Phase 1 — metadata catalog + column profiling.

Pure DuckDB/pandas, no LLM. This is the "boring" foundation every governance
programme needs: for each table/column, what's the type, how complete is it,
how much does it vary, what does it actually contain. The output feeds:

  * Phase 2 (RAG): each table becomes a "catalog card" document to retrieve over.
  * Phase 3 (agent): the agent calls `profile_column` / `run_sql` as tools.

Design notes for the architect hat:
  * Profiling is sampled-friendly here (small data) but the same SQL shape runs
    on Snowflake/Databricks — swap the connection, push the aggregation down.
  * Column-level lineage here is *declared* (see LINEAGE). In production you'd
    parse transformation SQL (e.g. sqlglot) or read it from dbt / Unity Catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

import duckdb

from .config import DB_PATH

# Declared table-level lineage (we own the build, so we know it).
LINEAGE: dict[str, list[str]] = {
    "silver_customers": ["bronze_customers"],
    "silver_customers_rejected": ["bronze_customers", "silver_customers"],
    "silver_accounts": ["bronze_accounts", "silver_customers"],
    "silver_accounts_rejected": ["bronze_accounts", "bronze_customers", "silver_customers"],
    "silver_transactions": ["bronze_transactions", "silver_accounts"],
    "gold_customer_360": ["silver_customers", "silver_accounts", "silver_transactions"],
    "gold_monthly_channel_volume": ["silver_transactions"],
}

# Business owner / classification metadata — normally from a catalog tool.
DOMAIN_OWNERS: dict[str, str] = {
    "customers": "Retail Banking — Customer Domain",
    "customers_rejected": "Retail Banking — Customer Domain (quarantine)",
    "accounts": "Retail Banking — Deposits Domain",
    "accounts_rejected": "Retail Banking — Deposits Domain (quarantine)",
    "transactions": "Payments Domain",
    "customer_360": "Customer Analytics (data product)",
    "monthly_channel_volume": "Channel Analytics (data product)",
}

PII_COLUMNS = {"first_name", "last_name", "email", "date_of_birth"}


@dataclass
class ColumnProfile:
    name: str
    data_type: str
    null_count: int
    null_pct: float
    distinct_count: int
    distinct_pct: float
    sample_values: list[Any]
    min: Any = None
    max: Any = None
    mean: float | None = None
    stddev: float | None = None
    is_pii: bool = False


@dataclass
class TableProfile:
    name: str
    layer: str  # bronze | silver | gold
    domain: str
    row_count: int
    upstreams: list[str]
    columns: list[ColumnProfile] = field(default_factory=list)

    def to_card(self) -> str:
        """Render as a compact markdown 'catalog card' — the RAG document unit."""
        lines = [
            f"# Table: {self.name}",
            f"- Medallion layer: **{self.layer}**",
            f"- Business domain / owner: {self.domain}",
            f"- Row count: {self.row_count:,}",
            f"- Upstream tables (lineage): {', '.join(self.upstreams) or '— (raw landing)'}",
            "",
            "## Columns",
            "| column | type | null % | distinct | PII | min | max | sample values |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in self.columns:
            samples = ", ".join(str(v) for v in c.sample_values[:5])
            lines.append(
                f"| {c.name} | {c.data_type} | {c.null_pct:.1f}% | {c.distinct_count} | "
                f"{'yes' if c.is_pii else ''} | {c.min} | {c.max} | {samples} |"
            )
        return "\n".join(lines)


def _layer_of(table: str) -> str:
    return table.split("_", 1)[0]


def _domain_of(table: str) -> str:
    stem = table.split("_", 1)[1]
    return DOMAIN_OWNERS.get(stem, "Unassigned")


def _mask(v: Any) -> Any:
    """Redact a PII value while keeping enough shape to sanity-check format/plausibility
    (an email still looks like an email; a name is still one letter + stars) without
    exposing the real value. See docs/07 — this closes a real leak: is_pii was metadata
    only, never enforced, so profile_column/catalog cards returned raw PII."""
    if v is None:
        return None
    s = str(v)
    if "@" in s:
        local, _, domain = s.partition("@")
        return f"{local[:1]}***@{domain}"
    return s[0] + "*" * (len(s) - 1) if len(s) > 1 else "*"


def profile_column(con: duckdb.DuckDBPyConnection, table: str, col: str, dtype: str) -> ColumnProfile:
    row_count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] or 1
    nulls, distincts = con.execute(
        f'SELECT count(*) - count("{col}"), count(DISTINCT "{col}") FROM {table}'
    ).fetchone()
    samples = [
        r[0]
        for r in con.execute(
            f'SELECT DISTINCT "{col}" FROM {table} WHERE "{col}" IS NOT NULL LIMIT 5'
        ).fetchall()
    ]
    prof = ColumnProfile(
        name=col,
        data_type=dtype,
        null_count=nulls,
        null_pct=100.0 * nulls / row_count,
        distinct_count=distincts,
        distinct_pct=100.0 * distincts / row_count,
        sample_values=samples,
        is_pii=col in PII_COLUMNS,
    )
    numeric = any(t in dtype.upper() for t in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "BIGINT"))
    temporal = any(t in dtype.upper() for t in ("DATE", "TIMESTAMP", "TIME"))
    if numeric:
        mn, mx, mean, sd = con.execute(
            f'SELECT min("{col}"), max("{col}"), avg("{col}"), stddev("{col}") FROM {table}'
        ).fetchone()
        prof.min, prof.max, prof.mean, prof.stddev = mn, mx, mean, sd
    elif temporal:
        mn, mx = con.execute(f'SELECT min("{col}"), max("{col}") FROM {table}').fetchone()
        prof.min, prof.max = str(mn), str(mx)

    if prof.is_pii:
        prof.sample_values = [_mask(v) for v in prof.sample_values]
        prof.min, prof.max = _mask(prof.min), _mask(prof.max)
    return prof


def profile_table(con: duckdb.DuckDBPyConnection, table: str) -> TableProfile:
    cols = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    row_count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    tp = TableProfile(
        name=table,
        layer=_layer_of(table),
        domain=_domain_of(table),
        row_count=row_count,
        upstreams=LINEAGE.get(table, []),
    )
    tp.columns = [profile_column(con, table, name, dtype) for name, dtype in cols]
    return tp


def build_catalog(db_path=DB_PATH) -> list[TableProfile]:
    if not db_path.exists():
        raise SystemExit(f"{db_path} not found — run: uv run python data/build_lakehouse.py")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        return [profile_table(con, t) for t in tables]
    finally:
        con.close()


def catalog_to_json(catalog: list[TableProfile]) -> str:
    return json.dumps([asdict(t) for t in catalog], indent=2, default=str)


if __name__ == "__main__":
    cat = build_catalog()
    for t in cat:
        print(t.to_card())
        print()
