-- Quarantine: every bronze_customers ROW that did NOT reach silver_customers, with a
-- reason code. Mirrors the `CREATE TABLE silver_customers_rejected AS ...` block in
-- data/build_lakehouse.py. Reconciles row-for-row against bronze:
--   count(bronze_customers) = count(silver_customers where customer_id > 0)
--                            + count(silver_customers_rejected)
-- (contracts/silver_customers.yml's row_conservation rule; also checked below in
-- tests/assert_row_conservation.sql).

with ranked as (
    select
        *,
        row_number() over (
            partition by customer_id, first_name, last_name, email, date_of_birth,
                         city, kyc_status, risk_rating, created_at
            order by customer_id
        ) as _rn
    from {{ source('lakehouse', 'bronze_customers') }}
),

duplicate_rows as (
    select
        customer_id, first_name, last_name, email, date_of_birth, city, kyc_status,
        risk_rating, created_at,
        'DUPLICATE_ROW' as reason_code
    from ranked
    where _rn > 1
),

survivors as (
    select
        customer_id, first_name, last_name, email, date_of_birth, city, kyc_status,
        risk_rating, created_at
    from ranked
    where _rn = 1
),

rule_rejects as (
    select
        s.customer_id, s.first_name, s.last_name, s.email, s.date_of_birth, s.city,
        s.kyc_status, s.risk_rating, s.created_at,
        case
            when s.date_of_birth < date '1910-01-01'                          then 'DOB_IMPLAUSIBLE'
            when s.date_of_birth > s.created_at                               then 'DOB_AFTER_ONBOARDING'
            when s.date_of_birth > s.created_at - interval '18' year          then 'MINOR_AT_ONBOARDING'
            else 'UNKNOWN'
        end as reason_code
    from survivors s
    left join {{ ref('silver_customers') }} sc on s.customer_id = sc.customer_id
    where sc.customer_id is null
)

select *, 'silver_customers' as rejected_by, current_timestamp as rejected_at
from duplicate_rows
union all
select *, 'silver_customers' as rejected_by, current_timestamp as rejected_at
from rule_rejects
