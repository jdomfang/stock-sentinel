-- The state the adjudicator SAW, not just the answer it gave.
--
-- verdict_log already records the verdict, the confidence and the price at the
-- moment of the call. That is enough to ask "did Buy outperform Avoid". It is
-- not enough to ask any of the questions that would actually improve the
-- product:
--
--   which pillar was blocking the Buys that would have worked
--   is the conflict veto saving us or costing us
--   does quality 0.30 separate winners from losers, or is 0.45 the real line
--   does a confirmed catalyst add anything once sentiment is controlled for
--
-- Each of those needs the INPUTS beside the outcome, and each needs the near
-- misses. A log of issued Buys alone has no comparison group: if Buy returns
-- +2% over five days, that means nothing without knowing what the Watches that
-- nearly became Buys returned.
--
-- ONE TABLE, EVERY VERDICT. An expert review proposed two mechanisms -- signal
-- logging and a separate "rejected Buy candidate" log. Two tables would need
-- two write paths, two schemas and a definition of "candidate" fixed in code
-- at write time, which could never be revised against history. Recording the
-- full state for every verdict makes a rejected candidate a QUERY instead:
--
--   select * from signal_log
--    where verdict <> 'Buy'
--      and branch in ('buy_downgraded_low_confidence', 'channel_conflict',
--                     'price_missing', 'no_alignment', 'risk_high');
--
-- and the definition can be changed afterwards, which is the entire point.
--
-- That query is written on `branch`, NOT on the scalars. The obvious version --
-- `social_direction > 0 and quality_tier in ('moderate','high')` -- excludes
-- three whole classes of near miss:
--
--   conflict-vetoed rows, whose social_direction is FORCED to exactly 0.0 by
--     utils/modules.py; the very question "is the conflict veto costing us"
--     would be unanswerable;
--   the catalyst route, where a Buy needs no positive sentiment at all;
--   and the nearest miss in the system -- a row that reached the Buy branch
--     with every blocking pillar passed and was then downgraded by the
--     confidence rules. It has no failing pillar to filter on.
--
-- Deliberately NOT a view over verdict_log: that table is already accumulating
-- and its shape is load-bearing for work in flight. This one starts empty and
-- carries the wide state.
--
-- RLS enabled with NO policies: service-role only, matching verdict_log,
-- x_call_metrics and sentiment_cache. Operator data, never read by a user.

