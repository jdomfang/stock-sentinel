-- Create ticker_master table (Nasdaq sector/industry source of truth)

create table if not exists public.ticker_master (
  symbol text primary key,
  name text,
  sector text,
  industry text,
  exchange text,
  country text,
  source text not null default 'nasdaq',
  updated_at timestamptz not null default now()
);

create index if not exists ticker_master_sector_idx on public.ticker_master (sector);
create index if not exists ticker_master_industry_idx on public.ticker_master (industry);

-- Optional: keep updated_at current on updates (lightweight trigger)
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_ticker_master_updated_at on public.ticker_master;
create trigger set_ticker_master_updated_at
before update on public.ticker_master
for each row execute function public.set_updated_at();

-- Enable RLS and allow public read.
alter table public.ticker_master enable row level security;

drop policy if exists "ticker_master_public_read" on public.ticker_master;
create policy "ticker_master_public_read"
on public.ticker_master
for select
to public
using (true);

-- Writes should be performed via service_role (bypasses RLS).
