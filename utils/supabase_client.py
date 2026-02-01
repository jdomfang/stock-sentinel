from __future__ import annotations

import streamlit as st


def get_supabase_url() -> str:
    url = st.secrets.get("SUPABASE_URL", "")
    if not url:
        raise RuntimeError("Missing SUPABASE_URL in secrets")
    return url


def get_supabase_anon_key() -> str:
    key = st.secrets.get("SUPABASE_ANON_KEY", "")
    if not key:
        raise RuntimeError("Missing SUPABASE_ANON_KEY in secrets")
    return key


def get_supabase_service_role_key() -> str:
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise RuntimeError(
            "Missing SUPABASE_SERVICE_ROLE_KEY in secrets (required for admin actions)"
        )
    return key


def get_client():
    """Return a Supabase client using the anon key.

    If a user session exists in st.session_state, restore it so RLS/auth works.
    """
    from supabase import create_client

    sb = create_client(get_supabase_url(), get_supabase_anon_key())

    sess = st.session_state.get("auth.session")
    try:
        # supabase-py session objects may have access_token/refresh_token attrs
        access = None
        refresh = None
        if isinstance(sess, dict):
            access = sess.get("access_token")
            refresh = sess.get("refresh_token")
        else:
            access = getattr(sess, "access_token", None)
            refresh = getattr(sess, "refresh_token", None)

        if access and refresh:
            sb.auth.set_session(access_token=access, refresh_token=refresh)
    except Exception:
        # If restore fails, client will behave as anon
        pass

    return sb


def get_admin_client():
    """Return a Supabase client using the service role key.

    Keep the service key server-side only (Streamlit secrets).
    """
    from supabase import create_client

    return create_client(get_supabase_url(), get_supabase_service_role_key())
