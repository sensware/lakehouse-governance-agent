# Data Quality Ruleset — `bronze_customers`

**Layer:** Bronze (raw landing) — Retail Banking, Customer Domain
**Audit date evidence base:** 309 rows, 300 distinct `customer_id`
**Regulatory relevance:** KYC (Know Your Customer), AML (risk_rating/kyc_status), GDPR/PII (name, DOB, email, city)

---

## Summary of Findings

| # | Issue | Evidence | Severity |
|---|-------|----------|----------|
| 1 | Exact duplicate rows | 9 `customer_id`s (5,45,103,145,160,168,189,234,284) each appear **twice** as byte-for-byte identical rows → 18/309 rows (5.8%) are duplicates, 9 excess rows | High |
| 2 | Primary key not unique | 300 distinct `customer_id` vs 309 rows | High |
| 3 | `kyc_status` inconsistent encoding | Mixed case + blank string used as pseudo-null: `verified`=55, `VERIFIED`=41, `pending`=38, `PENDING`=41, `REJECTED`=55, `''`=44 (14.2%), `NULL`=35 (11.3%). Missing/unknown status (blank+null) = 79 rows (25.6%) | Critical (AML/KYC) |
| 4 | `risk_rating` invalid sentinel | Value `99` used 47× (15.2%) — outside valid 1–5 scale; `NULL` 54× (17.5%). Combined invalid/missing = 101/309 (32.7%) | Critical (AML risk scoring) |
| 5 | `email` invalid format or null | 30/309 rows (9.7%) are NULL or fail basic `local@domain.tld` pattern | Medium |
| 6 | `email` not unique per identity | Only 201 distinct emails across 287 non-null rows; e.g. `omar.ortiz@example.com` shared by customer_ids 109, 173, **and** duplicated rows of 45 — 3 distinct legal identities collide on one email | Medium (identity resolution / AML) |
| 7 | `date_of_birth` sequencing errors | 5 rows where `date_of_birth > created_at` (born after account opened — impossible) | High |
| 8 | `date_of_birth` implies minor at onboarding | 15 rows where age at `created_at` < 18 years | High (KYC — minors cannot self-onboard) |
| 9 | `date_of_birth` implausible / future-leaning | Max DOB = 2023-08-31, i.e. record indicates an infant customer; 3.6% of DOB values are NULL | Medium |
| 10 | `city` formatting inconsistency | Same city stored 3 ways: `"London"` (27), `"LONDON"` (32), `"  London "` (40, padded) — 6 distinct raw values collapse to ~4 true cities; 35 rows (11.3%) NULL | Low–Medium |
| 11 | Completeness gaps (general) | `email` 7.1% null, `date_of_birth` 3.6% null, `city` 11.3% null, `kyc_status` 11.3% null, `risk_rating` 17.5% null | Medium |

---

## Testable Assertions

### 1. Uniqueness

```sql
-- R1: customer_id must be unique
SELECT customer_id, count(*) c
FROM bronze_customers
GROUP BY customer_id
HAVING count(*) > 1;
-- PASS if 0 rows returned. Currently: 9 offending customer_ids / 18 rows.
```

```sql
-- R2: full-row duplicate detection (defensive, catches PK-preserving dupes too)
SELECT *, count(*) c
FROM bronze_customers
GROUP BY ALL
HAVING count(*) > 1;
```

### 2. Completeness (nullability thresholds)

```sql
-- R3: critical identity fields must never be null
SELECT count(*) FROM bronze_customers
WHERE customer_id IS NULL OR first_name IS NULL OR last_name IS NULL;
-- PASS if 0. Currently: 0 (OK today, keep as guardrail).
```

```sql
-- R4: null-rate monitors (warn thresholds, not hard fails, at bronze)
SELECT
  round(100.0 * sum(email IS NULL::INT)        / count(*), 1) AS email_null_pct,        -- current 7.1%
  round(100.0 * sum(date_of_birth IS NULL::INT)/ count(*), 1) AS dob_null_pct,           -- current 3.6%
  round(100.0 * sum(city IS NULL::INT)         / count(*), 1) AS city_null_pct,          -- current 11.3%
  round(100.0 * sum(kyc_status IS NULL OR kyc_status = ''::INT) / count(*), 1) AS kyc_missing_pct, -- current 25.6%
  round(100.0 * sum(risk_rating IS NULL::INT)  / count(*), 1) AS risk_null_pct           -- current 17.5%
FROM bronze_customers;
```

