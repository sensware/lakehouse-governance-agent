## Governance Review — `silver_customers`

**Verified against data:**

- **PK uniqueness/not-null** ✅ Confirmed: 285 rows, 285 distinct `customer_id`, 0 nulls. Claim is accurate.
- **Quality rules** ✅ All rules currently pass with zero violations on the live table: `kyc_status` enum, `risk_rating` 1–5, DOB plausibility, no-minors-at-onboarding, `city` known-set, `email` format all return 0 bad rows.
- **Known data issue percentages** ✅ Match profiling exactly: email null 10.2% (~10%), kyc_status null 24.9% (~25%), risk_rating null 32.6% (~33%), city null 11.9% (~12%).
- **PII flags** ✅ `first_name`, `last_name`, `email`, `date_of_birth` correctly flagged PII; `customer_id`/`city`/`kyc_status`/`risk_rating` reasonably left unflagged.
- **Downstream lineage** ✅ `gold_customer_360` catalog card confirms `silver_customers` as an upstream source, consistent with contract.

**Issues found:**

1. **Lineage relationship is mischaracterized.** The contract states `bronze_customers → silver_customers` is "1:1 filtered." In reality, `bronze_customers` has 309 rows but only 300 distinct `customer_id`s (9 customer_ids have exact full-row duplicates, e.g. customer_id 5, 45, 103). These duplicates are silently collapsed to one row in silver (309 → 300 distinct → 285 silver + 15 rejected = 300). This is **deduplication**, not a 1:1 filter, and it is not reflected in `silver_customers_rejected` (whose only reason codes are `MINOR_AT_ONBOARDING`/`DOB_AFTER_ONBOARDING` — no dedup-related reason code exists). Dropped duplicate rows have no audit trail, which contradicts the consumer guarantee that "rows failing quality rules... are available in `silver_customers_rejected` for audit."
2. **`freshness_check` rule is not implementable as written.** It references `table_load_timestamp within SLA`, which is not a column in the schema and has no defined source — this assertion cannot actually be evaluated/enforced as SQL.
3. **No explicit dedup quality rule** is codified despite the top-level description claiming records are "deduplicated." Given exact duplicates exist upstream, a `no_exact_duplicate_rows` (or similar) rule should be added and its outcome logged/audited.
4. **GDPR/KYC documentation gap**: for a table carrying DOB, name, and email PII, the contract lacks any statement of retention period, lawful basis, or data-subject-rights (erasure/rectification) handling — only masking/entitlement is mentioned. Recommend adding a retention/erasure clause given GDPR obligations on customer PII.

None of these are data-correctness failures (the live data actually satisfies all stated quality rules), but the lineage description and freshness rule are factually/operationally inaccurate and should be corrected before sign-off.

VERDICT: REVISE
- Correct lineage relationship from "1:1 filtered" to accurately describe deduplication of exact-duplicate bronze rows (N:1 dedup + filter), and route/tag dropped duplicates to `silver_customers_rejected` with a distinct reason code for audit completeness.
- Fix or remove the `freshness_check` rule — it references a non-existent `table_load_timestamp` column and cannot be executed as specified.
- Add an explicit deduplication quality rule (e.g., assert no exact-duplicate rows per `customer_id` survive silently) to match the "deduplicated" claim in the description.
- Add a GDPR retention/erasure policy statement for the PII columns (first_name, last_name, email, date_of_birth) to strengthen compliance posture beyond masking/entitlement language.