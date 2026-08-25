-- Stock Sentinel: Auth + Profiles + Credits + Admin -- BOOTSTRAP ONLY
--
-- Paste into the Supabase SQL Editor and run, THEN APPLY EVERY FILE IN
-- supabase/migrations/ IN FILENAME ORDER. This file alone is not a working
-- database and never was; it predates the migrations folder and only creates
-- the objects those migrations then correct.
--
-- WHAT WAS WRONG WITH THIS FILE, and why it is worth stating at the top
--
-- It used to create two RLS policies that later migrations exist to DROP:
--
--   profiles_update_own  -- "limited fields is enforced app-side; RLS is
--                           row-level", i.e. any signed-in user could UPDATE
--                           their own row, INCLUDING scan_credits, deep_credits
--                           and role. Self-service credits and self-promotion
--                           to admin, in one policy.
--   usage_insert_own     -- any signed-in user could INSERT ledger rows, so the
--                           audit trail could be forged to match a stolen
--                           balance and reconciliation would agree.
--
-- 20260801020000_credit_integrity.sql calls these the "Profiles RLS hole" and
-- the "Ledger RLS hole" and drops both. But running THIS file against a current
-- database re-created them -- silently reopening a credit-minting and
-- privilege-escalation vulnerability by doing the documented setup step.
--
-- They are gone from here now. Do not add them back. If a user needs to change
-- something on their own profile, route it through a SECURITY DEFINER function
-- that can restrict the columns; a row-level policy cannot.
--
-- tests/test_bootstrap_safety.py pins this.

-- 1) Profiles table (1:1 with auth.users)
create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text,
  role text not null default 'user' check (role in ('user','admin')),
  disabled boolean not null default false,
  -- The live balance. scan_credits/deep_credits are FROZEN pre-merge snapshots
  -- kept for rollback; nothing writes them after
  -- 20260824030000_merge_credit_buckets.sql. A fresh database created from this
  -- file still needs that migration -- it is what installs the credit functions
  -- that spend this column.
  credits integer not null default 2,
  scan_credits integer not null default 1,
  deep_credits integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Keep updated_at current
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

-- 2) Usage events (audit log)
create table if not exists public.usage_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  event_type text not null check (event_type in ('scan','deep_analyze')),
  cost_scan_credits integer not null default 0,
  cost_deep_credits integer not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- 3) Auto-create profile on signup
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
-- PINNED. This runs as its owner on every signup, and every credit function in
-- this database pins search_path for the same reason: without it, anything that
-- can create an object earlier on the resolved path can shadow a table this
-- function writes and have the write happen as the owner instead of the caller.
-- The references below are already schema-qualified, so an empty path is safe.
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, email)
  values (new.id, new.email)
  on conflict (user_id) do update set email = excluded.email;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute procedure public.handle_new_user();

-- 4) Helpers to check admin
create or replace function public.is_admin(uid uuid)
returns boolean language sql stable as $$
  select exists(
    select 1 from public.profiles p
    where p.user_id = uid and p.role = 'admin' and p.disabled = false
  );
$$;

-- 5) RLS
alter table public.profiles enable row level security;
alter table public.usage_events enable row level security;

-- PROFILES policies
-- Users can read their own profile
drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own"
on public.profiles for select
to authenticated
using (auth.uid() = user_id);

-- Admins can read all profiles
drop policy if exists "profiles_select_admin" on public.profiles;
create policy "profiles_select_admin"
on public.profiles for select
to authenticated
using (public.is_admin(auth.uid()));

-- NO profiles_update_own. See the header. A row-level policy cannot restrict
-- WHICH COLUMNS a user may write, so "users can edit their own profile" is
-- indistinguishable from "users can set their own credits and role".
-- Dropped defensively in case an older run of this file created it.
drop policy if exists "profiles_update_own" on public.profiles;

-- Admins can update any profile
drop policy if exists "profiles_update_admin" on public.profiles;
create policy "profiles_update_admin"
on public.profiles for update
to authenticated
using (public.is_admin(auth.uid()))
with check (public.is_admin(auth.uid()));

-- USAGE_EVENTS policies
-- Users can read their own usage
drop policy if exists "usage_select_own" on public.usage_events;
create policy "usage_select_own"
on public.usage_events for select
to authenticated
using (auth.uid() = user_id);

-- Admins can read all usage
drop policy if exists "usage_select_admin" on public.usage_events;
create policy "usage_select_admin"
on public.usage_events for select
to authenticated
using (public.is_admin(auth.uid()));

-- NO usage_insert_own. The ledger is written ONLY by the SECURITY DEFINER
-- credit functions, which are service_role-only. A user who can insert ledger
-- rows can forge the audit trail that explains their balance, which makes the
-- reconciliation invariant agree with the theft.
-- Dropped defensively in case an older run of this file created it.
drop policy if exists "usage_insert_own" on public.usage_events;

-- NO usage_insert_admin either. Admins do not hand-write ledger rows; they use
-- admin_adjust_credits, which moves the balance and writes the matching row in
-- one transaction. An admin JWT that can INSERT directly can write a ledger row
-- with no balance movement behind it -- breaking the reconciliation invariant
-- from the one direction reconciliation cannot detect, because the ledger IS
-- the thing being checked against.
-- Dropped defensively in case an older run of this file created it.
drop policy if exists "usage_insert_admin" on public.usage_events;

-- 6) Bootstrap: set your admin email to admin role (run once; safe to re-run)
-- Replace the email below if needed.
update public.profiles
set role = 'admin'
where lower(email) = lower('kdjulianofr@hotmail.com');
