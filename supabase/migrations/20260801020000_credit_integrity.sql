-- Phase 0 · Credit integrity: close both RLS holes, make debits atomic, add refunds.
--
-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  DEPLOY ORDER -- THIS MIGRATION IS NOT SAFE OUT OF SEQUENCE              ║
-- ║                                                                          ║
-- ║   1. Deploy payments_api with the clamp in _apply_credit_delta           ║
-- ║      (new_scan = max(0, ...)). Section 2 below adds a non-negative       ║
-- ║      CHECK that the current unclamped chargeback path VIOLATES, and      ║
-- ║      the webhook swallows the error and returns 200 to Stripe -- so a    ║
-- ║      revocation would be lost silently.                                  ║
-- ║   2. Apply migration 20260801010000_purchases.sql                        ║
-- ║   3. Apply THIS migration                                               ║
-- ║   4. Deploy utils/credits.py cut over to consume_credit / refund_credit  ║
-- ║                                                                          ║
-- ║  Between 3 and 4 the old read-modify-write debit path is still live. It  ║
-- ║  CANNOT violate the new CHECK -- utils/credits.py guards on <= 0 and     ║
-- ║  writes read-1 from its own read, so it never produces a negative and    ║
-- ║  nothing will be raised. Do not calibrate for a loud failure. The real   ║
-- ║  exposure in that window is the ORIGINAL double-spend: two tabs both     ║
-- ║  read N and both write N-1 -- one credit charged, two actions            ║
-- ║  delivered, no error, no trace. Keep the gap short; prefer a quiet hour. ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- FOUR DEFECTS, ONE TRUST BOUNDARY
--
--   1. Profiles RLS hole. `profiles_update_own` grants authenticated users
--      UPDATE on their own row with NO column restriction, so any logged-in
--      user with the public anon key can set scan_credits = 9999 on themselves.
--
--   2. Ledger RLS hole. `usage_insert_own` grants authenticated users INSERT on
--      usage_events with only `auth.uid() = user_id` checked -- event_type,
--      cost columns and metadata are all caller-controlled. Left in place, a
--      user could forge refund rows: squatting metadata->>'refund_of' on their
--      own event ids would trip the idempotency guard below and permanently
--      deny them every refund they are owed, unfixably. Closing hole 1 while
--      leaving hole 2 open would make this migration a net negative.
--
--   3. Non-atomic debit. utils/credits.py reads the balance then writes
--      balance-1 in a second round trip. Two tabs racing the same balance both
--      read N and both write N-1: one credit spent, two actions granted. Works
--      today only because there is exactly one process.
--
--   4. No refund path. Credits are debited before any external call, so an X
--      API failure -- including the observed 402 "credits depleted" -- charges
--      the user for an outage with no recourse.
--
-- WHY IT BLOCKS THE MIGRATION
--   Phase 1 adds a worker. Phase 3 runs core-api and Streamlit concurrently by
--   design. The instant more than one process can touch a balance, a two-round-
--   trip debit stops being a race risk and becomes guaranteed corruption.
--
-- SAFETY
--   Dropping the three policies breaks nothing. Verified against the working
--   tree: every profiles UPDATE and every usage_events INSERT in this codebase
--   uses the service-role client, which bypasses RLS (utils/credits.py,
--   pages/Admin.py, payments_api/main.py). The three anon-key callers
--   (utils/profile.py, pages/Home.py, utils/navigation.py) are SELECT-only.
--
-- ROLLBACK
--   See the block at the foot of this file.

begin;

-- Fail fast rather than queue. Every ALTER below takes ACCESS EXCLUSIVE on a
-- hot table, and Postgres queues all later lock requests behind a waiting
-- ACCESS EXCLUSIVE -- so one idle-in-transaction session on `profiles` would
-- block the login path (utils/profile.py) and the credit badge for as long as
-- this waits. The table scans themselves are trivial (2 and 139 rows); the risk
-- is entirely the lock queue. On timeout the whole migration rolls back: re-run.
set local lock_timeout = '3s';
set local statement_timeout = '30s';


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Close the LEDGER hole.  ** Do not reorder below section 3. **
--
--    These two policies let any authenticated user insert arbitrary usage_events
--    rows -- arbitrary event_type, arbitrary cost_* values, arbitrary metadata.
--
--    ORDERING: inside this transaction the order is immaterial -- section 1
--    takes ACCESS EXCLUSIVE on usage_events and holds it to commit, so no other
--    session can write the table between here and section 3 either way. The
--    order is load-bearing anyway for the realistic failure mode: a 400-line
--    script pasted into the SQL editor and run statement-by-statement, or
--    resumed after a lock_timeout abort. Under any non-atomic execution,
--    section 1 before section 3 is the only safe sequence.
--
--    Nothing in the codebase inserts usage_events with the anon client; both
--    writers (utils/credits.py, payments_api/main.py) use the service role.
-- ─────────────────────────────────────────────────────────────────────────────
drop policy if exists "usage_insert_own"   on public.usage_events;
drop policy if exists "usage_insert_admin" on public.usage_events;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Floor the credit columns.
--    REQUIRES the payments_api clamp to be deployed first -- see DEPLOY ORDER.
--    Existing rows are all non-negative, so validation passes.
-- ─────────────────────────────────────────────────────────────────────────────
alter table public.profiles
  drop constraint if exists profiles_credits_non_negative;

