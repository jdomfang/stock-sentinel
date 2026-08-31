-- Demo snapshot schema v2: complete private source + safe public projection.
--
-- Existing v1 publications remain as immutable audit history. The public RPC
-- now selects only v2 rows and still returns only `bundle`; `source_payload`
-- is service-role-only and never crosses the anonymous reader boundary.

alter table public.public_demo_snapshots
    add column if not exists source_payload jsonb;


create or replace function public.is_valid_public_demo_bundle_v2(candidate jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    scan_payload     jsonb;
    analysis_payload jsonb;
    public_card      jsonb;
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
    public_card := analysis_payload -> 'public_card';
    scan_rows := scan_payload -> 'validated_rows';
    scan_sector := pg_catalog.lower(pg_catalog.btrim(scan_payload ->> 'sector'));
    analysis_sector := pg_catalog.lower(
        pg_catalog.btrim(analysis_payload ->> 'sector')
    );
    analysis_ticker := pg_catalog.upper(
        pg_catalog.btrim(analysis_payload ->> 'ticker')
    );

    if pg_catalog.jsonb_typeof(scan_payload) is distinct from 'object'
       or pg_catalog.jsonb_typeof(analysis_payload) is distinct from 'object'
       or pg_catalog.jsonb_typeof(public_card) is distinct from 'object'
       or pg_catalog.jsonb_typeof(scan_rows) is distinct from 'array'
       or pg_catalog.jsonb_array_length(scan_rows) = 0
       or scan_sector is null or scan_sector = ''
       or analysis_sector is null or analysis_sector <> scan_sector
       or analysis_ticker is null or analysis_ticker = ''
       or pg_catalog.upper(pg_catalog.btrim(public_card ->> 'ticker'))
            <> analysis_ticker
       or pg_catalog.lower(pg_catalog.btrim(public_card ->> 'sector'))
            <> scan_sector
       or pg_catalog.lower(pg_catalog.btrim(public_card ->> 'verdict'))
            not in ('buy', 'watch', 'avoid')
       or pg_catalog.lower(pg_catalog.btrim(public_card ->> 'confidence'))
            not in ('low', 'moderate', 'high')
       or pg_catalog.btrim(public_card ->> 'reason') = ''
       or pg_catalog.jsonb_typeof(public_card -> 'evidence')
            is distinct from 'object'
       or pg_catalog.jsonb_typeof(public_card -> 'movement')
            is distinct from 'object'
       or pg_catalog.jsonb_typeof(public_card -> 'evidence' -> 'price_points')
            is distinct from 'number'
       or ((public_card -> 'evidence' ->> 'price_points')::numeric % 1) <> 0
       or (public_card -> 'evidence' ->> 'price_points')::numeric < 5
       or pg_catalog.jsonb_typeof(public_card -> 'movement' -> 'horizon_days')
            is distinct from 'number'
       or ((public_card -> 'movement' ->> 'horizon_days')::numeric % 1) <> 0
       or (public_card -> 'movement' ->> 'horizon_days')::numeric < 1
       or pg_catalog.jsonb_typeof(public_card -> 'movement' -> 'band_pct')
            is distinct from 'number'
       or (public_card -> 'movement' ->> 'band_pct')::numeric < 0
       or not exists (
            select 1
            from pg_catalog.jsonb_array_elements(public_card -> 'tiles') tile
            where tile ->> 'key' = 'range_30d'
              and pg_catalog.btrim(tile ->> 'value') <> ''
              and pg_catalog.lower(pg_catalog.btrim(tile ->> 'value'))
                    <> 'unavailable'
       ) then
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
           or ((scan_row ->> 'Mentions')::numeric % 1) <> 0
           or (scan_row ->> 'Mentions')::numeric < 0
           or pg_catalog.jsonb_typeof(scan_row -> 'Evidence')
                is distinct from 'number'
           or ((scan_row ->> 'Evidence')::numeric % 1) <> 0
           or (scan_row ->> 'Evidence')::numeric < 0
           or pg_catalog.jsonb_typeof(scan_row -> 'Avg Sentiment Score')
                is distinct from 'number' then
            return false;
        end if;

        if pg_catalog.upper(pg_catalog.btrim(scan_row ->> 'Ticker'))
           = analysis_ticker then
            ticker_matches := true;
        end if;
    end loop;

    return ticker_matches;
exception
    when others then
        return false;
end;
$$;


create or replace function public.is_valid_demo_source_v2(candidate jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    scan_payload     jsonb;
    analysis_payload jsonb;
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
    scan_sector := pg_catalog.lower(pg_catalog.btrim(scan_payload ->> 'sector'));
    analysis_sector := pg_catalog.lower(
        pg_catalog.btrim(analysis_payload ->> 'sector')
    );
    analysis_ticker := pg_catalog.upper(
        pg_catalog.btrim(analysis_payload ->> 'ticker')
    );

    if pg_catalog.jsonb_typeof(scan_payload) is distinct from 'object'
       or pg_catalog.jsonb_typeof(analysis_payload) is distinct from 'object'
       or pg_catalog.jsonb_typeof(scan_payload -> 'rows') is distinct from 'array'
       or pg_catalog.jsonb_array_length(scan_payload -> 'rows') = 0
       or scan_sector is null or scan_sector = ''
       or analysis_sector is null or analysis_sector <> scan_sector
       or analysis_ticker is null or analysis_ticker = ''
       or pg_catalog.jsonb_typeof(analysis_payload -> 'card')
            is distinct from 'object'
       or pg_catalog.upper(
            pg_catalog.btrim(analysis_payload -> 'card' ->> 'ticker')
          ) <> analysis_ticker
       or pg_catalog.jsonb_typeof(analysis_payload -> 'analysis_results')
            is distinct from 'object'
       or analysis_payload -> 'analysis_results' = '{}'::jsonb then
        return false;
    end if;

    for scan_row in
        select value from pg_catalog.jsonb_array_elements(scan_payload -> 'rows')
    loop
        if pg_catalog.jsonb_typeof(scan_row) is distinct from 'object'
           or pg_catalog.btrim(scan_row ->> 'Ticker') = ''
           or pg_catalog.jsonb_typeof(scan_row -> 'Mentions')
                is distinct from 'number'
           or (scan_row ->> 'Mentions')::numeric < 0 then
            return false;
        end if;
        if pg_catalog.upper(pg_catalog.btrim(scan_row ->> 'Ticker'))
           = analysis_ticker then
            ticker_matches := true;
        end if;
    end loop;

    return ticker_matches;
exception
    when others then
        return false;
end;
$$;


create or replace function public.is_coherent_demo_snapshot_v2(
    public_bundle jsonb,
    private_source jsonb
)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
    public_row  jsonb;
    private_row jsonb;
    public_card jsonb := public_bundle -> 'deep_analysis' -> 'public_card';
    source_card jsonb := private_source -> 'deep_analysis' -> 'card';
begin
    -- Make the helper safe when called directly as well as from the table
    -- constraint. SQL's null comparison rules otherwise let two empty objects
    -- fall through the field comparisons and appear coherent.
    if not public.is_valid_public_demo_bundle_v2(public_bundle)
       or not public.is_valid_demo_source_v2(private_source) then
        return false;
    end if;

    if pg_catalog.lower(public_bundle -> 'scan' ->> 'sector')
         <> pg_catalog.lower(private_source -> 'scan' ->> 'sector')
       or pg_catalog.upper(public_bundle -> 'deep_analysis' ->> 'ticker')
         <> pg_catalog.upper(private_source -> 'deep_analysis' ->> 'ticker')
       or public_bundle -> 'scan' ->> 'generated_at'
         <> private_source -> 'scan' ->> 'generated_at'
       or public_bundle -> 'deep_analysis' ->> 'generated_at'
         <> private_source -> 'deep_analysis' ->> 'generated_at'
       or pg_catalog.upper(public_card ->> 'ticker')
         <> pg_catalog.upper(source_card ->> 'ticker')
       or pg_catalog.lower(public_card ->> 'verdict')
         <> pg_catalog.lower(source_card ->> 'verdict')
       or pg_catalog.lower(public_card ->> 'confidence')
         <> pg_catalog.lower(source_card ->> 'confidence')
       or public_card ->> 'reason' <> source_card ->> 'reason'
       or public_card -> 'evidence' -> 'price_points'
         <> source_card -> 'evidence' -> 'price_points'
       or public_card -> 'movement' -> 'horizon_days'
         <> source_card -> 'movement' -> 'horizon_days'
       or public_card -> 'movement' -> 'band_pct'
         <> source_card -> 'movement' -> 'band_pct' then
        return false;
    end if;

    for public_row in
        select value
        from pg_catalog.jsonb_array_elements(
            public_bundle -> 'scan' -> 'validated_rows'
        )
    loop
        select value into private_row
        from pg_catalog.jsonb_array_elements(private_source -> 'scan' -> 'rows')
        where pg_catalog.upper(value ->> 'Ticker')
              = pg_catalog.upper(public_row ->> 'Ticker')
        limit 1;

        if private_row is null
           or public_row -> 'Mentions' <> private_row -> 'Mentions'
           or public_row -> 'Evidence' <> private_row -> 'Evidence'
           or public_row -> 'Avg Sentiment Score'
                <> private_row -> 'Avg Sentiment Score'
           or pg_catalog.lower(public_row ->> 'Overall Sentiment')
                <> pg_catalog.lower(private_row ->> 'Overall Sentiment') then
            return false;
        end if;
    end loop;

    return true;
exception
    when others then
        return false;
end;
$$;


alter table public.public_demo_snapshots
    drop constraint if exists public_demo_snapshots_schema_version_chk;
alter table public.public_demo_snapshots
    drop constraint if exists public_demo_snapshots_bundle_chk;

alter table public.public_demo_snapshots
    add constraint public_demo_snapshots_schema_version_chk
        check (schema_version in (1, 2));
alter table public.public_demo_snapshots
    add constraint public_demo_snapshots_bundle_chk
        check (
            (schema_version = 1 and public.is_valid_public_demo_bundle(bundle))
            or
            (schema_version = 2
             and public.is_valid_public_demo_bundle_v2(bundle)
             and public.is_valid_demo_source_v2(source_payload)
             and public.is_coherent_demo_snapshot_v2(bundle, source_payload))
        );


create or replace function public.get_latest_public_demo()
returns table (
    id uuid,
    schema_version smallint,
    bundle jsonb,
    published_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
    select
        snapshot.id,
        snapshot.schema_version,
        snapshot.bundle,
        snapshot.published_at
    from public.public_demo_snapshots as snapshot
    where snapshot.schema_version = 2
      and public.is_valid_public_demo_bundle_v2(snapshot.bundle)
      and public.is_valid_demo_source_v2(snapshot.source_payload)
      and public.is_coherent_demo_snapshot_v2(
            snapshot.bundle, snapshot.source_payload
          )
    order by snapshot.published_at desc, snapshot.id desc
    limit 1
$$;

revoke all on function public.is_valid_public_demo_bundle_v2(jsonb)
    from public, anon, authenticated;
grant execute on function public.is_valid_public_demo_bundle_v2(jsonb)
    to service_role;
revoke all on function public.is_valid_demo_source_v2(jsonb)
    from public, anon, authenticated;
grant execute on function public.is_valid_demo_source_v2(jsonb)
    to service_role;
revoke all on function public.is_coherent_demo_snapshot_v2(jsonb, jsonb)
    from public, anon, authenticated;
grant execute on function public.is_coherent_demo_snapshot_v2(jsonb, jsonb)
    to service_role;

revoke all on function public.get_latest_public_demo() from public;
grant execute on function public.get_latest_public_demo()
    to anon, authenticated, service_role;

comment on column public.public_demo_snapshots.source_payload is
    'Schema v2 service-role-only complete Scan and Deep Analyze source; never returned by the public reader.';
comment on column public.public_demo_snapshots.bundle is
    'Versioned, allowlisted public projection derived from the canonical private source payload.';

-- Some production projects predate the repository's optional migration
-- ledger. Record this version when the ledger exists, but never make the
-- product migration depend on bookkeeping that is not part of Supabase.
do $migration_record$
begin
    if pg_catalog.to_regclass('public.schema_migrations') is not null then
        insert into public.schema_migrations (version)
        values ('20260830010000_demo_snapshot_v2')
        on conflict do nothing;
    end if;
end
$migration_record$;
