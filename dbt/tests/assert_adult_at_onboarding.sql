-- contracts/silver_customers.yml :: adult_at_onboarding
select count(*) as n
from {{ ref('silver_customers') }}
where date_of_birth is not null
  and date_of_birth > created_at - interval '18' year
having count(*) != 0