alter table public.profiles
  add constraint profiles_credits_non_negative
  check (scan_credits >= 0 and deep_credits >= 0);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Widen the usage_events type list to carry refunds.
--    Refunds are compensating rows with NEGATIVE cost, so the ledger stays
--    append-only and SUM(cost_*) yields net consumption directly.
-- ─────────────────────────────────────────────────────────────────────────────
alter table public.usage_events
  drop constraint if exists usage_events_event_type_check;

-- 'purchase' exists so grant/revocation rows written by payments_api are
-- DISTINGUISHABLE FROM DEBITS. They are currently written as event_type='scan'
-- with a NEGATIVE cost, which makes them indistinguishable from a real scan
-- debit -- so refund_credit would happily "refund" a purchase and mint a credit
-- that was never paid for, and reconciliation could not see it because both
-- rows carry the same negative cost. payments_api must be deployed writing
-- 'purchase' in the same step (see DEPLOY ORDER).
alter table public.usage_events
  add constraint usage_events_event_type_check
  check (event_type in ('scan', 'deep_analyze',
                        'scan_refund', 'deep_analyze_refund',
                        'purchase'));

-- NOTE on the two unique indexes below: `create unique index if not exists`
-- matches on NAME ONLY. An index of the same name with a DIFFERENT definition
-- (e.g. left over from an earlier draft of this migration on a staging project)
-- would silently survive and the migration would report success. Drop first, to
-- match the discipline this file already applies to constraints and functions.
drop index if exists public.usage_events_refund_of_key;

-- A given debit may be refunded at most once. Enforced in the database rather
-- than trusted to the caller, so a retried error handler cannot double-refund.
create unique index usage_events_refund_of_key
  on public.usage_events ((metadata ->> 'refund_of'))
  where metadata ? 'refund_of';

-- Same protection for the debit side: a caller-supplied request id makes
-- consume_credit safely retryable.
--
-- CONTRACT: p_request_id must be unique per (user, event_type). A per-action
-- UUID generated at the call site is the intended source. `event_type` is part
-- of the key deliberately -- without it, a caller deriving the id from a
-- Streamlit run id would have a scan and a deep_analyze issued in the same
-- rerun collide, and the second, entirely legitimate action would be rejected.
drop index if exists public.usage_events_request_id_key;

create unique index usage_events_request_id_key
  on public.usage_events (user_id, event_type, (metadata ->> 'request_id'))
  where metadata ? 'request_id';

create index if not exists usage_events_user_id_created_at_idx
  on public.usage_events (user_id, created_at desc);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Close the PROFILES hole.
