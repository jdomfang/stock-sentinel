-- Stock Sentinel: Auth + Profiles + Credits + Admin
-- Paste into Supabase SQL Editor and run.

-- 1) Profiles table (1:1 with auth.users)
create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text,
  role text not null default 'user' check (role in ('user','admin')),
  disabled boolean not null default false,
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
returns trigger language plpgsql security definer as $$
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

-- Users can update their own profile (limited fields is enforced app-side; RLS is row-level)
drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own"
on public.profiles for update
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

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

-- Users can insert their own usage events
drop policy if exists "usage_insert_own" on public.usage_events;
create policy "usage_insert_own"
on public.usage_events for insert
to authenticated
with check (auth.uid() = user_id);

-- Admins can insert usage events too
drop policy if exists "usage_insert_admin" on public.usage_events;
create policy "usage_insert_admin"
on public.usage_events for insert
to authenticated
with check (public.is_admin(auth.uid()));

-- 6) Bootstrap: set your admin email to admin role (run once; safe to re-run)
-- Replace the email below if needed.
update public.profiles
set role = 'admin'
where lower(email) = lower('kdjulianofr@hotmail.com');
