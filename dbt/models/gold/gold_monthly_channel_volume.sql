select
    date_trunc('month', txn_ts) as month,
    channel,
    count(*)                    as n_txns,
    round(sum(abs(amount)), 2)  as gross_value
from {{ ref('silver_transactions') }}
group by month, channel
order by month, channel
