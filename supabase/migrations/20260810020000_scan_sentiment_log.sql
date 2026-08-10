-- Per-ticker sentiment observations from every sector scan.
--
-- WHY THIS AND NOT verdict_log ALONE
--
-- verdict_log records what Deep Analyze decided. It is the only sample drawn
-- from the population users actually type, and it will never carry statistical
-- weight: Deep Analyze has run FOUR times in its entire history, ~1.3 per
-- active day. Detecting even a large (5%/month) edge needs ~180 observations,
-- which is 7+ months away at that rate. A realistic 2% edge is years.
--
-- Discovery runs 3.5x more often AND yields many tickers per run. One scan
-- produces ~10 valid ticker-sentiment pairs for posts already paid for, so
-- ~180 observations is roughly 8 active scanning days rather than 138. Same
-- question -- does this sentiment measurement carry information about forward
-- returns -- answered ~17x faster at zero incremental X cost.
--
-- The two logs do different jobs and neither substitutes for the other:
-- Discovery gives statistical power, verdict_log gives representativeness.
--
-- EVERY valid ticker in the corpus is logged, not only the ten displayed.
-- Logging only the shortlist would bias the sample toward whatever the ranking
-- happened to favour, and the discarded tail is where a null result would
-- show up first.
--
-- STRATIFICATION. price and volume are captured AT SCAN TIME because the open
-- question is whether a relationship calibrated on scan-surfaced mid-caps
-- transfers to the mega-caps users type. That question is only answerable if
-- the liquidity of each observation was recorded when it was made; it cannot
-- be reconstructed afterwards.
--
-- RLS enabled with no policies: service-role only, matching x_call_metrics,
-- sentiment_cache and verdict_log.

create table if not exists public.scan_sentiment_log (
    id                uuid primary key default gen_random_uuid(),
    created_at        timestamptz not null default now(),

    event_id          uuid,
    corpus_key        text,
    sector            text,

    ticker            text    not null,

    -- The measurement.
    mentions          integer,          -- times the cashtag appeared
    evidence_n        integer,          -- posts that were actually scored
    mean_margin       numeric,          -- mean(p_positive - p_negative)
    overall_sentiment text,             -- the label shown, incl. evidence-floor states

    -- Was this ticker in the displayed top 10, or in the discarded tail?
    displayed         boolean not null default false,

    -- Stratification, captured now because it cannot be recovered later.
    price_at_scan     numeric,
    volume_at_scan    bigint,

    model             text
);

create index if not exists scan_sentiment_log_ticker_time_idx
    on public.scan_sentiment_log (ticker, created_at desc);
create index if not exists scan_sentiment_log_time_idx
    on public.scan_sentiment_log (created_at desc);

alter table public.scan_sentiment_log enable row level security;
