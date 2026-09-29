-- Pre-aggregate each fact independently before combining — the fix for the join
-- fan-out bug docs/10 describes (a customer with N accounts and M transactions
-- produces up to N*M joined rows if you aggregate after one big join; total_balance
-- came out ~9x too high before this was fixed). "Policy/correctness before
-- computation" one layer down from access control (docs/07).

with acct_agg as (
    select
        customer_id,
        count(distinct account_id) as n_accounts,
        sum(balance)               as total_balance
    from {{ ref('silver_accounts') }}
    group by customer_id
),

txn_agg as (
    select
        a.customer_id,
        count(t.txn_id)                                        as n_txns_2024,
        sum(case when t.amount >= 10000 then 1 else 0 end)      as n_large_txns
    from {{ ref('silver_accounts') }} a
    inner join {{ ref('silver_transactions') }} t on t.account_id = a.account_id
    group by a.customer_id
)

select
    c.customer_id,
    c.first_name, c.last_name, c.city, c.kyc_status, c.risk_rating,
    coalesce(aa.n_accounts, 0)     as n_accounts,
    coalesce(aa.total_balance, 0)  as total_balance,
    coalesce(ta.n_txns_2024, 0)    as n_txns_2024,
    coalesce(ta.n_large_txns, 0)   as n_large_txns
from {{ ref('silver_customers') }} c
left join acct_agg aa on aa.customer_id = c.customer_id
left join txn_agg ta  on ta.customer_id = c.customer_id
