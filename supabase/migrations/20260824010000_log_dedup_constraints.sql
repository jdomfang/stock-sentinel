-- One paid analysis is one row. Enforced by the database, not by hope.
--
-- signal_log has had `unique (event_id, ticker, feature)` since 2026-08-17.
-- verdict_log and scan_sentiment_log never got the equivalent, and three
-- separate reviews during the core-api migration named the same consequence:
-- a duplicate write lands silently and is INDISTINGUISHABLE afterwards from a
-- genuine second analysis. That is not a cosmetic problem. These tables are
-- the observation set the whole "does this sentiment measurement predict
-- anything" question rests on, and a cohort that double-counts one event has
-- no way back to the truth once the X posts behind it have aged out.
--
-- Concretely, the paths that could produce one:
--   * a client that re-ran an analysis locally after a remote call it could
--     not prove had failed before spending
--   * core-api writing before it answers, plus a caller that writes again
--   * any future third writer that forgets the rule
--
-- The first two are closed in code. This closes them in the schema, which is
-- the only place that keeps holding after the code is rewritten again.
--
-- Verified before writing: 0 duplicates in either table today (9 verdict_log
-- rows, 50 scan_sentiment_log rows), so this cannot fail on existing data.

-- PARTIAL, on `event_id is not null`, and that is deliberate rather than lazy.
--
-- Postgres treats NULLs as distinct in a unique index, so rows without an
-- event_id would be unconstrained regardless -- writing the index as partial
-- says so out loud instead of leaving a reader to work out why the guarantee
-- has a hole. There is exactly one such row today: a verdict_log row written
-- before persist() carried event_id through. An event we cannot identify is an
-- event we cannot dedupe, and inventing an identity for it would be worse.
--
-- A UNIQUE INDEX, not a table constraint, because a constraint cannot be
-- partial. Both reject the second insert the same way: PostgREST returns 409,
-- and both writers already log-and-continue rather than raising into a page
-- the user has paid for.

-- One verdict per (paid event, ticker). persist() writes a single verdict_log
-- row per call, so a second row for one event is always a duplicate.
create unique index if not exists verdict_log_once_per_event
    on public.verdict_log (event_id, ticker)
    where event_id is not null;

-- One observation per (paid scan, ticker). A scan writes one row per VALID
-- ticker -- ten or so per event -- so the pair is the natural identity.
create unique index if not exists scan_sentiment_log_once_per_event
    on public.scan_sentiment_log (event_id, ticker)
    where event_id is not null;

comment on index public.verdict_log_once_per_event is
    'One verdict row per (event_id, ticker). Partial: rows predating event_id '
    'cannot be deduped. See 20260824010000_log_dedup_constraints.sql.';

comment on index public.scan_sentiment_log_once_per_event is
    'One observation per (event_id, ticker). Partial for the same reason as '
    'verdict_log_once_per_event.';
