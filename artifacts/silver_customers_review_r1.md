## Governance Review — `silver_customers` Data Contract

I validated the draft's claims against the lakehouse data (bronze_customers, silver_customers, silver_accounts).

### What checks out
- **PK uniqueness**: Confirmed — 291 rows, 291 distinct `customer_id` values, no duplicates, no nulls. Claim is accurate.
- **Email format rule**: 0 rows violate `email LIKE '%_@__%.__%'`. Claim accurate.
- **Null rates**: email ~10.3%, DOB ~3.8%, city ~12.0%, kyc_status ~25.4%, risk_rating ~32.6% — all match the documented figures.
- **kyc_status enum / risk_rating range**: values in silver are clean (only PENDING/VERIFIED/REJECTED; 1–5), consistent with the stated rules.
- **PII flags**: first_name, last_name, email, date_of_birth correctly flagged as PII; customer_id/city/kyc_status/risk_rating/created_at correctly left unflagged.
- **Bronze duplicate handling**: 9 customer_ids (5, 45, 103, 145, 160, 168, 189, 234, 284) exist as exact duplicate rows in bronze; these are correctly collapsed to one row each in silver — dedup logic works as documented for true duplicates.

### Problems found (data does not actually satisfy the contract as written)

1. **Critical quality rule `date_of_birth_plausible` is violated by real data.** 6 rows (customer_id 259, 274, 183, 90, 169, 106) have `date_of_birth > created_at - 18 years`, i.e., customers recorded as under 18 at onboarding — directly contradicting both the rule (marked `severity: critical`) and the metadata note claiming "implies min age ~18 at onboarding." Either the source data has a KYC/age-verification defect that must be fixed/quarantined, or the rule/description needs correction. As drafted, the contract asserts a critical invariant that the current table fails.

2. **Undisclosed data loss / incomplete lineage.** 9 customer_ids present exactly once in `bronze_customers` (18, 24, 31, 34, 46, 58, 144, 205, 236) are **entirely missing** from `silver_customers` — this is not a dedup effect (they aren't duplicates). Inspection shows these rows have irregular `kyc_status` values (empty string `""` or lower-case `"verified"`/`"pending"`), suggesting they are silently dropped by a validation/cleansing step rather than normalized. This contradicts:
   - the stated grain "one row per customer_id" (implying full conformance of the customer population, not silent exclusion),
   - the lineage description "1:1 cleansing/conformance (dedup, type casting, trimming)" — trimming/casting does not explain full-record disappearance,
   - GDPR/KYC recordkeeping expectations — customer records should not silently vanish; any exclusion needs to be an explicit, auditable business rule (e.g., a quarantine table), not an implicit pipeline side effect.
   No orphaned FKs were found in `silver_accounts` for these IDs, meaning downstream tables also silently lost these customers — this needs investigation into whether real customer/account data is being lost bank-wide.

3. **Freshness SLA is not verifiable as specified.** `max_staleness_hours: 24` implies sub-day monitoring granularity, but `created_at` is a `DATE` (no time component) and there is no `updated_at`/ingestion-timestamp column. As modeled, the table cannot actually be used to measure/enforce an hourly staleness SLA — either add a timestamp column or restate the SLA at daily granularity.

4. **Minor/quasi-identifier risk not addressed.** The combination of `city` + `date_of_birth` (both retained, DOB granular to the day) plus name could re-identify individuals even though each is documented individually. Not a blocker, but the masking/access-control guarantee should acknowledge quasi-identifier risk from combining non-PII-flagged and PII-flagged columns.

These are substantive enough (a failing "critical" rule + undisclosed silent record loss affecting lineage accuracy and downstream completeness) to require revision before sign-off.

VERDICT: REVISE
- Fix or re-scope the `date_of_birth_plausible` critical rule: either correct the 6 offending source records, quarantine them, or adjust rule/severity — do not leave a "critical" rule silently failing in production.
- Investigate and document the 9 customer_ids dropped between bronze and silver (18, 24, 31, 34, 46, 58, 144, 205, 236); update the lineage section to disclose the actual filtering/validation logic (e.g., malformed kyc_status handling) instead of describing it as pure "dedup, type casting, trimming"; decide whether these customers should be recovered/normalized rather than dropped.
- Confirm whether downstream `silver_accounts`/`gold_customer_360` records tied to those 9 customers were also dropped, and assess business impact of silently lost customer/account data.
- Either add a proper timestamp column (e.g., `updated_at`) to support the stated `max_staleness_hours: 24` SLA, or restate the SLA at the granularity the schema actually supports (daily).
- Consider adding a note on quasi-identifier risk (DOB + city + name) to the consumer guarantees/masking policy.