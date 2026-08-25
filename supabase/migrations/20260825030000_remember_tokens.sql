-- Stop putting a Supabase refresh token in the URL.
--
-- WHAT IT DID
--
-- "Remember me" wrote the refresh token to localStorage, then -- because
-- Streamlit has no way for browser JS to hand a value to Python -- read it back
-- out and put it in the address bar as ?rt=<refresh_token> so a rerun could see
-- it. The app cleared the parameter on the next rerun, which helps with the
-- address bar and nothing else. By then the token has been written to browser
-- history, the Streamlit Cloud request log, any reverse proxy in front of it,
-- referrer headers on outbound links, and any screenshot of the window.
--
-- A Supabase refresh token is a long-lived, full-scope credential: whoever
-- holds it can mint access tokens for that user until it is revoked. Putting
-- one in a URL is putting it in half a dozen logs nobody is guarding.
--
-- WHAT REPLACES IT
--
-- The browser holds an opaque code and nothing else. The refresh token stays
-- here, server-side. On return the browser presents the code, this exchanges it
-- for the token, and the row is DELETED -- one use, and only one.
--
-- That is strictly better on every axis that matters:
--   * a leaked URL now yields a code that is already spent
--   * the code is scoped to this app; it is not a Supabase credential
--   * every code has an expiry, and logout can revoke all of a user's codes
--   * reuse is detectable, because the second attempt finds nothing
--
-- WHAT IT DOES NOT FIX
--
-- The code still travels as a query parameter, because Streamlit still has no
-- other channel from browser JS to Python. It will still land in history and
-- logs. The difference is that what lands there is single-use, short-lived,
-- revocable, and useless against the Supabase API -- rather than a full-scope
-- credential with a long life.
--
-- ONLY HASHES ARE STORED for the code, so a database leak does not hand anyone
-- a working code. The refresh token itself cannot be hashed -- it has to be
-- replayable to Supabase -- so it is stored as-is, reachable only by
-- service_role, with RLS enabled and NO policies at all.

create table if not exists public.remember_tokens (
    -- sha256(code). The code itself is never written down anywhere but the
    -- browser that was issued it.
    code_hash     text primary key,
    user_id       uuid not null references auth.users(id) on delete cascade,
    refresh_token text not null,
    created_at    timestamptz not null default now(),
    expires_at    timestamptz not null,
    user_agent    text
);

create index if not exists remember_tokens_user_idx on public.remember_tokens (user_id);
create index if not exists remember_tokens_expires_idx on public.remember_tokens (expires_at);

alter table public.remember_tokens enable row level security;
-- DELIBERATELY NO POLICIES. RLS with no policy denies everything to anon and
-- authenticated; service_role bypasses RLS. This table holds credentials and
-- has no business being readable by a user JWT -- not even its owner's.
revoke all on public.remember_tokens from anon, authenticated;

comment on table public.remember_tokens is
    'Server-side half of "remember me". The browser holds only an opaque code; '
    'the refresh token never leaves this table. Rows are single-use and are '
    'deleted on exchange. See 20260825030000_remember_tokens.sql.';


-- Issue. Called right after a successful sign-in when the user asked to be
-- remembered.
create or replace function public.remember_issue(
    p_code_hash     text,
    p_user_id       uuid,
    p_refresh_token text,
    p_ttl           interval default '30 days',
    p_user_agent    text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
    -- Opportunistic cleanup. Expired rows are dead credentials; there is no
    -- reason to keep them and no separate job worth writing for it.
    delete from public.remember_tokens where expires_at < now();

    insert into public.remember_tokens
        (code_hash, user_id, refresh_token, expires_at, user_agent)
    values (p_code_hash, p_user_id, p_refresh_token, now() + p_ttl, p_user_agent)
    on conflict (code_hash) do update
        set refresh_token = excluded.refresh_token,
            expires_at    = excluded.expires_at;
end;
$$;


-- Exchange. Returns the token and DELETES the row in one statement, so two
-- concurrent attempts cannot both succeed: `delete ... returning` takes a row
-- lock and only one transaction sees a row to return.
create or replace function public.remember_consume(p_code_hash text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_user  uuid;
    v_token text;
    v_exp   timestamptz;
begin
    delete from public.remember_tokens
     where code_hash = p_code_hash
    returning user_id, refresh_token, expires_at into v_user, v_token, v_exp;

    if v_user is null then
        -- Either it never existed or it has already been spent. Those are the
        -- same answer to the caller and deliberately indistinguishable: saying
        -- which would tell an attacker whether a guessed code was ever real.
        return jsonb_build_object('ok', false, 'reason', 'unknown_or_used');
    end if;

    -- Expired rows are consumed rather than left behind, so a stale code cannot
    -- be retried until it happens to race a cleanup.
    if v_exp < now() then
        return jsonb_build_object('ok', false, 'reason', 'expired');
    end if;

    return jsonb_build_object('ok', true, 'user_id', v_user, 'refresh_token', v_token);
end;
$$;


-- Revoke everything for a user. Logout, password change, "sign out everywhere".
create or replace function public.remember_revoke_all(p_user_id uuid)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    n integer;
begin
    delete from public.remember_tokens where user_id = p_user_id;
    get diagnostics n = row_count;
    return n;
end;
$$;


-- service_role only, all three. These read and delete credentials.
revoke all on function public.remember_issue(text,uuid,text,interval,text) from public, anon, authenticated;
grant execute on function public.remember_issue(text,uuid,text,interval,text) to service_role;
revoke all on function public.remember_consume(text) from public, anon, authenticated;
grant execute on function public.remember_consume(text) to service_role;
revoke all on function public.remember_revoke_all(uuid) from public, anon, authenticated;
grant execute on function public.remember_revoke_all(uuid) to service_role;
