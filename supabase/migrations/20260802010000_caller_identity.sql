-- Phase 1: defence in depth -- verify the CALLER, not just the arguments.
--
-- Every credit function takes the identity it acts on as a PARAMETER and never
-- consults auth.uid(). Security therefore rests entirely on four
-- `grant execute ... to service_role` lines. That is a single point of failure,
-- and it has already failed once: migration 3 shipped to production with its
-- REVOKE/GRANT block accidentally omitted, leaving admin_adjust_credits at
-- Postgres's EXECUTE-TO-PUBLIC default. Any logged-in user could have granted
-- themselves credits and made themselves an admin. It was caught by a review
-- and fixed the same night, but the lesson is that one missing line is the
-- whole defence.
--
-- After this, a leaked grant is survivable rather than total: a signed-in
-- caller can still only act as themselves.
--
-- WHY auth.uid() IS NULL-TOLERANT
--
-- service_role calls carry no user JWT, so auth.uid() returns NULL and every
-- guard below passes. The app is completely unaffected. The guard bites only
-- when a request arrives with a real user JWT -- i.e. exactly the case that
-- should never happen and currently cannot.
--
-- Each function below is its CURRENT definition, verbatim, plus the guard.
-- Verified as a diff: guard added, nothing else changed.

set local lock_timeout = '3s';

begin;

-- ── PRE-FLIGHT: refuse to apply if the guard would break at runtime ──────────
-- These functions are SECURITY DEFINER, so auth.uid() executes as the FUNCTION
-- OWNER, not the caller. If the owner lacks USAGE on schema auth, every guarded
-- call raises "permission denied for schema auth" -- meaning every scan, every
-- deep analysis and every Stripe grant fails. That is a worse outcome than the
-- privilege-escalation risk this migration exists to reduce.
--
-- Caught by the test suite before this reached production: the fixture had not
-- granted it either, and the very first guarded call failed.
do $$
declare v_owner name;
begin
  select pg_get_userbyid(relowner) into v_owner
    from pg_class where oid = 'public.profiles'::regclass;

  if not has_schema_privilege(v_owner, 'auth', 'USAGE') then
    raise exception
      'Owner % lacks USAGE on schema auth. auth.uid() inside a SECURITY DEFINER '
      'function would fail at runtime and break every credit operation. '
      'Run: grant usage on schema auth to %I;  then re-apply.', v_owner, v_owner;
  end if;

  raise notice 'pre-flight OK: % can read schema auth', v_owner;
end $$;


-- ── consume_credit ─────────────────────────────────────────────────
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
  -- CALLER IDENTITY. a signed-in caller may only spend their OWN credits.
  -- auth.uid() is NULL for service_role (no JWT), so the app is unaffected.
  -- It is non-NULL only when a signed-in user reached this function directly,
  -- which today requires EXECUTE they do not have -- but that grant is the ONLY
  -- control, and it has already been omitted once in production (migration 3).
  -- This makes a leaked grant survivable instead of total.
  if auth.uid() is not null and auth.uid() <> p_user_id then
    return jsonb_build_object('ok', false, 'reason', 'caller_mismatch',
                              'remaining', null, 'event_id', null);
  end if;
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

-- ── refund_credit ─────────────────────────────────────────────────
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
  -- CALLER IDENTITY. refund_credit ADDS credits -- impersonation here mints them.
  -- auth.uid() is NULL for service_role (no JWT), so the app is unaffected.
  -- It is non-NULL only when a signed-in user reached this function directly,
  -- which today requires EXECUTE they do not have -- but that grant is the ONLY
  -- control, and it has already been omitted once in production (migration 3).
  -- This makes a leaked grant survivable instead of total.
  if auth.uid() is not null and auth.uid() <> p_user_id then
    return jsonb_build_object('ok', false, 'reason', 'caller_mismatch',
                              'remaining', null, 'event_id', null);
  end if;
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

