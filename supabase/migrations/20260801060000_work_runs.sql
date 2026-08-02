-- Phase 1, Track A: make unfinished paid work DETECTABLE, then refund it.
--
-- DEPLOY ORDER
--   1..5  (applied)
--   6. THIS FILE
--   7. Deploy the app, which calls complete_work() where it currently sets its
--      _delivered flag.
--
-- Safe in either order. Until step 7 lands, every run looks unfinished -- which
-- is why the reaper is NOT scheduled by this migration. Schedule it only after
-- the app is closing its runs (see the bottom of this file).
--
-- WHY
--
-- On 2026-08-01 a Discovery scan was killed mid-run by the kernel's OOM killer.
-- The log stops at 02:34:10 with no traceback and a fresh process appears at
-- 02:38:28. SIGKILL cannot be caught: no `except` ran, no `finally` ran, no
-- refund happened. The credit was spent and nothing was delivered.
--
-- bf401de moved every refund into try/finally, which covers Streamlit aborting
-- the script -- the common case. It cannot cover this one. Nothing INSIDE a
-- process can report that process's own death.
--
-- The deeper problem is that the ledger records the CHARGE but not the
-- DELIVERY, so a debit with results and a debit with nothing are identical
-- rows. Recovering the 02:34 credit needed a human noticing a blank page, then
-- a manual query, then a hand-issued refund.
--
-- work_runs closes that: a row is opened in the SAME TRANSACTION as the debit,
-- so a debit without a lifecycle row is impossible rather than merely
-- discouraged, and an abandoned row is a row anyone can find with a WHERE
-- clause.
--
-- RELATIONSHIP TO THE TARGET ARCHITECTURE
--
-- The agreed design has scans/analyses tables doubling as cache and job queue.
-- This is deliberately narrower: one table, one concern -- did paid work
-- finish. Caching has different keys, a different lifetime and different access
-- patterns, and conflating them now would make both harder to change. When the
-- cache layer arrives it can join to work_runs on event_id.

set local lock_timeout = '3s';

begin;

-- ── 1. the lifecycle table ──────────────────────────────────────────────────

create table if not exists public.work_runs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  -- One run per debit. UNIQUE is what makes "every debit has exactly one
  -- lifecycle row" enforceable rather than aspirational, and it makes the
  -- insert inside consume_credit idempotent under replay for free.
  event_id    uuid not null unique references public.usage_events(id) on delete cascade,
  kind        text not null check (kind in ('scan', 'deep_analyze')),
  status      text not null default 'running'
                check (status in ('running', 'completed', 'failed', 'orphaned')),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  detail      text
);

-- The reaper's only query: running rows older than a threshold. Partial, so it
-- stays small no matter how much history accumulates -- completed rows leave
-- the index entirely.
create index if not exists work_runs_running_idx
  on public.work_runs (started_at)
  where status = 'running';

create index if not exists work_runs_user_idx on public.work_runs (user_id, started_at desc);

alter table public.work_runs enable row level security;

-- Users may READ their own runs; nothing may write except the SECURITY DEFINER
-- functions below. The same reasoning as usage_events: a client that can insert
-- or update its own lifecycle rows can mark abandoned work "completed" and
-- defeat the reaper, or mark delivered work "running" and harvest refunds.
drop policy if exists "work_runs_select_own" on public.work_runs;
create policy "work_runs_select_own" on public.work_runs
  for select to authenticated using (auth.uid() = user_id);

drop policy if exists "work_runs_select_admin" on public.work_runs;
create policy "work_runs_select_admin" on public.work_runs
  for select to authenticated using (public.is_admin(auth.uid()));


-- ── 2. open the run inside consume_credit ───────────────────────────────────
-- Replacing the whole function rather than adding a second call the app must
-- remember to make: a debit and its lifecycle row must commit together, or the
-- gap between them is exactly the failure this migration exists to detect.
--
-- Body is unchanged from 20260801020000 except for the marked INSERT.

