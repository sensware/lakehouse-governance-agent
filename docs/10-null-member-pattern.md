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

## The repoint, done

This was left as "the obvious next exercise" the first time this doc was written —
repoint the accounts in `silver_accounts_rejected` at `customer_id = 0` in
`silver_accounts` so their balances aren't silently missing from `gold_customer_360`'s
totals. Done. The mechanism: `silver_accounts` changed from a `SEMI JOIN` (keep an
account only if its customer resolves) to a `LEFT JOIN` + `coalesce(c.customer_id, 0)`
(keep every account; repoint the unresolvable ones). One line.

```sql
-- before: drops any account whose customer_id doesn't resolve
FROM bronze_accounts a
SEMI JOIN silver_customers c ON a.customer_id = c.customer_id

-- after: keeps every account; unresolved ones repoint to the Null Member
FROM bronze_accounts a
LEFT JOIN silver_customers c ON a.customer_id = c.customer_id
-- customer_id: coalesce(c.customer_id, 0)
```

This is the textbook-correct combination — quarantine **and** Unknown Member, not
quarantine **or** Unknown Member. Kimball's own guidance is to load the fact against
the Unknown Member *and* log the exception for follow-up, so aggregates stay complete
while the anomaly stays visible. `silver_accounts_rejected` is untouched and still logs
the same 40 rows with the same reason codes — it stops being "the only place these
accounts exist" and becomes what it should always have been: an audit log explaining
*why* a repoint happened, deliberately duplicating rows that also now live in
`silver_accounts` against `customer_id = 0`.

**Verified, live:**

```
count(bronze_accounts) = count(silver_accounts)              606 = 606   (was 566 — full coverage now)
gold_customer_360 WHERE customer_id = 0:
    n_accounts = 40, total_balance = £4,803,756.99, n_txns_2024 = 259
sum(silver_accounts.balance) = sum(gold_customer_360.total_balance)      exactly, both £39,265,983.98
```

`£4,803,756.99` is the exact figure docs/05/06 called "£4.80M... invisible before" —
now it's a real row in the aggregate table, not just an audit-log total. And
`branch_ops_london`'s row filters (docs/08) needed **zero changes**: `customer_id = 0`
has `city IS NULL`, so it was already excluded from every branch-scoped view before this
exercise even started — confirmation that filtering on the dimension's own attributes,
not a hardcoded exception list, was the right original design.

### A real, previously-undetected bug this exercise surfaced

Making the repoint visible exposed something unrelated to the Null Member itself:
`gold_customer_360`'s first draft computed `total_balance` as

```sql
SELECT customer_id, sum(a.balance) AS total_balance, count(t.txn_id) AS n_txns_2024, ...
FROM silver_customers c
LEFT JOIN silver_accounts a     ON a.customer_id = c.customer_id
LEFT JOIN silver_transactions t ON t.account_id = a.account_id
GROUP BY ALL
```

— a classic **join fan-out**: joining accounts to transactions before aggregating means
a customer with 2 accounts and 10 transactions produces up to 20 rows, and `sum(a.balance)`
adds that account's balance in once per matching transaction, not once per account.
This bug has been in `gold_customer_360` since Phase 0. It went unnoticed because every
real customer has few enough accounts and transactions that the inflation looked like
plausible variance, not an obvious error — until the Null Member concentrated 40
accounts and 259 transactions onto one row and `total_balance` came out **~9x** too
high (£42.9M instead of £4.8M). Fixed by aggregating accounts and transactions
independently in their own CTEs *before* joining them to the customer. Table-wide,
`sum(gold_customer_360.total_balance)` now matches `sum(silver_accounts.balance)`
exactly — it didn't, before.

The lesson is the same one this whole repo keeps re-teaching from different angles
(docs/05, docs/07): a concentrated edge case is often what makes a systemic bug visible,
not what causes it.

## Interview soundbite

> "A quarantine table and a Null Member solve two different problems that look similar.
> Quarantine says 'this row is wrong, keep it out, log why.' Null Member says 'this row
> is fine, I just can't resolve its dimension key, and I'd rather have a complete total
> than a technically-cleaner one.' I built both in the same project, then actually
> implemented the repoint I'd first left as an exercise — and it exposed a real
> join-fan-out bug that had been quietly inflating a gold-layer total since the very
> first phase. The Null Member didn't cause that bug; concentrating 40 accounts onto one
> row is what finally made a ~9x error impossible to miss."