-- ── admin_adjust_credits ─────────────────────────────────────────────────
create or replace function public.admin_adjust_credits(
  p_actor_id     uuid,
  p_user_id      uuid,
  p_scan_credits integer,
  p_deep_credits integer,
  p_disabled     boolean,
  p_role         text,
  p_reason       text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_old_scan     integer;
  v_old_deep     integer;
  v_old_disabled boolean;
  v_old_role     text;
  v_d_scan       integer;
  v_d_deep       integer;
  v_event_id     uuid;
begin
  -- CALLER IDENTITY. the actor is an ARGUMENT; without this, knowing an admin's uuid is enough.
  -- auth.uid() is NULL for service_role (no JWT), so the app is unaffected.
  -- It is non-NULL only when a signed-in user reached this function directly,
  -- which today requires EXECUTE they do not have -- but that grant is the ONLY
  -- control, and it has already been omitted once in production (migration 3).
  -- This makes a leaked grant survivable instead of total.
  if auth.uid() is not null and auth.uid() <> p_actor_id then
    return jsonb_build_object('ok', false, 'reason', 'caller_mismatch');
  end if;
  -- Validate before touching anything. The balance CHECK added in migration 2
  -- would catch negatives anyway, but as a constraint violation rather than a
  -- reason code the UI can render.
  if p_scan_credits is null or p_deep_credits is null
     or p_scan_credits < 0 or p_deep_credits < 0 then
    return jsonb_build_object('ok', false, 'reason', 'invalid_credits');
  end if;

  if p_role is null or p_role not in ('user', 'admin') then
    return jsonb_build_object('ok', false, 'reason', 'invalid_role');
  end if;

  -- The actor must be an enabled admin IN THE DATABASE. Admin.py gates on
  -- ADMIN_EMAIL from Streamlit secrets, which is a different fact living in a
  -- different system: anything holding the service-role key can call this
  -- function without ever passing that gate. Checking here means the rule is
  -- enforced where the data is, not only where the UI is.
  if not exists (
    select 1 from public.profiles
     where user_id = p_actor_id and role = 'admin' and disabled = false
  ) then
    return jsonb_build_object('ok', false, 'reason', 'not_admin');
  end if;

  -- NO SELF-LOCKOUT. The check above requires the actor to be an enabled admin,
  -- which makes demoting or disabling yourself a one-way door: the very next
  -- call returns not_admin, and the admin page can no longer undo the change
  -- that caused it. Recovery would mean hand-editing profiles in the SQL
  -- editor. Refuse the two moves that create that state; an admin can still
  -- demote or disable ANOTHER account, and can still adjust their own credits.
  if p_actor_id = p_user_id and p_role <> 'admin' then
    return jsonb_build_object('ok', false, 'reason', 'cannot_demote_self');
  end if;

  if p_actor_id = p_user_id and coalesce(p_disabled, false) then
    return jsonb_build_object('ok', false, 'reason', 'cannot_disable_self');
  end if;

  -- Lock the target row for the rest of the transaction so a concurrent
  -- consume_credit cannot slip between the read and the write and get its
  -- debit silently overwritten by this absolute assignment.
  select scan_credits, deep_credits, disabled, role
    into v_old_scan, v_old_deep, v_old_disabled, v_old_role
    from public.profiles
   where user_id = p_user_id
     for update;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'profile_not_found');
  end if;

  v_d_scan := v_old_scan - p_scan_credits;
  v_d_deep := v_old_deep - p_deep_credits;

  update public.profiles
     set scan_credits = p_scan_credits,
         deep_credits = p_deep_credits,
         disabled     = coalesce(p_disabled, disabled),
         role         = p_role
   where user_id = p_user_id;

  -- Only write a ledger row when credits actually moved. A save that changes
  -- only role or disabled is an account change, not a credit movement, and a
  -- zero-cost row would be noise in a ledger whose whole purpose is explaining
  -- balance. This also makes a double-clicked Save naturally idempotent: the
  -- second call computes a zero delta because the values are already set.
  if v_d_scan <> 0 or v_d_deep <> 0 then
    insert into public.usage_events (
      user_id, event_type, cost_scan_credits, cost_deep_credits, metadata
    )
    values (
      p_user_id,
      'adjustment',
      v_d_scan,
      v_d_deep,
      jsonb_build_object(
        'actor_id',      p_actor_id,
        'reason',        left(coalesce(p_reason, 'admin adjustment'), 200),
        'previous_scan', v_old_scan,
        'previous_deep', v_old_deep,
        'new_scan',      p_scan_credits,
        'new_deep',      p_deep_credits
      )
    )
    returning id into v_event_id;
  end if;

  return jsonb_build_object(
    'ok',            true,
    'event_id',      v_event_id,          -- null when only role/disabled changed
    'scan_credits',  p_scan_credits,
    'deep_credits',  p_deep_credits,
    'delta_scan',    -v_d_scan,           -- signed as the balance moved
    'delta_deep',    -v_d_deep,
    'role_changed',     (v_old_role is distinct from p_role),
    'disabled_changed', (v_old_disabled is distinct from coalesce(p_disabled, v_old_disabled))
  );
end;
$$;

