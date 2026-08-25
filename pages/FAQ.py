import streamlit as st

# PROJECT ROOT ON sys.path, BEFORE THE FIRST `utils` IMPORT.
#
# Streamlit Cloud can resolve `utils` to an installed site-packages module
# instead of this repo's package. The symptom is not a clean ImportError -- it
# is `KeyError: 'utils'` raised from deep inside the import machinery
# (_find_and_load_unlocked), because the parent package disappears from
# sys.modules midway through loading a submodule.
#
# pages/Discovery.py has carried this guard for exactly that reason; every other
# entrypoint was left exposed, so whichever page a user happened to land on
# first decided whether the app worked. Home is the landing page, so it is the
# one that fails.
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))


from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page


st.set_page_config(
    page_title="Stock Sentinel - FAQ",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
render_top_nav()
apply_theme()

st.markdown('<div class="clawd-app-wrapper">', unsafe_allow_html=True)

st.markdown(
    """
    <div style="margin: -22px 0 8px 0;">
      <div style="color: rgba(56,189,248,.95); font-weight: 750; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.78rem; margin-bottom: 10px;">Support</div>
      <div style="font-size: 2.05rem; font-weight: 850; letter-spacing: -0.03em; line-height: 1.1; margin: 0 0 6px 0;">FAQ</div>
      <div style="color: rgba(148,163,184,.95); font-size: 1.02rem; line-height: 1.5; margin: 0 0 10px 0; max-width: 980px;">Quick answers for short-term traders using Scan + Deep Analyze.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
      .faq-card {
        border: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.72));
        border-radius: 14px;
        padding: 14px 14px;
        margin: 10px 0;
      }
      .faq-note {
        color: rgba(229,231,235,.70);
        font-size: 0.92rem;
        margin-top: 2px;
        margin-bottom: 10px;
      }
      /* Expanders */
      div[data-testid="stExpander"] {
        border: 1px solid rgba(148,163,184,0.18) !important;
        border-radius: 14px !important;
        background: rgba(2,6,23,.18) !important;
      }
      div[data-testid="stExpander"] summary {
        font-weight: 750 !important;
      }
      @media (max-width: 640px) {
        .faq-card { padding: 12px; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

query = st.text_input("Search FAQs", placeholder="Try: credits, confidence, alerts, data source…")

st.markdown("<div class='faq-note'>If you still have questions, use <b>Services → Contact</b> and we’ll help.</div>", unsafe_allow_html=True)


def show(q: str, a: str, *, tags: list[str]):
    if query:
        hay = (q + " " + a + " " + " ".join(tags)).lower()
        if query.lower() not in hay:
            return
    with st.expander(q):
        st.markdown(a)


st.subheader("Getting started")
show(
    "What does Stock Sentinel do?",
    """
Stock Sentinel helps you find **short-term opportunities** by:
- scanning social chatter to surface tickers getting unusual attention
- validating momentum with market data
- generating a clear **Buy / Watch / Avoid** style readout with catalysts + risks

It’s designed to speed up your **idea generation + triage**, not replace your trading plan.
    """.strip(),
    tags=["basics", "scan", "deep analyze"],
)
show(
    "Is this financial advice?",
    """
No. Stock Sentinel is **educational/informational** and is **not financial advice**. Markets move fast; always do your own research and manage risk.
    """.strip(),
    tags=["disclaimer", "advice"],
)

st.subheader("Scan")
show(
    "What is a credit?",
    """
One credit runs **one sector scan or one deep analysis** — they come from the same
balance, so you choose how to spend them. $5 buys 2 credits. Credits never expire,
and a run that fails returns its credit automatically.
    """.strip(),
    tags=["credits", "scan", "billing"],
)
show(
    "Why do some rows show N/A for price?",
    """
Some tickers may be missing a clean recent close due to data gaps, symbol issues, or temporary provider limitations. When that happens, we display **N/A** rather than guessing.
    """.strip(),
    tags=["price", "N/A", "data"],
)

st.subheader("Deep Analyze")
show(
    "What does Buy / Watch / Avoid mean?",
    """
They’re shorthand outputs meant for **short-term trade triage**:
- **Buy**: supportive signals + catalysts, risks noted
- **Watch**: mixed signals; needs confirmation
- **Avoid**: weak/unstable setup or elevated red flags

They’re not a guarantee—think of them as a structured second opinion.
    """.strip(),
    tags=["recommendation", "signals"],
)
show(
    "What does Confidence mean?",
    """
Confidence is an internal summary of how consistent the available signals are with the recommendation. It can be lowered by conflicting indicators, weak data quality, or high uncertainty.
    """.strip(),
    tags=["confidence", "method"],
)

st.subheader("Account & billing")
show(
    "Is there a subscription?",
    """
No. There is nothing to cancel — credits are bought one pack at a time, and you
are only charged when you choose to buy. Credits never expire, so an unused
balance keeps until you use it.
    """.strip(),
    tags=["billing", "cancel", "subscription"],
)
show(
    "Do you offer refunds?",
    """
If something went wrong (billing issue, duplicate charge, etc.), contact us and we’ll make it right. See the Refund Policy in the footer if you’ve published one.
    """.strip(),
    tags=["refund"],
)

st.subheader("Data & reliability")
show(
    "Where does the data come from?",
    """
We use third-party market data providers and public social sources. Specific providers may change over time as we improve reliability.
    """.strip(),
    tags=["data", "providers"],
)
show(
    "Can the site be wrong?",
    """
Yes. Short-term trading is noisy. Social sentiment can reverse quickly, and market data can lag or be impacted by news/halts. Use Stock Sentinel as a **signal**, then confirm with your own checklist.
    """.strip(),
    tags=["limitations", "risk"],
)

close_page()