--    Users keep SELECT on their own row. UPDATE is revoked; every credit
--    mutation now goes through the SECURITY DEFINER functions below.
--
--    `profiles_update_admin` is intentionally left in place: it is an admin-only
--    path, unused today (the Admin page uses the service role), and removing it
--    is a separate decision from closing this hole.
-- ─────────────────────────────────────────────────────────────────────────────
drop policy if exists "profiles_update_own" on public.profiles;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. consume_credit() -- atomic debit + ledger row.
--
--    The guard and the decrement are ONE statement, so two concurrent callers
--    cannot both observe the same balance. Under READ COMMITTED the blocked
--    statement re-evaluates its WHERE against the updated tuple, so a balance
--    of 1 yields exactly one success and one 'no_*_credits'.
--
--    p_request_id makes the call idempotent. Each PostgREST RPC commits before
--    the HTTP response is written, so without it a dropped connection plus a
--    retry -- or a Streamlit rerun -- debits twice, and balance and ledger agree
--    perfectly while the user has silently paid twice.
--
--    Returns jsonb, uniform shape on every path:
--      { ok, reason, remaining, event_id }
--    reason on failure: no_scan_credits | no_deep_credits | account_disabled
--                     | profile_not_found | invalid_event_type | duplicate_request
-- ─────────────────────────────────────────────────────────────────────────────
-- The arity changed from 3 to 4 during review. `create or replace` with a
-- different argument list creates a SECOND OVERLOAD rather than replacing the
-- first -- and PostgREST cannot disambiguate two candidates when the extra
-- argument has a default, failing every RPC with PGRST203. Free on a clean
-- database; essential on any environment where an earlier draft was ever run.
drop function if exists public.consume_credit(uuid, text, jsonb);

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


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. refund_credit() -- compensating restore, idempotent per original event.
--
--    Called when a metered action fails after its credit was taken: X API down,
--    inference unreachable, upstream 402.
--
--    p_original_event_id is REQUIRED. It is the idempotency key, and without it
--    every guard here fails open at once -- the duplicate check is skipped, the
--    metadata key is absent, and the partial unique index does not apply -- so
--    a retry loop would mint unlimited credits while the ledger agreed with the
--    balance and reconciliation reported healthy.
--
--    The original event is resolved and its user_id / event_type are checked,
--    so a stale session or a mispaired call cannot refund one user against
--    another's event, or grant a scan credit for a deep_analyze debit.
--
--    Returns jsonb, uniform shape:
--      { ok, reason, remaining, event_id }
--    reason on failure: already_refunded | missing_original_event_id
--                     | original_event_not_found | original_event_mismatch
--                     | profile_not_found | invalid_event_type
-- ─────────────────────────────────────────────────────────────────────────────
-- Same overload hazard as consume_credit: this signature has changed across
-- review rounds, and `create or replace` with a different argument list leaves
-- the old one behind as a second candidate that PostgREST cannot disambiguate.
drop function if exists public.refund_credit(uuid, text, uuid);
drop function if exists public.refund_credit(uuid, text, text);
drop function if exists public.refund_credit(uuid, text);

create or replace function public.refund_credit(
  p_user_id           uuid,
  p_event_type        text,
  p_original_event_id uuid  default null,
  p_reason            text  default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_remaining  integer;
  v_event_id   uuid;
  v_orig_user  uuid;
  v_orig_type  text;
  v_orig_cost  integer;
begin
  if p_event_type not in ('scan', 'deep_analyze') then
    return jsonb_build_object('ok', false, 'reason', 'invalid_event_type',
                              'remaining', null, 'event_id', null);
  end if;

  if p_original_event_id is null then
    return jsonb_build_object('ok', false, 'reason', 'missing_original_event_id',
                              'remaining', null, 'event_id', null);
  end if;

  -- The original must exist and must belong to this user, in this currency.
  select user_id, event_type,
         case when p_event_type = 'scan' then cost_scan_credits
              else cost_deep_credits end
    into v_orig_user, v_orig_type, v_orig_cost
    from public.usage_events where id = p_original_event_id;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'original_event_not_found',
                              'remaining', null, 'event_id', null);
  end if;

  if v_orig_user is distinct from p_user_id or v_orig_type is distinct from p_event_type then
    return jsonb_build_object('ok', false, 'reason', 'original_event_mismatch',
                              'remaining', null, 'event_id', null);
  end if;

  -- The original must actually have been a DEBIT. Matching event_type is not
  -- enough: payments_api writes grant rows with a negative cost, and (before the
  -- 'purchase' type existed) it wrote them as event_type='scan'. Refunding one
  -- of those mints a credit nobody paid for -- and reconciliation cannot see it,
  -- because the grant row and the refund row carry the same negative cost, so
  -- SUM(cost) moves in step with the balance. This is the one failure class the
  -- whole migration exists to make impossible.
  if coalesce(v_orig_cost, 0) <= 0 then
    return jsonb_build_object('ok', false, 'reason', 'original_event_not_a_debit',
                              'remaining', null, 'event_id', null);
  end if;

  -- `metadata ? 'refund_of'` is repeated here so the qual implies the partial
  -- index predicate; without it the planner cannot use usage_events_refund_of_key
  -- and every refund sequentially scans the whole ledger.
  if exists (
       select 1 from public.usage_events
        where metadata ? 'refund_of'
          and metadata ->> 'refund_of' = p_original_event_id::text)
  then
    select case when p_event_type = 'scan' then scan_credits else deep_credits end
      into v_remaining
      from public.profiles where user_id = p_user_id;
    return jsonb_build_object('ok', false, 'reason', 'already_refunded',
                              'remaining', v_remaining, 'event_id', null);
  end if;

  -- Inner block: the EXISTS check above is an unlocked read, so it only closes
  -- the sequential retry. Two concurrent refunds both pass it; the unique index
  -- rejects the loser, and this handler turns that into a graceful return
  -- instead of an HTTP 500. The subtransaction rollback also undoes the +1.
  -- NOTE: deliberately NO `disabled` guard here, unlike consume_credit. A
  -- suspended account is still owed a refund for work that was charged and
  -- not delivered. Do not 'fix' this into symmetry.
  begin
    if p_event_type = 'scan' then
      update public.profiles
         set scan_credits = scan_credits + 1
       where user_id = p_user_id
      returning scan_credits into v_remaining;
    else
      update public.profiles
         set deep_credits = deep_credits + 1
       where user_id = p_user_id
      returning deep_credits into v_remaining;
    end if;

    if v_remaining is null then
      return jsonb_build_object('ok', false, 'reason', 'profile_not_found',
                                'remaining', null, 'event_id', null);
    end if;

    insert into public.usage_events
      (user_id, event_type, cost_scan_credits, cost_deep_credits, metadata)
    values
      (p_user_id,
       p_event_type || '_refund',
       case when p_event_type = 'scan' then -1 else 0 end,
       case when p_event_type = 'scan' then 0 else -1 end,
       jsonb_strip_nulls(jsonb_build_object(
         'refund_of', p_original_event_id::text,
         'reason',    p_reason)))
    returning id into v_event_id;
  exception
    when unique_violation then
      -- A concurrent refund won the race. The +1 above is rolled back with the
      -- failed insert. Read the settled balance so the caller can still render
      -- a credit badge rather than a null.
      select case when p_event_type = 'scan' then scan_credits else deep_credits end
        into v_remaining
        from public.profiles where user_id = p_user_id;
      return jsonb_build_object('ok', false, 'reason', 'already_refunded',
                                'remaining', v_remaining, 'event_id', null);
  end;

  return jsonb_build_object('ok', true, 'reason', '',
                            'remaining', v_remaining, 'event_id', v_event_id);
