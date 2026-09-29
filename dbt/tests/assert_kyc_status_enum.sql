-- contracts/silver_customers.yml :: kyc_status_enum
select count(*) as n
from {{ ref('silver_customers') }}
where kyc_status is not null
  and kyc_status not in ('VERIFIED', 'PENDING', 'REJECTED')
having count(*) != 0
