from __future__ import annotations

import streamlit as st
import json

from utils.supabase_client import get_client


SESSION_KEY = "auth.session"
USER_KEY = "auth.user"
CACHE_SESSION_KEY = "_cached_session"
CACHE_USER_KEY = "_cached_user"
REMEMBER_ME_KEY = "_remember_me"


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
        
        return True, ""
    except Exception as e:
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
        return False, str(e)


def restore_session_from_query_params() -> None:
    """Placeholder.

    Later, when we add magic links / OAuth redirects, we can parse tokens.
    For email/password in Streamlit, we rely on session_state.
    """
    return


def _cache_auth_to_browser(session: dict | None, user: dict | None, email: str, password: str) -> None:
    """Cache auth credentials and session to st.session_state (persists within browser session)."""
    try:
        st.session_state[CACHE_SESSION_KEY] = json.dumps(session) if session else None
        st.session_state[CACHE_USER_KEY] = json.dumps(user) if user else None
        st.session_state[REMEMBER_ME_KEY] = True
    except Exception as e:
        print(f"Warning: Failed to cache auth: {e}")


def _clear_browser_cache() -> None:
    """Clear cached auth from st.session_state."""
    try:
        import streamlit.components.v1 as components
        
        st.session_state.pop(CACHE_SESSION_KEY, None)
        st.session_state.pop(CACHE_USER_KEY, None)
        st.session_state.pop(REMEMBER_ME_KEY, None)
        
        # Inject JS to clear browser localStorage (if available)
        components.html(
            """
            <script>
            localStorage.removeItem('_auth_session');
            localStorage.removeItem('_auth_user');
            localStorage.removeItem('_auth_email');
            localStorage.removeItem('_auth_token');
            sessionStorage.clear();
            </script>
            """,
            height=0,
        )
    except Exception as e:
        print(f"Warning: Failed to clear browser cache: {e}")


def try_restore_cached_session() -> bool:
    """Attempt to restore cached session from st.session_state.
    
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
