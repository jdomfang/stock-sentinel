-- Phase 0 · Create the `purchases` table.
--
-- WHY
--   payments_api/main.py::_record_purchase() has been inserting into
--   public.purchases since launch, but the table was never created. PostgREST
--   returns PGRST205 ("Could not find the table"), the insert is swallowed by a
--   bare `except`, and the service logs "purchases insert skipped/failed
--   (non-fatal)" before carrying on. Every purchase audit record so far has been
--   discarded silently.
--
-- WHY IT BLOCKS THE MIGRATION
--   Phase 3 runs a dual-path window where the old Streamlit credit path and the
--   new API path are both live. The only thing that can prove the ledger stayed
--   intact through that window is a reconciliation of the form
--
--       balance = grants + purchases - consumption + refunds
--
--   Without this table the `purchases` term does not exist at all.
--
-- SAFETY
--   Purely additive. Creates one new table; touches no existing table, policy,
--   or function. Zero impact on the running portal. Safe to apply at any time.
--
-- ROLLBACK
--   drop table if exists public.purchases;

create table if not exists public.purchases (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references auth.users(id) on delete cascade,
  event_id             text,        -- Stripe event id      (evt_...)
  checkout_session_id  text,        -- Stripe Checkout id   (cs_...)
  payment_intent_id    text,        -- Stripe PaymentIntent (pi_...)
  amount_total         integer,     -- minor units (cents)
  currency             text,
  status               text,        -- payment_status / status from the session
  -- How many credits this purchase actually granted. Money (`amount_total`) and
  -- credits are different units: without these two columns there is no way to
  -- express the `purchases` term of the reconciliation invariant, because the
  -- credit counts live only in Stripe's session metadata and were never
  -- persisted anywhere. Adding them now is free -- the table is empty.
  -- payments_api::_record_purchase must be updated to populate them.
  scan_credits_granted integer not null default 0,
  deep_credits_granted integer not null default 0,
  created_at           timestamptz not null default now()
);

comment on table public.purchases is
  'Audit record of completed Stripe purchases. Written only by payments_api via '
  'the service role. Supplies the `purchases` term to credit reconciliation.';

-- One audit row per Stripe event. Idempotency is enforced upstream by
-- stripe_events_processed; this is a second line of defence if that guard is
-- ever bypassed. Partial, so backfilled rows without an event_id do not collide.
create unique index if not exists purchases_event_id_key
  on public.purchases (event_id)
  where event_id is not null;

-- event_id uniqueness only dedupes retries of the SAME Stripe event. A single
-- payment can legitimately emit several distinct events, and each would pass the
-- index above -- silently multiplying the `purchases` term. Key on the payment
-- itself as well.
create unique index if not exists purchases_payment_intent_id_key
  on public.purchases (payment_intent_id)
  where payment_intent_id is not null;

create index if not exists purchases_user_id_created_at_idx
  on public.purchases (user_id, created_at desc);

alter table public.purchases enable row level security;

-- Users may read their own purchase history (billing / receipts view later).
drop policy if exists "purchases_select_own" on public.purchases;
create policy "purchases_select_own"
on public.purchases for select
to authenticated
using (auth.uid() = user_id);

-- Admins may read all purchases.
drop policy if exists "purchases_select_admin" on public.purchases;
create policy "purchases_select_admin"
on public.purchases for select
to authenticated
using (public.is_admin(auth.uid()));

-- NOTE: no INSERT / UPDATE / DELETE policy is defined, deliberately.
-- Writes come exclusively from payments_api using the service role, which
-- bypasses RLS. A user must never be able to fabricate a purchase record.
-- (Contrast usage_events, which shipped with an INSERT policy for authenticated
-- users -- see migration 20260801020000, section 1, which removes it.)

-- KNOWN TRADE-OFF, accepted deliberately: `on delete cascade` means deleting an
-- auth.users row erases that user's purchase history. That is the wrong default
-- for a financial audit table -- a chargeback or tax query after account
-- deletion would have no record. It is kept because (a) `user_id` is NOT NULL so
-- `on delete set null` is not available without a schema change, and (b) there
-- is no archival destination yet. Revisit when account deletion is implemented.


-- ── verify (run after applying) ──────────────────────────────────────────────
-- select count(*) from public.purchases;                        -- expect 0
-- select policyname, cmd from pg_policies
--   where schemaname='public' and tablename='purchases';        -- expect 2 SELECT
-- select indexname, indexdef from pg_indexes
--   where schemaname='public' and tablename='purchases';        -- expect 3 + pkey
