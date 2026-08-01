-- Phase 0: make admin credit changes appear in the ledger.
--
-- DEPLOY ORDER
--   1. 20260801010000_purchases.sql          (applied)
--   2. 20260801020000_credit_integrity.sql   (applied)
--   3. THIS FILE
--   4. Deploy pages/Admin.py, which calls admin_adjust_credits() instead of
--      updating public.profiles directly.
--
-- Steps 3 and 4 are safe in either order: this function is additive, and the
-- old direct-UPDATE path keeps working until Admin.py is deployed.
--
-- WHY
--
-- pages/Admin.py sets scan_credits and deep_credits with a service-role UPDATE
-- straight against public.profiles. The service role bypasses RLS, the update
-- succeeds, and nothing is written to public.usage_events. Every credit ever
-- granted from the admin page is therefore invisible to the ledger.
--
-- Measured on production before this migration:
--
--   admin account   balance  9 scan /  5 deep
--                   ledger   sum(cost_scan)=66  sum(cost_deep)=40
--
-- Reading the ledger alone, that account has spent 66 scans and holds -66.
-- It actually holds 9. The ~75/45 gap is grants that were never recorded.
--
-- That gap is not cosmetic. It means:
--   * no reconciliation invariant can be written -- there is no expression over
--     usage_events and purchases that should equal profiles.scan_credits;
--   * a credit that appears from nowhere is indistinguishable from a bug in
--     consume_credit, so the ledger cannot be used to investigate one;
--   * there is no record of WHO changed a balance, WHEN, or from what to what.
--
-- After this migration every path that moves a credit -- debit, refund,
-- purchase, admin adjustment -- writes a row, and balance movement is fully
-- explained by the ledger.

set local lock_timeout = '3s';

-- ── 1. allow the new event type ─────────────────────────────────────────────
-- 'adjustment' rather than reusing 'purchase': a purchase is money changing
-- hands and belongs in revenue reconciliation against public.purchases; an
-- admin grant is not. Conflating them would make purchase totals lie.
--
-- Widening a CHECK is safe here -- no existing row can violate a superset --
-- and the INSERT policies on usage_events were dropped in migration 2, so no
-- client can forge a row of the new type.

alter table public.usage_events
  drop constraint if exists usage_events_event_type_check;

alter table public.usage_events
  add constraint usage_events_event_type_check
  check (event_type in ('scan', 'deep_analyze',
                        'scan_refund', 'deep_analyze_refund',
                        'purchase', 'adjustment'));


-- ── 2. admin_adjust_credits ─────────────────────────────────────────────────
-- Sets absolute balances (matching the Admin UI, which shows number inputs, not
-- deltas) and records the movement as a signed cost.
--
-- SIGN CONVENTION, consistent with the rest of the ledger:
--   cost = old - new
--   positive cost = credits removed (same direction as a scan debit)
--   negative cost = credits granted (same direction as a purchase)
-- so for any user:  balance = opening + sum(-cost)

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


-- ── 3. ownership ────────────────────────────────────────────────────────────
-- SECURITY DEFINER bypasses RLS only through the OWNER EXEMPTION -- Supabase's
-- `postgres` is not a superuser and has no BYPASSRLS. The function must
-- therefore be owned by the role that owns the tables. Derive that owner rather
-- than hard-coding `postgres`: hard-coding turns a wrong guess into a failed
-- migration on a database where the owner differs.

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
      'Ownership mismatch: profiles owned by %, usage_events by %. '
      'admin_adjust_credits must be able to write both.', v_prof_owner, v_ue_owner;
  end if;

  execute format(
    'alter function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) owner to %I',
    v_prof_owner
  );

  raise notice 'admin_adjust_credits owned by %', v_prof_owner;
end $$;


-- ── 4. grants ───────────────────────────────────────────────────────────────
-- service_role only. `authenticated` must never reach this: it takes the actor
-- id as an argument, so any logged-in caller who could execute it would simply
-- pass the admin's uuid and mint themselves credits. The actor check above
-- guards against a stolen service key, not against handing out EXECUTE.

revoke all on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)
  from public, anon, authenticated;

grant execute on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)
  to service_role;


-- ── ROLLBACK ────────────────────────────────────────────────────────────────
-- Redeploy the previous pages/Admin.py first, then:
--
--   drop function if exists public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text);
--
--   -- only if no 'adjustment' rows have been written yet; otherwise they would
--   -- violate the narrowed constraint:
--   -- delete from public.usage_events where event_type = 'adjustment';
--   alter table public.usage_events drop constraint if exists usage_events_event_type_check;
--   alter table public.usage_events add  constraint usage_events_event_type_check
--     check (event_type in ('scan','deep_analyze','scan_refund','deep_analyze_refund','purchase'));
