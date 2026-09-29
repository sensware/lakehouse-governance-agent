# 11 — dbt as a deployment option for the transform layer

**Files:** `dbt/` (the whole directory) · **Commands:** `uv run lga dbt-build`, or
`cd dbt && uv run --group dbt dbt build --profiles-dir .`

This is not a seventh AI phase — the stamp on this project stays "6 phases built."
It's an **alternative deployment** of work Phase 0 already does: the same
`silver_*`/`gold_*` tables, built declaratively with dbt instead of the inline SQL in
`data/build_lakehouse.py`. It exists because almost every BFSI platform team already
runs dbt on Snowflake or Databricks for exactly this layer, and "how does this
project's transform layer map onto our dbt project" is a question worth answering
with working code, not a paragraph.

## Why this is additive, not a replacement

`data/build_lakehouse.py` still owns bronze — the seeded, deliberately-defective raw
data every phase in this project reads. Nothing here changes that. What dbt takes
over is the layer *after* bronze: the cleaning, deduplication, quarantine, repoint,
and aggregation logic that was previously inline SQL strings inside one Python
script. `dbt build` runs against the same `data/lakehouse.duckdb` file and replaces
the `silver_*`/`gold_*` tables in place — the agent, RAG index, MCP server, contracts,
and ABAC roles all read the result exactly as before, because it's the same tables,
same schema, same file. Run `build-data` then `dbt-build`, in either order relative to
the rest of the project's commands, and everything downstream is unaffected.

```
uv run lga build-data      # bronze_* (Python — unchanged, still the only seed)
uv run lga dbt-build       # silver_*/gold_* (dbt — replaces build_lakehouse.py's own SQL for these)
uv run lga catalog         # reads whichever built silver_*/gold_* last; identical either way
```

## What changed, going from inline SQL to dbt models

| `data/build_lakehouse.py` | `dbt/` | Why it matters to the team |
|---|---|---|
| One Python script, five `CREATE TABLE ... AS` statements plus one `INSERT` | One dbt model per table (`dbt/models/silver/*.sql`, `dbt/models/gold/*.sql`) | Each transformation is independently reviewable, testable, and lineage-traceable — the unit a data platform team actually works in |
| `con.execute("CREATE MACRO initcap(s) AS ...")` — a DuckDB-only session macro, because DuckDB has no native `initcap()` | `macros/initcap_portable.sql` — dispatches on `target.type`: `initcap()` on Snowflake/Databricks, the manual expression only on DuckDB | The exact same model SQL compiles correctly on every target in `profiles.yml`; DuckDB's one gap is patched, not exposed to every model that needs it |
| Implicit dependency order (top-to-bottom statements in one file) | Explicit `{{ ref(...) }}` graph — dbt topologically sorts and can run subsets, in parallel (4 threads here) | `dbt run --select silver_customers+` rebuilds one lineage branch; the script always rebuilds everything |
| Manual row-count print at the end | `dbt test`: 7 generic tests (not_null/unique/accepted_values/relationships) + 5 singular tests, one per `contracts/silver_customers.yml` `quality_rule` not already covered by a generic test | The contract's assertions are no longer a YAML file a reviewer agent reads by convention — they're an enforced CI gate, exit-code and all |
| No documentation artifact | `dbt docs generate && dbt docs serve` — a browsable lineage graph + column docs, for free | This is the artifact a platform team already expects from a transform layer |
| One target: the local DuckDB file | Three targets in `profiles.yml`: `duckdb` (default, exercised), `snowflake` and `databricks` (illustrative, need their adapters + `env_var()`s set) | Answers "does this run on our platform" directly: `dbt build --target snowflake`, same models |

## The one place the SQL had to change: `initcap`

`build_lakehouse.py` defines `initcap` as a session `MACRO` because DuckDB lacks a
native one — Snowflake and Databricks/Spark SQL both have it built in. A dbt macro
makes that difference invisible to every model that needs title-cased text
(`silver_customers.first_name/last_name/city`):

