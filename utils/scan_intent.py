"""Helpers for persisting scan/deep-analysis intent across auth redirects.

Why this exists:
- Streamlit reruns reset in-memory state on navigation/login.
- Home -> Auth -> Discovery should preserve the user's intent (e.g. sector)
  and optionally auto-start the scan.

We prefer URL query params because they survive page switches.

This module is intentionally small + dependency-free.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


# ---- Query param wrappers (support both old + new Streamlit APIs) ----

def _normalize_qp_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v)


def get_query_params() -> dict[str, str]:
    """Return query params as a simple str->str dict."""
    # New Streamlit: st.query_params (MutableMapping)
    try:
        qp = dict(st.query_params)  # type: ignore[attr-defined]
        return {k: _normalize_qp_value(v) for k, v in qp.items()}
    except Exception:
        pass

    # Older Streamlit: st.experimental_get_query_params (str -> list[str])
    try:
        qp = st.experimental_get_query_params()  # type: ignore[attr-defined]
        return {k: _normalize_qp_value(v) for k, v in qp.items()}
    except Exception:
        return {}


def set_query_params(params: dict[str, str]) -> None:
    """Replace query params with the provided mapping."""
    clean = {k: _normalize_qp_value(v) for k, v in (params or {}).items() if v is not None}

    try:
        # New Streamlit
        st.query_params.clear()  # type: ignore[attr-defined]
        st.query_params.update(clean)  # type: ignore[attr-defined]
        return
    except Exception:
        pass

    try:
        # Older Streamlit
        st.experimental_set_query_params(**clean)  # type: ignore[attr-defined]
    except Exception:
        # Best-effort only
        return


def patch_query_params(patch: dict[str, str | None]) -> None:
    """Update/remove individual query params."""
    qp = get_query_params()
    for k, v in (patch or {}).items():
        if v is None:
            qp.pop(k, None)
        else:
            qp[k] = _normalize_qp_value(v)
    set_query_params(qp)


# ---- Intent helpers ----

_ALLOWED_KEYS = {
    # navigation
    "next",  # Discovery | Deep_Analysis | Home
    # scan
    "autostart",  # 1/0
    "sector",
    # deep analyze
    "ticker",
}


def set_intent(**kwargs: str) -> None:
    """Write a whitelisted intent payload into query params."""
    qp = get_query_params()
    for k, v in kwargs.items():
        if k not in _ALLOWED_KEYS:
            continue
        qp[k] = _normalize_qp_value(v)
    set_query_params(qp)


def consume_autostart_flag() -> bool:
    """Return True if autostart is requested; also clears it from the URL."""
    qp = get_query_params()
    raw = (qp.get("autostart") or "").strip().lower()
    want = raw in {"1", "true", "yes", "y", "on"}
    if want:
        # Clear autostart to avoid repeated runs on rerun/refresh
        patch_query_params({"autostart": None})
    return want


def get_next_page(default: str = "Discovery") -> str:
    qp = get_query_params()
    nxt = (qp.get("next") or "").strip()
    return nxt or default


# ---- Navigation helpers ----

def _page_to_path(page: str) -> str:
    """Map a logical page name to a URL path used by Streamlit multipage.

    Accepts:
    - "Discovery" / "Auth" / "Home" / "Deep_Analysis"
    - "/Discovery" (already a path)
    """
    p = (page or "").strip()
    if not p:
        return "/Discovery"
    if p.startswith("/"):
        return p

    key = p.strip().lower()
    if key in {"auth", "login", "sign-in", "signin"}:
        return "/Auth"
    if key in {"deep_analysis", "deep-analysis", "deep", "analysis"}:
        return "/Deep_Analysis"
    if key in {"home"}:
        return "/Home"
    return "/Discovery"


def redirect(page: str, params: dict[str, str] | None = None) -> None:
    """Client-side redirect that preserves query params.

    Why JS redirect:
    - `st.switch_page()` may drop query params depending on Streamlit version.
    - We need intent (sector/autostart/ticker) to survive Home -> Auth -> Discovery.

    This stays within the same app origin.
    """
    import urllib.parse
    import streamlit.components.v1 as components

    path = _page_to_path(page)
    qp = {k: _normalize_qp_value(v) for k, v in (params or {}).items() if v is not None and v != ""}
    qs = urllib.parse.urlencode(qp, doseq=False)

    # Build absolute URL in-browser (avoids guessing host/proxy setup)
    path_with_qs = f"{path}{'?' + qs if qs else ''}"
    components.html(
        f"""
        <script>
          (function() {{
            const url = window.location.origin + {path_with_qs!r};
            try {{
              // components.html runs inside an iframe; navigate the parent page.
              window.parent.location.assign(url);
            }} catch (e) {{
              window.location.assign(url);
            }}
          }})();
        </script>
        """,
        height=0,
    )
    st.stop()


def redirect_to_next_page(default: str = "Discovery") -> None:
    """Redirect to the `next=` page, carrying forward current query params."""
    qp = get_query_params()
    nxt = get_next_page(default=default)
    qp.pop("next", None)
    redirect(nxt, qp)


# Public research choices contain no results or payment intent. They survive the
# anonymous -> authenticated boundary separately from private account state.
def _research_choice(kind: str, value: str) -> dict | None:
    import re
    from utils.sector_query import UI_TO_NASDAQ

    if kind == "scan" and value.strip().lower() in UI_TO_NASDAQ:
        return {"kind": kind, "value": value.strip().lower()}
    if kind == "deep" and re.fullmatch(r"[A-Z0-9.\-]{1,6}", value.strip().upper()):
        return {"kind": kind, "value": value.strip().upper()}
    return None


def public_research_intent() -> dict | None:
    """Return a short-lived, nonpaying choice, rejecting other-account intent."""
    import time
    from utils.auth import user_id

    raw = st.session_state.get("_public_research_intent")
    if not isinstance(raw, dict):
        return None
    try:
        valid_age = 0 <= time.time() - float(raw.get("created_at", 0)) <= 1800
    except (TypeError, ValueError):
        valid_age = False
    owner = raw.get("owner") or ""
    choice = _research_choice(str(raw.get("kind", "")), str(raw.get("value", "")))
    if not valid_age or not choice or (owner and owner != user_id()):
        st.session_state.pop("_public_research_intent", None)
        return None
    return choice


def open_research(kind: str, value: str) -> None:
    """Navigate to a selected research task; never request automatic execution."""
    import time
    from utils.auth import is_logged_in, user_id

    choice = _research_choice(kind, value)
    if not choice:
        return
    st.session_state["_public_research_intent"] = {
        **choice, "created_at": time.time(), "owner": user_id(),
    }
    # A new explicit, nonpaying selection must not inherit old autorun flags.
    for key in ("_autostart_discovery_scan", "_autorun_deep_analysis", "_pulse_scan_request"):
        st.session_state.pop(key, None)
    patch_query_params({"autostart": None, "ticker": None, "sector": None})
    destination = "Discovery" if kind == "scan" else "Deep_Analysis"
    if is_logged_in():
        st.switch_page(f"pages/{destination}.py")
    else:
        st.session_state["_after_auth_page"] = destination
        st.session_state["auth_initial_mode"] = "Create Account"
        st.switch_page("pages/Auth.py")


def take_research_intent(kind: str) -> str | None:
    """Consume only on the protected destination, after its identity guard."""
    choice = public_research_intent()
    if not choice or choice["kind"] != kind:
        return None
    st.session_state.pop("_public_research_intent", None)
    return choice["value"]


def queue_pulse_scan(sector: str) -> None:
    """Capture the clicked row before a cache refresh can change its position."""
    from utils.auth import user_id
    choice = _research_choice("scan", sector)
    uid = user_id()
    if choice and uid:
        st.session_state["_pulse_scan_request"] = {"owner": uid, "sector": choice["value"]}


def take_pulse_scan() -> str | None:
    from utils.auth import user_id
    pending = st.session_state.pop("_pulse_scan_request", None)
    if not isinstance(pending, dict) or not pending.get("owner") or pending["owner"] != user_id():
        return None
    choice = _research_choice("scan", str(pending.get("sector", "")))
    return choice["value"] if choice else None