-- ── grant_credits ─────────────────────────────────────────────────
create or replace function public.grant_credits(
  p_user_id    uuid,
  p_scan_delta integer,
  p_deep_delta integer,
  p_reason     text,
  p_request_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_cur_scan     integer;
  v_cur_deep     integer;
  v_new_scan     integer;
  v_new_deep     integer;
  v_applied_scan integer;
  v_applied_deep integer;
  v_event_id     uuid;
begin
  -- CALLER IDENTITY. no end user ever grants credits -- only Stripe webhooks, via service_role.
  -- auth.uid() is NULL for service_role (no JWT), so the app is unaffected.
  -- It is non-NULL only when a signed-in user reached this function directly,
  -- which today requires EXECUTE they do not have -- but that grant is the ONLY
  -- control, and it has already been omitted once in production (migration 3).
  -- This makes a leaked grant survivable instead of total.
  if auth.uid() is not null then
    return jsonb_build_object('ok', false, 'reason', 'caller_not_permitted');
  end if;
  -- An idempotency key is MANDATORY here, unlike consume_credit where a missing
  -- one merely loses retry safety. Stripe retries a failed webhook for ~3 days;
  -- without a key every retry would grant again.
  if p_request_id is null or length(trim(p_request_id)) = 0 then
    return jsonb_build_object('ok', false, 'reason', 'missing_request_id');
  end if;

  if p_scan_delta is null or p_deep_delta is null then
    return jsonb_build_object('ok', false, 'reason', 'invalid_delta');
  end if;

  -- Lock for the rest of the transaction. This is the whole point: it closes
  -- the window that the select-then-update in payments_api left open.
  select scan_credits, deep_credits
    into v_cur_scan, v_cur_deep
    from public.profiles
   where user_id = p_user_id
     for update;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'profile_not_found');
  end if;

  -- Clamp at zero: a revocation cannot take a user negative, and
  -- profiles_credits_non_negative would reject it as a constraint violation
  -- rather than something the caller can report.
  v_new_scan := greatest(0, v_cur_scan + p_scan_delta);
  v_new_deep := greatest(0, v_cur_deep + p_deep_delta);

  -- Record what was APPLIED, not what was REQUESTED. Clamping means those can
  -- differ, and writing the requested value would put a number in the ledger
  -- that the balance never moved by -- the precise way a reconciliation
  -- invariant rots.
  v_applied_scan := v_new_scan - v_cur_scan;
  v_applied_deep := v_new_deep - v_cur_deep;

  begin
    update public.profiles
       set scan_credits = v_new_scan,
           deep_credits = v_new_deep
     where user_id = p_user_id;

    insert into public.usage_events (
      user_id, event_type, cost_scan_credits, cost_deep_credits, metadata
    )
    values (
      p_user_id,
      'purchase',
      -v_applied_scan,
      -v_applied_deep,
      jsonb_build_object(
        'request_id',     p_request_id,
        'reason',         left(coalesce(p_reason, 'stripe'), 200),
        'requested_scan', p_scan_delta,
        'requested_deep', p_deep_delta,
        'applied_scan',   v_applied_scan,
        'applied_deep',   v_applied_deep,
        'clamped',        (v_applied_scan <> p_scan_delta or v_applied_deep <> p_deep_delta)
      )
    )
    returning id into v_event_id;

  exception
    -- The UPDATE is INSIDE this block deliberately. In 20260801020000 an
    -- earlier draft enclosed only the INSERT, so a duplicate request committed
    -- the balance change with no ledger row. Here that would mean a replayed
    -- webhook granting credits invisibly.
    when unique_violation then
      select id into v_event_id
        from public.usage_events
       where user_id = p_user_id
         and event_type = 'purchase'
         and metadata ? 'request_id'
         and metadata ->> 'request_id' = p_request_id;

      if v_event_id is null then
        raise;  -- some OTHER unique violation; do not swallow it
      end if;

      select scan_credits, deep_credits into v_new_scan, v_new_deep
        from public.profiles where user_id = p_user_id;

      return jsonb_build_object(
        'ok', true, 'reason', 'duplicate_request',
        'event_id', v_event_id,
        'applied_scan', 0, 'applied_deep', 0,
        'scan_credits', v_new_scan, 'deep_credits', v_new_deep
      );
  end;

  return jsonb_build_object(
    'ok', true, 'reason', '',
    'event_id', v_event_id,
    'applied_scan', v_applied_scan,
    'applied_deep', v_applied_deep,
    'scan_credits', v_new_scan,
    'deep_credits', v_new_deep,
    'clamped', (v_applied_scan <> p_scan_delta or v_applied_deep <> p_deep_delta)
  );
end;
$$;

-- ── ownership + grants (unchanged, restated so a partial apply cannot strip them)
do $$
declare v_owner name;
begin
  select pg_get_userbyid(relowner) into v_owner
    from pg_class where oid = 'public.profiles'::regclass;
  execute format('alter function public.consume_credit(uuid,text,jsonb,text) owner to %I', v_owner);
  execute format('alter function public.refund_credit(uuid,text,uuid,text) owner to %I', v_owner);
  execute format('alter function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) owner to %I', v_owner);
  execute format('alter function public.grant_credits(uuid,integer,integer,text,text) owner to %I', v_owner);
  raise notice 'credit functions owned by %', v_owner;
end $$;

revoke all on function public.consume_credit(uuid,text,jsonb,text) from public, anon, authenticated;
grant execute on function public.consume_credit(uuid,text,jsonb,text) to service_role;
revoke all on function public.refund_credit(uuid,text,uuid,text) from public, anon, authenticated;
grant execute on function public.refund_credit(uuid,text,uuid,text) to service_role;
revoke all on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) from public, anon, authenticated;
grant execute on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) to service_role;
revoke all on function public.grant_credits(uuid,integer,integer,text,text) from public, anon, authenticated;
grant execute on function public.grant_credits(uuid,integer,integer,text,text) to service_role;

commit;
