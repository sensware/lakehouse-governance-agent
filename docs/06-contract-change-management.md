# 06 — Data contract change management (Phase 5)

Phase 4 drafts a contract from scratch every run — no memory, nothing cumulative
(docs/05 closes on that limitation). Phase 5 fixes it: an **approved contract is a
versioned artifact in `contracts/`**, and every pipeline change goes through
detect → propose → diff → review → promote.

This is the JD's *"drive consensus on standards (e.g. data contracts, lineage) across
different data organizations"* and *"created architectural guardrails"*, mechanised.

## The pieces

| Component | LLM? | Job |
|---|---|---|
| `contracts/<table>.yml` | — | the approved source of truth, versioned + change_log |
| `contract.detect_drift` | no | live table vs contract: new/removed/retyped columns, enum expansion, null-rate moves, **every quality-rule assertion re-run** |
| `contract.diff` | no | structural diff, each change pre-classified `additive` / `breaking` / `metadata` |
| author agent | yes | given the drift report, proposes a **minimal** revision (tools: profile the new columns first) |
| reviewer agent | yes | verifies **only the diff**: does it resolve the drift, is each classification right, are new fields described accurately |
| `contract.save` | no | on APPROVE: bump version from the classification (`breaking → major`), prepend change_log, write |

Versioning is **not** the agents' job — it's deterministic from the diff. The first
live run got this wrong (the author set its own version, `save` bumped again → double
bump); now the author is told to leave `version` / `change_log` alone.

## Try it

```bash
uv run lga build-data                          # clean lakehouse, matches contract v2.0.0
uv run lga contract-status silver_customers     # -> "No drift"  (exit 0)

uv run lga evolve                               # +2 columns, relabel some kyc_status -> EXPIRED
uv run lga contract-status silver_customers     # -> drift table (exit 1 — wire into CI)

uv run lga contract-revise silver_customers     # author -> diff -> reviewer -> promote
```

`contract-status` exiting non-zero on drift is the CI hook: block the merge until the
contract is revised, or the change is reverted.

> `contract-revise` rewrites `contracts/silver_customers.yml` in place on APPROVE.
> `git checkout contracts/ && uv run lga build-data` resets to the committed v2.0.0 baseline.

## What the live run showed

Drift after `evolve`:

| kind | detail |
|---|---|
| SCHEMA_ADDED | `marketing_consent BOOLEAN` |
| SCHEMA_ADDED | `customer_segment VARCHAR` |
| ENUM_EXPANDED | `kyc_status` has `EXPIRED`, not in the contract enum |
| RULE_FAILING | `kyc_status_enum` assertion returned 25 (expected 0) |

The author profiled all three columns, then proposed: two new column entries, the
`kyc_status_enum` rule widened to allow `EXPIRED`, a new `customer_segment_enum` rule,
and `profile_expectations` for the new columns. It marked `customer_segment` as
`nullable: false` (live data has zero nulls).

The structured diff classified that as **2 breaking + 1 additive**:

- `customer_segment` added as **NOT NULL** → breaking (a consumer that already
  materialised this table without the column now has a schema mismatch)
- `kyc_status_enum` **loosened** → breaking (consumers relied on "`EXPIRED` can't
  happen"; now it can — needs the 30-day notice the contract itself promises)
- `marketing_consent` nullable → additive

Reviewer verified each against the live table (null rates, distinct values), confirmed
the classifications, **APPROVE on the first round**. Promoted **v2.0.0 → v3.0.0** (major,
because breaking). Full trace: `artifacts/silver_customers_revision_review_r1.md`.

## Why "only the diff" matters

Phase 4's reviewer re-read the entire contract every round and its findings wandered
(a wrong statistic here, a missing GDPR clause there — see docs/05). Phase 5's reviewer
has a bounded task: *N changes, each with a proposed classification, verify them*. The
review is faster (7 tool calls vs 40+), reproducible, and the disagreement surface is
small enough to actually reach consensus on — which is the point of a contract.

## Talking points

- "Contract drift detection re-runs every quality-rule assertion, so a rule that
  silently starts failing is caught before the pipeline ships it."
- "Version bumps are derived from a change classification, not a human guess — breaking
  changes can't be shipped as a patch."
- "The reviewer agent reviews a typed diff, not prose — which is what makes multi-agent
  review converge instead of wander."

## Known gaps / next

- Drift detection is schema + profile + rules; it doesn't yet parse *transformation*
  SQL for column-level lineage (sqlglot would).
- No notification side-effect — a real system would open a ticket / post to the domain's
  channel on a breaking change and start the 30-day clock.
- `evolve` is a toy; a real drift source is the warehouse's `INFORMATION_SCHEMA` +
  a scheduled profiler writing snapshots.
