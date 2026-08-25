-- One wallet. Two balances become one, and a credit stops naming its purpose.
--
-- WHY
--
-- scan_credits and deep_credits were separate, non-fungible currencies. A user
-- holding 0 scan and 4 deep could not scan, which is a rule nobody asked for
-- and nobody could see. Worse, Discovery renders the SCAN balance beside a
-- table whose per-row button spends a DEEP credit -- so the number on screen
-- was not the number the nearest button charged.
--
-- After this: `profiles.credits`. One scan or one deep analysis costs 1.
--
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DO, and it is the whole design
--
-- It does not touch `usage_events`. Debits keep writing cost_scan_credits or
-- cost_deep_credits exactly as before, chosen by event_type. Those columns stop
-- meaning "which currency" and start meaning "which kind of work" -- which is
-- information nothing else records and which the observation set needs.
--
-- That choice is what makes the reconciliation invariant survive for free.
-- Today 20260801040000 guarantees, per user:
--
--     scan_credits = -sum(cost_scan_credits)
--     deep_credits = -sum(cost_deep_credits)
--
-- Backfilling credits as the SUM makes the merged invariant an algebraic
-- consequence rather than something to re-establish:
--
--     credits = -( sum(cost_scan_credits) + sum(cost_deep_credits) )
--
-- It is true the instant this migration commits, with ZERO historical rows
-- rewritten and no compensating adjustment row. Any non-linear conversion --
-- max(), a scale factor, a floor -- would break that identity and force a
-- per-user fixup, which is exactly where a credit migration corrupts balances.
--
-- The alternative considered and rejected: adding usage_events.cost_credits and
-- writing only that. It fails hard and quietly. refund_credit decides "was the
-- original actually a debit" by reading the per-kind cost column; against rows
-- written before the change that read returns 0, `original_event_not_a_debit`
-- fires, and EVERY refund of a pre-migration debit is refused. The reaper then
-- takes its failure branch, leaves the row 'running', and worker/reap.py exits
-- 1 every five minutes forever -- the identical crash loop 20260824020000 was
-- written to end, re-entered through a door that fix does not cover.
--
-- ROLLBACK, AND ITS SHARP EDGE
--
-- scan_credits and deep_credits are LEFT IN PLACE, frozen at their pre-merge
-- values. Nothing writes them after this migration, so they are a snapshot of
-- the world as it stood the moment this ran -- and `credits = 8` cannot be
-- decomposed back into (5,3) once a single credit is spent.
--
-- DO NOT restore the old function bodies on their own. The snapshot is correct
-- only until the first post-merge event, and it goes stale within one reaper
-- cycle: seed (5,3), spend one, migrate, let the reaper refund it, and the live
-- balance is 8 while the frozen pair says 7. Reverting the functions at that
-- point would silently
--
--   * destroy every credit BOUGHT after the merge -- real money, with the
--     purchases row still asserting the customer paid;
--   * restore every credit SPENT after the merge -- free metered work, and X
--     posts are billed per post returned;
--   * write no compensating ledger row for either, so the reconciliation query
--     in 20260801040000 reports every active user as broken, forever.
--
-- The rollback is TWO statements, and the second is not optional:
--
--     -- 1. restore the four function bodies from 20260802010000_caller_identity.sql
--     -- 2. carry the live balance back into the column they read:
--     update public.profiles set scan_credits = credits, deep_credits = 0;
--
-- That collapses everyone into a single scan-shaped bucket, which is wrong in
-- kind but right in amount -- and right in amount is the property that matters,
-- because it is the one a customer can notice. Expect to hand-correct anyone
-- who was mid-purchase.
--
-- Do not drop the frozen columns until no un-refunded debit and no 'running'
-- work_run predates this migration.
--
-- SIGNATURES ARE UNCHANGED, every one of them. `create or replace` with a
-- different argument list does not replace a function -- it creates a second
-- overload, and PostgREST then fails every RPC with PGRST203, or worse resolves
-- to the old body and keeps debiting a column the UI no longer reads. The
-- semantics move; the shapes do not.

-- ---------------------------------------------------------------- the column
--
-- DEFAULT 0 FIRST, then backfill, then set the real default. The order is the
-- guard, and it is deliberate.
--
-- Keying the backfill on "does the column exist" protects the wrong fact. This
-- project applies SQL by hand in the Supabase editor, so the column can easily
-- exist WITHOUT the backfill having run -- someone adds it in the table editor,
-- or pastes the top of this file and then the whole thing. An existence check
-- then skips the conversion and leaves every balance sitting at the default.
-- With a default of 2 that is invisible: a user holding 40 credits silently
-- becomes 2, and a user holding 0 becomes 2, with no error and no output.
--
-- Opening at 0 makes the same mistake loud. A skipped backfill leaves everyone
-- at zero, which is the first thing anyone notices and nobody mistakes for
-- correct. The signup default is applied afterwards, once the conversion has
-- actually happened.
alter table public.profiles
  add column if not exists credits integer not null default 0;

