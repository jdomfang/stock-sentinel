-- Allow core-api as a feature, and stop the CHECK from silently eating rows.
--
-- signal_log_feature_chk was written when exactly two things produced verdicts,
-- and it did its job: the first core-api analysis was rejected with 23514 and
-- the row was lost. That is the constraint working -- a typo at a new call site
-- would have created a silent fourth cohort -- but the migration adding a third
-- LEGITIMATE producer has to come with it.
--
-- Re-runnable, like the file it amends.

do $$
begin
    if exists (select 1 from pg_constraint where conname = 'signal_log_feature_chk') then
        alter table public.signal_log drop constraint signal_log_feature_chk;
    end if;
    alter table public.signal_log add constraint signal_log_feature_chk
        check (feature in ('deep_analyze', 'discovery', 'core_api'));
end $$;

comment on column public.signal_log.feature is
    'deep_analyze | discovery | core_api. Never pool these without meaning to: '
    'discovery''s evidence comes from a basket query with its own selection '
    'bias, and core_api rows may be replays rather than paid user analyses.';
