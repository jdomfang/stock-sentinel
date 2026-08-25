-- Rotate the stored token in place, instead of minting a credential each time.
--
-- THE PROBLEM
--
-- GoTrue issues a new refresh token on every refresh, so the row behind a
-- remember code goes stale and has to be updated. The first implementation did
-- that by ISSUING A NEW CODE and writing it to the browser -- which works, and
-- leaves the previous code valid for its full thirty days.
--
-- refresh_session_if_needed() runs at the top of the spend pages and again on
-- each per-row Deep Analyze, so codes accumulated at roughly the rate the user
-- clicked. A single busy session could leave a dozen live thirty-day
-- credentials behind, of which the browser holds one. Every earlier one is a
-- working sign-in for anyone who captured it -- from a proxy log, a screenshot,
-- a shared machine -- and nothing revokes them short of an explicit sign-out.
--
-- THE FIX
--
-- The caller remembers the HASH of the code it issued, so it can update that
-- exact row. The browser's code stays the same and stays valid; only the token
-- behind it moves. One credential per device, for as long as that device keeps
-- using it.
--
-- Issuing a new code remains the fallback for the one case that needs it: a
-- restore, where the presented code was consumed by the exchange and the
-- browser genuinely needs a replacement.

create or replace function public.remember_rotate(
    p_code_hash     text,
    p_refresh_token text,
    p_ttl           interval default '30 days'
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hit boolean;
begin
    update public.remember_tokens
       set refresh_token = p_refresh_token,
           -- Slide the expiry forward too. A device in active use should not be
           -- signed out on the thirtieth day because the code it holds was
           -- first issued then; the credential being rotated is what keeps it
           -- alive, and an idle device still ages out on schedule.
           expires_at    = now() + p_ttl
     where code_hash = p_code_hash
       and expires_at > now()
    returning true into v_hit;

    -- FALSE means "no such live row" -- expired, revoked, or never issued. The
    -- caller falls back to issuing a fresh code rather than treating a silent
    -- no-op as success, which would leave a stale token behind a live code and
    -- log the user out on their next visit.
    return coalesce(v_hit, false);
end;
$$;

comment on function public.remember_rotate(text,text,interval) is
    'Update the token behind an EXISTING remember code and slide its expiry. '
    'Keeps one credential per device instead of minting a new thirty-day code '
    'on every session refresh. See 20260825050000_remember_rotate_in_place.sql.';

revoke all on function public.remember_rotate(text,text,interval) from public, anon, authenticated;
grant execute on function public.remember_rotate(text,text,interval) to service_role;
