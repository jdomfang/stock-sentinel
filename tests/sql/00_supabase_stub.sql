-- Reproduce enough of Supabase inside a plain Postgres that the Phase 0
-- migrations apply UNMODIFIED. Test fixture only -- never run against production.
--
-- Everything below mirrors the live database as captured in
-- ~/stock-sentinel-backups/<ts>/schema/captured.json (policies, function bodies,
-- constraints, triggers) plus scripts/supabase_schema.sql. If production and this
-- file drift, the tests stop meaning anything -- re-capture and update.

-- ── roles ────────────────────────────────────────────────────────────────────
-- Supabase ships these. The migration's REVOKE/GRANT statements name them
-- explicitly, so they must exist for it to run at all.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon')          then create role anon          nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role')  then create role service_role  nologin bypassrls; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then create role authenticator noinherit login password 'postgres'; end if;
end $$;

-- `postgres` on Supabase is NOT a superuser and does NOT carry BYPASSRLS. The
-- whole RLS-bypass in consume_credit/refund_credit rests on the OWNER EXEMPTION,
-- which a superuser owner would mask entirely -- so these tests would pass even
-- if the ownership design were wrong. Create it powerless and own the tables
-- with it, exactly as production does.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'postgres') then
    create role postgres nologin nosuperuser nobypassrls createdb createrole;
  end if;
end $$;
grant usage, create on schema public to postgres;

grant anon, authenticated, service_role to authenticator;
grant usage on schema public to anon, authenticated, service_role;

-- ── auth schema ──────────────────────────────────────────────────────────────
create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;
-- `postgres` too. The credit functions are SECURITY DEFINER owned by postgres
-- with search_path='', so auth.uid() inside them executes AS postgres -- not as
-- the calling role. Without this the guard raises "permission denied for schema
-- auth" and every debit fails. Production must have the same grant, or
-- 20260802010000 breaks the app; that migration pre-flights it and refuses to
-- apply if it is missing.
grant usage on schema auth to postgres;

create table if not exists auth.users (
  id    uuid primary key default gen_random_uuid(),
  email text
);

-- Supabase derives auth.uid() from the request JWT. Here it reads a GUC so a
-- test can impersonate a user with:  set local request.jwt.claim.sub = '<uuid>'
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;


-- ── public schema, exactly as production has it ──────────────────────────────
create table if not exists public.profiles (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  email        text,
  role         text not null default 'user' check (role in ('user','admin')),
  disabled     boolean not null default false,
  scan_credits integer not null default 1,
  deep_credits integer not null default 1,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table if not exists public.usage_events (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,
  event_type        text not null check (event_type in ('scan','deep_analyze')),
  cost_scan_credits integer not null default 0,
  cost_deep_credits integer not null default 0,
  metadata          jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now()
);

create table if not exists public.stripe_events_processed (
  event_id            text primary key,
  user_id             uuid,
  checkout_session_id text,
  created_at          timestamptz not null default now()
);

-- ── functions, verbatim in behaviour from the captured definitions ───────────
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

-- NOTE: stable, NOT security definer -- same as production. Its interaction with
-- the profiles SELECT policies is part of what these tests exercise.
create or replace function public.is_admin(uid uuid)
returns boolean language sql stable as $$
  select exists(
    select 1 from public.profiles p
    where p.user_id = uid and p.role = 'admin' and p.disabled = false
  );
$$;

-- ── RLS + the policies the migration will drop ───────────────────────────────
-- These MUST be present, or the migration's DROP POLICY statements are no-ops
-- and the tests would prove nothing about closing the holes.
alter table public.profiles     enable row level security;
alter table public.usage_events enable row level security;

drop policy if exists "profiles_select_own"   on public.profiles;
create policy "profiles_select_own"   on public.profiles for select to authenticated
  using (auth.uid() = user_id);

drop policy if exists "profiles_select_admin" on public.profiles;
create policy "profiles_select_admin" on public.profiles for select to authenticated
  using (public.is_admin(auth.uid()));

-- THE HOLE: no column restriction. A user can set their own credit balance.
drop policy if exists "profiles_update_own"   on public.profiles;
create policy "profiles_update_own"   on public.profiles for update to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "profiles_update_admin" on public.profiles;
create policy "profiles_update_admin" on public.profiles for update to authenticated
  using (public.is_admin(auth.uid())) with check (public.is_admin(auth.uid()));

drop policy if exists "usage_select_own"   on public.usage_events;
create policy "usage_select_own"   on public.usage_events for select to authenticated
  using (auth.uid() = user_id);

drop policy if exists "usage_select_admin" on public.usage_events;
create policy "usage_select_admin" on public.usage_events for select to authenticated
  using (public.is_admin(auth.uid()));

-- THE SECOND HOLE: arbitrary event_type, cost and metadata, caller-controlled.
drop policy if exists "usage_insert_own"   on public.usage_events;
create policy "usage_insert_own"   on public.usage_events for insert to authenticated
  with check (auth.uid() = user_id);

drop policy if exists "usage_insert_admin" on public.usage_events;
create policy "usage_insert_admin" on public.usage_events for insert to authenticated
  with check (public.is_admin(auth.uid()));

grant select, insert, update, delete on public.profiles, public.usage_events
  to anon, authenticated, service_role;


-- Own everything with the non-superuser `postgres`, mirroring Supabase, so the
-- migration's owner-derivation logic has something real to derive from.
alter table public.profiles               owner to postgres;
alter table public.usage_events           owner to postgres;
alter table public.stripe_events_processed owner to postgres;
alter function public.is_admin(uuid)      owner to postgres;
alter function public.set_updated_at()    owner to postgres;
alter schema public                       owner to postgres;

-- Supabase grants table privileges broadly by default; RLS is the ONLY gate.
-- Without these, an RLS test can "pass" with `permission denied` -- proving
-- nothing about the policies.
grant all on all tables    in schema public to anon, authenticated, service_role;
grant all on all sequences in schema public to anon, authenticated, service_role;

-- stock_prices. Like profiles and usage_events, this table predates the
-- migrations folder -- it was created by hand and has no migration of its own.
-- The columns below are transcribed from the schema documented in
-- 20260808010000_stock_prices_volume.sql, which adds `volume` to it.
--
-- It exists here so the FULL production chain applies to the test database.
-- Without it that one migration raises UndefinedTable, which is what forced the
-- SQL suites onto hand-picked migration lists -- and a hand-picked list is how
-- a credit migration gets omitted and a suite passes against the function it
-- was supposed to replace.
create table if not exists public.stock_prices (
  ticker       text primary key,
  close_price  numeric,
  last_updated timestamptz,
  currency     text
);
