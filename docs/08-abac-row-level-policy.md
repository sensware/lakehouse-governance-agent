# 08 — Attribute-based access control (Phase 6)

**File:** `src/lga/policy.py` · **Wired into:** `src/lga/tools.py`, `src/lga/catalog.py`
**Try it:** `uv run lga --role branch_ops_london catalog` won't change (that command is
an unrestricted operator view — see the note at the end); the tool-facing path does:
`LGA_ROLE=branch_ops_london uv run lga agent "..."` or `uv run lga --role ... review ...`.

This closes the gap docs/07 found and left open: *"ABAC across SQL and vector
search — real gap, unresolved."* Two Databricks articles named it independently;
Snowflake's guide (docs/09) implements the same idea as a **row access policy** plus a
**masking policy**. This is that, built with plain Python and SQL text substitution
instead of an engine feature — with the honest limits that implies, stated plainly
below.

## The shape

One decision — a caller's `Role` — answers three questions, enforced identically by
every tool that reaches the lakehouse:

```
Role
 ├─ allowed_tables   which tables can this caller touch at all?          (table ABAC)
 ├─ row_filters       which ROWS of an allowed table can it see?          (row ABAC)
 └─ unmask_pii        does a PII preview come back real, or redacted?     (column masking)
```

```python
# policy.py
ROLES = {
    "analyst":            Role("analyst"),                       # today's status quo
    "dq_auditor":         Role("dq_auditor", unmask_pii=True),   # Phase 3's DQ persona
    "branch_ops_london":  Role("branch_ops_london",
                                allowed_tables={"silver_customers", "silver_accounts",
                                                 "silver_transactions", "gold_customer_360"},
                                row_filters={"silver_customers": "city = 'London'", ...}),
}
```

Role comes from `LGA_ROLE` — an environment variable an **operator** sets when
launching the agent, the MCP server, or the CLI (`lga --role ...`). It is never a tool
argument, so a model can't grant itself a different role by asking nicely; the same
reason a real system reads role from an authenticated session/token, not a request body.

## Enforcement, one policy, five tools

| Tool | What policy.py adds |
|---|---|
| `list_tables` | drops tables outside `allowed_tables` — can't even discover them |
| `run_sql` | rejects any referenced table outside `allowed_tables`; **rewrites** every allowed table with a `row_filters` entry into a pre-filtered subquery before the query runs |
| `profile_column` | same table check; profiles the filtered subquery, not the raw table; masks PII unless `unmask_pii` |
| `search_catalog` (RAG) | drops retrieved cards for tables outside `allowed_tables` — the vector-search half of "SQL and vector search enforce it the same way" |
| `read_contract` | same table check before returning a contract |

### Row filtering: substitute the table, not the output

