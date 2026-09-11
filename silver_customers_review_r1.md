## Review Findings

**Verified as accurate:**
- Row counts match: `silver_customers` = 285 rows, `bronze_customers` = 309 rows.
- PK checks pass: `customer_id` is 100% non-null and unique (285/285 distinct) — `primary_key_not_null` / `primary_key_unique` hold.
- All other declared quality rules pass against live data: no null names, no null/future `created_at`, `kyc_status` values fully constrained to `{VERIFIED,PENDING,REJECTED}` or NULL, `risk_rating` fully within 1–5 or NULL, `date_of_birth` within plausible range, `email` matches the format regex where present.
- Declared null rates are accurate: email ≈10.2%, kyc_status ≈24.9%, risk_rating ≈32.6%, all within their stated thresholds.
- PII flags on `first_name`, `last_name`, `email`, `date_of_birth` are correctly set to true; `customer_id`, `city`, `kyc_status`, `risk_rating`, `created_at` correctly not flagged as direct PII.

**Problems found (lineage does not match reality):**
- Bronze has 300 distinct `customer_id`s (plus 9 duplicated ids → 309 rows). Silver has only 285 rows/285 distinct ids. That means **15 distinct bronze customers (5% of the source population) are silently dropped**, not merely deduplicated — the contract's lineage claims a "1:1 cleansing/standardization (dedup, type casting, trimming)" relationship, which is false; it is dedup **plus an undocumented row-elimination/filter step**.
- Inspecting the 15 dropped rows shows likely causes: implausible/very-recent dates of birth (e.g. DOB in 2020–2023, implying customers aged 1–4), and malformed values that should have been cleaned rather than dropped (e.g. `city = "  London "`, `"LONDON"` case/whitespace variants — trimming logic clearly works for survivors, since silver only contains clean `"London"`, but these specific malformed rows vanished instead of being normalized).
- There is no quality rule, rejection log, or reconciliation metric covering this row loss (e.g., a `row_count_reconciliation` or `rejected_records` rule), so the contract's "authoritative customer reference" and "no silent backfills" guarantees are not actually enforced/observable.
- GDPR/retention gap: the contract states "Retained indefinitely... no historical versioning" but does not address data-subject erasure/right-to-be-forgotten handling, which is a KYC/GDPR expectation for a customer master that carries direct PII (name, email, DOB).
- Minor: `kyc_status` and `risk_rating`, while not classified as PII, are still personal data once linked to an identifiable `customer_id`; consider adding a `personal_data: true` classification (distinct from `pii`) so access-control policy for these fields is explicit, not just implied via table-level PII column list.

VERDICT: REVISE
- Correct the lineage description: document the actual bronze→silver transformation as "dedup + standardization + row-level rejection filter," and specify the filter criteria (e.g., implausible DOB).
- Add a completeness/reconciliation quality rule (e.g., `dropped_row_pct <= X%` or an explicit rejected-records audit trail) so record loss is monitored and not silent.
- Fix or document the cleansing bug where malformed rows (e.g., untrimmed/mis-cased `city`, extreme `date_of_birth`) are dropped instead of corrected or explicitly quarantined.
- Add a GDPR data-subject erasure/retention exception process to the contract, given the "retained indefinitely" policy on a table holding direct PII.
- Consider tagging `kyc_status`/`risk_rating` as personal data (not just non-PII) for access-control clarity.