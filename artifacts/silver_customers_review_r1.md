## Governance Review — `silver_customers` Data Contract

**Verification performed:**

1. **Lineage integrity** — `bronze_customers` (309 rows) = `silver_customers` (285 rows) + `silver_customers_rejected` (24 rows), exactly balancing. Rejection reason codes (`MINOR_AT_ONBOARDING`: 10, `DOB_AFTER_ONBOARDING`: 5, `DUPLICATE_ROW`: 9 = 24 total) match the contract's stated quarantine reasons. Downstream `gold_customer_360` correctly sources from `silver_customers` (plus `silver_accounts`/`silver_transactions`, consistent with a 360 view). Lineage claims are accurate.

2. **Primary key** — `customer_id`: 285 rows, 285 distinct, 0 nulls → truly unique and non-null. Claim verified.

3. **Quality rules** — Ran all ten assertions directly against `silver_customers`:
   - PK not-null/unique ✅
   - names required ✅ (0 violations)
   - created_at populated/not future ✅
   - kyc_status enum ✅
   - risk_rating 1–5 ✅
   - adult_at_onboarding ✅ (0 violations)
   - dob_before_onboarding ✅ (0 violations)
   - email format ✅
   - known_city ✅ (0 violations)
   - no duplicate (first_name,last_name,dob) ✅ (0 violations)
   All ten rules are satisfied by the actual data — none are aspirational only.

4. **Null-rate claims** — email 10.2% (~10% ✓), date_of_birth 3.9% (~4% ✓), city 11.9% (~12% ✓), kyc_status 24.9% (~25% ✓), risk_rating 32.6% (~33% ✓). All match catalog stats closely.

5. **City normalization** — Bronze contains messy variants (`"  London "`, `"LONDON"`, null); Silver contains exactly the 6 clean enum values used in the `known_city` rule, confirming the cleansing transformation is real and effective, not just asserted.

6. **PII flagging** — first_name, last_name, email, date_of_birth are correctly flagged `pii: true`; customer_id (surrogate key), city, kyc_status, risk_rating are correctly left unflagged. This aligns with GDPR expectations (direct/near-direct identifiers flagged) and KYC sensitivity is addressed via access-control language in consumer guarantees, though kyc_status/risk_rating being financial-sensitive (not strictly PII) is a reasonable classification.

7. **Consumer guarantees** — Masking/tokenization requirement for PII in non-prod, stable surrogate keys, and quarantine exclusions are all consistent with observed data behavior.

No discrepancies were found between the contract's claims and the actual lakehouse data.

VERDICT: APPROVE