-- Summation is the only conversion that preserves the action count exactly.
-- Before: at most (scan + deep) metered actions. After: exactly (scan + deep)
-- actions of any mix. Nobody loses an action; the user holding (0 scan, 1 deep)
-- can now choose which kind to spend it on.
--
-- `where credits = 0` is what makes a re-paste safe. A second run finds every
-- converted row already non-zero and skips it, so it cannot restore credits
-- spent since the first run. The rows it does still touch are the ones that
-- genuinely converted to zero -- writing 0 over 0 changes nothing.
update public.profiles
   set credits = coalesce(scan_credits, 0) + coalesce(deep_credits, 0)
 where credits = 0;

-- THE SIGNUP GRANT, applied only now. handle_new_user inserts just
-- (user_id, email), so a new account's free trial is entirely this default --
-- 1 scan + 1 deep before the merge, so 2 after it. Like the defaults it
-- replaces, it is unledgered; routing the welcome grant through grant_credits
-- is separate work, not something to smuggle into a balance migration.
alter table public.profiles
  alter column credits set default 2;

-- Its own floor. The existing profiles_credits_non_negative constrains only the
-- two frozen columns, so without this the clamp reasoning inside grant_credits
-- ("the constraint would reject it") is quietly false for the live balance.
alter table public.profiles
  drop constraint if exists profiles_credits_merged_non_negative;
alter table public.profiles
  add constraint profiles_credits_merged_non_negative check (credits >= 0);

comment on column public.profiles.credits is
  'The live balance. One scan or one deep analysis costs 1. scan_credits and '
  'deep_credits are FROZEN pre-merge snapshots kept for rollback -- nothing '
  'writes them. See 20260824030000_merge_credit_buckets.sql.';

-- ------------------------------------------------------------- purchases
--
-- What a purchase GRANTED, in merged units. payments_api reads this to size a
-- chargeback revocation, and its docstring already warns that deriving the
-- quantity from the amount misfires "if the pack table changed between the
-- purchase and the refund". This migration IS that event: existing rows granted
-- (1,1) under the old pack, new rows grant 2 under the new one. Backfilling
-- here means the authoritative path answers correctly for both eras and the
-- amount-derived fallback is never reached for a pre-merge purchase.
alter table public.purchases
  add column if not exists credits_granted integer not null default 0;

update public.purchases
   set credits_granted = coalesce(scan_credits_granted, 0)
                       + coalesce(deep_credits_granted, 0)
 where credits_granted = 0
   and (coalesce(scan_credits_granted, 0) + coalesce(deep_credits_granted, 0)) > 0;

comment on column public.purchases.credits_granted is
  'Merged credits granted by this purchase. Backfilled from the two legacy '
  'columns, which are retained so a pre-merge chargeback revokes what was '
  'actually granted rather than what today''s pack table would imply.';

-- ============================================================ consume_credit
--
-- Unchanged except for which column moves. p_event_type still gates, still
-- names the ledger row, still becomes work_runs.kind: the WALLET merged, the
-- vocabulary of work did not.
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
  -- ledger row and return normally -- a silent credit leak.
  begin
    update public.profiles
       set credits = credits - 1
     where user_id = p_user_id
       and disabled = false
       and credits > 0
    returning credits into v_remaining;

    -- Nothing was debited. Work out why so the caller can say something useful.
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
        -- ONE reason code now, because there is one wallet. The old
        -- no_scan_credits / no_deep_credits pair described which bucket was
        -- empty, a distinction that no longer exists.
        return jsonb_build_object('ok', false, 'reason', 'no_credits',
                                  'remaining', 0, 'event_id', null);
      end if;
    end if;

    -- STILL PER-KIND, deliberately. See the header: these columns now record
    -- which kind of work was done, and refund_credit's "was this a debit" guard
    -- reads them -- for pre-merge and post-merge rows alike.
    insert into public.usage_events
      (user_id, event_type, cost_scan_credits, cost_deep_credits, metadata)
    values
      (p_user_id,
       p_event_type,
       case when p_event_type = 'scan' then 1 else 0 end,
       case when p_event_type = 'scan' then 0 else 1 end,
       v_metadata)
    returning id into v_event_id;

    -- Same transaction as the debit, so there is no window in which a credit is
    -- spent with no record that work began.
    insert into public.work_runs (user_id, event_id, kind)
    values (p_user_id, v_event_id, p_event_type);

  exception
    when unique_violation then
      -- ONLY our own idempotency key may be swallowed here.
      if v_metadata ->> 'request_id' is null then
        raise;
      end if;

      -- Key on v_metadata, NOT p_request_id: a caller may embed request_id
      -- inside p_metadata, where p_request_id would be NULL and match nothing.
      select id into v_event_id
        from public.usage_events
       where user_id = p_user_id
         and event_type = p_event_type
         and metadata ? 'request_id'
         and metadata ->> 'request_id' = v_metadata ->> 'request_id';

      if v_event_id is null then
        raise;
      end if;

      select credits into v_remaining
        from public.profiles where user_id = p_user_id;

      return jsonb_build_object('ok', true, 'reason', 'duplicate_request',
                                'remaining', v_remaining, 'event_id', v_event_id);
  end;

  return jsonb_build_object('ok', true, 'reason', '',
                            'remaining', v_remaining, 'event_id', v_event_id);
