-- Phase 0: reconcile credits granted before the ledger recorded grants.
--
-- DEPLOY ORDER
--   1. 20260801010000_purchases.sql            (applied)
--   2. 20260801020000_credit_integrity.sql     (applied)
--   3. 20260801030000_admin_adjust_credits.sql (applied)
--   4. THIS FILE
--
-- Must run AFTER 3: it writes event_type='adjustment', which only exists once
-- that migration widens the CHECK constraint.
--
-- WHY
--
-- Migration 3 made every future credit movement write a ledger row. It did
-- nothing about the past. Before it, two paths moved credits without recording
-- anything:
--
--   * profiles.scan_credits / deep_credits default to 1 on signup -- a grant,
--     never ledgered;
--   * pages/Admin.py set balances with a direct service-role UPDATE.
--
-- So the ledger and the balances disagree, measured on production:
--
--   jkenmoefr@yahoo.fr        balance 6/0    sum(cost) 12/23   (35 events, all debits)
--   kdjulianofr@hotmail.com   balance 9/5    sum(cost) 66/40   (116 events)
--
-- Read literally, the first account spent 12 scans while holding 6, having
-- never been granted any. The arithmetic is impossible, which is the point:
-- there is no expression over usage_events that equals profiles.scan_credits,
-- so the ledger cannot be used to check the balance. "Your balance looks wrong"
-- is currently unfalsifiable, and a genuine consume_credit bug would be
-- indistinguishable from an unrecorded grant.
--
-- WHAT THIS DOES
--
-- Writes ONE 'adjustment' row per account carrying the whole unrecorded
-- history, chosen so that afterwards, for every account:
--
--     profiles.scan_credits = -sum(usage_events.cost_scan_credits)
--     profiles.deep_credits = -sum(usage_events.cost_deep_credits)
--
-- It does NOT change a single balance. Nobody gains or loses a credit. This is
-- an accounting entry that makes the existing balances explainable, not a
-- correction of them -- the balances are the ground truth here, because they
-- are what users have been spending all along.
--
-- The row deliberately does NOT record an actor_id. There is no human actor,
-- and usage_select_own lets a user read their own rows, so putting the admin's
-- uuid in user-readable metadata would be a gratuitous disclosure.
--
-- The amounts are computed at run time rather than hard-coded, so a scan
-- between writing this and running it cannot make the result wrong.
--
-- IDEMPOTENT: guarded on metadata->>'kind'. Running it twice is a no-op, not a
-- double credit. Verify with the query at the bottom.

do $$
declare
  r            record;
  v_need_scan  integer;
  v_need_deep  integer;
  v_written    integer := 0;
  v_skipped    integer := 0;
begin
  for r in
    select p.user_id,
           p.email,
           p.scan_credits,
           p.deep_credits,
           coalesce((select sum(e.cost_scan_credits)
                       from public.usage_events e where e.user_id = p.user_id), 0) as sum_scan,
           coalesce((select sum(e.cost_deep_credits)
                       from public.usage_events e where e.user_id = p.user_id), 0) as sum_deep
      from public.profiles p
     order by p.created_at
  loop
    if exists (
      select 1 from public.usage_events e
       where e.user_id = r.user_id
         and e.event_type = 'adjustment'
         and e.metadata ->> 'kind' = 'opening_balance_backfill'
    ) then
      raise notice 'skip % -- already backfilled', r.email;
      v_skipped := v_skipped + 1;
      continue;
    end if;

    -- Want: balance = -(sum_existing + new)  =>  new = -balance - sum_existing
    v_need_scan := -r.scan_credits - r.sum_scan;
    v_need_deep := -r.deep_credits - r.sum_deep;

    if v_need_scan = 0 and v_need_deep = 0 then
      raise notice 'skip % -- already reconciles', r.email;
      v_skipped := v_skipped + 1;
      continue;
    end if;

    insert into public.usage_events (
      user_id, event_type, cost_scan_credits, cost_deep_credits, metadata
    )
    values (
      r.user_id,
      'adjustment',
      v_need_scan,
      v_need_deep,
      jsonb_build_object(
        'kind',   'opening_balance_backfill',
        'source', 'migration 20260801040000',
        'reason', 'Reconciliation: credits granted before admin_adjust_credits existed. '
                  'Signup defaults and admin-page grants were applied directly to '
                  'profiles and never written to the ledger. This entry records that '
                  'history so balance equals -sum(cost). No balance was changed.',
        'balance_at_backfill_scan', r.scan_credits,
        'balance_at_backfill_deep', r.deep_credits,
        'ledger_sum_before_scan',   r.sum_scan,
        'ledger_sum_before_deep',   r.sum_deep
      )
    );

    v_written := v_written + 1;
    raise notice 'backfilled % -- cost %/% (balance %/%, ledger was %/%)',
      r.email, v_need_scan, v_need_deep,
      r.scan_credits, r.deep_credits, r.sum_scan, r.sum_deep;
  end loop;

  raise notice 'backfill complete: % written, % skipped', v_written, v_skipped;
end $$;


-- ── VERIFY ──────────────────────────────────────────────────────────────────
-- Run separately. Every row must read reconciles = true. This is the invariant
-- that has never held on this database.
--
--   select jsonb_pretty(coalesce(jsonb_agg(x), '[]'::jsonb)) from (
--     select jsonb_build_object(
--              'email',       p.email,
--              'balance',     p.scan_credits || '/' || p.deep_credits,
--              'ledger',      (-coalesce(sum(e.cost_scan_credits),0)) || '/'
--                          || (-coalesce(sum(e.cost_deep_credits),0)),
--              'reconciles',  p.scan_credits = -coalesce(sum(e.cost_scan_credits),0)
--                         and p.deep_credits = -coalesce(sum(e.cost_deep_credits),0)
--            ) as x
--       from public.profiles p
--       left join public.usage_events e on e.user_id = p.user_id
--      group by p.user_id, p.email, p.scan_credits, p.deep_credits
--   ) t;


-- ── ROLLBACK ────────────────────────────────────────────────────────────────
-- Removes only the rows this migration wrote. Balances are untouched either
-- way, so this is safe at any time.
--
--   delete from public.usage_events
--    where event_type = 'adjustment'
--      and metadata ->> 'kind' = 'opening_balance_backfill';
