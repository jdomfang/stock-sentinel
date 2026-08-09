-- Two tables whose value is entirely a function of when they START.
--
-- Neither changes any current output. Both exist because the data they hold
-- cannot be reconstructed later:
--
--   verdict_log   -- no Deep Analyze verdict has ever been persisted, so no
--                    forward-return evaluation of the product's core claim is
--                    possible. X's search index covers 7 days and cannot be
--                    backfilled, so a verdict not written today is a verdict
--                    that can never be scored.
--
--   price_history -- stock_prices holds ONE ROW PER TICKER (latest close). The
--                    grouped-daily sync already fetches the whole US market for
--                    a specific trading day and then discards the date; see the
--                    note in utils/prices.py which called this "a schema change
--                    worth making separately". Projection work needs 90 trading
--                    days minimum and 252 preferred, so the clock starts on the
--                    day this table does.
--
-- RLS is enabled with NO policies on both: service-role only, matching
-- x_call_metrics and sentiment_cache. These are operator data, never read by a
-- signed-in user.

-- ---------------------------------------------------------------- verdicts --

create table if not exists public.verdict_log (
    id                  uuid primary key default gen_random_uuid(),
    created_at          timestamptz not null default now(),

    -- Correlates a verdict with the credit that paid for it and the corpus it
    -- was built from, so a disputed call can be reconstructed end to end.
    event_id            uuid,
    corpus_key          text,

    ticker              text not null,
    sector              text,

    verdict             text not null,   -- Buy | Watch | Avoid
    confidence          text,            -- Low | Moderate | High

    -- The adjudicator's inputs, stored so a threshold change can be replayed
    -- against history instead of only against future runs.
    avg_sentiment       numeric,
    red_flag_rate       numeric,
    disagreement        numeric,
    total_mentions      integer,

    -- Price state AT the moment of the call. Without this, forward return is
    -- unmeasurable no matter how much history accumulates.
    price_at_verdict    numeric,

    projected_p10       numeric,
    projected_p90       numeric,
    suggested_hold_days integer,
    success_rate        numeric,         -- computed today, never displayed

    model               text
);

create index if not exists verdict_log_ticker_time_idx
    on public.verdict_log (ticker, created_at desc);
create index if not exists verdict_log_time_idx
    on public.verdict_log (created_at desc);

alter table public.verdict_log enable row level security;

-- ----------------------------------------------------------- price history --

create table if not exists public.price_history (
    ticker      text        not null,
    trade_date  date        not null,   -- the BAR's date, not the write time
    close       numeric     not null,
    volume      bigint,
    inserted_at timestamptz not null default now(),
    primary key (ticker, trade_date)
);

-- Backfilling a single day across the whole market, and reading one ticker's
-- series, are the only two access patterns.
create index if not exists price_history_date_idx
    on public.price_history (trade_date);

alter table public.price_history enable row level security;
