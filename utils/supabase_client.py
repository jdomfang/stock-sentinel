"""Supabase clients. Config is portable; only the USER session needs the portal.

WHY THIS SPLIT

Everything in this file read st.secrets at module scope, which made it -- and
therefore utils/finance.py, which imports it -- unimportable outside Streamlit.
But the two clients want completely different things:

  get_admin_client()  service-role key, no user, no session. This is what every
                      background job and every analysis module uses, and it has
                      no business knowing the portal exists.

  get_client()        anon key PLUS the signed-in user's session, so RLS sees
                      the right identity. That genuinely is request state.

So the config reads move to utils.config, and the session becomes an argument
the caller may pass. In the portal nothing changes: get_client() with no
argument still finds st.session_state. Outside it, the admin client works and
the anon client degrades to anonymous instead of raising at import.
"""

from __future__ import annotations

import logging

from utils.config import require

# DELIBERATE PRECEDENCE CHANGE, recorded because it is a behaviour change and
# not a refactor. These three read st.secrets ONLY at HEAD; utils.config is
# environment-first, matching the ten other modules that already were. That is
# the consistency the migration needs -- a container injects config through the
# environment -- but it means a stray SUPABASE_URL in a shell now outranks
# .streamlit/secrets.toml, which is how someone points the portal at a test
# database without noticing. utils/auth.py still reads st.secrets directly for
# the same URL, so the portal currently has two sources of truth for it; that
# is worth collapsing when auth moves.

logger = logging.getLogger(__name__)


def get_supabase_url() -> str:
    return require("SUPABASE_URL")


def get_supabase_anon_key() -> str:
    return require("SUPABASE_ANON_KEY")


def get_supabase_service_role_key() -> str:
    return require("SUPABASE_SERVICE_ROLE_KEY")


def _session_from_portal():
    """The signed-in user's session, if we are running inside Streamlit.

    Lazy and guarded: outside the portal there is no session and that is not an
    error, it is the ordinary case for a worker or a service.
    """
    try:
        import streamlit as st
        return st.session_state.get("auth.session")
    except Exception:
        return None


def get_client(session=None):
    """Anon client, with the user's session restored so RLS sees them.

    `session` is accepted explicitly so a service can pass a token it received
    on the request. When omitted the portal's session_state is consulted, which
    is what every existing caller relies on.
    """
    from supabase import create_client

    sb = create_client(get_supabase_url(), get_supabase_anon_key())
    sess = session if session is not None else _session_from_portal()

    try:
        # supabase-py session objects may expose either shape.
        if isinstance(sess, dict):
            access, refresh = sess.get("access_token"), sess.get("refresh_token")
        else:
            access = getattr(sess, "access_token", None)
            refresh = getattr(sess, "refresh_token", None)
        if access and refresh:
            sb.auth.set_session(access_token=access, refresh_token=refresh)
    except Exception:
        # Falling back to anonymous is the intended behaviour, but it changes
        # what RLS will allow, so it is not silent.
        logger.warning("supabase: session restore failed; client is anonymous",
                       exc_info=True)

    return sb


def get_admin_client():
    """Service-role client. No user, no session, no Streamlit.

    The key bypasses RLS, so this must never be constructed from anything a
    browser can reach.
    """
    from supabase import create_client

    return create_client(get_supabase_url(), get_supabase_service_role_key())