end;
$$;

-- ============================================================= refund_credit
--
-- The +1 lands in the merged wallet. Everything that decides WHETHER to refund
-- is untouched, and that matters more than the arithmetic: the per-kind cost
-- read below is what lets a refund issued today settle a debit recorded before
-- the merge. A job in flight across the cutover refunds correctly.
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

  -- The original must exist and must belong to this user, for this kind of work.
  -- Reading the per-kind cost column is what makes this work identically for
  -- rows written before and after the merge -- see the header.
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
  -- enough: grant rows carry a negative cost, and refunding one mints a credit
  -- nobody paid for -- invisibly, because the grant row and the refund row
  -- carry the same negative cost, so SUM(cost) moves in step with the balance.
  if coalesce(v_orig_cost, 0) <= 0 then
    return jsonb_build_object('ok', false, 'reason', 'original_event_not_a_debit',
                              'remaining', null, 'event_id', null);
  end if;

  -- `metadata ? 'refund_of'` is repeated so the qual implies the partial index
  -- predicate; without it every refund sequentially scans the whole ledger.
  if exists (
       select 1 from public.usage_events
        where metadata ? 'refund_of'
          and metadata ->> 'refund_of' = p_original_event_id::text)
  then
    select credits into v_remaining
      from public.profiles where user_id = p_user_id;
    return jsonb_build_object('ok', false, 'reason', 'already_refunded',
                              'remaining', v_remaining, 'event_id', null);
  end if;

  -- Inner block: the EXISTS above is an unlocked read, so it only closes the
  -- sequential retry. Two concurrent refunds both pass it; the unique index
  -- rejects the loser and this handler turns that into a graceful return.
  -- NOTE: deliberately NO `disabled` guard, unlike consume_credit. A suspended
  -- account is still owed a refund for work charged and not delivered.
  begin
    update public.profiles
       set credits = credits + 1
     where user_id = p_user_id
    returning credits into v_remaining;

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
      -- A concurrent refund won the race. The +1 is rolled back with the failed
      -- insert. Read the settled balance so the caller can still render a badge.
      select credits into v_remaining
        from public.profiles where user_id = p_user_id;
      return jsonb_build_object('ok', false, 'reason', 'already_refunded',
                                'remaining', v_remaining, 'event_id', null);
  end;

  return jsonb_build_object('ok', true, 'reason', '',
                            'remaining', v_remaining, 'event_id', v_event_id);
end;
$$;

-- ============================================================= grant_credits
--
-- THE TWO DELTAS ARE SUMMED, and that is what makes a pre-merge chargeback
-- correct. payments_api revokes using what a purchase actually granted: an old
-- purchase recorded (1,1) and must give back 2; a new one records (10,0) and
-- must give back 10. Summing serves both without the caller knowing which era
-- a purchase belongs to. The signature is kept for the same reason as
-- everywhere else -- changing it creates an overload, not a replacement.
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
  v_delta        integer;
  v_cur          integer;
  v_new          integer;
  v_applied      integer;
  v_event_id     uuid;
