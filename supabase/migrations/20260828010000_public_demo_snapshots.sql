-- Durable, versioned bundles for the illustrative public product preview.
--
-- WHY THIS EXISTS
--
-- Admin currently writes scan_latest.json and deep_latest.json into the app's
-- local data/education directory. That works only until the process restarts or
-- a new deployment replaces the container. It also lets the two files drift:
-- Home can receive a scan from one run and a Deep Analyze result from another.
--
-- This table stores one coherent scan + analysis bundle outside the hosting
-- filesystem. Publications are append-only. Home reads the newest valid row;
-- retaining older rows provides an audit trail and makes rollback a republish,
-- not a database repair.
--
-- SECURITY
--
-- Neither an anonymous browser nor a signed-in user receives direct table
-- access. RLS is enabled with no policies and table privileges are granted only
-- to service_role. A follow-up migration exposes the single newest reviewed
-- bundle through a narrow read-only function for the public landing page.


-- Keep structural validation in Postgres as a final boundary. Application code
-- will provide clearer validation messages before insert, but the database must
-- still refuse an incomplete or internally inconsistent public demo.
create or replace function public.is_valid_public_demo_bundle(candidate jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    scan_payload     jsonb;
    analysis_payload jsonb;
    scan_rows        jsonb;
    scan_row         jsonb;
    scan_sector      text;
    analysis_sector  text;
    analysis_ticker  text;
    ticker_matches   boolean := false;
begin
    if pg_catalog.jsonb_typeof(candidate) is distinct from 'object' then
        return false;
    end if;

    scan_payload := candidate -> 'scan';
    analysis_payload := candidate -> 'deep_analysis';

    if pg_catalog.jsonb_typeof(scan_payload) is distinct from 'object'
       or pg_catalog.jsonb_typeof(analysis_payload) is distinct from 'object' then
        return false;
    end if;

    scan_sector := pg_catalog.lower(pg_catalog.btrim(scan_payload ->> 'sector'));
    analysis_sector := pg_catalog.lower(
        pg_catalog.btrim(analysis_payload ->> 'sector')
    );
    analysis_ticker := pg_catalog.upper(
        pg_catalog.btrim(analysis_payload ->> 'ticker')
    );
    scan_rows := scan_payload -> 'validated_rows';

    if scan_sector is null or scan_sector = ''
       or analysis_sector is null or analysis_sector = ''
       or scan_sector <> analysis_sector
       or analysis_ticker is null or analysis_ticker = ''
       or pg_catalog.jsonb_typeof(scan_rows) is distinct from 'array'
       or pg_catalog.jsonb_array_length(scan_rows) = 0
       or pg_catalog.jsonb_typeof(
            analysis_payload -> 'analysis_results'
          ) is distinct from 'object'
       or analysis_payload -> 'analysis_results' = '{}'::jsonb then
        return false;
    end if;

    for scan_row in
        select value from pg_catalog.jsonb_array_elements(scan_rows)
    loop
        if pg_catalog.jsonb_typeof(scan_row) is distinct from 'object'
           or pg_catalog.btrim(scan_row ->> 'Ticker') = ''
           or pg_catalog.lower(
                pg_catalog.btrim(scan_row ->> 'Overall Sentiment')
              ) not in ('bullish', 'bearish', 'neutral')
           or pg_catalog.jsonb_typeof(scan_row -> 'Mentions')
                is distinct from 'number'
           or not ((scan_row ->> 'Mentions') ~ '^[0-9]+$') then
            return false;
        end if;

        if pg_catalog.upper(pg_catalog.btrim(scan_row ->> 'Ticker'))
           = analysis_ticker then
            ticker_matches := true;
        end if;
    end loop;

    return ticker_matches;
exception
    -- A check constraint should reject malformed JSON rather than leak a cast
    -- or traversal error back through the public publishing workflow.
    when others then
        return false;
end;
$$;


create table if not exists public.public_demo_snapshots (
    id              uuid primary key default gen_random_uuid(),
    schema_version  smallint not null default 1,
    bundle          jsonb not null,
    published_at    timestamptz not null default now(),

    -- Deliberately not a foreign key. An audit record must survive deletion of
    -- the administrator account that originally published it.
    published_by    uuid not null,

    constraint public_demo_snapshots_schema_version_chk
        check (schema_version = 1),
    constraint public_demo_snapshots_bundle_chk
        check (public.is_valid_public_demo_bundle(bundle))
);

create index if not exists public_demo_snapshots_latest_idx
    on public.public_demo_snapshots (published_at desc, id desc);

alter table public.public_demo_snapshots enable row level security;

-- Be explicit instead of relying on project-level default privileges. There are
-- intentionally no RLS policies: service_role bypasses RLS; everyone else is
-- denied both by privileges and by the absence of a policy.
revoke all on table public.public_demo_snapshots
    from public, anon, authenticated;
grant select, insert on table public.public_demo_snapshots to service_role;

revoke all on function public.is_valid_public_demo_bundle(jsonb)
    from public, anon, authenticated;
grant execute on function public.is_valid_public_demo_bundle(jsonb)
    to service_role;

comment on table public.public_demo_snapshots is
    'Append-only, service-role-only publication history for the public illustrative scan and Deep Analyze bundle.';

comment on column public.public_demo_snapshots.bundle is
    'Schema v1 JSON object with scan and deep_analysis payloads. The analyzed ticker must belong to the scan and every scan row must include Mentions.';
