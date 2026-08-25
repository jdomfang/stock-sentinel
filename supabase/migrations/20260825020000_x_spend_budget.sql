-- A spend ceiling for the X API, in the database rather than in one process.
--
-- WHY
--
-- core-api's MAX_CONCURRENT is a CONCURRENCY limit, not a spend limit. Three
-- requests at a time, sustained, is unbounded cost: X bills $0.005 per post
-- READ, a sector scan buys up to 300 posts and a deep analysis up to ~400, so
-- three slots running flat out is roughly $3.60 an hour with no ceiling of any
-- kind. A leaked X-Core-Secret, a retry storm, or a loop in a caller all spend
-- until somebody notices the bill.
--
-- The semaphore also lives in ONE process. Add a replica and the limit doubles.
-- A budget has to be shared state, which means the database.
--
-- x_call_metrics already records posts_billed per paid call, including 0 for a
-- corpus-cache hit, so the spend is already being written down. Nothing was
-- reading it back.
--
-- WHAT THIS IS NOT
--
-- It is not transactional with the spend. The check runs before the work and
-- the metrics row is written after, so concurrent callers can each pass a check
-- the others are about to invalidate. The overshoot is bounded by
-- MAX_CONCURRENT x the per-action cap -- about 1,200 posts, or $6 -- which is a
-- rounding error against an unbounded bill and not worth a lock on the hot
-- path. Treat the budget as a ceiling with a known overshoot, not a guarantee.

create or replace function public.x_posts_billed_since(
  p_since interval default '24 hours'
)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(posts_billed), 0)::integer
    from public.x_call_metrics
   where created_at >= now() - p_since;
$$;

comment on function public.x_posts_billed_since(interval) is
    'Total X posts BILLED in the window, across every caller and replica. The '
    'input to core-api''s spend ceiling. Cache hits contribute 0, which is the '
    'point -- the budget should only be consumed by money actually spent.';

-- service_role only, like every other function that informs a spending
-- decision. Nothing here is secret, but a signed-in user has no reason to
-- enumerate platform spend.
revoke all on function public.x_posts_billed_since(interval) from public, anon, authenticated;
grant execute on function public.x_posts_billed_since(interval) to service_role;

-- NO INDEX HERE. x_call_metrics_created_idx on (created_at desc) already
-- exists, created by 20260806010000_x_call_metrics.sql. A second identical
-- index would add write amplification on the paid path for no read benefit,
-- and building it takes an ACCESS EXCLUSIVE lock on a table this service
-- writes to. The recent-window sum this function runs is already served.