begin
  -- CALLER IDENTITY. no end user ever grants credits -- only Stripe webhooks.
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

  v_delta := p_scan_delta + p_deep_delta;

  -- Lock for the rest of the transaction. This closes the window that the
  -- select-then-update in payments_api left open.
  select credits into v_cur
    from public.profiles
   where user_id = p_user_id
     for update;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'profile_not_found');
  end if;

  -- Clamp at zero: a revocation cannot take a user negative, and
  -- profiles_credits_merged_non_negative would reject it as a constraint
  -- violation rather than something the caller can report.
  v_new := greatest(0, v_cur + v_delta);

  -- Record what was APPLIED, not what was REQUESTED. Clamping means those can
  -- differ, and writing the requested value would put a number in the ledger
  -- that the balance never moved by -- the precise way a reconciliation
  -- invariant rots.
  v_applied := v_new - v_cur;

  begin
    update public.profiles
       set credits = v_new
     where user_id = p_user_id;

    -- The whole applied amount is booked to cost_scan_credits and none to
    -- cost_deep_credits. It has to go somewhere for the invariant to hold, and
    -- a purchase is not "scan work" or "deep work" -- event_type='purchase'
    -- already says what it is. Splitting it across both columns would invent a
    -- composition the money never had.
    insert into public.usage_events (
      user_id, event_type, cost_scan_credits, cost_deep_credits, metadata
    )
    values (
      p_user_id,
      'purchase',
      -v_applied,
      0,
      jsonb_build_object(
        'request_id',      p_request_id,
        'reason',          left(coalesce(p_reason, 'stripe'), 200),
        'requested',       v_delta,
        'applied',         v_applied,
        'requested_scan',  p_scan_delta,
        'requested_deep',  p_deep_delta,
        'clamped',         (v_applied <> v_delta)
      )
    )
    returning id into v_event_id;

  exception
    -- The UPDATE is INSIDE this block deliberately: enclosing only the INSERT
    -- lets a duplicate request commit the balance change with no ledger row,
    -- i.e. a replayed webhook granting credits invisibly.
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

      select credits into v_new
        from public.profiles where user_id = p_user_id;

      return jsonb_build_object(
        'ok', true, 'reason', 'duplicate_request',
        'event_id', v_event_id,
        'applied', 0, 'credits', v_new,
        -- Legacy keys, still emitted. payments_api logs applied_scan/applied_deep
        -- and a KeyError on a money path is not worth saving four lines.
        'applied_scan', 0, 'applied_deep', 0,
        'scan_credits', v_new, 'deep_credits', 0
      );
  end;

  return jsonb_build_object(
    'ok', true, 'reason', '',
    'event_id', v_event_id,
    'applied', v_applied,
    'credits', v_new,
    'applied_scan', v_applied, 'applied_deep', 0,
    'scan_credits', v_new, 'deep_credits', 0,
    'clamped', (v_applied <> v_delta)
  );
end;
$$;

-- ====================================================== admin_adjust_credits
--
-- p_scan_credits is now THE MERGED TOTAL, and p_deep_credits must be 0.
--
-- Requiring the zero is the point. The alternative -- quietly summing the two
-- arguments -- turns the Admin page into a balance doubler: it renders two
-- number inputs, and once both default from the same merged number, an admin
-- who opens a user with 8 credits sees 8 and 8, changes only a role, saves,
-- and writes 16. The ledger records it as a deliberate adjustment with an actor
-- and a reason, so reconciliation AGREES and nothing anywhere notices. Doing it
-- twice gives 32.
--
-- A page that has not been updated now fails loudly on the first save instead.
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
  v_old_credits  integer;
  v_old_disabled boolean;
  v_old_role     text;
  v_delta        integer;
  v_event_id     uuid;
