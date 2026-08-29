-- Narrow public reader for the latest reviewed illustrative demo.
--
-- Browsers still cannot read public_demo_snapshots directly. This function
-- exposes only the newest validated bundle and its publication metadata, so a
-- public landing page (or a future frontend/API) does not need the service-role
-- key merely to render intentionally public product-preview content.

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
    where snapshot.schema_version = 1
      and public.is_valid_public_demo_bundle(snapshot.bundle)
    order by snapshot.published_at desc, snapshot.id desc
    limit 1
$$;

revoke all on function public.get_latest_public_demo() from public;
grant execute on function public.get_latest_public_demo()
    to anon, authenticated, service_role;

comment on function public.get_latest_public_demo() is
    'Returns only the newest validated public illustrative demo; grants no access to publication history or publisher identities.';
