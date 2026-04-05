from __future__ import annotations

import streamlit as st
import json

from utils.supabase_client import get_client


SESSION_KEY = "auth.session"
USER_KEY = "auth.user"
CACHE_SESSION_KEY = "_cached_session"
CACHE_USER_KEY = "_cached_user"
REMEMBER_ME_KEY = "_remember_me"


def refresh_session_if_needed() -> bool:
    """Silently refresh Supabase session token if it exists but may be stale.

    Called at the top of pages that run long operations (e.g. Deep Analyze).
    Returns True if session is valid after the attempt.
    """
    session = st.session_state.get(SESSION_KEY)
    if not session:
        return False
    try:
        sb = get_client()
        # Get refresh token from stored session dict
        refresh_token = (
            session.get("refresh_token") if isinstance(session, dict)
            else getattr(session, "refresh_token", None)
        )
        if not refresh_token:
            return bool(st.session_state.get(USER_KEY))

        res = sb.auth.refresh_session(refresh_token)
        new_session = getattr(res, "session", None)
        new_user = getattr(res, "user", None)

        if new_session is not None and hasattr(new_session, "model_dump"):
            new_session = new_session.model_dump()
        if new_user is not None and hasattr(new_user, "model_dump"):
            new_user = new_user.model_dump()

        if new_session:
            st.session_state[SESSION_KEY] = new_session
        if new_user:
            st.session_state[USER_KEY] = new_user

        # Update cache if remember-me is on
        if st.session_state.get(REMEMBER_ME_KEY):
            if new_session:
                st.session_state[CACHE_SESSION_KEY] = json.dumps(new_session)
            if new_user:
                st.session_state[CACHE_USER_KEY] = json.dumps(new_user)

        return True
    except Exception:
        # Non-fatal — session may still be valid
        return bool(st.session_state.get(USER_KEY))


def is_logged_in() -> bool:
    return bool(st.session_state.get(USER_KEY))


def get_user() -> dict | None:
    return st.session_state.get(USER_KEY)


def sign_out() -> None:
    # Best-effort sign out with supabase; always clear local session.
    try:
        sb = get_client()
        sb.auth.sign_out()
    except Exception:
        pass

    st.session_state.pop(SESSION_KEY, None)
    st.session_state.pop(USER_KEY, None)
    st.session_state.pop(CACHE_SESSION_KEY, None)
    st.session_state.pop(CACHE_USER_KEY, None)
    st.session_state.pop(REMEMBER_ME_KEY, None)
    
    # Clear browser cache
    _clear_browser_cache()


def sign_in(email: str, password: str, remember_me: bool = False) -> tuple[bool, str]:
    try:
        sb = get_client()
        res = sb.auth.sign_in_with_password({"email": email, "password": password})

        session = getattr(res, "session", None)
        user = getattr(res, "user", None)

        # Store as plain dict when possible (more stable across reruns)
        if session is not None and hasattr(session, "model_dump"):
            session = session.model_dump()
        if user is not None and hasattr(user, "model_dump"):
            user = user.model_dump()

        st.session_state[SESSION_KEY] = session
        st.session_state[USER_KEY] = user

        # Cache to browser if remember_me enabled
        if remember_me:
            _cache_auth_to_browser(session, user, email, password)
            # Store refresh token for deferred localStorage write on next page
            # (st.switch_page fires before components.html can execute JS)
            if session:
                rt = session.get("refresh_token") if isinstance(session, dict) else None
                if rt:
                    st.session_state["_pending_rt_save"] = rt

        return True, ""
    except Exception as e:
        # Add extra diagnostics for deployment debugging (does NOT print secrets).
        try:
            import socket
            from urllib.parse import urlparse

            def _dns_check(h: str) -> str:
                try:
                    infos = socket.getaddrinfo(h, 443)
                    ips = sorted({info[4][0] for info in infos if info and info[4]})
                    return f"OK ({', '.join(ips[:6])}{'...' if len(ips) > 6 else ''})"
                except Exception as dns_e:
                    return f"FAILED ({dns_e})"

            supabase_url = st.secrets.get("SUPABASE_URL", "")
            host = urlparse(supabase_url).hostname or "(no hostname parsed)"

            debug = (
                f"{e}\n"
                f"SUPABASE_URL={supabase_url}\n"
                f"Parsed host={host}\n"
                f"DNS supabase={_dns_check(host)}\n"
                f"DNS example.com={_dns_check('example.com')}\n"
                f"DNS google.com={_dns_check('google.com')}"
            )
            return False, debug
        except Exception:
            return False, str(e)