begin
  -- CALLER IDENTITY. the actor is an ARGUMENT; without this, knowing an
  -- admin's uuid is enough.
  if auth.uid() is not null and auth.uid() <> p_actor_id then
    return jsonb_build_object('ok', false, 'reason', 'caller_mismatch');
  end if;

  if p_scan_credits is null or p_scan_credits < 0 then
    return jsonb_build_object('ok', false, 'reason', 'invalid_credits');
  end if;

  -- The loud failure described above. A caller still sending a real deep value
  -- is running against the two-bucket contract and must not be guessed at.
  if coalesce(p_deep_credits, 0) <> 0 then
    return jsonb_build_object('ok', false, 'reason', 'deep_credits_retired');
  end if;

  if p_role is null or p_role not in ('user', 'admin') then
    return jsonb_build_object('ok', false, 'reason', 'invalid_role');
  end if;

  -- The actor must be an enabled admin IN THE DATABASE. Admin.py gates on
  -- ADMIN_EMAIL from Streamlit secrets, which is a different fact in a
  -- different system: anything holding the service-role key can call this
  -- without ever passing that gate.
  if not exists (
    select 1 from public.profiles
     where user_id = p_actor_id and role = 'admin' and disabled = false
  ) then
    return jsonb_build_object('ok', false, 'reason', 'not_admin');
  end if;

  -- NO SELF-LOCKOUT. Demoting or disabling yourself is a one-way door: the
  -- very next call returns not_admin and the page can no longer undo it.
  if p_actor_id = p_user_id and p_role <> 'admin' then
    return jsonb_build_object('ok', false, 'reason', 'cannot_demote_self');
  end if;

  if p_actor_id = p_user_id and coalesce(p_disabled, false) then
    return jsonb_build_object('ok', false, 'reason', 'cannot_disable_self');
  end if;

  -- Lock the target row so a concurrent consume_credit cannot slip between the
  -- read and the write and get its debit overwritten by this absolute assignment.
  select credits, disabled, role
    into v_old_credits, v_old_disabled, v_old_role
    from public.profiles
   where user_id = p_user_id
     for update;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'profile_not_found');
  end if;

  v_delta := v_old_credits - p_scan_credits;

  update public.profiles
     set credits  = p_scan_credits,
         disabled = coalesce(p_disabled, disabled),
         role     = p_role
   where user_id = p_user_id;

  -- Only write a ledger row when credits actually moved. A save that changes
  -- only role or disabled is an account change, not a credit movement. This
  -- also makes a double-clicked Save naturally idempotent: the second call
  -- computes a zero delta because the value is already set.
  if v_delta <> 0 then
    insert into public.usage_events (
      user_id, event_type, cost_scan_credits, cost_deep_credits, metadata
    )
    values (
      p_user_id,
      'adjustment',
      v_delta,
      0,
      jsonb_build_object(
        'actor_id',         p_actor_id,
        'reason',           left(coalesce(p_reason, 'admin adjustment'), 200),
        'previous_credits', v_old_credits,
        'new_credits',      p_scan_credits
      )
    )
    returning id into v_event_id;
  end if;

  return jsonb_build_object(
    'ok',           true,
    'event_id',     v_event_id,          -- null when only role/disabled changed
    'credits',      p_scan_credits,
    'delta',        -v_delta,            -- signed as the balance moved
    'scan_credits', p_scan_credits,      -- legacy keys; see grant_credits
    'deep_credits', 0,
    'delta_scan',   -v_delta,
    'delta_deep',   0,
    'role_changed',     (v_old_role is distinct from p_role),
    'disabled_changed', (v_old_disabled is distinct from coalesce(p_disabled, v_old_disabled))
  );
end;
$$;

-- ------------------------------------------------------------- EXECUTE lockdown
--
-- Re-issued after every `create or replace`. Omitting this block once already
-- shipped a credit-minting function at the PUBLIC default, where any logged-in
-- user could grant themselves credits (see 20260802010000). `create or replace`
-- preserves existing grants, so this is belt-and-braces -- but the one time it
-- was left out is the reason it is written out in full every time.
revoke all on function public.consume_credit(uuid,text,jsonb,text) from public, anon, authenticated;
grant execute on function public.consume_credit(uuid,text,jsonb,text) to service_role;
revoke all on function public.refund_credit(uuid,text,uuid,text) from public, anon, authenticated;
grant execute on function public.refund_credit(uuid,text,uuid,text) to service_role;
revoke all on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) from public, anon, authenticated;
grant execute on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) to service_role;
revoke all on function public.grant_credits(uuid,integer,integer,text,text) from public, anon, authenticated;
grant execute on function public.grant_credits(uuid,integer,integer,text,text) to service_role;

comment on function public.consume_credit(uuid,text,jsonb,text) is
  'Debit one merged credit and open a work_runs row, in one transaction. '
  'p_event_type still records WHICH KIND of work, and still becomes '
  'work_runs.kind -- the wallet merged, the vocabulary of work did not. '
  'See 20260824030000_merge_credit_buckets.sql.';
comment on function public.grant_credits(uuid,integer,integer,text,text) is
  'Apply a signed credit delta. The two delta arguments are SUMMED: a pre-merge '
  'purchase revokes (1,1) = 2, a post-merge one revokes (2,0) = 2, and the '
  'caller need not know which era a purchase belongs to.';
comment on function public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text) is
  'p_scan_credits is the merged total; p_deep_credits MUST be 0 and is rejected '
  'as deep_credits_retired otherwise -- a two-input page would otherwise double '
  'a balance on an unrelated save and the ledger would agree with it.';
