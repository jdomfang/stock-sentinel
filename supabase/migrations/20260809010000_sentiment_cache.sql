-- Stop re-scoring text FinBERT has already seen.
--
-- DEPLOY ORDER
--   1..10  (applied)
--   11. THIS FILE
--   12. Deploy. utils/sentiment_cache.py reads and writes it.
--
-- Safe in either order: the module swallows its own errors, so before this
-- table exists every lookup is a miss and scoring behaves exactly as today.
--
-- WHY
--
-- FinBERT is deterministic: the same text at the same model version always
-- produces the same distribution. Yet nothing caches it. Dedup exists only
-- WITHIN a single call, so:
--
--   * a repeat scan of a sector inside the 6h corpus-cache window costs ZERO
--     X API posts and still pays the full ~17s of inference, re-scoring the
--     identical posts it just replayed;
--   * Deep Analyze re-slices one corpus into 8 keyword buckets and scores each
--     separately -- measured at 434 scored texts for a 178-post corpus, 2.4x;
--   * the same post recurs across overlapping sector queries and across days.
--
-- Latency here is not comfort. This is Streamlit: any new interaction stops the
-- running script, and the scan's own source notes that the likeliest way a scan
-- dies is a user clicking again because nothing appears to be happening -- which
-- spends a credit and delivers nothing. Every second cut is abort risk cut.
--
-- KEYED ON MODEL AND TEXT
--
-- text_sha256 is the hash of the EXACT string sent to the model, after the
-- 512-character truncation, with no normalisation. Normalising would mean the
-- cached score belongs to a different string than the one that would be scored,
-- which is a silent correctness bug rather than a cache miss.
--
-- model is part of the key because the whole point of storing a distribution is
-- that it came from a specific model. Swapping ProsusAI/finbert for anything
-- else must miss every row rather than serve the old model's opinions.

create table if not exists public.sentiment_cache (
    text_sha256  text not null,
    model        text not null,

    -- The full class distribution, which is what makes this worth storing.
    -- Callers aggregate on margin = p_positive - p_negative: continuous, so it
    -- cannot tie the way a vote over discrete labels does.
    p_positive   real not null,
    p_negative   real not null,
    p_neutral    real not null,

    -- The derived label at the current threshold, denormalised so a reader does
    -- not have to reimplement the mapping. If the threshold ever changes these
    -- become stale -- which is why the probabilities above are the source of
    -- truth and this is a convenience.
    label        text,
    sentiment    text,

    scored_at    timestamptz not null default now(),

    primary key (text_sha256, model)
);

-- Sweeping old rows, if it ever becomes necessary. A scored text is ~120 bytes,
-- so a year of heavy use is a few hundred MB; there is no urgency.
create index if not exists sentiment_cache_scored_at_idx
    on public.sentiment_cache (scored_at);

-- RLS ON WITH NO POLICIES = service role only, same as x_corpus_cache and
-- x_call_metrics. These are scores of the paid product's input data.
alter table public.sentiment_cache enable row level security;

comment on table public.sentiment_cache is
    'FinBERT class distributions keyed by exact scored text and model. Deterministic model, so a hit is identical to a recompute. Service-role only.';
