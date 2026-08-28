"""Buying credits. One implementation, three call sites.

WHY THIS MODULE EXISTS

The only way to buy credits was an upgrade modal that appeared when
consume_credit REFUSED -- you had to spend everything before you could buy
more. Worse, the two functions behind it were page-private in
pages/Discovery.py, which is exactly why pages/Deep_Analysis.py had no buy
option at all: running out there was a bare st.error() and a dead end.

Home had a "+ Buy Credits" control the whole time. It was an inert <span> with
cursor:not-allowed and title="Coming soon".

ONE WALLET. scan_credits and deep_credits were merged into profiles.credits on
2026-08-24; a scan and a deep analysis each cost 1. render_credit_meter lost its
`kind` argument with that change -- see its docstring for the bug that removed.

WHERE THE AFFORDANCE GOES, and why it costs no layout

Both spend cards already reserve an empty column beside the button that spends
the credit -- Discovery's st.columns([1.4, 0.9, 1.7]) and Deep Analysis's
st.columns([0.55, 0.45, 2.0]). The balance and the buy link go there. Nothing
grows, nothing moves, and the top nav is untouched: utils/navigation.py records
that putting credits in the nav made it taller on login and broke the hero.

THE BALANCE IS ALREADY IN MEMORY. Both pages call require_active_account(),
which returns a profile carrying `credits`. Rendering it
costs zero queries; it was being fetched and discarded.

TWO CLICKS, DELIBERATELY

The checkout URL is not known until a POST to payments-api returns, and
st.link_button needs its href at render time. The alternative -- forcing a
redirect with parent-frame JS -- navigates the app's own tab away and destroys
the Streamlit session. So: click to fetch, then click the real link. Two honest
clicks beat one fragile one.
"""

from __future__ import annotations

import html
import logging

import streamlit as st

logger = logging.getLogger(__name__)

# WHAT A PACK CONTAINS, for display only. payments_api owns the real number --
# it derives the grant from the amount Stripe reports, so this constant cannot
# change what anyone actually receives. It can only make the page LIE about it.
#
# If the two drift, the portal advertises the wrong size and the balance simply
# moves by a different amount. payments_api publishes its live pack table at
# /health, so the check is one request rather than a code read.
PACK_CREDITS = 2
PACK_PRICE = "$5"

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


