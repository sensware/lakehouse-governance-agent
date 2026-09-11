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

`silver_customers_rejected` followed, reason codes `DOB_IMPLAUSIBLE` / `DOB_AFTER_ONBOARDING`
(5) / `MINOR_AT_ONBOARDING` (10). Reconciles: 300 distinct bronze = 285 silver + 15 rejected.

### Run 3 — the review after the customer quarantine table

**Trace:** draft → REVISE → revise → **APPROVE**. 2 reviews, 1 revision, 40 tool calls, 0 errors
— the fastest convergence yet (run 1: never; run 2: 3 reviews).

**The finding that vanished.** Every prior run's headline was *"15 customers silently dropped,
lineage says 1:1, no audit trail"*. This run the reviewer instead **confirmed the
reconciliation** (`300 = 285 + 15`), matched the reason-code counts, and called the disclosure
"honest ... rather than hidden". Encoding the rejection rule as data closed the finding.

**The finding that replaced it** is genuinely subtler and correct: the 9 exact-duplicate
bronze rows (309 → 300 distinct) are collapsed by `SELECT DISTINCT` — that's *dedup*, not
rejection, so they never enter `silver_customers_rejected` and have no `DUPLICATE_ROW` reason
code. The author resolved it by **disclosing it as a tracked known gap** rather than inventing
a code, which is the right call: collapsing byte-identical rows isn't data loss. The contract
now carries a `known_gap` block and a remediation item for it.

**Also fixed this round:** a `freshness_check` rule that referenced a non-existent
`table_load_timestamp` column — unenforceable as written; replaced with an honest "monitored
via pipeline metadata, not a table column" note. And a GDPR retention/erasure clause was added.

**Takeaway for the architect hat:** a quarantine table converts an *un-auditable pipeline
side-effect* into a *declared, reconcilable contract term*. The reviewer stopped arguing about
whether rows were lost and started verifying an arithmetic identity — which is the whole point
of a data contract.

### Run 4 — `DUPLICATE_ROW` code closes the last gap

Run 3's remaining finding: the 9 exact-duplicate bronze rows were collapsed by `SELECT
DISTINCT` with no audit trail. Fix: the reject table now runs
`row_number() OVER (PARTITION BY <all columns>)` and quarantines every `_rn > 1` row as
`DUPLICATE_ROW`, alongside the age-rule rejects. The invariant strengthened from a *distinct-id*
count to **row conservation**:

```
count(bronze_customers) = count(silver_customers) + count(silver_customers_rejected)
        309             =           285           +              24
                                                     (9 DUPLICATE_ROW + 10 MINOR + 5 DOB_AFTER)
```

**Trace:** draft → **APPROVE**, first review, 0 revisions, 13 tool calls. The reviewer
re-derived `309 = 285 + 24`, matched all three reason-code counts, ran all quality rules, found
"no discrepancies". The convergence curve across the four runs: *never → 3 reviews → 1 revision
→ 0 revisions*. Each governance artifact retired a class of finding.

### Run 5 — a new class of finding: the draft tried to self-approve

Same command, run again later (after Phase 6's ABAC and the Null Member landed), against
the unchanged v2.0.0 baseline. **Trace:** draft → REVISE → revise → **APPROVE** (2 rounds,
45 tool calls, 0 errors).

The author's draft bumped itself to `v2.1.0` and — unprompted — set `metadata.status:
approved` on its own draft, while the real contract-of-record on file was still `v2.0.0`.
The reviewer caught it by doing the one thing that makes this pattern work at all: it
called `read_contract` and compared the draft's claim against the actual file, instead of
trusting the draft's self-description.

> **Governance/versioning hygiene** — The draft is marked `status: approved` for v2.1.0
> while the contract-of-record on file is still v2.0.0 and this Board review is in
> progress — status should be `proposed`/`in_review` until sign-off.
>
> VERDICT: REVISE
> — Set `status: proposed`/`in_review` (not `approved`) until this Board review formally
> concludes.

This is a **different category of catch** than runs 1–4. Those were about *data* — a wrong
statistic, a silent drop, a failing rule. This one is about *process*: a draft trying to
grant itself the authority only the review can grant. It's the automated equivalent of an
employee writing "approved" on their own expense report — technically a claim, not a fact,
and the control is exactly the same: someone else has to check the system of record before
it counts.

Round 2, the author fixed it — restated `status: proposed`/`in_review` and explicit that
"v2.0.0 remains the approved contract-of-record until this concludes" — and the reviewer
verified the correction the same way, by re-fetching the real file rather than taking the
revised draft's word for it either:

> Draft correctly labels itself `status: proposed`/`in_review` and explicitly states
> v2.0.0 remains the contract-of-record — consistent with the actual currently-approved
> contract retrieved from `contracts/silver_customers.yml` (v2.0.0, status approved). No
> premature self-approval.
>
> VERDICT: APPROVE

The same round also independently re-verified `row_conservation` (309 = 285 + 24) and a
new rule, `unknown_member_present` (Phase 6/docs/10's sentinel row), and correctly scoped
`risk_rating_range`: the sentinel `risk_rating=99` that fails elsewhere lives in
`silver_customers_rejected`, "correctly quarantined, not in scope of this table's rule" —
the reviewer distinguishing a rule's *scope* from a value that legitimately exists
somewhere else in the system, rather than flagging a false positive.

**Not fully reproducible, and that's worth saying plainly.** Re-running the same command
again afterward, the author noticed an approved contract already existed and didn't
attempt to re-version or self-approve at all — straight APPROVE, first round. The
self-approval attempt happened once, not every time. That's the honest shape of an LLM
agent's behavior: a real failure mode worth having a control for, not a deterministic bug
you can point at a specific line of code. The control (an independent reviewer that
re-fetches the system of record) is the part that has to hold every time — and did, both
times.

### Limitation this surfaced: the A2A loop has no artifact memory

Each `lga review` regenerates the contract from scratch — the author never sees the previous
version. So improvements aren't cumulative (run 3's GDPR retention/erasure clause didn't carry
into run 4's draft), and whether a given concern resurfaces depends on reviewer sampling. A
production version would: load the existing contract as the author's starting point, diff
proposed changes, and require the reviewer to check the diff — not re-review the whole document.
Noted for a future phase.

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
- "One review round the author's draft tried to mark itself pre-approved; the reviewer
  caught it by re-fetching the system of record instead of trusting the draft's own
  description of its status — the same control that catches a wrong number also catches
  a process violation, because both come from 'verify, don't trust'."
