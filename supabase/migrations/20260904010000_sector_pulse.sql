-- sector_pulse: one row per sector per trading day, written by the nightly sync.
--
-- WHY THIS TABLE EXISTS
--
-- Discovery asks the user to pick one of ten sectors and spend a credit before
-- showing any evidence about which sector is worth it. Every input needed to
-- rank sectors by where money is entering already sits in price_history and
-- ticker_master; nothing computed it. This table holds that computation so the
-- page reads ten rows instead of 190,000, and so the landing page can show it
-- to anonymous visitors without a service key.
--
-- WHAT IS DELIBERATELY NOT HERE
--
-- No raw dollar volume as a ranking column. Measured on 2026-08-19, healthcare
-- "doubled" and 70% of the increase was one name (MRNA, +177%, 90x volume).
-- Dollar-weighted anything turns one stock into a sector. The columns are the
-- equal-weight and count-based measures that survived that day: breadth, the
-- up-volume/down-volume ratio, accumulation minus distribution days, and the
-- equal-weight return -- with the dominant names recorded by NAME so the page
-- can say "that's MRNA, not the sector".
--
-- ACCESS. Same shape as public_demo_snapshots: RLS on with no policies, table
-- privileges only to service_role (the sync writes, the portal reads with its
-- service key), and a narrow SECURITY DEFINER reader for browsers that returns
-- only the trailing window the landing page needs. Nothing in a pulse row is
-- user data, but the table is still not the API -- the function is.

create table if not exists public.sector_pulse (
    sector          text        not null,
    trade_date      date        not null,   -- the BAR's date, not the write time

    n_eligible      integer     not null check (n_eligible >= 0),

    -- $ volume on up days / $ volume on down days over the trailing 5 sessions.
    -- >1.3 reads as accumulation, <0.8 as distribution. NULL when no down-day
    -- volume exists (all-up window) -- a NULL is honest, an infinity is not.
    ud_ratio_5d     numeric,
    -- Share of eligible names whose volume exceeds 1.5x their ROBUST baseline
    -- (20-day median with the name's own top three days excluded -- the fix for
    -- a spike poisoning its own baseline).
    breadth         numeric     check (breadth is null or (breadth >= 0 and breadth <= 1)),
    acc_days_5d     smallint    not null default 0 check (acc_days_5d between 0 and 5),
    dist_days_5d    smallint    not null default 0 check (dist_days_5d between 0 and 5),
    eq_return_5d    numeric,               -- equal-weight, never dollar-weight
    pct_up_5d       numeric     check (pct_up_5d is null or (pct_up_5d >= 0 and pct_up_5d <= 1)),

    -- accumulating | distributing | event | quiet. Thresholds live in
    -- utils/sector_pulse.py and are provisional until sector_pulse_backtest.py
    -- has run over a year of bars; the CHECK only pins the vocabulary.
    state           text        not null
                    check (state in ('accumulating', 'distributing', 'event', 'quiet')),

    -- [{ticker, share_of_rise, ret_1d, rel_vol}] -- the names driving the day,
    -- so an event-driven sector is explained rather than merely flagged.
    top_contrib     jsonb       not null default '[]'::jsonb
                    check (jsonb_typeof(top_contrib) = 'array'),

    -- month_end | quarter_end | opex | null. Days when every sector's volume
    -- jumps for reasons that are not conviction (08-31 lit all ten).
    calendar_flag   text        check (calendar_flag is null
                                       or calendar_flag in ('month_end', 'quarter_end', 'opex')),

    computed_at     timestamptz not null default now(),
    primary key (sector, trade_date)
);

-- The two access patterns: "the latest date for every sector" and "one
-- sector's trailing window for a sparkline".
create index if not exists sector_pulse_date_idx
    on public.sector_pulse (trade_date desc);

alter table public.sector_pulse enable row level security;

-- No policies on purpose. Browser roles get nothing on the table itself;
-- service_role bypasses RLS and gets exactly the verbs the nightly upsert and
-- the portal read need. No delete: a pulse row is a record of what the job
-- computed that night, and correcting one means writing a new row, not
-- erasing the old.
revoke all on table public.sector_pulse from public, anon, authenticated;
grant select, insert, update on table public.sector_pulse to service_role;

-- ------------------------------------------------------------ the reader --
-- Returns every sector's rows for the most recent `days` distinct trading
-- dates: enough for the current state plus a short sparkline, and nothing
-- else. Anonymous visitors on the landing page call this; they never touch
-- the table. `days` is clamped so the function cannot be used to page out the
-- whole history.
create or replace function public.get_sector_pulse_recent(days integer default 6)
returns table (
    sector          text,
    trade_date      date,
    n_eligible      integer,
    ud_ratio_5d     numeric,
    breadth         numeric,
    acc_days_5d     smallint,
    dist_days_5d    smallint,
    eq_return_5d    numeric,
    pct_up_5d       numeric,
    state           text,
    top_contrib     jsonb,
    calendar_flag   text,
    computed_at     timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
    with recent as (
        select distinct p.trade_date
          from public.sector_pulse as p
         order by p.trade_date desc
         limit greatest(1, least(coalesce(days, 6), 30))
    )
    select
        p.sector, p.trade_date, p.n_eligible, p.ud_ratio_5d, p.breadth,
        p.acc_days_5d, p.dist_days_5d, p.eq_return_5d, p.pct_up_5d,
        p.state, p.top_contrib, p.calendar_flag, p.computed_at
      from public.sector_pulse as p
      join recent as r on r.trade_date = p.trade_date
     order by p.trade_date desc, p.sector
$$;

revoke all on function public.get_sector_pulse_recent(integer) from public;
grant execute on function public.get_sector_pulse_recent(integer)
    to anon, authenticated, service_role;

comment on table public.sector_pulse is
    'Nightly per-sector money-flow measures from price_history. Equal-weight by construction; see utils/sector_pulse.py.';
comment on function public.get_sector_pulse_recent(integer) is
    'Narrow browser reader: every sector for the most recent N (<=30) trading dates. The table itself is service-role only.';

-- Record this migration where the others record themselves, so
-- test_every_migration_records_itself and the hand-applied ledger agree.
do $migration_record$
begin
    if pg_catalog.to_regclass('public.schema_migrations') is not null then
        insert into public.schema_migrations (version)
        values ('20260904010000_sector_pulse')
        on conflict do nothing;
    end if;
end
$migration_record$;
