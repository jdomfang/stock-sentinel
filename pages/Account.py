"""Account and credit presentation for Stock Sentinel.

This page intentionally reads existing profile/auth state and delegates the
existing Stripe handoff to ``utils.billing``. It does not own account,
payment, or credit behavior.
"""

from __future__ import annotations

import html
from pathlib import Path as _Path
import sys as _sys

import streamlit as st

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from utils import billing
from utils.auth import flush_pending_rt_save, get_user
from utils.guard import require_active_account
from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, render_footer


st.set_page_config(
    page_title="Account - Stock Sentinel",
    page_icon="👤",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
apply_theme()
flush_pending_rt_save()
profile = require_active_account()
credits = int((profile or {}).get("credits") or 0)
render_top_nav(active="account", credits=credits)

user = get_user() or {}
email = (
    user.get("email") if isinstance(user, dict) else getattr(user, "email", "")
) or ""
safe_email = html.escape(str(email), quote=True)
credit_word = "credit" if credits == 1 else "credits"

st.markdown(
    """
    <style>
      div[data-testid="stMainBlockContainer"] {
        max-width:1100px;margin:0 auto;padding-top:.25rem;
      }
      .ss-account-header {margin:0 0 1.1rem;max-width:720px;}
      .ss-account-header h1 {
        margin:0 0 .35rem;font-size:clamp(2rem,4vw,2.7rem);
        letter-spacing:-.035em;line-height:1.08;
      }
      .ss-account-header p {margin:0;color:var(--muted);line-height:1.5;}
      .ss-account-card {
        border:1px solid var(--border);border-radius:var(--radius-panel);
        background:rgba(15,23,42,.72);padding:1.1rem;
      }
      .ss-account-kicker {
        color:#8192aa;font-size:.7rem;font-weight:800;letter-spacing:.07em;
        text-transform:uppercase;margin-bottom:.35rem;
      }
      .ss-account-card h2 {font-size:1rem;margin:0 0 .8rem;}
      .ss-account-email-label {color:#8192aa;font-size:.72rem;font-weight:750;text-transform:uppercase;letter-spacing:.06em;}
      .ss-account-email {font-size:.94rem;font-weight:680;overflow-wrap:anywhere;margin-top:.2rem;}
      .ss-account-balance {display:flex;align-items:baseline;gap:.45rem;margin-top:.9rem;}
      .ss-account-balance strong {font-size:2rem;color:var(--accent);line-height:1;}
      .ss-account-balance span {color:var(--muted);font-size:.9rem;}
      .ss-account-list {margin:.85rem 0 0;padding:0;list-style:none;}
      .ss-account-list li {
        padding:.62rem 0;border-top:1px solid rgba(148,163,184,.11);
        color:#cbd5e1;font-size:.88rem;line-height:1.4;
      }
      .st-key-account_purchase {
        border:1px solid var(--border);border-radius:var(--radius-panel);
        background:rgba(15,23,42,.72);padding:1.1rem;height:100%;
      }
      .st-key-account_purchase h2 {font-size:1rem;margin:0 0 .3rem;}
      .st-key-account_purchase p {color:var(--muted);font-size:.86rem;margin:0 0 .8rem;}
      @media (max-width:720px) {
        .st-key-account_grid [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;}
        .st-key-account_grid [data-testid="column"] {
          flex:1 1 100%!important;min-width:100%!important;
        }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.html(
    """
    <header class="ss-account-header">
      <h1>Account</h1>
      <p>Manage your credit balance and review how purchases work.</p>
    </header>
    """
)

billing.render_payment_return()

with st.container(key="account_grid"):
    identity_col, purchase_col = st.columns([1.05, .95])
    with identity_col:
        st.html(
            f"""
            <section class="ss-account-card" aria-labelledby="account-overview-title">
              <div class="ss-account-kicker">Signed-in account</div>
              <h2 id="account-overview-title">Account overview</h2>
              <div class="ss-account-email-label">Email address</div>
              <div class="ss-account-email">{safe_email}</div>
              <div class="ss-account-balance">
                <strong>{credits}</strong><span>{credit_word} available</span>
              </div>
              <ul class="ss-account-list">
                <li>One credit runs one Market Scan or one Deep Analyze request.</li>
                <li>Credits never expire.</li>
                <li>Eligible failed runs are automatically refunded. Contact support if your balance does not update.</li>
              </ul>
            </section>
            """
        )
    with purchase_col:
        with st.container(key="account_purchase"):
            st.markdown(
                "<h2>Add credits</h2>"
                "<p>One-time purchase. No subscription and nothing to cancel.</p>",
                unsafe_allow_html=True,
            )
            billing.render_buy_credits(key="account", primary=True)

st.caption(
    "Payments are completed on Stripe. Stock Sentinel does not collect or "
    "display your card number."
)
render_footer()
