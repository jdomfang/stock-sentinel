-- Measure what every paid X call actually bought.
--
-- DEPLOY ORDER
--   1..8  (applied)
--   9. THIS FILE
--   10. Deploy the app. utils/x_metrics.py writes one row per scan.
--
-- Safe in either order: the recorder swallows its own errors, so before this
-- table exists a scan behaves exactly as it does today.
--
-- WHY
--
-- X bills per POST RETURNED. One production scan returned 99 posts, of which 50
-- produced no ticker at all -- bought and discarded. Nobody knew that until it
-- was measured by hand, once, and there is currently no way to tell whether a
-- query edit made it better or worse.
--
-- Worse than the waste: the pipeline reads BARE uppercase words as tickers, and
-- 17 of 20 ordinary English words tested are real listed symbols. AIR is AAR
-- Corp, RAIL is FreightCar America, BOOM is DMC Global -- all Industrials, so
-- all three pass the strict sector check and render identically to CAT and LMT.
-- The product has never been able to distinguish a real recommendation from a
-- fabricated one, and neither has anyone reading the logs.
--
-- Everything in this table is derived from posts ALREADY PAID FOR. Recording it
-- costs no additional X spend.
--
-- WHAT MAKES IT A FEEDBACK LOOP RATHER THAN A DASHBOARD
--
-- Rows are keyed by query_hash -- the sha256 of the exact query string, the same
-- content-addressed scheme utils/corpus_cache.py uses. Each query version then
-- accumulates its own track record, so "did that edit help?" becomes a GROUP BY
-- instead of an argument. It also catches the trap where a tighter query raises
-- precision while lowering distinct tickers: that shows up as distinct_validated
-- falling in the same row where waste falls.

create table if not exists public.x_call_metrics (
    id           uuid primary key default gen_random_uuid(),

    -- Links to the credit ledger, so "what did this user get for their credit"
    -- is answerable. Nullable: losing the link must not cost us the metrics.
    event_id     uuid references public.usage_events(id) on delete cascade,

    kind         text not null check (kind in ('scan', 'deep')),
    subject      text not null,                  -- sector, or ticker for deep
    query_hash   text not null,                  -- sha256(query)[:12]
    created_at   timestamptz not null default now(),

    -- ---- cost ----
    -- billed is 0 on a corpus-cache hit; processed is what extraction saw.
    -- Keeping both is what lets the report separate "we got cheaper" from
    -- "we got better", which are different wins and easy to confuse.
    posts_billed    integer not null default 0 check (posts_billed >= 0),
    posts_processed integer not null default 0 check (posts_processed >= 0),
    pages_fetched   integer not null default 0 check (pages_fetched >= 0),
    from_cache      boolean not null default false,

    -- ---- where every processed post ended up ----
    -- Four mutually exclusive buckets. The CHECK below forces them to account
    -- for every post, so a partition that silently loses posts cannot be
    -- stored -- an under-counted bucket would understate waste, which is the
    -- one number this table exists to report honestly.
    posts_contributed        integer not null default 0 check (posts_contributed >= 0),

    -- EXPECTED TO BE ZERO, and that is not a bug. Validation stops at 10 and
    -- df_valid.head(10) therefore truncates nothing, so every validated ticker
    -- is displayed. This stays as an ANOMALY DETECTOR: it can only become
    -- non-zero if a validated ticker carries no sentiment score, which would
    -- mean the scoring and extraction paths disagreed. Read a non-zero here as
    -- a defect to investigate, never as an "we over-fetched" signal -- the
    -- genuine over-fetch signal is distinct_validatable above.
    posts_validated_hidden   integer not null default 0 check (posts_validated_hidden >= 0),
    posts_no_valid_ticker    integer not null default 0 check (posts_no_valid_ticker >= 0),
    posts_no_candidates      integer not null default 0 check (posts_no_candidates >= 0),

    -- Posts carrying at least one CASHTAG that validated. This is what makes
    -- the cashtags-only variant measurable without running it: it is the
    -- would-be corpus of a run with bare-word extraction disabled.
    posts_with_valid_cashtag integer not null default 0 check (posts_with_valid_cashtag >= 0),

    -- ---- outputs ----
    distinct_validated integer not null default 0 check (distinct_validated >= 0),
    displayed          integer not null default 0 check (displayed >= 0),

    -- UNCAPPED counts. These exist because the two above are censored and
    -- censored in the worst possible direction.
    --
    -- _try_validate_from_current_ranking stops the moment 10 tickers validate,
    -- so distinct_validated is clipped to [0,10] in every row ever written.
    -- Worse, validation walks candidates in MENTION-RANK order, and bare-word
    -- phantoms are ordinary English words -- by construction the most frequent
    -- tokens in any corpus, further inflated by the duplicate defect in
    -- extract_tickers. So phantoms consume the 10-slot budget FIRST and real
    -- cashtag symbols are never even checked.
    --
    -- Measuring the phantom problem with a metric that phantoms crowd out
    -- would have produced a confident answer in the wrong direction: "most of
    -- your tickers come from bare words, so you cannot remove it." These two
    -- columns re-run validation over EVERY candidate with no cap, which costs
    -- a few in-memory dict lookups and no API calls, and are what actually
    -- answers "if bare-word extraction is disabled, do 10 tickers survive?"
    distinct_validatable integer not null default 0
        check (distinct_validatable >= 0),
    distinct_validatable_cashtag_only integer not null default 0
        check (distinct_validatable_cashtag_only >= 0),

    -- Right-censoring marker. 'validated_target' means pagination stopped early
    -- because 10 tickers were found, so this row's corpus is a PREFIX of what a
    -- different query would have processed. Averaging censored rows with
    -- uncensored ones silently compares different amounts of evidence.
    stop_reason text,

    -- Corpus identity, so cache replays are not counted as independent samples.
    -- Five users scanning industrials inside the 6h TTL write five rows over
    -- ONE corpus; pooling them weights the statistics by cache popularity
    -- rather than by evidence.
    corpus_key text,

    -- Displayed tickers that were NEVER seen with a $ anywhere in the corpus.
    -- Not proof of a phantom, but a strong prior: a genuine discussion of AAR
    -- Corp says $AIR somewhere. Costs nothing and flags AIR/RAIL immediately.
    phantom_suspects   integer not null default 0 check (phantom_suspects >= 0),

    -- {"CAT": {"cashtag": 12, "bare": 3, "displayed": true}, ...}
    --
    -- Per-ticker, not just counts. Aggregates can tell you "3 suspects" but
    -- never WHICH, and the which is what makes a bad scan debuggable. Ten
    -- tickers per scan is nothing to store, and finer data can always be
    -- aggregated later while coarse data can never be refined.
    ticker_provenance jsonb not null default '{}'::jsonb,

    constraint x_call_metrics_partition_totals check (
        posts_contributed + posts_validated_hidden
      + posts_no_valid_ticker + posts_no_candidates = posts_processed
    )
);

-- The report groups by query version over time.
create index if not exists x_call_metrics_query_idx
    on public.x_call_metrics (kind, query_hash, created_at desc);

create index if not exists x_call_metrics_created_idx
    on public.x_call_metrics (created_at desc);

-- RLS ON WITH NO POLICIES = service role only, same as x_corpus_cache.
-- This is operational telemetry about paid work; there is no reason for anon
-- or authenticated to read it, and ticker_provenance leaks scan results.
alter table public.x_call_metrics enable row level security;

comment on table public.x_call_metrics is
    'Per-call effectiveness of paid X API calls: what was bought, what was used, what was thrown away. Service-role only.';
