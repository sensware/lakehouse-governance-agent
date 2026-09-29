-- contracts/silver_customers.yml :: unknown_member_present
-- exactly one Null/Unknown Member sentinel row (customer_id = 0) must exist.
select count(*) as n
from {{ ref('silver_customers') }}
where customer_id = 0
having count(*) != 1