def sign_up(email: str, password: str) -> tuple[bool, str]:
    try:
        sb = get_client()
        res = sb.auth.sign_up({"email": email, "password": password})

        # If email confirmation is ON, session may be None until confirmed.
        session = getattr(res, "session", None)
        user = getattr(res, "user", None)

        if session is not None and hasattr(session, "model_dump"):
            session = session.model_dump()
        if user is not None and hasattr(user, "model_dump"):
            user = user.model_dump()

        st.session_state[SESSION_KEY] = session
        st.session_state[USER_KEY] = user
        return True, ""
    except Exception as e:
        # Add extra diagnostics for deployment debugging (does NOT print secrets).
        try:
            import socket
            from urllib.parse import urlparse

            supabase_url = st.secrets.get("SUPABASE_URL", "")
            host = urlparse(supabase_url).hostname or "(no hostname parsed)"

            dns_result = "not checked"
            try:
                infos = socket.getaddrinfo(host, 443)
                ips = sorted({info[4][0] for info in infos if info and info[4]})
                dns_result = f"OK ({', '.join(ips[:6])}{'...' if len(ips) > 6 else ''})"
            except Exception as dns_e:
                dns_result = f"FAILED ({dns_e})"

            debug = (
                f"{e}\n"
                f"SUPABASE_URL={supabase_url}\n"
                f"Parsed host={host}\n"
                f"DNS lookup={dns_result}"
            )
            return False, debug
        except Exception:
            return False, str(e)


def restore_session_from_query_params() -> None:
    """Placeholder.

    Later, when we add magic links / OAuth redirects, we can parse tokens.
    For email/password in Streamlit, we rely on session_state.
    """
    return


def flush_pending_rt_save() -> None:
    """Call once per page (near top) to write pending refresh token to localStorage.

    Because st.switch_page() kills the current render before components.html
    can execute, we defer the localStorage write to the first page that loads
    after login. Safe to call on every page — no-ops when nothing pending.
    """
    rt = st.session_state.pop("_pending_rt_save", None)
    if rt:
        _save_token_to_browser(rt)


def _save_token_to_browser(refresh_token: str) -> None:
    """Inject JS to persist refresh token in localStorage (survives tab close/reopen)."""
    import streamlit.components.v1 as components
    components.html(
        f"""
        <script>
        try {{
            window.parent.localStorage.setItem('ss_refresh_token', {json.dumps(refresh_token)});
        }} catch(e) {{}}
        </script>
        """,
        height=0,
    )


def _cache_auth_to_browser(session: dict | None, user: dict | None, email: str = "", password: str = "") -> None:
    """Cache session to st.session_state (within-session fast path) AND localStorage (cross-session)."""
    try:
        st.session_state[CACHE_SESSION_KEY] = json.dumps(session) if session else None
        st.session_state[CACHE_USER_KEY] = json.dumps(user) if user else None
        st.session_state[REMEMBER_ME_KEY] = True
        # Persist refresh token to localStorage for cross-session restore
        if session:
            rt = session.get("refresh_token") if isinstance(session, dict) else None
            if rt:
                _save_token_to_browser(rt)
    except Exception as e:
        print(f"Warning: Failed to cache auth: {e}")


def _clear_browser_cache() -> None:
    """Clear cached auth from session_state and localStorage."""
    import streamlit.components.v1 as components
    st.session_state.pop(CACHE_SESSION_KEY, None)
    st.session_state.pop(CACHE_USER_KEY, None)
    st.session_state.pop(REMEMBER_ME_KEY, None)
    components.html(
        """
        <script>
        try {
            window.parent.localStorage.removeItem('ss_refresh_token');
        } catch(e) {}
        </script>
        """,
        height=0,
    )


def restore_session_from_refresh_token(refresh_token: str) -> bool:
    """Call Supabase refresh_session with a stored token; restore session on success.

    Used by the Auth page when it detects a saved token in localStorage (via ?rt= query param).
    Returns True if session was restored.
    """
    try:
        sb = get_client()
        res = sb.auth.refresh_session(refresh_token)
        session = getattr(res, "session", None)
        user = getattr(res, "user", None)
        if session is None or user is None:
            return False
        if hasattr(session, "model_dump"):
            session = session.model_dump()
        if hasattr(user, "model_dump"):
            user = user.model_dump()
        st.session_state[SESSION_KEY] = session
        st.session_state[USER_KEY] = user
        st.session_state[REMEMBER_ME_KEY] = True
        # Cache to session_state for within-session fast path
        st.session_state[CACHE_SESSION_KEY] = json.dumps(session)
        st.session_state[CACHE_USER_KEY] = json.dumps(user)
        # Update localStorage with the new refresh token Supabase issued
        new_rt = session.get("refresh_token") if isinstance(session, dict) else None
        if new_rt:
            _save_token_to_browser(new_rt)
        return True
    except Exception as e:
        print(f"Warning: Failed to restore session from refresh token: {e}")
        return False


def try_restore_cached_session() -> bool:
    """Fast-path: restore session from st.session_state (within-session navigation).

    Returns True if session was restored, False otherwise.
    """
    try:
        if st.session_state.get(REMEMBER_ME_KEY):
            cached_session_json = st.session_state.get(CACHE_SESSION_KEY)
            cached_user_json = st.session_state.get(CACHE_USER_KEY)
            if cached_session_json and cached_user_json:
                session = json.loads(cached_session_json)
                user = json.loads(cached_user_json)
                st.session_state[SESSION_KEY] = session
                st.session_state[USER_KEY] = user
                return True
    except Exception as e:
        print(f"Warning: Failed to restore cached session: {e}")
    return False
