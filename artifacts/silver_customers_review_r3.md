All figures in the contract check out against the lakehouse.

**Verification summary:**
- **PK uniqueness (QR-1):** Confirmed — 285 rows, 285 distinct `customer_id`, 0 nulls.
- **Lineage counts:** Bronze = 309 rows / 300 distinct ids; exactly the 9 stated ids (5,45,103,145,160,168,189,234,284) are duplicated; silver = 285 rows / 285 distinct ids. Matches exactly.
- **Dropped-id set (KI-1):** `bronze EXCEPT silver` returns exactly the 15 ids implied (18,24,31,34,46,58,90,106,144,169,183,205,236,259,274).
- **DOB correlation stat (the corrected claim):** All 15 dropped rows have `date_of_birth >= 2003-01-01` → **15/15 = 100%**, confirming the v2.0.1 correction (previous "14/15" was indeed wrong). Retained silver rows with DOB ≥ 2003-01-01 = 4, matching the evidence block exactly.
- **QR-2/QR-3 (row reconciliation / audit trail):** Consistent with the corrected 15/15 figure.
- **QR-4 (sentinel cleansing):** 0 rows with `risk_rating=99` or out-of-range in silver; bronze had exactly 47 rows with sentinel 99, matching the transformation note.
- **QR-5 (kyc enum):** Distinct silver values are exactly {VERIFIED, PENDING, REJECTED, NULL}.
- **QR-6 (city standardization):** 0 rows fail trim/title-case check.
- **QR-7 (email format):** 0 regex violations.
- **QR-8 (null-rate thresholds):** Observed null rates recomputed independently: email 10.2%, dob 3.9%, city 11.9%, kyc_status 24.9%, risk_rating 32.6% — all match the contract exactly and sit within stated bounds.
- **QR-9 (age plausibility):** 0 rows with DOB implying age <18.
- **PII/personal_data classification:** direct_pii_columns (first_name, last_name, email, date_of_birth) and personal_data_only columns (city, kyc_status, risk_rating) match the schema's per-column flags; classification rationale (direct identifier vs quasi-identifier) is reasonable and GDPR-consistent.
- **GDPR erasure clause:** references DPO routing, Art. 17(3)(b) exception with per-request scoping and audit logging — reasonable and not a blanket override, consistent with KYC/AML retention obligations.

No discrepancies found between the draft's numeric claims and the underlying data. The change log's central correction (14/15 → 15/15 dropped rows with DOB ≥ 2003-01-01) is verified accurate, and all dependent rule descriptions (QR-2, QR-3, KI-1 evidence block) are internally consistent with this corrected figure. KI-1 remains appropriately disclosed as an open, tracked issue (not silently swept under the rug) with a remediation ticket and appropriate consumer caveat.

VERDICT: APPROVE