-- Every bronze account loads here — a LEFT JOIN + COALESCE, not a SEMI JOIN. An
-- account whose customer_id doesn't resolve in silver_customers (orphan, or a
-- customer the age rule rejected) is repointed to customer_id = 0, the Null Member,
-- instead of being dropped. It's still logged with a reason code in
-- silver_accounts_rejected below — this is Kimball's repoint-and-quarantine
-- combination, not a replacement of one by the other. See docs/10.

select
    a.account_id,
    coalesce(c.customer_id, 0)                                 as customer_id,
    case
        when upper(trim(a.account_type)) in ('CURRENT', 'CUR') then 'CURRENT'
        when upper(trim(a.account_type)) in ('SAVINGS', 'SAV') then 'SAVINGS'
        else null
    end                                                         as account_type,
    round(a.balance, 2)                                        as balance,
    a.currency,
    a.opened_date,
    upper(nullif(trim(a.status), ''))                          as status
from {{ source('lakehouse', 'bronze_accounts') }} a
left join {{ ref('silver_customers') }} c on a.customer_id = c.customer_id
