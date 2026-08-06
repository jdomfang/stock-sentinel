-- Phase 1: stop re-buying the same tweets from X.
--
-- DEPLOY ORDER
--   1..7  (applied)
--   8. THIS FILE
--   9. Deploy the app. utils/corpus_cache.py reads and writes this table.
--
-- Safe in either order. Before step 9 the table is simply unused; after step 8
-- is skipped, corpus_cache degrades to always-miss and the app behaves exactly
-- as it does today (see the module's failure policy).
--
-- WHY
--
-- X bills per POST RETURNED, not per request. A Discovery scan of "industrials"
-- costs ~99 posts, and today every scan buys them fresh: pages/Discovery.py
-- has no cache of any kind, so the same user re-scanning 30 seconds later, and
-- every other user scanning that sector in the same hour, each pay again.
--
-- A scan result is not user-specific. It is a function of (sector, time
-- window) alone. And the sector catalogue is TEN entries. So with a shared
-- cache the maximum X spend of the entire scan feature is 10 keys per TTL
-- window, no matter how many users exist. That is the point of this table: it
-- changes the shape of the bill from "grows per user" to "bounded", rather
-- than shaving a percentage off it.
--
-- WHY NOT THE EXISTING CACHE
--
-- utils/deep_analysis.py already caches, to st.session_state plus local disk at
-- data/cache/deep_analysis/. On Streamlit Cloud that filesystem is erased on
-- every reboot and every redeploy -- and this repo has pushed 8-16 times a day,
-- so entries routinely die before their 45 minute TTL expires. Postgres is
-- outside the container, survives deploys, and is visible to the worker.
--
-- WHY THE RAW CORPUS AND NOT THE FINISHED RESULT
--
-- The tweets are what cost money; scoring them again is free. Caching the raw
-- corpus means a sentiment fix, a threshold change or a scoring bug can be
-- re-run against posts already paid for. Caching the finished result would
-- freeze today's scoring logic into every stored row.

create table if not exists public.x_corpus_cache (
    -- '{kind}:{subject}:{timeframe_h}h:{query_hash}'. Built by
    -- utils.corpus_cache.make_key so the two sides cannot disagree.
    cache_key       text primary key,

    kind            text not null check (kind in ('sector', 'ticker', 'influencer')),
    subject         text not null,
    timeframe_h     integer not null check (timeframe_h > 0),

    -- Content-addressed: sha256 of the exact query string, truncated. The
    -- alternative is a hand-bumped version constant, which is what
    -- deep_analysis.py uses (CACHE_VERSION = "v3") and which silently serves
    -- corpora built by the OLD query the first time someone edits a query
    -- without remembering to bump it. A hash cannot drift from what it names.
    query_hash      text not null,
    query_text      text not null,

    -- The posts themselves, exactly as X returned them.
    tweets          jsonb not null,

    -- tweet_count is denormalised so the sweeper and the monitoring queries do
    -- not have to deserialise a 300-element array to count it.
    tweet_count     integer not null check (tweet_count >= 0),

    -- A corpus that early-stopped at 100 posts because the scan already had ten
    -- validated tickers is NOT the same as one that exhausted the query at 300.
    -- Storing both means a thin corpus is recognisable rather than being reused
    -- forever as though it were complete.
    pages_fetched   integer not null default 0 check (pages_fetched >= 0),
    stop_reason     text,

    fetched_at      timestamptz not null default now(),

    -- Two boundaries, not one:
    --   expires_at       -- past this, refetch.
    --   hard_expires_at  -- past this, the row is unusable even if X is down.
    -- Serving between them is how an X outage degrades to "showing chatter from
    -- 3 hours ago" instead of a hard failure. Serving past hard_expires_at
    -- would be the silent-staleness failure this codebase keeps getting burned
    -- by -- a nightly sync that failed 43 times unnoticed, an inference service
    -- 40x slow with every health check green.
    expires_at      timestamptz not null,
    hard_expires_at timestamptz not null,

    check (expires_at <= hard_expires_at)
);

-- The sweeper's only access path.
create index if not exists x_corpus_cache_hard_expires_idx
    on public.x_corpus_cache (hard_expires_at);

-- Useful for "which sectors are actually being scanned" without a full scan.
create index if not exists x_corpus_cache_kind_subject_idx
    on public.x_corpus_cache (kind, subject);

-- RLS ON WITH NO POLICIES = service role only.
--
-- Deliberately NOT the pattern used by ticker_master, which grants public
-- read. This table holds the paid product's raw inputs: a user who can select
-- from it gets every sector's tweet corpus without spending a credit, which is
-- the product. No policy is created here, so PostgREST returns nothing to anon
-- and authenticated, and only the service-role key (which bypasses RLS) can
-- read or write it. utils/corpus_cache.py uses that key exclusively.
alter table public.x_corpus_cache enable row level security;

comment on table public.x_corpus_cache is
    'Shared cache of raw X (Twitter) corpora, keyed by query content. Service-role only: this is the paid product''s input data.';
