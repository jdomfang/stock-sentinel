"""Buying credits. One implementation, three call sites.

WHY THIS MODULE EXISTS

The only way to buy credits was an upgrade modal that appeared when
consume_credit REFUSED -- you had to spend everything before you could buy
more. Worse, the two functions behind it were page-private in
pages/Discovery.py, which is exactly why pages/Deep_Analysis.py had no buy
option at all: running out there was a bare st.error() and a dead end.

Home had a "+ Buy Credits" control the whole time. It was an inert <span> with
cursor:not-allowed and title="Coming soon".

WHERE THE AFFORDANCE GOES, and why it costs no layout

Both spend cards already reserve an empty column beside the button that spends
the credit -- Discovery's st.columns([1.4, 0.9, 1.7]) and Deep Analysis's
st.columns([0.55, 0.45, 2.0]). The balance and the buy link go there. Nothing
grows, nothing moves, and the top nav is untouched: utils/navigation.py records
that putting credits in the nav made it taller on login and broke the hero.

THE BALANCE IS ALREADY IN MEMORY. Both pages call require_active_account(),
which returns a profile carrying scan_credits and deep_credits. Rendering it
costs zero queries; it was being fetched and discarded.

TWO CLICKS, DELIBERATELY

The checkout URL is not known until a POST to payments-api returns, and
st.link_button needs its href at render time. The alternative -- forcing a
redirect with parent-frame JS -- navigates the app's own tab away and destroys
the Streamlit session. So: click to fetch, then click the real link. Two honest
clicks beat one fragile one.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

# How long a fetched Checkout URL stays usable in this session. Stripe Sessions
# remain payable for ~24h, so a stale one in session_state is a second charge
# waiting to happen if the user clicks it again after buying.
_URL_TTL_S = 20 * 60


def _cfg(name: str) -> str:
    """Read a secret without importing streamlit's secrets at module scope."""
    try:
        return (st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def get_checkout_url(user_id: str) -> tuple[str | None, str | None]:
    """Create a Stripe Checkout Session. Returns (url, error_message).

    RETURNS THE REASON, rather than None. The version this replaces did
    `if r.status_code == 200` and `except Exception: pass`, then rendered a
    DISABLED button -- so a user facing a broken payments service saw a dead
    control and no explanation, and an operator saw nothing at all.
    """
    base = _cfg("PAYMENTS_API_BASE_URL").rstrip("/")
    secret = _cfg("PAYMENTS_API_SHARED_SECRET")
    if not base or not secret:
        logger.error("billing: PAYMENTS_API_BASE_URL / SHARED_SECRET not configured")
        return None, "Payments are not configured yet."
    if not user_id:
        return None, "Please sign in first."

    try:
        import requests
        r = requests.post(
            f"{base}/create-checkout-session",
            json={"user_id": user_id},
            headers={"X-Payments-Shared-Secret": secret},
            timeout=12,
        )
    except Exception as e:
        logger.exception("billing: create-checkout-session unreachable")
        return None, "Couldn't reach checkout. Try again in a moment."

    if r.status_code == 200:
        url = (r.json() or {}).get("checkout_url")
        if url:
            return url, None
        logger.error("billing: 200 with no checkout_url: %s", r.text[:200])
        return None, "Checkout did not return a link. Try again."

    # Named, because these two are the ones that actually happen and they mean
    # different things to whoever has to fix them.
    logger.error("billing: create-checkout-session %s: %s", r.status_code, r.text[:200])
    if r.status_code == 401:
        return None, "Payments are misconfigured. Please contact support."
    return None, "Checkout is unavailable right now. Try again shortly."


def _uid() -> str:
    user = st.session_state.get("auth.user") or {}
    return (user.get("id") if isinstance(user, dict)
            else getattr(user, "id", None)) or ""


def _pending_url(uid: str) -> str | None:
    """A Checkout URL fetched moments ago in this session, if still fresh."""
    import time
    got = st.session_state.get("billing.url") or {}
    if got.get("uid") == uid and (time.time() - float(got.get("at") or 0)) < _URL_TTL_S:
        return got.get("url")
    return None


def clear_pending_url() -> None:
    """Forget a fetched Checkout URL.

    Called when the user returns from Stripe. A Session stays payable for about
    a day, so leaving the link on screen after a purchase invites a second one.
    """
    st.session_state.pop("billing.url", None)


def render_buy_credits(*, key: str, label: str = "+ Buy credits",
                       primary: bool = False) -> None:
    """The buy control. Two clicks: fetch, then follow the link.

    `key` is mandatory -- it disambiguates the widget (Streamlit raises on
    duplicate ids) and names the container for CSS.
    """
    import time
    uid = _uid()
    url = _pending_url(uid)

    if url:
        st.link_button("Continue to checkout →", url,
                       type="primary", use_container_width=True)
        st.caption("Opens Stripe. Credits never expire.")
        return

    if st.button(label, key=f"buy_{key}",
                 type="primary" if primary else "secondary",
                 use_container_width=primary):
        with st.spinner("Opening secure checkout…"):
            got, err = get_checkout_url(uid)
        if got:
            st.session_state["billing.url"] = {"uid": uid, "url": got, "at": time.time()}
            st.rerun()
        else:
            # SHOWN, not swallowed. See get_checkout_url.
            st.error(err or "Checkout is unavailable right now.")


def render_credit_meter(*, kind: str, profile: dict | None, key: str) -> None:
    """Balance + buy, for the empty pad beside a spend button.

    `kind` is "scan" or "deep" -- a page shows only the credit it can spend,
    because a number you cannot spend here is noise.

    The profile comes from require_active_account(), which both spend pages
    already call, so this adds no query.
    """
    field = "scan_credits" if kind == "scan" else "deep_credits"
    noun = "scan" if kind == "scan" else "analysis"
    plural = "scans" if kind == "scan" else "analyses"
    n = int((profile or {}).get(field) or 0)

    with st.container(key=f"credit_meter_{key}"):
        if n <= 0:
            # The ONLY loud state, and it is loud because the user is blocked,
            # not to persuade them.
            st.markdown(
                f'<div style="color:rgba(248,113,113,.95);font-size:0.82rem;'
                f'font-weight:700;margin-bottom:4px;">No {plural} left</div>',
                unsafe_allow_html=True)
            render_buy_credits(key=key, label="Buy credits →", primary=True)
            return

        # 1 left turns the NUMBER amber and changes nothing else -- same size,
        # same position. A number that changes colour is information; a banner
        # that appears is pressure.
        colour = "rgba(245,158,11,.95)" if n == 1 else "rgba(148,163,184,.75)"
        word = noun if n == 1 else plural
        st.markdown(
            f'<div style="font-size:0.82rem;color:rgba(148,163,184,.60);'
            f'margin-bottom:2px;">'
            f'<b style="color:{colour};font-weight:800;">{n}</b> {word} left</div>',
            unsafe_allow_html=True)
        render_buy_credits(key=key)


def render_upgrade_modal(reason: str, event_type: str = "scan", *,
                         key: str = "modal") -> None:
    """The at-zero interruption. A backstop now, not the only door.

    Lifted verbatim from pages/Discovery.py, where being page-private meant
    pages/Deep_Analysis.py could not use it and simply dead-ended instead.
    """
    if event_type == "scan":
        icon, title, what_you_get = "📡", "Unlock more scans", [
            "Scan any sector for momentum signals",
            "Processed from real X data in seconds",
            "Shortlist of validated US tickers",
        ]
    else:
        icon, what_you_get = "🔍", [
            "Full sentiment breakdown",
            "Confidence score + trend context",
            "Catalysts, red flags & projections",
            "Clear Buy / Watch / Avoid signal",
        ]
        title = "Unlock Deep Analysis"

    st.markdown(
        f"""
        <div style="
          border:1px solid rgba(56,189,248,.35);
          background:linear-gradient(180deg,rgba(56,189,248,.06),rgba(15,23,42,.92));
          border-radius:16px;
          padding:24px 24px 20px 24px;
          margin:1rem 0;
          box-shadow:0 0 0 1px rgba(56,189,248,.15),0 12px 32px rgba(56,189,248,.08);
        ">
          <div style="font-size:1.4rem;font-weight:800;color:rgba(248,250,252,.98);margin-bottom:6px;">{icon} {title}</div>
          <div style="color:rgba(148,163,184,.85);font-size:0.93rem;margin-bottom:14px;">{reason}</div>
          <ul style="list-style:none;padding:0;margin:0 0 18px 0;">
            {"".join(f'<li style="color:rgba(229,231,235,.90);font-size:0.93rem;margin-bottom:6px;">✓ {item}</li>' for item in what_you_get)}
          </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col_a, col_b = st.columns([1.2, 2.8])
    with col_a:
        render_buy_credits(key=f"modal_{key}", label="Buy credits →", primary=True)
    with col_b:
        st.caption("Secure checkout via Stripe. Credits never expire.")


def consume_payment_return() -> str | None:
    """Read and clear the ?payment= Stripe redirects back to. Call in app.py.

    IT CANNOT BE READ ON Home. app.py ends with an unconditional
    st.switch_page("pages/Home.py"), which raises immediately, and Streamlit
    clears query params across a page switch -- so by the time Home renders,
    the parameter is gone. Every "read it on the landing page" design fails
    silently here.
    """
    try:
        val = (st.query_params.get("payment") or "").strip().lower()
    except Exception:
        return None
    if val not in ("success", "cancel"):
        return None
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.session_state["billing.return"] = val
    # A paid Session must not stay clickable: it is payable for ~24h.
    if val == "success":
        clear_pending_url()
    return val


def render_payment_return() -> None:
    """Acknowledge a return from Stripe. Call once, high on the landing page.

    NEVER ASSERTS THAT CREDITS ARRIVED. They are granted asynchronously by a
    webhook that can lag or fail, so the balance on screen may still be the old
    one. Claiming success we cannot see is how a working purchase gets
    charged back.
    """
    val = st.session_state.pop("billing.return", None)
    if not val:
        return
    if val == "cancel":
        st.info("Checkout cancelled — you have not been charged.")
        return
    st.success(
        "Payment received. Credits usually appear within a few seconds — "
        "refresh the page if the balance below has not updated yet."
    )