def render_buy_credits(
    *, key: str, label: str = "", primary: bool = False,
    compact: bool = False,
) -> None:
    """The buy control. Two clicks: request a review, then follow the link.

    `key` is mandatory -- it disambiguates the widget (Streamlit raises on
    duplicate ids) and names the container for CSS.
    """
    import time
    # The default label states both the action and the complete offer. A bare
    # "Buy credits" leaves the user to guess how many; a leading plus sign does
    # not clearly communicate that this starts a purchase.
    label = label or f"Buy {PACK_CREDITS} credits · {PACK_PRICE}"
    uid = _uid()
    url = _pending_url(uid)

    if url:
        if compact:
            if st.button(
                "Review purchase →", key=f"review_{key}",
                type="primary" if primary else "secondary",
                use_container_width=True,
            ):
                st.switch_page("pages/Account.py")
            st.caption("Review the total and Stripe handoff on Account.")
            return

        # This is a purchase REVIEW, not a second upsell. The Checkout Session
        # already exists, but no charge happens until the user completes the
        # Stripe surface. Keeping quantity, total, and terms visible here makes
        # the handoff explicit without changing the payment implementation.
        st.markdown(
            f"""
            <div class="ss-purchase-review" role="region" aria-label="Purchase review">
              <div class="ss-purchase-review-head">
                <strong>Purchase review</strong>
                <span>One-time purchase</span>
              </div>
              <dl>
                <div><dt>Credits</dt><dd>{PACK_CREDITS}</dd></div>
                <div><dt>Total</dt><dd>{PACK_PRICE}</dd></div>
                <div><dt>Credit expiration</dt><dd>Never</dd></div>
                <div><dt>Payment</dt><dd>Secure Stripe checkout</dd></div>
              </dl>
            </div>
            <style>
              .ss-purchase-review {{
                border:1px solid rgba(56,189,248,.24);border-radius:12px;
                background:rgba(8,15,30,.72);padding:.8rem;margin:.15rem 0 .65rem;
              }}
              .ss-purchase-review-head {{display:flex;justify-content:space-between;gap:12px;margin-bottom:.55rem;}}
              .ss-purchase-review-head strong {{font-size:.9rem;}}
              .ss-purchase-review-head span {{color:#94a3b8;font-size:.75rem;}}
              .ss-purchase-review dl {{margin:0;}}
              .ss-purchase-review dl div {{display:flex;justify-content:space-between;gap:16px;padding:.3rem 0;border-top:1px solid rgba(148,163,184,.1);}}
              .ss-purchase-review dt {{color:#94a3b8;font-size:.78rem;}}
              .ss-purchase-review dd {{margin:0;color:#e2e8f0;font-size:.78rem;font-weight:700;text-align:right;}}
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.link_button("Continue to Stripe →", url,
                       type="primary", use_container_width=True)
        st.caption("You can review the final payment details on Stripe before paying.")
        if st.button("Not now", key=f"cancel_{key}", use_container_width=True):
            clear_pending_url()
            st.rerun()
        return

    if st.button(label, key=f"buy_{key}",
                 type="primary" if primary else "secondary",
                 use_container_width=primary):
        with st.spinner("Opening secure checkout…"):
            got, err = get_checkout_url(uid)
        if got:
            st.session_state["billing.url"] = {"uid": uid, "url": got, "at": time.time()}
            if compact:
                st.switch_page("pages/Account.py")
            else:
                st.rerun()
        else:
            # SHOWN, not swallowed. See get_checkout_url.
            st.error(err or "Checkout is unavailable right now.")


def render_credit_meter(*, profile: dict | None, key: str) -> None:
    """Balance + Account link for the compact task toolbar.

    ONE NUMBER, and no `kind` argument. The old signature took kind="scan" or
    kind="deep" and showed only the matching bucket, which produced a real bug
    on Discovery: the meter read the SCAN balance while the per-row button in
    the table below it charged a DEEP credit, so a user could read "3 scans
    left" and be refused by a button two inches away. With one wallet the
    number on screen is the number every button on the page charges.

    The profile comes from require_active_account(), which both spend pages
    already call, so this adds no query.
    """
    n = int((profile or {}).get("credits") or 0)

    with st.container(key=f"credit_meter_{key}"):
        if n <= 0:
            st.markdown(
                '<div class="ss-credit-meter-status ss-credit-meter-status--empty" '
                'style="color:rgba(248,113,113,.95);font-size:0.82rem;'
                'font-weight:700;">Out of credits</div>',
                unsafe_allow_html=True)
            st.page_link(
                "pages/Account.py",
                label=f"Buy {PACK_CREDITS} credits · {PACK_PRICE}",
                use_container_width=False,
            )
            return

        # LOW IS 1, and it has to be, because a pack is PACK_CREDITS = 2.
        #
        # This was <= 3 while the pack was going to be ten-for-$5. Against a
        # pack of two it means a user who has just paid opens the app already in
        # the warning state -- the buy flow's own success condition rendering as
        # a problem. A threshold that is true immediately after buying is not a
        # warning, it is decoration.
        #
        # Expressed against the pack so the two cannot drift: warn once a
        # balance can no longer cover a full pack's worth of work.
        low_at = max(1, PACK_CREDITS - 1)
        colour = "rgba(125,211,252,.95)" if n <= low_at else "rgba(148,163,184,.75)"
        word = "credit" if n == 1 else "credits"
        st.markdown(
            f'<div class="ss-credit-meter-status" style="font-size:0.82rem;'
            f'color:#94a3b8;">'
            f'<b style="color:{colour};font-weight:800;">{n}</b> {word} left</div>',
            unsafe_allow_html=True)
        st.page_link(
            "pages/Account.py", label="Add credits", use_container_width=False,
        )


# The refusal reasons that actually mean "buy something". Every OTHER reason
# consume_credit can return -- account_disabled, profile_not_found, rpc_error,
# bad_response, not_logged_in -- means the purchase would not help, and two of
# those are transient. See render_upgrade_modal.
_BUYABLE_REASONS = frozenset({"no_credits", "no_scan_credits", "no_deep_credits"})


def render_upgrade_modal(reason: str, event_type: str = "", *,
                         key: str = "modal") -> None:
    """The at-zero interruption. A backstop, not the only door.

    ONE PANEL. It used to branch on event_type into scan-flavoured and
    deep-flavoured copy, which was the right call when those were different
    currencies you could be out of independently. There is one wallet now, so
    the only thing that varies is `reason` -- and every call site already passes
    a situational string, so the panel stays contextual without branching.

    `event_type` is accepted and ignored so the three call sites can be updated
    without a flag day.

    Prefer render_credit_refusal() at a spend site -- it decides whether buying
    would even help. Call this directly only where the answer is already known.
    """
    del event_type  # retired; see docstring

    what_you_get = [
        f"{PACK_CREDITS} credits for $5 — one per scan or analysis",
        "Spend them whichever way you want",
        "Eligible failed runs are automatically refunded",
    ]
    safe_reason = html.escape(str(reason), quote=True)
    safe_benefits = [html.escape(item, quote=True) for item in what_you_get]

    st.markdown(
        f"""
        <section class="ss-system-state" data-kind="warning" role="status" aria-live="polite" aria-atomic="true">
          <p class="ss-system-state__eyebrow">Action required</p>
          <h2 class="ss-system-state__title">You're out of credits</h2>
          <p class="ss-system-state__message">{safe_reason}</p>
          <ul style="list-style:none;padding:0;margin:0 0 18px 0;">
            {"".join(f'<li style="color:rgba(229,231,235,.90);font-size:0.93rem;margin-bottom:6px;">✓ {item}</li>' for item in safe_benefits)}
          </ul>
        </section>
        """,
        unsafe_allow_html=True,
    )
    col_a, col_b = st.columns([1.2, 2.8])
    with col_a:
        render_buy_credits(
            key=f"modal_{key}", label="Buy credits →", primary=True,
            compact=True,
        )
    with col_b:
        st.caption("Secure checkout via Stripe. Credits never expire.")


def render_credit_refusal(result, offer: str, *, key: str = "modal") -> None:
    """Render a refused debit. Offers a purchase only when one would help.

    THE BUG THIS EXISTS TO PREVENT. consume_credit refuses for six reasons and
    only one of them is "you have no credits". The others are account_disabled,
    profile_not_found, not_logged_in, and two transient failures -- rpc_error
    and bad_response. Handing all six to the upgrade panel puts "You're out of
    credits" and a primary Buy button in front of:

      * a suspended account, which can then pay $5 and STILL be refused, because
        grant_credits deliberately has no `disabled` guard while consume_credit
        does. Money taken, nothing delivered.
      * every user at once during a Supabase blip, i.e. the product asking the
        whole userbase to pay for an outage.

    Both are worse than the dead end this modal replaced, because a dead end
    does not take money.

    `result` is a CreditResult; `offer` is the situational line shown above the
    benefits when a purchase genuinely is the answer.
    """
    reason = getattr(result, "reason", "") or ""
    if reason in _BUYABLE_REASONS:
        render_upgrade_modal(offer, key=key)
        return

    # Not a balance problem. Say what actually happened -- result.message is the
    # only carrier of that, and it is why utils/credits keeps a reason->text map.
    msg = getattr(result, "message", "") or "Could not use a credit."
    st.error(msg)
    if reason in ("rpc_error", "bad_response"):
        st.caption("This is usually temporary. Try again in a moment — "
                   "you have not been charged a credit.")


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
        "Returned from Stripe. If your payment completed, credits usually "
        "appear within a few seconds — refresh the page if the balance below "
        "has not updated yet."
    )
