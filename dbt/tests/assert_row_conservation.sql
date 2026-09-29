-- contracts/silver_customers.yml :: row_conservation
-- count(bronze_customers) = count(silver_customers where customer_id > 0)
--                          + count(silver_customers_rejected)
-- customer_id > 0 excludes the Null/Unknown Member sentinel (docs/10), which is
-- synthetic and was never a bronze row to begin with.
with counted as (
    select
        (select count(*) from {{ source('lakehouse', 'bronze_customers') }})
        - (select count(*) from {{ ref('silver_customers') }} where customer_id > 0)
        - (select count(*) from {{ ref('silver_customers_rejected') }})
        as diff
)
select abs(diff) as n
from counted
where abs(diff) != 0
