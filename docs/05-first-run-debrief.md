# 05 — First live run: what the agents found, what they missed, what it teaches

Model: `claude-opus-5` (adaptive thinking). Artifacts from this run are committed under
`artifacts/` so you can read them alongside this note.

## Phase 2 · RAG — `lga ask`

**Question:** PII tables, owners, and bronze→gold lineage for the customer domain.

**What happened:** with `k=3` the retriever returned `bronze_customers`, `bronze_transactions`,
`gold_customer_360` — and *not* `silver_customers`. The model answered what it could, then said
the silver cards were "not supplied" and refused to invent them.

**Lesson:** the grounding prompt worked exactly as designed — the failure moved from the
generator (hallucination) to the retriever (recall), where it's measurable. Fixes, in order of
cheapness: raise `k`; add a table-name keyword match (hybrid search); re-rank. This is the
"retrieval quality is the product" point from `docs/02`.

## Phase 3 · ReAct agent — `lga agent` DQ audit of `bronze_customers`

**Trace:** 5 reasoning steps · 21 tool calls (18 `run_sql`) · 0 tool errors.
**Output:** [`artifacts/dq_rules_bronze_customers.md`](../artifacts/dq_rules_bronze_customers.md)
— 11 findings, 12 SQL assertions, blocking-vs-warning gating, remediation.

Found every defect that was **seeded** (duplicates, enum chaos, `99` sentinel, malformed
emails, city whitespace, implausible DOBs) **plus three emergent ones** nobody planted —
they fell out of the random generator and the agent caught them only because it queried:

- one email shared by three distinct `customer_id`s (identity-resolution / fraud signal)
- `date_of_birth > created_at` (born after onboarding)
- age < 18 at `created_at` (KYC exception)

**Lesson:** tools that return *real data* are what make agents useful. A model with only the
catalog card would have listed the seeded defects; a model with `run_sql` found things the
author of the dataset didn't know were there.

## Phase 4 · A2A — `lga review silver_customers`

**Trace:** author draft → review (REVISE) → revise → review (REVISE) → revise → *loop ended*.

The reviewer was **right both times**:

| Round | Reviewer caught | Verified how |
|---|---|---|
| 1 | A rule marked `critical` that the live data fails (6 rows) | re-ran the assertion |
| 1 | 9 customers silently dropped between bronze and silver, undisclosed in lineage | anti-join bronze vs silver |
| 1 | `max_staleness_hours: 24` on a table with only a `DATE` column — unmeasurable | `DESCRIBE` |
| 1 | quasi-identifier risk (`city` + DOB + name) not addressed | reasoning |
| 2 | Author's revision *misstated its own evidence* — claimed all 9 dropped rows had irregular `kyc_status`; 3 were clean `REJECTED` | queried the 9 ids directly |

Read [`silver_customers_review_r1.md`](../artifacts/silver_customers_review_r1.md) and
[`..._r2.md`](../artifacts/silver_customers_review_r2.md); this is what "separation of duties
as a responsible-AI control" looks like in practice.

### What the agents missed — and why it's the most useful lesson

Both agents left the 9-row drop **"UNDETERMINED"** and hypothesised about `kyc_status`. The real
cause, one query away:

```sql
SELECT b.customer_id, b.date_of_birth FROM (SELECT DISTINCT * FROM bronze_customers) b
LEFT JOIN silver_customers s USING (customer_id) WHERE s.customer_id IS NULL;
-- all 9 have date_of_birth in 2020–2023
```

Silver filters `date_of_birth <= current_date - INTERVAL 18 YEAR`. The dropped rows are the
seeded "too young" DOBs. Nothing to do with KYC.

**Anchoring.** The author formed a `kyc_status` hypothesis early, the reviewer *disproved the
evidence* for it but adopted the same frame, and neither pivoted to "check every column of the
dropped rows". Mitigations you'd design as an architect:

1. Prompt the investigator to enumerate candidate causes *before* querying (a lightweight
   Tree-of-Thought), not after.
2. Give the reviewer an explicit "propose an alternative root cause" instruction.
3. Add a `diff_rows(table_a, table_b, key)` tool — make the right query cheap.
4. Keep a human in the loop for anything marked `OPEN_CRITICAL` (which the contract did).

### And a real bug the reviewer surfaced

The 6 "under-18 at onboarding" rows in silver are a **genuine pipeline defect**: the filter
uses `current_date - 18 YEAR` where the business rule is `created_at - 18 YEAR`. Someone born
in 2004 and onboarded in 2020 was 16 then, is 22 now, and passes. Fix the silver SQL, and the
contract's "documented exceptions" go to zero.

