# 10 — The Null Member / Unknown Member pattern

**File:** `data/build_lakehouse.py` (the `INSERT INTO silver_customers` right after it's
created) · **Contract:** `contracts/silver_customers.yml`'s `row_conservation` +
`unknown_member_present` rules

This is a classic Kimball dimensional-modeling technique, not an AI concept — worth
having in the same repo as the AI-governance work because it's the *other* answer to
a question this project keeps running into: **what do you do with a fact row whose
dimension key doesn't resolve?**

## The problem, restated from what's already in this repo

`silver_accounts_rejected` (docs/06) exists because `ORPHAN_CUSTOMER` accounts
reference a `customer_id` that doesn't exist anywhere. The project's answer so far has
been **quarantine**: log the row with a reason code, keep it out of the clean table,
make the drop auditable and reconcilable (`count(bronze) = count(silver) +
count(rejected)`). That's the right answer when the row itself is suspect and you don't
want it polluting a dimension or a metric.

The **Null Member** is a different tool for a related but distinct problem: sometimes
you *do* want the fact to load — you don't want a report's total to be silently short
by the rows you excluded — but you still don't have a real dimension row to attach it
to. The classic Kimball fix: keep exactly one synthetic, always-present row in the
dimension, with a well-known surrogate key (commonly `0` or `-1`), and point unresolved
facts at *that* instead of a database `NULL` or a dropped row.

## What's built

```sql
-- data/build_lakehouse.py, right after silver_customers is created
INSERT INTO silver_customers VALUES
(0, 'Unknown', 'Member', NULL, NULL, NULL, NULL, NULL, DATE '1900-01-01')
```

One row. `customer_id = 0`. Not sourced from `bronze_customers` — it's synthetic,
present in every build, deliberately outside the bronze→silver reconciliation:

```yaml
# contracts/silver_customers.yml
- {name: row_conservation, ...
   assertion: "... count(*) FROM silver_customers WHERE customer_id > 0 ..."}   # excludes it
- {name: unknown_member_present, ...
   assertion: "SELECT abs(1 - (SELECT count(*) FROM silver_customers WHERE customer_id = 0))"}
```

`row_conservation` would otherwise be off by exactly one forever; excluding
`customer_id > 0` fixes that without weakening what the rule actually checks.
`unknown_member_present` is the companion a real warehouse needs too: a check that the
sentinel row itself hasn't been accidentally deleted (which would silently turn every
future unresolved join back into a `NULL`).

### It's already "in use" — for free

`gold_customer_360` is built with `FROM silver_customers c LEFT JOIN silver_accounts a
... GROUP BY ALL`. Adding the one row to `silver_customers` makes it show up there too,
automatically, with `n_accounts = 0, total_balance = 0.0, n_txns_2024 = 0` — exactly
what a Kimball dimension's Unknown Member row looks like in an aggregate report: always
present, harmless, zero by default, ready for something to point at it.

### ABAC respects it without any special-casing (Phase 6)

`branch_ops_london`'s row filter on `silver_customers` is `city = 'London'`. The Unknown
Member's `city` is `NULL`; `NULL = 'London'` is `NULL`, which is falsy in `WHERE` — so
it's excluded from every branch-scoped view automatically, the same way any other
non-London row is. No extra rule needed; this is what "the filter is just SQL" buys you.

## What this project deliberately does *not* do with it

The natural next step — repoint the 5 `ORPHAN_CUSTOMER` accounts (or the 35
`CUSTOMER_REJECTED_UPSTREAM` ones, £4.8M, docs/05) at `customer_id = 0` in
`silver_accounts` so their balances aren't silently missing from `gold_customer_360`'s
totals — is the textbook-correct combination (quarantine *and* Unknown Member, not
quarantine *or* Unknown Member: Kimball's own guidance is to load the fact against the
Unknown Member **and** log the exception for follow-up, so aggregates stay complete
while the anomaly stays visible). It's not done here because it would change
`silver_accounts`'s row count and every number quoted across docs/05–09 for it. Left as
the obvious, well-scoped next exercise rather than done quietly in a way that would
invalidate everything already written about those figures.

## Interview soundbite

> "A quarantine table and a Null Member solve two different problems that look similar.
> Quarantine says 'this row is wrong, keep it out, log why.' Null Member says 'this row
> is fine, I just can't resolve its dimension key, and I'd rather have a complete total
> than a technically-cleaner one.' I built both in the same project: `silver_customers_
> rejected` for the first, a sentinel `customer_id = 0` row for the second — and I can
> point to the exact £4.8M figure in this repo's own docs where using the Null Member
> instead of pure quarantine would have kept that money visible in the aggregate."
