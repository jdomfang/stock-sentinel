from __future__ import annotations

import logging

import streamlit as st
import json

from utils.supabase_client import get_client
from utils import config as _config

# Referenced by the remember-me helpers below. Its absence was a NameError on
# the ORDINARY path -- every expired or already-spent code -- which raised out
# of consume_remember_code, past the caller, into a Streamlit exception page,
# skipping the branch that clears the dead code from localStorage. The next load
# read the same dead code and did it again. An unrecoverable loop, shipped by a
# suite that only ever read this file as text.
logger = logging.getLogger(__name__)


SESSION_KEY = "auth.session"
USER_KEY = "auth.user"
CACHE_SESSION_KEY = "_cached_session"
CACHE_USER_KEY = "_cached_user"
REMEMBER_ME_KEY = "_remember_me"

# Product results live in Streamlit's websocket-local session_state. That
# session survives page navigation and can also survive signing out and signing
# back in without closing the tab. Every key below contains private work,
# derives from private work, or can replay a paid/private action. They therefore
# belong to exactly one authenticated user at a time.
PRODUCT_STATE_OWNER_KEY = "_product_state_owner_user_id"
USER_SCOPED_SESSION_KEYS = frozenset({
    # Market Scan results and provenance.
    "df_valid",
    "df_unvalidated",
    "selected_sector",
    "demo_scan_sector",
    "scan_corpus_age_s",
    "scan_completed_at",
    "scan_result_metadata",
    "_scan_last_close_key",
    "_scan_last_close_map",
    # Deep Analyze results and provenance.
    "selected_ticker",
    "analysis_sector",
    "deep_analysis_card",
    "deep_analysis_results",
    "deep_analysis_completed_at",
    "deep_analysis_metadata",
    "analysis_result_origin",
    # Cross-page intents and in-flight actions. These must never replay for the
    # next person who signs into the same browser tab.
    "_pending_discovery_analysis",
    "_pulse_scan_request",
    "discovery_sector",
    "_intent_sector_applied",
    "_autostart_discovery_scan",
    "_scan_autostart_consumed",
    "prefill_deep_ticker",
    "_autorun_deep_analysis",
    "da_ticker_input",
    # Stripe return/checkout state is private even though billing.url carries a
    # uid of its own. Clearing both is the safer identity-boundary contract.
    "billing.return",
    "billing.url",
})

_IDENTITY_SCOPED_AUTH_KEYS = frozenset({
    "_pending_rt_save",
    "_remember_hash",
})

_AUTH_FORM_KEYS = frozenset({
    # Explicit widget keys can otherwise retain the previous account's email
    # and password in the same Streamlit websocket after logout.
    "auth_email",
    "auth_password",
})

_CURRENT_USER = object()


def user_id(user: object | None = _CURRENT_USER) -> str:
    """Return a stable authenticated user id from a dict or Supabase model."""
    candidate = (
        st.session_state.get(USER_KEY)
        if user is _CURRENT_USER
        else user
    )
    raw = (
        candidate.get("id")
        if isinstance(candidate, dict)
        else getattr(candidate, "id", None)
    )
    return str(raw or "").strip()


def clear_user_scoped_state(*, clear_owner: bool = True) -> None:
    """Remove private product state without disturbing unrelated UI state."""
    for key in USER_SCOPED_SESSION_KEYS:
        st.session_state.pop(key, None)
    if clear_owner:
        st.session_state.pop(PRODUCT_STATE_OWNER_KEY, None)


def _clear_identity_scoped_auth_state(*, include_form: bool = False) -> None:
    """Drop credentials/artifacts that cannot follow a different identity."""
    keys = (
        CACHE_SESSION_KEY,
        CACHE_USER_KEY,
        REMEMBER_ME_KEY,
        *_IDENTITY_SCOPED_AUTH_KEYS,
    )
    if include_form:
        keys = (*keys, *_AUTH_FORM_KEYS)
    for key in keys:
        st.session_state.pop(key, None)


