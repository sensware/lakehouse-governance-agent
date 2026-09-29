-- contracts/silver_customers.yml :: risk_rating_range
select count(*) as n
from {{ ref('silver_customers') }}
where risk_rating is not null
  and risk_rating not between 1 and 5
having count(*) != 0
