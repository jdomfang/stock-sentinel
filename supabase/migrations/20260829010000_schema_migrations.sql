-- Record which migrations have been applied. Nothing did.
--
-- THE GAP
--
-- There are 27 migration files, applied BY HAND in the Supabase SQL editor,
-- and no record anywhere of which ones a given database has actually run. "Is
-- production up to date" has been answerable only from memory, and a skipped
-- file is undetectable -- the symptom arrives later as a missing column or a
-- function still running its previous body.
--
-- That is the one irreversible system in this project with no state of its own.
-- Everything else can be re-derived: balances from usage_events, money from
-- Stripe. Schema drift cannot.
--
-- HOW IT IS USED
--
-- Every migration from here on ends with one line:
--
--     insert into public.schema_migrations (version) values ('<this filename>')
--       on conflict do nothing;
--
-- on conflict do nothing, so a re-paste is a no-op rather than an error -- the
-- same property the migrations themselves already have.
--
-- Then "what is live" is a query:
--
--     select version from public.schema_migrations order by version;
--
-- and "what is missing" is that list diffed against `ls supabase/migrations/`.
--
-- THE BACKFILL BELOW IS AN ASSERTION, NOT A MEASUREMENT.
--
-- It claims every migration in the repository as of 20260828 has been
-- applied to this database. That is true for production -- verified 2026-08-29
-- by checking the objects those migrations create: profiles.credits, the merged
-- balances, purchases.credits_granted, remember_tokens, is_open_paid_work,
-- x_posts_billed_since, and the RLS policy set via scripts/verify_security.sql
-- (8/8 PASS).
--
-- It is NOT true of a database restored from an older backup, or a second
-- project stood up later. If you run this file anywhere else, DELETE the rows
-- for migrations that project has not actually run -- otherwise this table will
-- confidently report a schema it does not have, which is worse than the silence
-- it replaces.

create table if not exists public.schema_migrations (
    version    text primary key,
    applied_at timestamptz not null default now()
);

comment on table public.schema_migrations is
    'One row per applied migration, named by filename without .sql. Written by '
    'the last statement of each migration. See 20260829010000.';

-- No RLS policies, deliberately: enabling RLS with none denies anon and
-- authenticated everything, and service_role bypasses. Schema state is
-- operational information and has no business being readable by a user JWT.
alter table public.schema_migrations enable row level security;
revoke all on public.schema_migrations from anon, authenticated;

insert into public.schema_migrations (version) values
    ('20260319_ticker_master'),
    ('20260801010000_purchases'),
    ('20260801020000_credit_integrity'),
    ('20260801030000_admin_adjust_credits'),
    ('20260801040000_backfill_opening_balance'),
    ('20260801050000_grant_credits'),
    ('20260801060000_work_runs'),
    ('20260802010000_caller_identity'),
    ('20260805010000_x_corpus_cache'),
    ('20260806010000_x_call_metrics'),
    ('20260808010000_stock_prices_volume'),
    ('20260809010000_sentiment_cache'),
    ('20260810010000_verdict_log_and_price_history'),
    ('20260810020000_scan_sentiment_log'),
    ('20260817010000_signal_log'),
    ('20260820010000_signal_log_core_api'),
    ('20260820020000_contact_messages'),
    ('20260824010000_log_dedup_constraints'),
    ('20260824020000_reaper_already_refunded'),
    ('20260824030000_merge_credit_buckets'),
    ('20260825010000_security_hardening'),
    ('20260825020000_x_spend_budget'),
    ('20260825030000_remember_tokens'),
    ('20260825040000_paid_work_bypasses_budget'),
    ('20260825050000_remember_rotate_in_place'),
    ('20260828010000_public_demo_snapshots'),
    ('20260828020000_public_demo_reader'),
    -- ITSELF. The backfill above was generated from the files that existed
    -- when this one was written, so it did not include this one -- leaving the
    -- table reporting 27 of 28 migrations from the moment it was created. The
    -- table that exists to detect a missing migration was itself the missing
    -- migration. tests/test_runtime_compat.py now asserts every file in
    -- supabase/migrations/ has a row, which is what caught it.
    ('20260829010000_schema_migrations')
on conflict do nothing;