create or replace function public.consume_credit(
  p_user_id    uuid,
  p_event_type text,
  p_metadata   jsonb default '{}'::jsonb,
  p_request_id text  default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_remaining integer;
  v_event_id  uuid;
  v_disabled  boolean;
  v_metadata  jsonb;
begin
  if p_event_type not in ('scan', 'deep_analyze') then
    return jsonb_build_object('ok', false, 'reason', 'invalid_event_type',
                              'remaining', null, 'event_id', null);
  end if;

  v_metadata := coalesce(p_metadata, '{}'::jsonb);
  if p_request_id is not null then
    v_metadata := v_metadata || jsonb_build_object('request_id', p_request_id);
  end if;

  -- The exception block MUST enclose the UPDATE, not just the INSERT.
  -- A plpgsql subtransaction begins at this block's `begin`; anything executed
  -- before it belongs to the parent and is NOT rolled back by the handler. With
  -- the UPDATE outside, a duplicate request would commit the debit with no
  -- ledger row and return normally -- a silent credit leak, strictly worse than
  -- letting 23505 abort the whole transaction.
  -- `profiles` has no unique constraint the UPDATE could violate (the PK is
  -- user_id and is never modified), so widening the block cannot mis-catch.
  begin
    if p_event_type = 'scan' then
      update public.profiles
         set scan_credits = scan_credits - 1
       where user_id = p_user_id
         and disabled = false
         and scan_credits > 0
      returning scan_credits into v_remaining;
    else
      update public.profiles
         set deep_credits = deep_credits - 1
       where user_id = p_user_id
         and disabled = false
         and deep_credits > 0
      returning deep_credits into v_remaining;
    end if;

    -- Nothing was debited. Work out why so the caller can say something useful.
    -- (Returning from inside the block exits normally: the subtransaction is
    -- released, not rolled back. Nothing to undo on these paths anyway.)
    if v_remaining is null then
      select disabled into v_disabled
        from public.profiles where user_id = p_user_id;

      if not found then
        return jsonb_build_object('ok', false, 'reason', 'profile_not_found',
                                  'remaining', null, 'event_id', null);
      elsif v_disabled then
        return jsonb_build_object('ok', false, 'reason', 'account_disabled',
                                  'remaining', null, 'event_id', null);
      else
        return jsonb_build_object('ok', false, 'remaining', 0, 'event_id', null,
          'reason', case when p_event_type = 'scan' then 'no_scan_credits'
                         else 'no_deep_credits' end);
      end if;
    end if;

    insert into public.usage_events
      (user_id, event_type, cost_scan_credits, cost_deep_credits, metadata)
    values
      (p_user_id,
       p_event_type,
       case when p_event_type = 'scan' then 1 else 0 end,
       case when p_event_type = 'scan' then 0 else 1 end,
       v_metadata)
    returning id into v_event_id;

    -- >>> THE ONLY ADDITION vs 20260801020000. Same transaction as the debit,
    -- so there is no window in which a credit is spent with no record that work
    -- began. If this row is missing, the reaper cannot see the orphan.
    insert into public.work_runs (user_id, event_id, kind)
    values (p_user_id, v_event_id, p_event_type);

  exception
    when unique_violation then
      -- ONLY our own idempotency key may be swallowed here. usage_events also
      -- carries usage_events_refund_of_key, which is driven by caller-supplied
      -- metadata -- and reporting success for a conflict we did not cause would
      -- hand the caller a free action against no debit. Anything else re-raises.
      if v_metadata ->> 'request_id' is null then
        raise;
      end if;

      -- Key on v_metadata, NOT p_request_id. A caller may embed request_id
      -- inside p_metadata rather than passing the parameter; the partial index
      -- still applies to that row, but p_request_id would be NULL here and
      -- match nothing -- returning ok=true with a null event_id, i.e. a second
      -- action delivered against a single debit.
      select id into v_event_id
        from public.usage_events
       where user_id = p_user_id
         and event_type = p_event_type
         and metadata ? 'request_id'
         and metadata ->> 'request_id' = v_metadata ->> 'request_id';

      -- Could not find the original: the conflict was not ours (or the snapshot
      -- predates it under an isolation level above READ COMMITTED). Never
      -- fabricate success -- let it propagate.
      if v_event_id is null then
        raise;
      end if;

      select case when p_event_type = 'scan' then scan_credits else deep_credits end
        into v_remaining
        from public.profiles where user_id = p_user_id;

      return jsonb_build_object('ok', true, 'reason', 'duplicate_request',
                                'remaining', v_remaining, 'event_id', v_event_id);
  end;

  return jsonb_build_object('ok', true, 'reason', '',
                            'remaining', v_remaining, 'event_id', v_event_id);
end;
$$;


-- ── 3. close the run ────────────────────────────────────────────────────────
-- Called by the app where it currently sets its _delivered flag. Idempotent and
-- never fatal: closing an already-closed run is a no-op, because the caller is
-- often in a finally block and must not raise from there.

create or replace function public.complete_work(
  p_event_id uuid,
  p_status   text default 'completed',
  p_detail   text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
begin
  if p_status not in ('completed', 'failed') then
    return jsonb_build_object('ok', false, 'reason', 'invalid_status');
  end if;

  update public.work_runs
     set status      = p_status,
         finished_at = now(),
         detail      = left(coalesce(p_detail, ''), 300)
   where event_id = p_event_id
     and status = 'running'
  returning status into v_status;

  if v_status is null then
    -- Either unknown, or already closed. Both are fine: a retry closing an
    -- already-completed run must not error, and the reaper closing a run the
    -- app then also closes must not error either.
    return jsonb_build_object('ok', true, 'reason', 'not_running');
  end if;

  return jsonb_build_object('ok', true, 'reason', '', 'status', v_status);
end;
$$;


-- ── 4. the reaper ───────────────────────────────────────────────────────────
-- Refunds work that started, was charged, and never finished. This is the only
-- mechanism that can recover from a process death, because it runs in a
-- DIFFERENT process.
--
-- p_older_than must comfortably exceed the longest legitimate run. A Discovery
-- scan paginates up to 5 pages of X results and scores them; a deep analysis is
-- similar. Minutes, not tens of minutes. The 15-minute default is deliberately
-- far outside that: refunding work that is merely slow would hand back credits
-- for results the user is about to receive.

create or replace function public.reap_orphaned_work(
  p_older_than interval default '15 minutes'
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r          record;
  v_refund   jsonb;
  v_reaped   integer := 0;
  v_failed   integer := 0;
  v_details  jsonb := '[]'::jsonb;
begin
  for r in
    select w.id, w.user_id, w.event_id, w.kind, w.started_at
      from public.work_runs w
     where w.status = 'running'
       and w.started_at < now() - p_older_than
     order by w.started_at
     -- Skip rows another reaper instance already holds, rather than queueing
     -- behind it. Two overlapping runs then do disjoint work instead of
     -- double-refunding or blocking.
     for update skip locked
  loop
    v_refund := public.refund_credit(
      r.user_id, r.kind, r.event_id,
      'orphaned: no completion after ' || p_older_than::text
    );

    if coalesce((v_refund ->> 'ok')::boolean, false) then
      update public.work_runs
         set status = 'orphaned', finished_at = now(),
             detail = 'auto-refunded by reaper: ' || coalesce(v_refund ->> 'reason', '')
       where id = r.id;
      v_reaped := v_reaped + 1;
    else
      -- Leave status='running' so the next pass retries. Marking it closed
      -- would strand a user who was charged and never refunded -- the exact
      -- outcome this function exists to prevent.
      v_failed := v_failed + 1;
      v_details := v_details || jsonb_build_object(
        'event_id', r.event_id, 'reason', v_refund ->> 'reason');
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true, 'reaped', v_reaped, 'failed', v_failed,
    'failures', v_details, 'older_than', p_older_than::text
  );
end;
$$;


-- ── 5. ownership + grants ───────────────────────────────────────────────────

do $$
declare v_owner name;
begin
  select pg_get_userbyid(relowner) into v_owner
    from pg_class where oid = 'public.profiles'::regclass;

  execute format('alter table public.work_runs owner to %I', v_owner);
  execute format('alter function public.consume_credit(uuid,text,jsonb,text) owner to %I', v_owner);
  execute format('alter function public.complete_work(uuid,text,text) owner to %I', v_owner);
  execute format('alter function public.reap_orphaned_work(interval) owner to %I', v_owner);

  raise notice 'work_runs + complete_work + reap_orphaned_work owned by %', v_owner;
end $$;

grant select on public.work_runs to anon, authenticated, service_role;

-- service_role only, same as every other credit function. complete_work could
-- otherwise be called by a client to mark abandoned work finished and defeat
-- the reaper; reap_orphaned_work issues refunds.
revoke all on function public.complete_work(uuid,text,text)   from public, anon, authenticated;
revoke all on function public.reap_orphaned_work(interval)    from public, anon, authenticated;
grant execute on function public.complete_work(uuid,text,text) to service_role;
grant execute on function public.reap_orphaned_work(interval)  to service_role;

commit;


-- ── AFTER THE APP DEPLOY: schedule the reaper ───────────────────────────────
-- Do NOT run this until the app is calling complete_work(), or the first pass
-- will refund every run ever recorded. Verify first:
--
--   select status, count(*) from public.work_runs group by status;
--
-- Expect mostly 'completed'. Then schedule reap_orphaned_work() every 5 minutes
-- from the worker (Phase 1 Track C) or any external scheduler.
--
-- Dry run at any time -- it only reaps rows older than the interval:
--   select public.reap_orphaned_work('15 minutes');


-- ── ROLLBACK ────────────────────────────────────────────────────────────────
--   Restore consume_credit from 20260801020000 FIRST (it references work_runs),
--   then:
--     drop function if exists public.reap_orphaned_work(interval);
--     drop function if exists public.complete_work(uuid,text,text);
--     drop table if exists public.work_runs;
