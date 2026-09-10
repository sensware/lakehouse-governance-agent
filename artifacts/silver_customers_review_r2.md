## Governance Review: `silver_customers` Data Contract v2.0.0

**Verification performed against live lakehouse data:**

| Claim | Contract states | Verified value | Match? |
|---|---|---|---|
| PK uniqueness | `customer_id` unique, non-null | 285 rows, 285 distinct, 0 nulls | ✅ |
| Bronze row/id counts | 309 rows, 300 distinct ids, 9 duplicated | Confirmed exactly 9 duplicate ids (5,45,103,145,160,168,189,234,284) | ✅ |
| Undocumented filter drop | 15 distinct bronze ids (5%) missing from silver | Confirmed: `bronze EXCEPT silver` = 15 rows | ✅ |
| Row-count reconciliation 5.0% | 15/300 = 5.0% | Confirmed | ✅ |
| Risk_rating sentinel 99 handling | Sentinel 99 nulled correctly | Bronze has 47 rows with 99; silver has 0 rows with 99 or out-of-range values | ✅ |
| kyc_status enum/casing standardization | Uppercase, enum-only, blanks nulled | Confirmed only {VERIFIED, PENDING, REJECTED, NULL} remain, 0 lowercase | ✅ |
| City trimming/casing | Standardized trimmed/title-case | Confirmed 0 rows with untrimmed/non-title-case values | ✅ |
| Null-rate thresholds (email 10.2%, kyc_status 24.9%, risk_rating 32.6%) | As stated | Profile matches exactly | ✅ |
| Email regex compliance | 100% of non-null match regex | Confirmed 0 violations | ✅ |
| DOB plausibility (≥18yrs) | 0 under-18 rows retained | Confirmed 0 rows with DOB > current_date−18y | ✅ |
| PII/personal_data flags | first/last/email/DOB = direct PII; city/kyc_status/risk_rating = personal_data only | Consistent with actual column semantics (quasi-identifiers vs direct identifiers) — reasonable GDPR classification | ✅ |
| GDPR erasure process | DPO-routed, legal-obligation exception under Art. 17(3)(b), audit log | Policy narrative, not independently verifiable via SQL, but internally consistent and appropriately scoped (not a blanket override) | ✅ (procedural, plausible) |

**Discrepancy found:**
The lineage narrative states *"14 of 15 dropped bronze customer_ids have date_of_birth on/after 2003-01-01 (versus only 4 such rows retained in silver)."* Direct query of the actual 15 dropped rows shows **all 15 (100%), not 14**, have `date_of_birth >= '2003-01-01'`. The "4 retained" figure is correct (confirmed independently), but the "14 of 15" figure is wrong — it should be "15 of 15." This is a factual/numeric error in a document whose central argument (that the undocumented filter is a buggy, DOB-correlated defect) rests specifically on these cited statistics. Since this document is meant to serve as an auditable evidentiary record supporting a remediation ticket (DATA-4821) and regulatory reconciliation rule, the underlying evidence must be exactly correct.

Everything else — PK integrity, standardization logic, sentinel-null handling, PII/personal_data classification, quality rule pass/fail status, and the GDPR erasure workflow — checks out faithfully against the data.

VERDICT: REVISE
- Correct the lineage narrative statistic: 15 of 15 (not 14 of 15) dropped bronze `customer_id`s have `date_of_birth >= 2003-01-01`; re-verify and update the "known_issues" (KI-1) and `filter_undocumented` text accordingly before sign-off, since this figure is cited as evidentiary support for the root-cause diagnosis.
- Once corrected, re-confirm the row_count_reconciliation and rejected_records_audit_trail rule descriptions still align with the corrected stat (no other numeric changes expected).
- No other defects found; PK, dedup counts, standardization, sentinel-null cleansing, null-rate thresholds, PII/personal_data classification, and GDPR erasure process are all verified accurate and may be re-approved once the above correction is made.