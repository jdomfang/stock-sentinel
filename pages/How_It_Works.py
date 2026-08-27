"""Public, credit-free explanation of the Stock Sentinel workflow."""

from __future__ import annotations

from pathlib import Path as _Path
import sys as _sys

import streamlit as st

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, render_footer


st.set_page_config(
    page_title="How It Works - Stock Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
render_sidebar_navigation()
render_top_nav(active="how_it_works")
apply_theme()

st.markdown(
    """
    <style>
      div[data-testid="stMainBlockContainer"] {max-width:1100px;margin:0 auto;padding-top:.25rem;}
      .ss-how-hero {max-width:800px;margin:0 0 1.35rem;}
      .ss-how-kicker {color:#7dd3fc;font-size:.73rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.55rem;}
      .ss-how-hero h1 {margin:0 0 .45rem;font-size:clamp(2.15rem,5vw,3.35rem);letter-spacing:-.045em;line-height:1.03;}
      .ss-how-hero p {margin:0;color:#a8b5c7;font-size:1rem;line-height:1.6;max-width:720px;}
      .ss-how-flow {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:1.3rem 0;}
      .ss-how-step {position:relative;border:1px solid var(--border);border-radius:15px;background:rgba(15,23,42,.7);padding:1.15rem;min-height:185px;}
      .ss-how-number {display:inline-flex;width:30px;height:30px;align-items:center;justify-content:center;border:1px solid rgba(56,189,248,.4);border-radius:9px;color:#7dd3fc;font-size:.78rem;font-weight:800;margin-bottom:.9rem;}
      .ss-how-step h2 {font-size:1.04rem;margin:0 0 .42rem;}
      .ss-how-step p {color:#a8b5c7;font-size:.86rem;line-height:1.55;margin:0;}
      .ss-how-output {border:1px solid rgba(56,189,248,.3);border-radius:15px;background:linear-gradient(135deg,rgba(56,189,248,.07),rgba(15,23,42,.6));padding:1.15rem;margin:0 0 1.2rem;}
      .ss-how-output h2 {font-size:1.05rem;margin:0 0 .65rem;}
      .ss-how-boundary {display:grid;grid-template-columns:1fr 1fr;gap:12px;}
      .ss-how-boundary div {border-top:1px solid rgba(148,163,184,.14);padding-top:.75rem;}
      .ss-how-boundary strong {display:block;color:#f1f5f9;font-size:.86rem;margin-bottom:.25rem;}
      .ss-how-boundary span {color:#94a3b8;font-size:.82rem;line-height:1.45;}
      .ss-how-method {margin:1.35rem 0;}
      .ss-how-method > h2 {font-size:1.15rem;margin:0 0 .3rem;}
      .ss-how-method > p {color:#94a3b8;font-size:.86rem;line-height:1.5;margin:0 0 .85rem;max-width:760px;}
      .ss-how-method-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;}
      .ss-how-method-card {border-top:1px solid rgba(148,163,184,.18);padding:.85rem .15rem 0;}
      .ss-how-method-card strong {display:block;font-size:.86rem;margin-bottom:.28rem;}
      .ss-how-method-card span {display:block;color:#94a3b8;font-size:.8rem;line-height:1.5;}
      .st-key-how_trust_link {margin:-.35rem 0 1.2rem;}
      .st-key-how_trust_link [data-testid="stPageLink"] a {display:inline-flex;width:auto;color:#7dd3fc!important;font-size:.82rem;text-decoration:none!important;padding:0!important;}
      .st-key-how_trust_link [data-testid="stPageLink"] a:hover {text-decoration:underline!important;}
      .st-key-how_cta {border:1px solid var(--border);border-radius:15px;background:rgba(15,23,42,.55);padding:1rem;margin-bottom:.5rem;}
      .st-key-how_cta [data-testid="stHorizontalBlock"] {align-items:center!important;}
      .ss-how-cta-copy strong {display:block;font-size:1rem;margin-bottom:.2rem;}
      .ss-how-cta-copy span {color:#94a3b8;font-size:.83rem;}
      @media (max-width:760px) {
        .ss-how-flow {grid-template-columns:1fr;}
        .ss-how-step {min-height:0;}
        .ss-how-boundary {grid-template-columns:1fr;}
        .ss-how-method-grid {grid-template-columns:1fr;}
        .st-key-how_cta [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;}
        .st-key-how_cta [data-testid="column"] {flex:1 1 100%!important;min-width:100%!important;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.html(
    """
    <header class="ss-how-hero">
      <div class="ss-how-kicker">Product workflow</div>
      <h1>How Stock Sentinel works</h1>
      <p>Scan and Deep Analyze solve different problems. The scan creates a sentiment shortlist; Deep Analyze evaluates one selected ticker and produces a separate action recommendation.</p>
    </header>
    <section class="ss-how-flow" aria-label="Stock Sentinel workflow">
      <article class="ss-how-step">
        <span class="ss-how-number">01</span>
        <h2>Scan a sector</h2>
        <p>Choose a sector to find stocks receiving unusual recent social attention. Scan results use only Bullish, Bearish, or Neutral sentiment.</p>
      </article>
      <article class="ss-how-step">
        <span class="ss-how-number">02</span>
        <h2>Select one ticker</h2>
        <p>Review the shortlist, price context, sentiment, and evidence availability. No Buy, Watch, or Avoid recommendation appears at this stage.</p>
      </article>
      <article class="ss-how-step">
        <span class="ss-how-number">03</span>
        <h2>Run Deep Analyze</h2>
        <p>Analyze the selected ticker to receive Buy, Watch, or Avoid, confidence, key reasons, risks, and the evidence behind the result.</p>
      </article>
    </section>
    <section class="ss-how-output" aria-labelledby="output-boundary">
      <h2 id="output-boundary">Two actions, one clear credit model</h2>
      <div class="ss-how-boundary">
        <div><strong>Market Scan · 1 credit</strong><span>Returns a ranked sector shortlist with Bullish, Bearish, or Neutral sentiment.</span></div>
        <div><strong>Deep Analyze · 1 credit</strong><span>Returns a Buy, Watch, or Avoid recommendation for one ticker, with supporting context.</span></div>
      </div>
    </section>
    <section class="ss-how-method" aria-labelledby="signal-formation">
      <h2 id="signal-formation">How the signal is formed</h2>
      <p>Stock Sentinel shows the context needed to judge a result instead of presenting a recommendation as certainty.</p>
      <div class="ss-how-method-grid">
        <div class="ss-how-method-card"><strong>Evidence and freshness</strong><span>When available, results show source counts, time horizons, and data freshness. Coverage can vary by ticker.</span></div>
        <div class="ss-how-method-card"><strong>Confidence is not probability</strong><span>Confidence summarizes the consistency and quality of available evidence. It does not predict the chance of a gain.</span></div>
        <div class="ss-how-method-card"><strong>Uncertainty stays visible</strong><span>Social and market data can be incomplete, delayed, or change quickly. Results support research; they are not financial advice.</span></div>
      </div>
    </section>
    """
)

with st.container(key="how_trust_link"):
    st.page_link(
        "pages/Trust_Center.py",
        label="Read the Trust Center",
    )

with st.container(key="how_cta"):
    copy_col, action_col = st.columns([3, 1])
    with copy_col:
        st.markdown(
            '<div class="ss-how-cta-copy"><strong>Ready to try the workflow?</strong>'
            '<span>Create an account with 2 free credits. No card required.</span></div>',
            unsafe_allow_html=True,
        )
    with action_col:
        if st.button("Start free", type="primary", use_container_width=True):
            st.session_state["auth_initial_mode"] = "Create Account"
            st.session_state["_after_auth_page"] = "Home"
            st.switch_page("pages/Auth.py")

render_footer()