### Loop-shape bug (fixed)

The first version reviewed → revised → reviewed → revised → **stopped**, so the author's final
correction was never checked. `a2a.py` now always ends on a review: `MAX_REVISIONS` revisions,
`MAX_REVISIONS + 1` reviews, and on exhaustion it names the review file to escalate to a human.
Verified offline with stubbed agents.

## Run 2 — after fixing the age filter and the loop shape

**Change:** silver's rule became `date_of_birth <= created_at - 18 YEAR`. Silver now keeps
285 of 300 distinct customers (9 too-young DOBs + 6 minors-at-onboarding dropped).
**Trace:** draft → REVISE → revise → REVISE → revise → **APPROVE** (3 reviews, 75 tool calls,
0 errors). Artifacts: `silver_customers_contract.yml` v2.0.1, `..._review_r1..r3.md`.

| Round | What happened |
|---|---|
| 1 | Reviewer: lineage claims "1:1 cleansing" but 15 customers (5%) vanish — an *undocumented rejection filter*; no reconciliation rule; no GDPR erasure process on a PII master; suggests `personal_data` tag for `kyc_status`/`risk_rating`. It guessed the cause was partly malformed `city` — **wrong**, but it raised the right issue. |
| 2 | Author had pivoted to the correct DOB correlation and added a reconciliation rule, a rejected-records audit trail, and an erasure clause. Reviewer verified 11 claims in a table and found **one number wrong**: "14 of 15 dropped rows have DOB ≥ 2003" — actually 15 of 15. REVISE, "re-approve once corrected". |
| 3 | Reviewer re-ran every check independently, confirmed the correction, APPROVE — with the row drop kept open as a tracked known issue rather than swept away. |

### What changed vs run 1, and what didn't

- **The anchoring resolved.** With the `kyc_status` noise gone from the dropped set, the author
  found the DOB pattern by round 2. Cleaner data → cleaner hypotheses; the mitigations listed
  above still apply when the signal is weaker.
- **The author got a number wrong in both runs.** Run 1: "all 9 have irregular kyc_status" (6 did).
  Run 2: "14 of 15" (15 did). Both were *evidentiary* statistics in a governance document. Both
  times the reviewer caught it by re-querying. **This is the strongest argument for the
  author/reviewer pattern: LLM-stated numbers are claims until a tool re-derives them.**
- **The agents still call the filter "undocumented" and "buggy".** They're right to: the
  rejection rule lives only in `build_lakehouse.py`, which they can't read. In a real platform the
  contract (or dbt model docs) is where that rule must be declared — the agent is telling you
  your lineage metadata is incomplete, which is a correct governance finding.
- **The loop ended on a verdict.** Round 2's "re-approve once corrected" would have been the
  last word under the old loop; now round 3 exists to say APPROVE.

## Follow-up — quarantine instead of silent drops

Both runs flagged the same anti-pattern: rows vanishing in a `SEMI JOIN`. The fix is a
**rejection table with reason codes**, built in the same step that filters:

```
silver_accounts_rejected = bronze_accounts ANTI JOIN silver_customers
  + reason_code  ∈ {ORPHAN_CUSTOMER, CUSTOMER_REJECTED_UPSTREAM}
  + rejected_by, rejected_at
```

Invariant now testable in a contract: `count(bronze) = count(silver) + count(rejected)`, no
overlap. First result through the MCP tools: 35 upstream-rejected accounts hold **£4.80M** in
balances — the age-rule fix is quarantining material money, which nobody could see before.
Same treatment belongs on `silver_customers` (reason codes `DOB_IMPLAUSIBLE`, `MINOR_AT_ONBOARDING`)
— left as the next exercise.

## Cost & observability notes

- Opus 5 with adaptive thinking: the agent audit was ~5 model calls; the A2A run ~5 agent runs
  (≈25–30 model calls). For iteration set `ANTHROPIC_MODEL=claude-sonnet-5` in `.env`.
- Every tool call is printed with its raw observation — that is your audit log. In production
  you'd persist `(run_id, step, tool, args, result_hash, latency)` and the final artifact hash.

## Talking points this run gives you

- "I built a governance agent; the reviewer agent caught the author overstating evidence."
- "RAG failed gracefully — the grounding contract turned a hallucination risk into a recall
  metric."
- "Agents anchored on a wrong hypothesis; I can explain the mitigation as a prompt/tool/HITL
  design choice."
- "The agent found a real bug in a pipeline I wrote — `current_date` vs `created_at`."
