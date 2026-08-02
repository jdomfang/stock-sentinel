-- Phase 0: make the money-IN path as atomic as the money-OUT path.
--
-- DEPLOY ORDER
--   1..4  (applied)
--   5. THIS FILE
--   6. Deploy payments_api, which calls grant_credits() instead of doing its
--      own select-then-update.
--
-- Order matters here: step 6 without step 5 makes every webhook grant fail.
-- The function is additive, so 5 without 6 is harmless.
--
-- WHY
--
-- 20260801020000 rewrote consume_credit so the guard and the decrement are a
-- single statement, because a read-then-write let two browser tabs both read N
-- and both write N-1. payments_api._apply_credit_delta was left on exactly that
-- pattern -- on the path where the value being written was paid for:
--
--     resp = sb.table("profiles").select("scan_credits,deep_credits")...
--     cur_scan = int(data.get("scan_credits") or 0)
--     ...
--     sb.table("profiles").update({"scan_credits": new_scan, ...})
--
-- Two grants racing (two packs bought in quick succession, or a grant
-- overlapping a revocation) both read 3 and both write 4: one paid grant is
-- lost. Worse, BOTH write an audit row, so usage_events records two grants that
-- the balance does not reflect -- permanently breaking the
-- balance = -sum(cost) invariant that 20260801040000 just established.
--
-- A consume_credit landing between the read and the write is silently
-- overwritten too, handing the user back a credit they had already spent.
--
-- This is currently unreachable: Stripe is in test mode and zero purchases and
-- zero webhook events have ever been processed. It is fixed now because the
-- migration never rewrites payments -- it stays the existing Railway service
-- through every phase -- so "we will clean it up later" has no later.

set local lock_timeout = '3s';

begin;

-- ── grant_credits ───────────────────────────────────────────────────────────
-- Applies a signed delta, clamped at zero, and records what was ACTUALLY
-- applied. Used for both directions: a purchase grants (positive delta) and a
-- refund or dispute revokes (negative delta).
--
-- SIGN CONVENTION, matching the rest of the ledger:
--   cost = -applied_delta
--   a grant is negative cost (like the existing 'purchase' rows)
--   a revocation is positive cost (like a debit)

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


-- ── ownership ───────────────────────────────────────────────────────────────
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
    raise exception 'Ownership mismatch: profiles %, usage_events %', v_prof_owner, v_ue_owner;
  end if;

  execute format(
    'alter function public.grant_credits(uuid,integer,integer,text,text) owner to %I',
    v_prof_owner
  );
  raise notice 'grant_credits owned by %', v_prof_owner;
end $$;


-- ── grants ──────────────────────────────────────────────────────────────────
-- service_role only. This function MINTS credits and takes the target user as
-- an argument, so any role that can execute it can grant itself unlimited
-- credits. Migration 3 shipped with this block accidentally omitted and left
-- EXECUTE at the PUBLIC default -- do not let that happen twice. The whole file
-- is wrapped in begin/commit so a partial apply is impossible.
revoke all on function public.grant_credits(uuid,integer,integer,text,text)
  from public, anon, authenticated;

grant execute on function public.grant_credits(uuid,integer,integer,text,text)
  to service_role;

commit;


-- ── VERIFY (run separately) ─────────────────────────────────────────────────
--   select jsonb_pretty(jsonb_build_object(
--     'owner',   (select pg_get_userbyid(proowner) from pg_proc p join pg_namespace n
--                   on n.oid=p.pronamespace where n.nspname='public' and proname='grant_credits'),
--     'secdef',  (select prosecdef from pg_proc p join pg_namespace n
--                   on n.oid=p.pronamespace where n.nspname='public' and proname='grant_credits'),
--     'grants',  (select coalesce(string_agg(grantee,','),'(none)')
--                   from information_schema.routine_privileges
--                  where routine_name='grant_credits' and privilege_type='EXECUTE')));
--
-- Expect owner=postgres, secdef=true, grants containing service_role and
-- NEITHER anon nor authenticated.


-- ── ROLLBACK ────────────────────────────────────────────────────────────────
-- Redeploy the previous payments_api first, then:
--   drop function if exists public.grant_credits(uuid,integer,integer,text,text);
