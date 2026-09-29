-- Cleansed, deduplicated customer master. Mirrors data/build_lakehouse.py's
-- `CREATE TABLE silver_customers AS ...` + the Null Member INSERT that follows it —
-- here as one model instead of two statements, since dbt models are single SELECTs.
-- See contracts/silver_customers.yml for the contract this table must satisfy, and
-- docs/10 for why row 0 (the Null/Unknown Member) is unioned in rather than sourced
-- from bronze.

with deduped as (
    select distinct * from {{ source('lakehouse', 'bronze_customers') }}
),

cleaned as (
    select
        customer_id,
        {{ initcap_portable('trim(first_name)') }}              as first_name,
        {{ initcap_portable('trim(last_name)') }}                as last_name,
        case when email like '%@%.%' then lower(email) end       as email,
        date_of_birth,
        {{ initcap_portable('trim(city)') }}                     as city,
        upper(nullif(trim(kyc_status), ''))                      as kyc_status,
        nullif(risk_rating, 99)                                  as risk_rating,
        created_at
    from deduped
    -- Age rule is relative to onboarding (created_at), not today — see docs/05, run 4.
    where date_of_birth is null
       or date_of_birth between date '1910-01-01' and created_at - interval '18' year
),

-- The Null/Unknown Member (Kimball): a synthetic, always-present sentinel row,
-- surrogate key 0, deliberately not sourced from bronze_customers. docs/10 has the
-- full writeup; contracts/silver_customers.yml's row_conservation rule excludes it
-- (customer_id > 0) and unknown_member_present checks it's still here.
unknown_member as (
    select
        0                          as customer_id,
        'Unknown'                  as first_name,
        'Member'                   as last_name,
        cast(null as varchar)      as email,
        cast(null as date)         as date_of_birth,
        cast(null as varchar)      as city,
        cast(null as varchar)      as kyc_status,
        cast(null as integer)      as risk_rating,
        date '1900-01-01'          as created_at
)

select * from cleaned
union all
select * from unknown_member