def bind_user_scoped_state(user: object | None = _CURRENT_USER) -> bool:
    """Bind private product state to ``user``, clearing it on any mismatch.

    An absent owner is treated as untrusted legacy state, not implicitly
    adopted. That fail-closed rule prevents results created before this guard
    existed from being exposed to whichever account happens to log in next.
    """
    uid = user_id(user)
    if not uid:
        clear_user_scoped_state()
        return False

    owner = str(st.session_state.get(PRODUCT_STATE_OWNER_KEY) or "").strip()
    if owner != uid:
        clear_user_scoped_state()
    st.session_state[PRODUCT_STATE_OWNER_KEY] = uid
    return True


def ensure_user_scoped_state_owner() -> bool:
    """Enforce the identity boundary for the currently authenticated user."""
    return bind_user_scoped_state(st.session_state.get(USER_KEY))


def user_scoped_state_belongs_to(user: object | None = _CURRENT_USER) -> bool:
    """True only when private state is explicitly owned by ``user``."""
    uid = user_id(user)
    owner = str(st.session_state.get(PRODUCT_STATE_OWNER_KEY) or "").strip()
    return bool(uid and owner == uid)


def _establish_authenticated_state(
    session: object | None,
    user: object | None,
) -> None:
    """Install auth state and enforce isolation before the identity is usable."""
    previous_uid = user_id()
    next_uid = user_id(user)
    owner = str(st.session_state.get(PRODUCT_STATE_OWNER_KEY) or "").strip()
    identity_changed = bool(
        next_uid
        and (
            (previous_uid and previous_uid != next_uid)
            or (owner and owner != next_uid)
        )
    )
    if identity_changed:
        # This can run from the Auth form itself, where Streamlit forbids
        # mutating instantiated widget keys. Form credentials are cleared on
        # logout; this path clears non-widget credential artifacts only.
        _clear_identity_scoped_auth_state()

    st.session_state[SESSION_KEY] = session
    st.session_state[USER_KEY] = user
    bind_user_scoped_state(user)


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

        if new_session or new_user:
            _establish_authenticated_state(
                new_session or st.session_state.get(SESSION_KEY),
                new_user or st.session_state.get(USER_KEY),
            )

        # Update cache if remember-me is on
        if st.session_state.get(REMEMBER_ME_KEY):
            if new_session:
                st.session_state[CACHE_SESSION_KEY] = json.dumps(new_session)
            if new_user:
                st.session_state[CACHE_USER_KEY] = json.dumps(new_user)

            # RE-ISSUE, because GoTrue rotated the refresh token and the one
            # stored in remember_tokens is now revoked.
            #
            # Without this, "remember me" works exactly once and only for users
            # who never load a page: this runs at the top of Discovery and Deep
            # Analysis, so one page view invalidates the stored token. On the
            # next visit the code exchanges cleanly, the token behind it is
            # dead, refresh_session fails, and the user is logged out having
            # spent their single-use code. Replaying a rotated token can also
            # trip GoTrue's reuse detection and revoke the whole family.
            #
            # The row behind the browser's OLD code cannot be updated -- only a
            # hash was stored, and the code itself was never written down. So a
            # new one is issued and the old row is left to expire.
            _rt = new_session.get("refresh_token") if isinstance(new_session, dict) else None
            _uid = new_user.get("id") if isinstance(new_user, dict) else None
            if not _uid:
                _u = st.session_state.get(USER_KEY) or {}
                _uid = _u.get("id") if isinstance(_u, dict) else getattr(_u, "id", None)
            if _rt and _uid:
                # IN PLACE FIRST. The browser's code is still valid and still
                # in localStorage; only the token behind it went stale. Minting
                # a replacement on every refresh left the previous code live for
                # its full thirty days, and this runs on each spend page and
                # each per-row Deep Analyze -- so credentials accumulated at
                # roughly the rate the user clicked.
                if not rotate_remember_token(_rt):
                    # No live row to update: expired, revoked, or this session
                    # restored from a code that the exchange consumed. A new one
                    # is genuinely needed.
                    _code = issue_remember_code(str(_uid), _rt)
                    if _code:
                        _save_token_to_browser(_code)

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
    #
    # REVOKE FIRST, while the user id is still in session_state. Clearing
    # localStorage only removes the browser's copy of the code; the row it
    # points at would otherwise stay valid for thirty days, so anyone holding a
    # copy of that code -- from a shared machine, a screenshot, a proxy log --
    # could still exchange it after the user believed they had signed out.
    try:
        _u = st.session_state.get(USER_KEY) or {}
        _uid = _u.get("id") if isinstance(_u, dict) else getattr(_u, "id", None)
        if _uid:
            revoke_remember_codes(str(_uid))
    except Exception:
        pass

    try:
        sb = get_client()
        sb.auth.sign_out()
    except Exception:
        pass

    clear_user_scoped_state()
    st.session_state.pop("_public_research_intent", None)
    _clear_identity_scoped_auth_state(include_form=True)
    st.session_state.pop(SESSION_KEY, None)
    st.session_state.pop(USER_KEY, None)
    
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

        _establish_authenticated_state(session, user)

        # Cache to browser if remember_me enabled
        if remember_me:
            _cache_auth_to_browser(session, user, email, password)
            # Store refresh token for deferred localStorage write on next page
            # (st.switch_page fires before components.html can execute JS)
            if session:
                rt = session.get("refresh_token") if isinstance(session, dict) else None
                uid = (user or {}).get("id") if isinstance(user, dict) else getattr(user, "id", None)
                if rt and uid:
                    # THE CODE, not the token. The token is written to
                    # remember_tokens and never reaches the browser again.
                    code = issue_remember_code(str(uid), rt)
                    if code:
                        st.session_state["_pending_rt_save"] = code

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

            # utils.config, so "only config.py touches st.secrets" holds with no
            # exceptions to remember. These two are diagnostics inside an
            # except block -- already safe, but an allow-list of "safe"
            # direct reads is a list somebody has to keep correct.
            supabase_url = _config.get("SUPABASE_URL", "")
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

        _establish_authenticated_state(session, user)
        return True, ""
    except Exception as e:
        # Add extra diagnostics for deployment debugging (does NOT print secrets).
        try:
            import socket
            from urllib.parse import urlparse

            # utils.config, so "only config.py touches st.secrets" holds with no
            # exceptions to remember. These two are diagnostics inside an
            # except block -- already safe, but an allow-list of "safe"
            # direct reads is a list somebody has to keep correct.
            supabase_url = _config.get("SUPABASE_URL", "")
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


