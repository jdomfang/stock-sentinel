"""Static product trust, methodology, data, privacy, and terms surface."""

from __future__ import annotations

from pathlib import Path as _Path
import sys as _sys

import streamlit as st

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, render_footer


st.set_page_config(
    page_title="Trust Center - Stock Sentinel",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_theme()
render_sidebar_navigation()
render_top_nav()

st.markdown(
    """
    <style>
      .ss-trust-header {max-width:760px;margin:0 0 1.2rem;}
      .ss-trust-header h1 {margin:0 0 .35rem;font-size:clamp(2rem,4vw,2.7rem);letter-spacing:-.035em;}
      .ss-trust-header p {margin:0;color:var(--muted);line-height:1.55;}
      .ss-trust-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}
      .ss-trust-card {border:1px solid var(--border);border-radius:14px;background:rgba(15,23,42,.68);padding:1rem;}
      .ss-trust-card h2 {font-size:1rem;margin:0 0 .45rem;}
      .ss-trust-meta {color:#8192aa;font-size:.7rem;margin-bottom:.5rem;}
      .ss-trust-card p,.ss-trust-card li {color:#a8b5c7;font-size:.86rem;line-height:1.55;}
      .ss-trust-card ul {margin:.45rem 0 0;padding-left:1.05rem;}
      .ss-trust-notice {margin-top:1rem;padding:.9rem 1rem;border-left:3px solid var(--accent);background:rgba(56,189,248,.045);color:#b9c6d8;font-size:.86rem;line-height:1.5;}
      @media (max-width:700px) {.ss-trust-grid {grid-template-columns:1fr;}}
    </style>
    """,
    unsafe_allow_html=True,
)

st.html(
    """
    <header class="ss-trust-header">
      <h1>Trust Center</h1>
      <p>How Stock Sentinel presents evidence, handles product data, and sets expectations for short-term market analysis.</p>
    </header>
    <div class="ss-trust-grid">
      <section class="ss-trust-card">
        <h2>Methodology</h2>
        <p>Market Scan identifies unusual social attention and labels sentiment as Bullish, Bearish, or Neutral. Deep Analyze separately combines available social and market evidence into Buy, Watch, or Avoid for short-term triage.</p>
        <ul><li>Market Scan sentiment and Deep Analyze recommendations are separate outputs.</li><li>Deep Analyze evaluates one ticker at a time.</li><li>Available analysis context includes key reasons and, when calculated, its horizon.</li></ul>
      </section>
      <section class="ss-trust-card">
        <h2>Data sources and freshness</h2>
        <p>Current analysis uses recent public posts from X and Polygon market-price and ticker-reference data. Coverage and availability vary by ticker, source, and timeframe.</p>
        <ul><li>Recent social corpora and closing-price data may be cached for reliability and rate-limit management; cached scan context is age-labelled when available.</li><li>Social evidence is a sampled discussion set, not exhaustive or independently verified.</li><li>Missing values are shown as unavailable rather than estimated, and illustrative examples are explicitly identified as not live.</li></ul>
      </section>
      <section class="ss-trust-card">
        <h2>Confidence and limitations</h2>
        <p>Confidence summarizes how consistent and complete the available evidence is. It is not a probability of gain or a guarantee of a future outcome.</p>
        <ul><li>Conflicting or sparse evidence lowers confidence.</li><li>Results identify key reasons and, when available, what could change the call.</li><li>Short-term signals can reverse quickly.</li></ul>
      </section>
      <section class="ss-trust-card">
        <h2>Credits and refunds</h2>
        <p>One credit runs one Market Scan or one Deep Analyze request. The cost and available balance are shown before a signed-in action.</p>
        <ul><li>Credits are one-time purchases and do not expire.</li><li>Eligible failed runs return the reserved credit automatically.</li><li>There is no recurring subscription to cancel.</li></ul>
      </section>
      <section class="ss-trust-card">
        <h2>Privacy and payment handling</h2>
        <div class="ss-trust-meta">Product summary · updated August 26, 2026</div>
        <p>Account information is used to provide authentication, credit access, support, and product operation. Card details are entered on Stripe's checkout surface and are not displayed by Stock Sentinel.</p>
        <ul><li>This is a product summary, not a complete legal privacy policy.</li><li>Do not submit sensitive personal information through ticker inputs or support messages.</li><li>Contact support for access, retention, deletion, or other privacy questions while a formal policy is prepared.</li></ul>
      </section>
      <section class="ss-trust-card">
        <h2>Not financial advice</h2>
        <div class="ss-trust-meta">Product summary · updated August 26, 2026</div>
        <p>Stock Sentinel is an informational research tool. Outputs are not financial advice, an offer, or a guarantee of future performance.</p>
        <ul><li>This is a plain-language product summary, not a complete legal terms document.</li><li>You remain responsible for investment decisions and risk management.</li><li>Market and social data can be delayed, incomplete, or inaccurate.</li></ul>
      </section>
    </div>
    <div class="ss-trust-notice"><strong>Important:</strong> Short-term market signals can change quickly. Verify material information independently before making a financial decision.</div>
    """
)

with st.container(key="trust_contact"):
    st.caption("Questions about methodology, data, privacy, or product use?")
    st.page_link("pages/Contact.py", label="Contact Stock Sentinel")

render_footer()
