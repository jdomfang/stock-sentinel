-- A paying customer must never be refused by a spend cap.
--
-- THE MISTAKE THIS CORRECTS
--
-- 20260825020000 added a daily ceiling on X posts and applied it to every
-- request. That is wrong, and the owner caught it: the customer has already
-- paid for the credit. Refusing to serve work they bought is taking money for
-- a service and then declining to provide it, and the cap did exactly that
-- once the shared pool ran out -- for everyone, until the rolling window moved.
--
-- The economics say the same thing. A credit sells for $2.50. Serving it costs
-- at most $2.00 of X posts (400 at $0.005) and usually far less, because a
-- sector corpus is cached for six hours across all users. PAID WORK IS
-- PROFITABLE BY CONSTRUCTION -- more of it cannot bankrupt anyone. A ceiling on
-- it protects against nothing and costs revenue.
--
-- What a ceiling IS for is work nobody paid for: a leaked X-Core-Secret, a
-- retry loop, a bug that calls the endpoint without debiting first. That spend
-- has no revenue behind it and no natural limit.
--
-- So the budget now asks a different question. Not "how much have we spent
-- today" but "is this request backed by a credit somebody actually bought".
--
-- HOW IT KNOWS
--
-- consume_credit debits the balance and opens a work_runs row in ONE
-- transaction, and hands the caller that usage_events id. core-api has been
-- receiving it all along as `event_id` and using it only for logging. Checking
-- it is the difference between a shared secret that authorises spending and one
-- that authorises spending AGAINST A SPECIFIC PAID JOB.
--
-- 'running' is the whole test. A completed run cannot be replayed, a refunded
-- one has been made whole, and an id that was never issued matches nothing --
-- so a leaked secret cannot manufacture one without also debiting a real user's
-- real credit, which is the thing it was trying to avoid.

create or replace function public.is_open_paid_work(p_event_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from public.work_runs w
     where w.event_id = p_event_id
       and w.status = 'running'
  );
$$;

comment on function public.is_open_paid_work(uuid) is
    'Is this event_id an OPEN, paid, not-yet-settled unit of work? core-api '
    'exempts such requests from the daily spend ceiling -- the customer already '
    'paid, and a credit is worth more than the posts it buys. See '
    '20260825040000_paid_work_bypasses_budget.sql.';

revoke all on function public.is_open_paid_work(uuid) from public, anon, authenticated;
grant execute on function public.is_open_paid_work(uuid) to service_role;