# ── remember me: an opaque code in the browser, the token on the server ─────
#
# The browser used to hold the Supabase refresh token itself and hand it back
# through the URL, because Streamlit gives browser JS no other way to reach
# Python. A refresh token is a long-lived full-scope credential, and a URL is
# not a private channel: it lands in browser history, the platform request log,
# any reverse proxy, referrer headers and screenshots. Clearing the query param
# on the next rerun tidies the address bar and nothing else.
#
# Now the browser holds only an opaque code. The token lives in
# public.remember_tokens, reachable by service_role alone, and the code is
# exchanged for it exactly once -- the row is deleted by the same statement that
# reads it. A leaked URL therefore yields a code that is already spent.
#
# The code still travels as a query parameter; Streamlit has no other channel.
# What changed is what a leak is worth.

_REMEMBER_TTL_DAYS = 30


def _hash_code(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_remember_code(user_id: str, refresh_token: str) -> str | None:
    """Store the refresh token server-side; return the opaque code for the browser.

    Returns None on failure -- "remember me" is a convenience, and a database
    problem must not stop somebody signing in.
    """
    if not user_id or not refresh_token:
        return None
    import secrets
    code = secrets.token_urlsafe(32)
    try:
        from utils.supabase_client import get_admin_client
        get_admin_client().rpc("remember_issue", {
            "p_code_hash": _hash_code(code),
            "p_user_id": user_id,
            "p_refresh_token": refresh_token,
            "p_ttl": f"{_REMEMBER_TTL_DAYS} days",
            "p_user_agent": None,
        }).execute()
        # REMEMBER WHICH ROW THIS IS. Only the hash is stored server-side, so
        # without keeping it here the rotation path cannot find the row it just
        # created and has to mint another code -- which is how a busy session
        # accumulated a dozen live thirty-day credentials.
        st.session_state["_remember_hash"] = _hash_code(code)
        return code
    except Exception:
        logger.exception("remember: could not issue a code")
        return None


def rotate_remember_token(refresh_token: str) -> bool:
    """Point this session's EXISTING code at a freshly rotated token.

    Returns False when there is no live row to update -- expired, revoked, or
    never issued in this session -- and the caller should issue a new code
    instead. Never raises: remember-me is a convenience.
    """
    code_hash = st.session_state.get("_remember_hash")
    if not code_hash or not refresh_token:
        return False
    try:
        from utils.supabase_client import get_admin_client
        res = get_admin_client().rpc("remember_rotate", {
            "p_code_hash": code_hash,
            "p_refresh_token": refresh_token,
            "p_ttl": f"{_REMEMBER_TTL_DAYS} days",
        }).execute()
        return bool(getattr(res, "data", False))
    except Exception:
        logger.warning("remember: in-place rotation failed", exc_info=True)
        return False


def consume_remember_code(code: str) -> str | None:
    """Exchange an opaque code for its refresh token. One use, then it is gone."""
    if not code:
        return None
    try:
        from utils.supabase_client import get_admin_client
        res = get_admin_client().rpc(
            "remember_consume", {"p_code_hash": _hash_code(code)}).execute()
        data = getattr(res, "data", None) or {}
    except Exception:
        logger.exception("remember: could not exchange a code")
        return None
    # The row is gone either way now -- consume deletes it. Drop any stale hash
    # so the rotation path does not keep trying to update a row that no longer
    # exists and silently stop refreshing.
    st.session_state.pop("_remember_hash", None)
    if not data.get("ok"):
        # unknown_or_used vs expired -- logged, never SHOWN. Telling the caller
        # which would say whether a guessed code was ever real.
        #
        # WARNING, not info, for unknown_or_used specifically: presenting a code
        # that was already spent is the strongest signal available that one has
        # been captured -- from a proxy log, a shared machine, a screenshot. It
        # is also what an ordinary double-load produces, so it is a signal to
        # look at rather than an alarm to act on. Nothing here can revoke the
        # user's other codes, because a spent code no longer identifies anyone.
        if data.get("reason") == "unknown_or_used":
            logger.warning("remember: a spent or unknown code was presented -- "
                           "expected after a double-load, worth investigating "
                           "if it recurs")
        else:
            logger.info("remember: code refused (%s)", data.get("reason"))
        return None
    return data.get("refresh_token")


def revoke_remember_codes(user_id: str) -> None:
    """Drop every stored code for a user. Called on sign-out."""
    if not user_id:
        return
    try:
        from utils.supabase_client import get_admin_client
        get_admin_client().rpc("remember_revoke_all", {"p_user_id": user_id}).execute()
    except Exception:
        logger.exception("remember: could not revoke codes")


def flush_pending_rt_save() -> None:
    """Write the pending remember CODE to localStorage. Call once per page.

    st.switch_page() kills the current render before components.html can
    execute, so the write is deferred to the first page that loads after login.
    Safe to call on every page -- no-ops when nothing is pending.

    The pending value is an OPAQUE CODE, never a refresh token. Named `code`
    rather than `rt` deliberately: the variable was called `rt` when this held a
    refresh token, and the name outliving the change is how a raw token found
    its way back into localStorage once already.
    """
    code = st.session_state.pop("_pending_rt_save", None)
    if code:
        _save_token_to_browser(code)


def _save_token_to_browser(remember_code: str) -> None:
    """Persist the opaque REMEMBER CODE in localStorage (survives tab close).

    Named for what it used to hold. It now holds a single-use code that is
    worthless once exchanged -- never a Supabase refresh token. Keeping the
    localStorage key stable would silently feed an old refresh token into the
    new code path, so the key changed too; an old value is simply ignored and
    the user signs in again once.

    Uses components.html — srcdoc iframes inherit the parent origin so
    window.localStorage inside the iframe IS the same storage as the parent page.
    """
    import streamlit.components.v1 as components
    components.html(
        f"""<script>
        (function(){{
          try {{
            localStorage.setItem('ss_remember_code', {json.dumps(remember_code)});
          }} catch(e) {{}}
        }})();
        </script>""",
        height=0,
    )


def _cache_auth_to_browser(session: dict | None, user: dict | None, email: str = "", password: str = "") -> None:
    """Cache session to st.session_state (within-session fast path) AND localStorage (cross-session)."""
    try:
        st.session_state[CACHE_SESSION_KEY] = json.dumps(session) if session else None
        st.session_state[CACHE_USER_KEY] = json.dumps(user) if user else None
        st.session_state[REMEMBER_ME_KEY] = True
        # NOTHING IS WRITTEN TO localStorage HERE, and that is the point of the
        # whole remember-me change.
        #
        # This used to persist the raw Supabase refresh token. When the storage
        # key was renamed to ss_remember_code, this call came along with it --
        # so the token was still written to the browser, still read back out by
        # the JS in pages/Auth.py, and still pushed into the URL. The change
        # that exists to keep a refresh token out of the address bar put it
        # there anyway, and the test guarding it checked the KEY NAME rather
        # than the VALUE, so it passed.
        #
        # sign_in issues an opaque code through issue_remember_code and defers
        # the write to flush_pending_rt_save. That is the only path allowed to
        # touch localStorage.
    except Exception as e:
        print(f"Warning: Failed to cache auth: {e}")


def _clear_browser_cache() -> None:
    """Clear cached auth from session_state and localStorage."""
    import streamlit.components.v1 as components
    st.session_state.pop(CACHE_SESSION_KEY, None)
    st.session_state.pop(CACHE_USER_KEY, None)
    st.session_state.pop(REMEMBER_ME_KEY, None)
    components.html(
        """<script>
        (function(){ try { localStorage.removeItem('ss_remember_code'); localStorage.removeItem('ss_refresh_token'); } catch(e) {} })();
        </script>""",
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
        _establish_authenticated_state(session, user)
        st.session_state[REMEMBER_ME_KEY] = True
        # Cache to session_state for within-session fast path
        st.session_state[CACHE_SESSION_KEY] = json.dumps(session)
        st.session_state[CACHE_USER_KEY] = json.dumps(user)
        # ROTATE. Supabase issues a new refresh token on every refresh, and the
        # code that got us here has already been consumed, so the browser needs
        # a fresh one or "remember me" works exactly once.
        new_rt = session.get("refresh_token") if isinstance(session, dict) else None
        new_uid = (user or {}).get("id") if isinstance(user, dict) else None
        if new_rt and new_uid:
            code = issue_remember_code(str(new_uid), new_rt)
            if code:
                _save_token_to_browser(code)
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
                _establish_authenticated_state(session, user)
                # _establish_authenticated_state deliberately drops cached
                # credentials if it detects a different prior identity. These
                # local values are the newly verified identity, so restore its
                # fast-path cache after the boundary has been enforced.
                st.session_state[CACHE_SESSION_KEY] = json.dumps(session)
                st.session_state[CACHE_USER_KEY] = json.dumps(user)
                st.session_state[REMEMBER_ME_KEY] = True
                return True
    except Exception as e:
        print(f"Warning: Failed to restore cached session: {e}")
    return False