end;
$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Ownership and least privilege.
--
--    Ownership is stated explicitly rather than left to whichever role ran the
--    script: the RLS bypass these functions rely on comes from the owner being
--    exempt from RLS on tables it owns. NOTE: running
--    `alter table public.profiles force row level security` would break both
--    functions, because FORCE removes the owner exemption.
--
--    Both are SECURITY DEFINER, so the GRANT is the ONLY access control on
--    them -- and refund_credit() ADDS credits. Exposing it to `authenticated`
--    would be a strictly worse hole than the two this migration closes.
-- ─────────────────────────────────────────────────────────────────────────────
-- Bind the function owner to whoever ACTUALLY owns the tables, rather than
-- assuming `postgres`. Hard-coding it is not merely fragile -- if the tables are
-- owned by supabase_admin, `owner to postgres` would CREATE the exact mismatch
-- this section exists to avoid, and the migration would manufacture the outage
-- it warns about. Fail loudly if the two tables disagree, because then no single
-- owner can grant the exemption for both.
do $$
declare
  v_prof_owner name;
  v_ue_owner   name;
begin
  select pg_get_userbyid(relowner) into v_prof_owner
    from pg_class where oid = 'public.profiles'::regclass;
  select pg_get_userbyid(relowner) into v_ue_owner
    from pg_class where oid = 'public.usage_events'::regclass;

  if v_prof_owner is distinct from v_ue_owner then
    raise exception
      'Ownership mismatch: public.profiles is owned by %, public.usage_events by %. '
      'These functions bypass RLS through the owner exemption and cannot hold it for '
      'both tables. Reconcile table ownership before applying this migration.',
      v_prof_owner, v_ue_owner;
  end if;

  execute format('alter function public.consume_credit(uuid,text,jsonb,text) owner to %I', v_prof_owner);
  execute format('alter function public.refund_credit(uuid,text,uuid,text) owner to %I',   v_prof_owner);

  raise notice 'credit functions owned by % (matching table owner)', v_prof_owner;
end $$;

revoke all on function public.consume_credit(uuid, text, jsonb, text)
  from public, anon, authenticated;
revoke all on function public.refund_credit(uuid, text, uuid, text)
  from public, anon, authenticated;

grant execute on function public.consume_credit(uuid, text, jsonb, text) to service_role;
grant execute on function public.refund_credit(uuid, text, uuid, text)   to service_role;

commit;


