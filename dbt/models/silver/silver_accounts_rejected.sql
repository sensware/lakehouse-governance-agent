-- Audit/reason-code log for every bronze account repointed to the Null Member in
-- silver_accounts above — this table no longer decides whether the account survives
-- (it now always does, against customer_id = 0 if unresolved); it explains why.
--   ORPHAN_CUSTOMER            customer_id exists nowhere in bronze_customers
--   CUSTOMER_REJECTED_UPSTREAM customer exists in bronze but was rejected by silver_customers

select
    a.*,
    case
        when not exists (
            select 1 from {{ source('lakehouse', 'bronze_customers') }} b
            where b.customer_id = a.customer_id
        ) then 'ORPHAN_CUSTOMER'
        else 'CUSTOMER_REJECTED_UPSTREAM'
    end                              as reason_code,
    'silver_accounts'                as rejected_by,
    current_timestamp                as rejected_at
from {{ source('lakehouse', 'bronze_accounts') }} a
left join {{ ref('silver_customers') }} c on a.customer_id = c.customer_id
where c.customer_id is null