The core trick, `policy._substitute_table`, replaces `FROM silver_customers` with
`FROM (SELECT * FROM silver_customers WHERE city = 'London') AS silver_customers`
**before** the rest of the query runs — so a `COUNT(*)`, a `JOIN`, an aggregate, all see
only the allowed rows. This is "policy before computation" (docs/07's Databricks point)
done by text substitution instead of a query-planner hook:

```sql
-- what the agent asks
SELECT count(*) FROM silver_customers

-- what actually executes, for role=branch_ops_london
SELECT count(*) FROM (SELECT * FROM silver_customers WHERE city = 'London') AS silver_customers
```

Tables without a direct `city` column get a filter that joins back to one that does:

```python
"silver_accounts": "customer_id IN (SELECT customer_id FROM silver_customers WHERE city = 'London')"
```

### A filter that looked right and was silently wrong

Extending `branch_ops_london` to `silver_accounts_rejected` (the Phase-5.5 quarantine
table, docs/06) by copying the `silver_accounts` pattern —
`customer_id IN (SELECT customer_id FROM silver_customers WHERE city = 'London')` —
runs without error and returns **zero rows, always, for every city**. Not a bug in the
row-filter mechanism: `silver_accounts_rejected` is built as
`bronze_accounts ANTI JOIN silver_customers`, so by construction no row's `customer_id`
ever matches a `silver_customers` row. The filter was checking the one table this data
is guaranteed *not* to be in.

The real audit trail is one layer down, in bronze — and a live check found real cases:
customer 46 and customer 183 were rejected on the age rule (so absent from
`silver_customers`) but both have `bronze_customers.city = "  London "` (whitespace and
all, since bronze is pre-cleansing). The corrected filter reads from `bronze_customers`
directly, normalised the same way the silver build normalises it:

```python
"silver_accounts_rejected": (
    "customer_id IN (SELECT customer_id FROM bronze_customers "
    "WHERE upper(trim(city)) = 'LONDON')"
)
```

`branch_ops_london` running `SELECT reason_code, count(*) FROM silver_accounts_rejected
GROUP BY 1` now correctly returns 3 accounts (customers 46 and 183); `ORPHAN_CUSTOMER`
rows stay excluded, correctly — those customer IDs don't exist in `bronze_customers`
either, so no city can be attributed to them.

**The lesson, generalised:** a row filter's *correctness* depends on where the
attribute it filters on actually lives — for a table built by anti-joining against the
very table you'd normally filter from, that's never the table itself. This is exactly
the kind of thing an engine-native row access policy doesn't save you from either;
Snowflake and Databricks would catch the *syntax* but not this *semantic* error. Only
testing against real data does (docs/07's whole thesis, one level deeper).

The companion table, `silver_customers_rejected`, didn't repeat this mistake: it carries
bronze's own `city` directly on the row (it *is* a `bronze_customers` column, just
anti-joined out of `silver_customers`), so the filter is simply
`upper(trim(city)) = 'LONDON'` on the table itself — no join, no table to get wrong.
Live-verified: 4 of its 24 rows match (customers 46, 145, 183, 189 — a mix of untrimmed,
upper-case, and clean spellings, since it's pre-cleansing data).

### Live proof

```
$ LGA_ROLE=branch_ops_london ...                  list_tables         -> 6 tables (bronze_* gone)
                                                    run_sql city breakdown -> {'London': 95}   (only)
                                                    run_sql on silver_transactions -> 1278       (accounts-filtered via 2 nested joins)
                                                    run_sql on silver_accounts_rejected -> 3 rows (customers 46, 183 — filter reads bronze_customers)
                                                    run_sql on silver_customers_rejected -> 4 rows (customers 46, 145, 183, 189 — filter reads its own city)
                                                    run_sql on silver_customers WHERE customer_id=0 -> 0 rows (the Null Member, docs/10, has no city to match)
                                                    profile_column email  -> still masked ('s***@example.com', ...)
                                                    run_sql on bronze_customers -> ToolError: role 'branch_ops_london' is not permitted to access: bronze_customers
                                                    search_catalog "customer data quality" -> ['gold_customer_360','silver_customers','silver_accounts']  (no bronze_*)

$ LGA_ROLE=dq_auditor      profile_column email -> real values (hiro.jones@example.com, ...) — the deliberate exception from docs/07
$ LGA_ROLE=not_a_real_role list_tables          -> ToolError: Unknown LGA_ROLE 'not_a_real_role'. Known roles: [...]
```

## A bug this found in itself, before it shipped

The first version let `PolicyError` from `current_role()` (an unknown `LGA_ROLE`) leak
past several tools' `try/except` — `list_tables`, `profile_column`, `search_catalog`
called `policy.current_role()` directly, outside the block that translates policy
violations into `ToolError`. A bad role name would have surfaced as a raw Python
exception instead of a clean guardrail message. Caught by testing the exact case docs/07
flags as the point of this whole exercise — *verify the control actually fires* — and
fixed with one wrapper (`tools._role()`) every tool now calls through.

## Tests

`tests/test_policy.py` — 18 offline tests, no DB, no API: role definitions, `current_role`
env handling (default/set/unknown), `match_tables`, `check_allowed`, the alias-preserving
regex rewrite (bare table, `table alias`, `table AS alias`, inside a `JOIN`), and
`scoped_relation`.

## Honest limits (stated, not hidden)

- **Not engine-enforced.** The mask and the row filter are application logic in
  `catalog.py`/`tools.py`. A second tool reading `data/lakehouse.duckdb` directly
  bypasses all of it. Snowflake row access policies and Databricks Unity Catalog row
  filters are enforced *inside the query engine* — nothing downstream can route around
  them. This project can't be, without an actual engine-level feature (see docs/09).
- **Regex, not a parser.** `match_tables`/`_substitute_table` work by scanning for this
  project's small, fixed table vocabulary — not `sqlglot`. A hand-crafted adversarial
  query (the table name inside a string literal, a CTE that shadows it) could evade the
  rewrite. Sufficient for the SQL this project's own agents generate; not a security
  boundary against an adversarial caller.
- **Role, not identity.** One coarse role per process (`LGA_ROLE`), not a real
  authenticated user with row-level entitlements looked up per call.
- **`lga catalog` is intentionally unscoped.** It's the operator's full-inventory view
  (like a platform admin browsing Unity Catalog directly), not a tool an agent calls —
  `--role` doesn't touch it, on purpose. Everything an agent or MCP client can reach
  (`tools.py`) is scoped; the CLI's own inspection commands are not.

## What's still open

Both source articles (docs/07, docs/09) describe governance metadata (tags,
classifications, glossary) as something a platform *derives and enforces centrally*.
Here it's three things a human wrote by hand: `PII_COLUMNS`, `DOMAIN_OWNERS`, `ROLES`.
Correct today; each one is a place a real system would instead read from a governed,
continuously-updated source (Unity Catalog tags / Snowflake object tags) rather than a
Python literal that can silently drift from reality.