create table if not exists public.signal_log (
    id                  uuid primary key default gen_random_uuid(),
    created_at          timestamptz not null default now(),

    -- Correlates with the credit that paid for it and the corpus it was built
    -- from, so a disputed call can be reconstructed end to end.
    event_id            uuid,
    corpus_key          text,

    -- deep_analyze | discovery. Discovery runs far more often and yields ~10
    -- ticker observations per run, so it is the faster route to sample size --
    -- but its evidence comes from a basket query with a different selection
    -- bias, and mixing the two without this column would be a silent error.
    feature             text not null,

    ticker              text not null,
    sector              text,
    model               text,

    -- ---- the answer ----
    verdict             text not null,   -- Buy | Watch | Avoid
    confidence          text,            -- Low | Moderate | High
    earned_confidence   text,            -- before caps were applied
    reason              text,

    -- WHICH CASCADE BRANCH FIRED, as a stable token set by the branch itself.
    -- Not derived from the pillar list: those are in DISPLAY order, which is
    -- not the cascade's, so the first failing pillar names the wrong cause for
    -- reachable states. Not derived from `reason` either, which is prose and
    -- will be reworded. This is the column every later analysis will group by.
    branch              text,
    -- Kept alongside, but secondary, and NOT a synonym for "this was blocked":
    -- an issued Buy taking the catalyst exemption still fails the price pillar,
    -- so this is non-null under a green Buy.
    first_failed_pillar text,
    -- The cascade this row was produced by. Every threshold here is
    -- provisional; without a version, rows from before and after a change are
    -- indistinguishable and pooling them is silently wrong.
    adjudicator_version text,

    -- Price at the moment of the call, and THE SESSION IT BELONGS TO.
    --
    -- price_at_decision alone cannot be scored. A verdict issued at 02:00 UTC,
    -- on a weekend, or on a holiday has a created_at whose date is not a
    -- trading day, so `join price_history on trade_date = created_at::date`
    -- finds no row -- silently discarding roughly a third of the table with no
    -- error to notice. During market hours it is worse: created_at::date DOES
    -- match, but against a settled close while price_at_decision was an
    -- unsettled intraday print. decision_trade_date is the bar's own date, and
    -- it cannot be reconstructed for any row written without it.
    price_at_decision   numeric,
    decision_trade_date date,
    -- How old the evidence was when the verdict was issued. Discovery reuses a
    -- cached sector scan that may be hours old, so created_at is the write
    -- time, not the time the market was speaking.
    evidence_age_s      integer,

    -- ---- what the adjudicator saw ----
    -- Fractions (0..1) unless a column says otherwise. The one exception is
    -- proj_band, which is a PERCENT, because that is what simple_projection
    -- returns and rescaling it here would put two conventions in the codebase.
    quality_score       numeric,
    quality_tier        text,
    quality_aboutness   numeric,
    quality_integrity   numeric,
    quality_coverage    numeric,
    eligible_clusters   integer,
    -- Clusters from this ticker's OWN retrieval, excluding the reused sector
    -- scan. A corpus that is mostly seed cannot make a call, and without these
    -- two that Watch is indistinguishable from a genuine quality failure --
    -- both print "Evidence quality" as their first failing pillar.
    own_clusters        integer,
    seed_only           boolean,
    -- Why confidence was capped below what the evidence earned. The
    -- spam-share cap turns a Buy into a Watch and leaves no other trace.
    confidence_notes    text,

    social_direction    numeric,
    cashtag_direction   numeric,
    alias_direction     numeric,
    cashtag_clusters    integer,
    alias_clusters      integer,
    social_conflict     boolean,
    social_minor_disagreement boolean,
    social_lean         text,

    risk_high           boolean,
    risk_severe_clusters integer,
    risk_soft_clusters  integer,
    risk_soft_rate      numeric,
    risk_from_newswire  boolean,

    catalyst_present    boolean,
    catalyst_hard_clusters integer,
    catalyst_hard_direction numeric,
    -- How many confirmed rows carried a score. When 0 while hard_clusters is
    -- not, hard_direction defaults to 0.0 -- which passes the `>= 0` test and
    -- lets a Buy rest on an event nobody measured. Without this column that
    -- state is indistinguishable from a genuinely neutral event.
    catalyst_hard_scored integer,
    catalyst_soft_clusters integer,
    catalyst_soft_authors integer,
    catalyst_from_newswire boolean,

    -- neutral|declining|unconfirmed|caution|market_wide|missing
    price_status        text,
    price_return_20d    numeric,
    price_volume_ratio  numeric,
    price_caution       boolean,
    -- WAS IT THIS COMPANY, OR THE WHOLE SECTOR. The veto asks whether the tape
    -- contradicts a bullish story about THIS company, and until these columns
    -- existed it could not tell an 18% fall in a sector down 17% from an 18%
    -- fall in a sector that rose. `market_wide` is the status where the excess
    -- cleared the bar and the decline stopped blocking; storing the excess
    -- itself means the provisional threshold can later be replaced by a
    -- measured one against these very rows.
    price_sector_return_20d numeric,
    price_excess_return_20d numeric,
    price_benchmark     text,      -- the ETF used; the map is expected to change
    price_excess_window integer,   -- sessions the excess spans; NOT always 21
    price_excess_noise  numeric,   -- this pair's usual spread over that window

    -- THE CURATED-ACCOUNT CHANNEL, measured here for the first time. Twelve
    -- accounts are queried and billed on every run and have never been
    -- examined; three rounds of review put "measure the newswire" near the top
    -- of the list and each time it was skipped because it looked like it needed
    -- a spending experiment. Every column below comes from corpora an ordinary
    -- run already holds, so the answer accumulates for free.
    --
    -- newswire_duplicate is the one that decides whether the second query is
    -- worth making: the pages drop a wire post whose id the ticker arm already
    -- returned, so by the time the ledger exists the overlap is invisible.
    -- Counted before the drop. NULL means "not compared", not "no overlap".
    -- newswire_state is what makes the zeros readable. An empty wire corpus has
    -- four different causes -- not_configured, error, cache_hit, fetched -- and
    -- they arrive downstream as the same []. Without this column a month of
    -- rate limiting reads as "these accounts return nothing" and a working
    -- channel gets cut on the strength of an outage.
    newswire_state      text,
    newswire_returned   integer,   -- posts held, cache hit or not
    newswire_billed     integer,   -- posts actually CHARGED; 0 on a cache hit
    newswire_duplicate  integer,   -- of those held, already found by the main arm
    newswire_about_target integer, -- unique rows that are about the company
    newswire_eligible   integer,   -- unique rows that reach the adjudicator
    newswire_hard_catalysts integer,
    newswire_severe_risks integer,
    -- THE COLUMN THAT ACTUALLY DECIDES IT. Everything above measures
    -- participation, and participation is not influence: the wire can supply
    -- twenty eligible rows and change nothing because the main arm already
    -- carried the same finding. This re-adjudicates the same in-memory ledger
    -- with the wire rows removed, so `verdict <> verdict_without_wire` counts
    -- the runs where those twelve accounts changed what the user was told.
    -- Costs one pass over a list already in memory. NULL when there were no
    -- wire rows to remove.
    verdict_without_wire text,

    -- ---- the shape of the evidence underneath ----
    ledger_rows         integer,
    ledger_eligible     integer,
    ledger_scored       integer,
    ledger_spam_high    integer,
    rows_social         integer,
    rows_newswire       integer,
    rows_seed           integer,

    -- ---- what was published alongside ----
    --
    -- READ proj_quality_gated BEFORE COMPARING ANY OF THESE ACROSS COHORTS.
    -- The caller only enables the sentiment tilt when the verdict is Buy, so
    -- the rates below carry a bullish drift on Buy rows and none elsewhere.
    -- `select verdict, avg(proj_up_rate_5) ... group by 1` therefore measures
    -- the tilt flag, not the market, and produces a clean artefactual result.
    proj_band           numeric,   -- PERCENT, not a fraction
    proj_horizon_days   integer,
    proj_mae_p75        numeric,
    proj_up_first_5     numeric,   -- null when nothing touched either side
    proj_up_rate_5      numeric,
    proj_down_rate_5    numeric,
    proj_quality_gated  boolean,
    proj_sentiment_tilt numeric   -- PERCENT, as returned
    -- The unique constraint that makes one paid analysis one row is added in
    -- the guarded block below, so this file can be re-run.
);

