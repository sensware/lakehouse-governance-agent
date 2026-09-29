-- Dedup + type/enum conform + drop future-dated rows (~2% clock-skew on ingest) +
-- keep only transactions on an account that made it into silver_accounts.

with deduped as (
    select distinct * from {{ source('lakehouse', 'bronze_transactions') }}
)

select
    t.txn_id,
    t.account_id,
    round(t.amount, 2)                          as amount,
    upper(t.currency)                           as currency,
    t.txn_ts,
    upper(nullif(trim(t.channel), ''))          as channel,
    t.merchant_category_code
from deduped t
inner join {{ ref('silver_accounts') }} a on t.account_id = a.account_id
where t.txn_ts <= current_timestamp