### 3. Validity / domain constraints

```sql
-- R5: kyc_status must be one of the canonical (case-normalized) values, non-blank
SELECT count(*) FROM bronze_customers
WHERE upper(trim(kyc_status)) NOT IN ('VERIFIED','PENDING','REJECTED')
   OR kyc_status IS NULL OR trim(kyc_status) = '';
-- Currently fails: 79 blank/null + all rows need case normalization before silver.
```

```sql
-- R6: risk_rating must be an integer in [1,5]; 99 (or any other sentinel) is invalid
SELECT count(*) FROM bronze_customers
WHERE risk_rating IS NOT NULL AND risk_rating NOT BETWEEN 1 AND 5;
-- Currently: 47 rows (value 99).
```

```sql
-- R7: email, if present, must match basic RFC-lite pattern
SELECT count(*) FROM bronze_customers
WHERE email IS NOT NULL AND email NOT LIKE '%_@__%.__%';
-- Currently: contributes to the 30 invalid/null rows (combine with null check).
```

```sql
-- R8: city must be one of the known service-area list, case- and whitespace-normalized
SELECT DISTINCT city FROM bronze_customers
WHERE city IS NOT NULL
  AND upper(trim(city)) NOT IN ('LONDON','MANCHESTER','BIRMINGHAM','LEEDS','GLASGOW','BRISTOL');
```

### 4. Referential / temporal integrity

```sql
-- R9: date_of_birth must precede created_at (customer must exist before account creation)
SELECT count(*) FROM bronze_customers
WHERE date_of_birth > created_at;
-- Currently: 5 rows.
```

```sql
-- R10: customer must be >= 18 years old at created_at (KYC onboarding-age rule)
SELECT count(*) FROM bronze_customers
WHERE date_of_birth > created_at - INTERVAL 18 YEAR;
-- Currently: 15 rows.
```

```sql
-- R11: date_of_birth must be within a plausible human range
SELECT count(*) FROM bronze_customers
WHERE date_of_birth < DATE '1900-01-01' OR date_of_birth > current_date;
```

### 5. Identity resolution

```sql
-- R12: email should map to exactly one customer_id (flag, don't hard-fail at bronze;
-- must be resolved before silver conformance)
SELECT email, count(DISTINCT customer_id) AS n_customers
FROM bronze_customers
WHERE email IS NOT NULL
GROUP BY email
HAVING count(DISTINCT customer_id) > 1;
-- Currently: e.g. omar.ortiz@example.com -> {45, 109, 173}.
```

---

## Recommended Actions for Platform Team

1. **Bronze → Silver dedup rule**: apply `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at)` and keep rank 1 before promoting to silver; alert if duplicate rows are *not* byte-identical (would indicate conflicting updates, not just re-ingestion).
2. **Standardize `kyc_status`**: normalize to `UPPER(TRIM(...))`, map blank string to `NULL`, then require silver-layer NOT NULL + enum check `{VERIFIED, PENDING, REJECTED}`. Blank/unknown KYC status must block any AML risk scoring downstream — treat as **fail-open risk**, not default-verified.
3. **Fix `risk_rating` sentinel**: treat `99` as `NULL` (unscored) at ingestion, not a valid rating; route to re-scoring queue. Flag to AML/risk team — a 15% mis-coded risk rating is a control gap.
4. **Enforce email format + uniqueness** at silver; investigate the 3 genuinely distinct customers sharing one email as a potential data entry / identity-fraud signal.
5. **Normalize `city`** (trim + title-case) and maintain a reference list of valid branches/service areas.
6. **DOB sequencing/minors**: route the 5 sequencing errors and 15 under-18-at-onboarding rows to data steward review — onboarding a minor without guardian flags is a KYC compliance exception.
7. All of the above should run as **pre-silver gating checks**; rows failing R1, R9, R10 should be quarantined, not silently dropped or auto-corrected, given AML/KYC audit trail requirements.

---
*Ruleset generated from direct SQL profiling of `bronze_customers` (309 rows). Re-run assertions on each ingestion batch; wire into pipeline as blocking (R1, R9, R10) vs. warning (R4, R8, R11, R12) checks.*