-- RE-RUNNABLE FROM HERE DOWN. `create table if not exists` above is a no-op
-- against a table that already exists, so a schema created from an earlier
-- draft of this file would silently lack the columns added since -- and
-- PostgREST rejects the WHOLE insert with a 400 when one column is missing,
-- losing all sixty on every paid analysis with nothing but a WARNING to show
-- for it. These statements bring any earlier version up to date, and re-running
-- the file is safe.
alter table public.signal_log
    add column if not exists newswire_state       text,
    add column if not exists newswire_returned    integer,
    add column if not exists newswire_billed      integer,
    add column if not exists newswire_duplicate   integer,
    add column if not exists newswire_about_target integer,
    add column if not exists newswire_eligible    integer,
    add column if not exists verdict_without_wire text,
    add column if not exists decision_trade_date  date,
    add column if not exists evidence_age_s       integer,
    add column if not exists branch               text,
    add column if not exists first_failed_pillar  text,
    add column if not exists adjudicator_version  text,
    add column if not exists own_clusters         integer,
    add column if not exists seed_only            boolean,
    add column if not exists confidence_notes     text,
    add column if not exists catalyst_hard_scored integer,
    add column if not exists catalyst_soft_authors integer,
    add column if not exists proj_quality_gated   boolean,
    add column if not exists proj_sentiment_tilt  numeric,
    add column if not exists price_sector_return_20d numeric,
    add column if not exists price_excess_return_20d numeric,
    add column if not exists price_benchmark      text,
    add column if not exists price_excess_window  integer,
    add column if not exists price_excess_noise   numeric;

-- Cheap now, and the only thing standing between a typo at a future call site
-- and a silent fourth cohort nobody notices. `add constraint` has no IF NOT
-- EXISTS, so each is guarded rather than left to abort the whole file on a
-- second run.
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'signal_log_verdict_chk') then
        alter table public.signal_log add constraint signal_log_verdict_chk
            check (verdict in ('Buy','Watch','Avoid'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'signal_log_feature_chk') then
        alter table public.signal_log add constraint signal_log_feature_chk
            check (feature in ('deep_analyze','discovery'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'signal_log_once_per_event') then
        alter table public.signal_log add constraint signal_log_once_per_event
            unique (event_id, ticker, feature);
    end if;
end $$;

-- ONE index. Scoring joins ticker to price_history by date, so this is the
-- shape every forward-return query takes. A verdict index was considered and
-- dropped: three distinct values over a table growing at ~10k rows a year is a
-- sub-millisecond sequential scan the planner would ignore anyway, and each
-- index is write cost on the request path of a page the user has paid for.
create index if not exists signal_log_ticker_time_idx
    on public.signal_log (ticker, created_at desc);

alter table public.signal_log enable row level security;