```sql
{% macro initcap_portable(expr) %}
  {%- if target.type in ('snowflake', 'databricks', 'spark') -%}
    initcap({{ expr }})
  {%- else -%}
    (upper(substr(({{ expr }}), 1, 1)) || lower(substr(({{ expr }}), 2)))
  {%- endif -%}
{% endmacro %}
```

This is the dbt-native version of the claim in `docs/GUIDE.md` §4 — "every piece of
SQL here runs unchanged on Snowflake or Spark SQL" — extended to cover the one
function DuckDB is missing, instead of quietly relying on it never coming up.

## The contract's quality rules, now runnable as `dbt test`

`contracts/silver_customers.yml` has seven `quality_rules`, each written as a SQL
assertion that returns `0` when the rule holds. Two map directly onto dbt's built-in
generic tests (`pk_not_null_unique` → `not_null` + `unique` on `customer_id`;
`names_present` → `not_null` on `first_name`/`last_name`, in `dbt/models/silver/
schema.yml`). The other five — `kyc_status_enum`, `risk_rating_range`,
`adult_at_onboarding`, `row_conservation`, `unknown_member_present` — are singular
tests in `dbt/tests/`, each a near-literal transliteration of the contract's own
`assertion` string, `ref()`/`source()` swapped in for bare table names. Same seven
rules, same names, provable independently of `contract.py`'s own `detect_drift()` —
two different mechanisms checking the identical set of invariants is a stronger
guarantee than either alone, not a duplication to clean up.

## `row_conservation` and `unknown_member_present`, translated

The trickiest of the seven, because they reference bronze *and* two silver tables at
once, and — for `row_conservation` — do scalar arithmetic with no `FROM`, which needs
a wrapping CTE for `HAVING` to have something to attach to:

```sql
-- dbt/tests/assert_row_conservation.sql
with counted as (
    select
        (select count(*) from {{ source('lakehouse', 'bronze_customers') }})
        - (select count(*) from {{ ref('silver_customers') }} where customer_id > 0)
        - (select count(*) from {{ ref('silver_customers_rejected') }})
        as diff
)
select abs(diff) as n
from counted
where abs(diff) != 0
```

A dbt singular test fails if it returns **any** row — the inverse of the contract's
own convention (returns `0` = holds). Wrapping each assertion so it returns a row
only on failure is the one translation every one of the five singular tests makes.

## Why the target hierarchy in `profiles.yml` matters

`snowflake` and `databricks` are real target blocks, not comments — every value is
`env_var('SNOWFLAKE_ACCOUNT')` or similar, so dbt fails loudly (a clear "env var not
set" error) rather than silently falling back to the local file if someone runs
`dbt build --target snowflake` without configuring it. That's deliberate: the team
should see dbt *refuse* to guess which platform it's talking to, the same way
`policy.py`'s ABAC (docs/08) refuses to guess which role is calling.

## What this still doesn't do

- **No `dbt seed` for bronze.** Bronze stays Python-generated, on purpose — the
  deliberate defects (§4, `docs/00`) are procedurally seeded with a fixed random
  seed, which a static CSV seed file would have to duplicate row-for-row and freeze
  forever. A real platform's bronze layer is an ingestion job dbt doesn't touch
  either; this mirrors that division, it doesn't dodge it.
- **No `dbt-snowflake`/`dbt-databricks` installed.** Those targets are illustrative —
  correct, `env_var()`-gated, never exercised in this repo. Installing either adapter
  and pointing it at a real warehouse is the natural next step for a team that wants
  to run this for real, not something this prototype can verify without one.
- **No incremental models.** Every model here is `+materialized: table`, a full
  rebuild every run — appropriate for a ~300-customer demo, not for the transaction
  volume a real bank produces. `materialized='incremental'` with an `is_incremental()`
  filter on `txn_ts` is the standard fix, left as the obvious next exercise.
