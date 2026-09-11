"""Phase 5 — data contract change management.

A contract in `contracts/<table>.yml` is the *approved* source of truth. This module,
with no LLM, answers three questions a platform team asks on every pipeline change:

  1. detect_drift(table)  — has the live table diverged from its approved contract?
  2. diff(old, new)       — what exactly does a proposed revision change, and is each
                            change additive (safe) or breaking (needs a version bump + notice)?
  3. save(table, new)     — promote an approved revision, bumping the version + change_log.

The A2A loop (a2a.contract_revision) wraps these: the drift report briefs the author,
the structured diff is what the reviewer verifies — not the whole document. That's the
"drive consensus on standards (data contracts, lineage)" bullet, mechanised.

Contract YAML shape is fixed (see contracts/silver_customers.yml) so the diff can be
structural: `schema` is a list of {name,type,nullable,pii,description}; `quality_rules`
a list of {name,assertion,severity} where each assertion returns 0 when the rule holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import duckdb
import yaml

from .config import DB_PATH, REPO_ROOT

CONTRACTS_DIR = REPO_ROOT / "contracts"

Classification = Literal["additive", "breaking", "metadata"]


# --------------------------------------------------------------------------- load/save


def contract_path(table: str) -> Path:
    return CONTRACTS_DIR / f"{table}.yml"


def load(table: str) -> dict[str, Any]:
    p = contract_path(table)
    if not p.exists():
        raise SystemExit(f"No approved contract at {p.relative_to(REPO_ROOT)} — nothing to revise against.")
    return yaml.safe_load(p.read_text())


def _bump(version: str, level: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def save(table: str, new: dict[str, Any], *, base: dict[str, Any], level: str, note: str) -> str:
    """Promote a revision to contracts/. Versioning is owned here, not by the author:
    the bump level comes from the diff classification, the version from the baseline."""
    version = _bump(base["metadata"]["version"], level)
    new["metadata"]["version"] = version
    new["metadata"]["change_log"] = [{"version": version, "note": note}] + list(
        base["metadata"].get("change_log", [])
    )
    contract_path(table).write_text(yaml.safe_dump(new, sort_keys=False, width=100))
    return version


# --------------------------------------------------------------------------- drift


@dataclass
class Drift:
    kind: str          # SCHEMA_ADDED | SCHEMA_REMOVED | TYPE_CHANGED | NULLABILITY | ENUM_EXPANDED
                       # | NULL_RATE | ROW_COUNT | RULE_FAILING
    detail: str
    column: str | None = None
    severity: str = "review"


def detect_drift(table: str, contract: dict[str, Any]) -> list[Drift]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return _detect_drift(con, table, contract)
    finally:
        con.close()


def _detect_drift(con, table: str, contract: dict[str, Any]) -> list[Drift]:
    out: list[Drift] = []

    live = {
        n: d
        for n, d in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
            [table],
        ).fetchall()
    }
    declared = {c["name"]: c for c in contract["schema"]}

    for name, dtype in live.items():
        if name not in declared:
            out.append(Drift("SCHEMA_ADDED", f"{name} {dtype} present in table, absent from contract", name))
        elif declared[name]["type"].upper() != dtype.upper():
            out.append(Drift("TYPE_CHANGED", f"{name}: contract {declared[name]['type']} -> table {dtype}", name))
    for name in declared:
        if name not in live:
            out.append(Drift("SCHEMA_REMOVED", f"{name} declared in contract, missing from table", name, "breaking"))

    # declared NOT NULL but the table has nulls
    for name, col in declared.items():
        if name in live and col.get("nullable") is False:
            n = con.execute(f'SELECT count(*) FROM {table} WHERE "{name}" IS NULL').fetchone()[0]
            if n:
                out.append(Drift("NULLABILITY", f"{name} is nullable:false but has {n} nulls", name, "breaking"))

    # enum drift: any quality rule of the form  ... NOT IN ('A','B')  on a column
    for rule in contract.get("quality_rules", []):
        col, allowed = _parse_enum_rule(rule["assertion"])
        if col and col in live:
            seen = {
                r[0]
                for r in con.execute(f'SELECT DISTINCT "{col}" FROM {table} WHERE "{col}" IS NOT NULL').fetchall()
            }
            extra = sorted(seen - set(allowed))
            if extra:
                out.append(Drift("ENUM_EXPANDED", f"{col} has values not in contract enum: {extra}", col))

    # null-rate drift beyond tolerance
    pe = contract.get("profile_expectations", {})
    tol = pe.get("null_rate_tolerance", 0.05)
    total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] or 1
    for name, exp in pe.get("columns", {}).items():
        if name not in live:
            continue
        nulls = con.execute(f'SELECT count(*) FROM {table} WHERE "{name}" IS NULL').fetchone()[0]
        rate = nulls / total
        if abs(rate - exp["null_rate"]) > tol:
            out.append(Drift("NULL_RATE", f"{name} null rate {rate:.1%}, contract expects {exp['null_rate']:.0%} (±{tol:.0%})", name))

    rc_min = pe.get("row_count_min")
    if rc_min and total < rc_min:
        out.append(Drift("ROW_COUNT", f"{total} rows, contract expects >= {rc_min}", severity="breaking"))

    # every quality-rule assertion should return 0
    for rule in contract.get("quality_rules", []):
        try:
            val = con.execute(rule["assertion"]).fetchone()[0]
        except duckdb.Error as e:
            out.append(Drift("RULE_FAILING", f"{rule['name']}: assertion errored ({e})", severity="breaking"))
            continue
        if val not in (0, None):
            out.append(Drift("RULE_FAILING", f"{rule['name']}: assertion returned {val} (expected 0)", severity=rule.get("severity", "review")))

    return out


def _parse_enum_rule(assertion: str) -> tuple[str | None, list[str]]:
    import re

    m = re.search(r'WHERE\s+"?(\w+)"?\s+IS NOT NULL AND\s+"?\1"?\s+NOT IN \(([^)]+)\)', assertion, re.I)
    if not m:
        return None, []
    vals = [v.strip().strip("'\"") for v in m.group(2).split(",")]
    return m.group(1), vals


# --------------------------------------------------------------------------- diff


@dataclass
class Change:
    path: str
    kind: str  # added | removed | modified
    old: Any
    new: Any
    classification: Classification


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    changes += _diff_schema(old.get("schema", []), new.get("schema", []))
    changes += _diff_rules(old.get("quality_rules", []), new.get("quality_rules", []))
    changes += _diff_meta(old, new)
    return changes


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {i["name"]: i for i in items}


def _diff_schema(old: list[dict], new: list[dict]) -> list[Change]:
    o, n = _by_name(old), _by_name(new)
    out = []
    for name in n.keys() - o.keys():
        col = n[name]
        cls: Classification = "additive" if col.get("nullable", True) else "breaking"
        out.append(Change(f"schema.{name}", "added", None, col, cls))
    for name in o.keys() - n.keys():
        out.append(Change(f"schema.{name}", "removed", o[name], None, "breaking"))
    for name in o.keys() & n.keys():
        if o[name] != n[name]:
            cls = _classify_col_change(o[name], n[name])
            out.append(Change(f"schema.{name}", "modified", o[name], n[name], cls))
    return out


def _classify_col_change(old: dict, new: dict) -> Classification:
    if old.get("type") != new.get("type"):
        return "breaking"
    if old.get("nullable") is True and new.get("nullable") is False:
        return "breaking"  # consumers may hold nulls
    if old.get("nullable") is False and new.get("nullable") is True:
        return "additive"
    if old.get("pii") != new.get("pii"):
        return "breaking"  # access-control impact
    return "metadata"  # description only


def _diff_rules(old: list[dict], new: list[dict]) -> list[Change]:
    o, n = _by_name(old), _by_name(new)
    out = []
    for name in n.keys() - o.keys():
        out.append(Change(f"quality_rules.{name}", "added", None, n[name], "additive"))
    for name in o.keys() - n.keys():
        out.append(Change(f"quality_rules.{name}", "removed", o[name], None, "breaking"))
    for name in o.keys() & n.keys():
        if o[name] != n[name]:
            out.append(Change(f"quality_rules.{name}", "modified", o[name], n[name], "breaking"))
    return out


_META_KEYS = [("metadata", "domain"), ("metadata", "owner"), ("description",), ("semantics", "freshness")]


def _diff_meta(old: dict, new: dict) -> list[Change]:
    out = []
    for keys in _META_KEYS:
        ov, nv = old, new
        for k in keys:
            ov = (ov or {}).get(k) if isinstance(ov, dict) else None
            nv = (nv or {}).get(k) if isinstance(nv, dict) else None
        if ov != nv:
            out.append(Change(".".join(keys), "modified", ov, nv, "metadata"))
    return out


# --------------------------------------------------------------------------- render


def render_drift(drifts: list[Drift]) -> str:
    if not drifts:
        return "No drift — the live table matches its approved contract."
    lines = ["| kind | column | detail | severity |", "|---|---|---|---|"]
    for d in drifts:
        lines.append(f"| {d.kind} | {d.column or ''} | {d.detail} | {d.severity} |")
    return "\n".join(lines)


def _short(v: Any) -> str:
    if isinstance(v, dict):
        if "type" in v:  # a schema column
            return f"{v.get('type')}{'' if v.get('nullable', True) else ' NOT NULL'}{' PII' if v.get('pii') else ''}"
        if "assertion" in v:  # a quality rule
            return f"{v.get('severity', '?')}: `{v['assertion']}`"
    return str(v)


def render_diff(changes: list[Change]) -> str:
    if not changes:
        return "No structural changes between the two contracts."
    lines = ["| path | kind | classification | old → new |", "|---|---|---|---|"]
    for c in changes:
        lines.append(f"| {c.path} | {c.kind} | **{c.classification}** | {_short(c.old)} → {_short(c.new)} |")
    return "\n".join(lines)


def summarize(changes: list[Change]) -> dict[str, int]:
    out = {"additive": 0, "breaking": 0, "metadata": 0}
    for c in changes:
        out[c.classification] += 1
    return out
