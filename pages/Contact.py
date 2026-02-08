import logging

import streamlit as st

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page


LOG = logging.getLogger(__name__)

st.set_page_config(
    page_title="Stock Sentinel - Contact",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
render_top_nav()
apply_theme()

support_email = st.secrets.get("SUPPORT_EMAIL", "support@stocksentinel.ai")

st.markdown('<div class="clawd-app-wrapper">', unsafe_allow_html=True)

st.markdown(
    """
    <div style="margin: -22px 0 8px 0;">
      <div style="color: rgba(56,189,248,.95); font-weight: 750; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.78rem; margin-bottom: 10px;">Support</div>
      <div style="font-size: 2.05rem; font-weight: 850; letter-spacing: -0.03em; line-height: 1.1; margin: 0 0 6px 0;">Contact</div>
      <div style="color: rgba(148,163,184,.95); font-size: 1.02rem; line-height: 1.5; margin: 0 0 10px 0; max-width: 980px;">Questions, billing issues, or a bug report? Send a message—include screenshots and the ticker/sector if relevant.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
      .contact-card {
        border: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.72));
        border-radius: 14px;
        padding: 14px 14px;
      }
      .contact-muted { color: rgba(229,231,235,.72); font-size: 0.92rem; line-height: 1.45; }
      @media (max-width: 640px) { .contact-card { padding: 12px; } }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("<div class='contact-card'>", unsafe_allow_html=True)
st.markdown("### Contact us")
st.markdown(
    "<div class='contact-muted'>We typically respond within 1–2 business days.</div>",
    unsafe_allow_html=True,
)

with st.form("contact_form", clear_on_submit=True):
    topic = st.selectbox(
        "Topic",
        ["Question", "Bug report", "Billing", "Feature request", "Partnership"],
        index=0,
    )
    email = st.text_input("Your email", placeholder="you@example.com")
    message = st.text_area(
        "Message",
        placeholder="Include the ticker/sector if relevant. For bugs, share steps to reproduce.",
        height=160,
    )

    submitted = st.form_submit_button("Submit", type="primary")

if submitted:
    payload = {
        "topic": topic,
        "email": email,
        "message": message,
        "user_agent": st.context.headers.get("User-Agent"),
    }
    LOG.info("CONTACT_FORM_SUBMISSION: %s", payload)

    st.success("Message received. If you don’t hear back soon, email us directly (below).")

st.markdown(
    f"<div class='contact-muted'>Or email us directly: <b>{support_email}</b></div>",
    unsafe_allow_html=True,
)
st.markdown("</div>", unsafe_allow_html=True)

close_page()
