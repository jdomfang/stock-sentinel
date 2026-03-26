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
