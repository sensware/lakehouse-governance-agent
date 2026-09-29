# dbt deployment option

An alternative way to materialize `silver_*`/`gold_*` — declaratively, with dbt,
instead of the inline SQL in `data/build_lakehouse.py`. Full writeup:
[docs/11-dbt-deployment.md](../docs/11-dbt-deployment.md).

## Run it

```bash
cd /path/to/lakehouse-governance-agent
uv sync --group dbt                       # installs dbt-duckdb only for this
uv run lga build-data                     # bronze must exist first (dbt never seeds it)

cd dbt
uv run --group dbt dbt build --profiles-dir .    # run every model + every test
uv run --group dbt dbt docs generate --profiles-dir . && uv run --group dbt dbt docs serve --profiles-dir .
```

Or, from the repo root, the one-liner the rest of this project's commands follow:

```bash
uv run lga dbt-build
```

`dbt build` replaces the `silver_*`/`gold_*` tables in `data/lakehouse.duckdb` in
place — every other command (`lga catalog`, `lga agent`, `lga review`, the MCP
server, ABAC roles) reads the result exactly as it would after
`build_lakehouse.py`'s own silver/gold SQL, because it's the same tables, same
schema, same file.

## What's here

```
dbt_project.yml     project config; materializes everything as a table by default
profiles.yml        duckdb target (default) + illustrative snowflake/databricks targets
models/sources.yml  declares bronze_customers/accounts/transactions as sources
models/silver/*.sql one model per silver_* table, translated from build_lakehouse.py
models/gold/*.sql   gold_customer_360, gold_monthly_channel_volume
models/*/schema.yml generic tests (not_null, unique, accepted_values, relationships)
macros/initcap_portable.sql   adapter-dispatch macro — see its header comment
tests/assert_*.sql  singular tests, one per contracts/silver_customers.yml quality_rule
                     not already covered by a generic test in schema.yml
```

## Why this exists

See [docs/11-dbt-deployment.md](../docs/11-dbt-deployment.md) — short version: most
BFSI platforms run dbt on Snowflake or Databricks for exactly this layer, and a team
walkthrough will ask how this project's transformations map onto that. This
answers it by shipping a working dbt project instead of a paragraph about one.