-- ── verify (run after applying) ──────────────────────────────────────────────
-- -- Owners MUST match, or SECURITY DEFINER silently loses its RLS exemption.
-- -- The exemption comes from the owner being exempt on tables it owns --
-- -- `postgres` on Supabase is NOT a superuser and does NOT carry BYPASSRLS.
-- --
-- -- A mismatch has TWO failure modes, and they look nothing alike:
-- --   * with table GRANTs present (Supabase's default) -> every call silently
-- --     returns 'profile_not_found'. A total outage disguised as a business
-- --     error, and the one you will actually hit.
-- --   * without GRANTs -> a loud ERROR 42501 permission denied for table profiles.
-- -- Alert on BOTH. Watching only for 'profile_not_found' misses the 42501 variant.
-- --
-- -- Section 7 now derives the function owner from the table owner and raises if
-- -- the two tables disagree, so this is a confirmation rather than a gate.
-- -- handle_new_user is included because it is ALSO security definer and inserts
-- -- into profiles: if ITS owner drifts, signup breaks the same way.
-- select 'table' kind, c.relname obj, pg_get_userbyid(c.relowner) owner,
--        c.relforcerowsecurity forced
--   from pg_class c join pg_namespace n on n.oid = c.relnamespace
--  where n.nspname='public' and c.relname in ('profiles','usage_events')
-- union all
-- select 'function', p.proname, pg_get_userbyid(p.proowner), null
--   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--  where n.nspname='public'
--    and p.proname in ('consume_credit','refund_credit','handle_new_user');
-- --   expect: ONE owner across all five rows, and forced = false
--
-- -- both holes closed: expect NO profiles_update_own, NO usage_insert_* rows
-- select tablename, policyname, cmd from pg_policies
--  where schemaname='public' and tablename in ('profiles','usage_events')
--  order by tablename, policyname;
--
-- -- only service_role may execute:
-- select routine_name, grantee, privilege_type
--   from information_schema.routine_privileges
--  where routine_schema='public'
--    and routine_name in ('consume_credit','refund_credit');
--
-- -- indexes exist with the expected definitions (create-if-not-exists silently
-- -- no-ops on a name clash, so check the definition, not just existence):
-- select indexname, indexdef from pg_indexes
--  where schemaname='public' and tablename='usage_events';
--
-- -- round trip on the admin account (substitute the uuid):
-- -- select public.consume_credit('<uid>'::uuid, 'deep_analyze', '{"src":"smoke"}');
-- --   -> note the returned event_id, then:
-- -- select public.refund_credit('<uid>'::uuid, 'deep_analyze', '<event_id>'::uuid, 'smoke');
-- -- select public.refund_credit('<uid>'::uuid, 'deep_analyze', '<event_id>'::uuid, 'again');
-- --   -> expect {"ok": false, "reason": "already_refunded"}
-- -- select public.refund_credit('<uid>'::uuid, 'scan', '<event_id>'::uuid, 'x');
-- --   -> expect {"ok": false, "reason": "original_event_mismatch"}
-- -- Balance must be back where it started. Clean up the smoke rows:
-- -- delete from public.usage_events where metadata->>'src' = 'smoke'
-- --    or metadata->>'reason' in ('smoke','again');


-- ── ROLLBACK ─────────────────────────────────────────────────────────────────
-- begin;
--   drop function if exists public.consume_credit(uuid, text, jsonb, text);
--   drop function if exists public.consume_credit(uuid, text, jsonb);  -- old arity
--   drop function if exists public.refund_credit(uuid, text, uuid, text);
--   drop index  if exists public.usage_events_refund_of_key;
--   drop index  if exists public.usage_events_request_id_key;
--   -- reverting the CHECK fails if any *_refund rows exist -- remove them first:
--   -- delete from public.usage_events where event_type like '%\_refund';
--   alter table public.usage_events drop constraint if exists usage_events_event_type_check;
--   alter table public.usage_events add  constraint usage_events_event_type_check
--     check (event_type in ('scan','deep_analyze'));
--   alter table public.profiles drop constraint if exists profiles_credits_non_negative;
--   -- policies restored verbatim from
--   -- ~/stock-sentinel-backups/<ts>/schema/captured.json :
--   create policy "profiles_update_own" on public.profiles for update
--     to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
--   create policy "usage_insert_own" on public.usage_events for insert
--     to authenticated with check (auth.uid() = user_id);
--   create policy "usage_insert_admin" on public.usage_events for insert
--     to authenticated with check (public.is_admin(auth.uid()));
-- commit;
