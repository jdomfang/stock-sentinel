"""Shared Streamlit theme for Stock Sentinel.

Keep pages visually consistent by centralizing CSS here.
"""

from __future__ import annotations

import streamlit as st


THEME_CSS = """
<style>
/* --- Global dark theme (TradingView-lite) --- */
:root {
  --bg: #0B1220;
  --panel: #0F172A;
  --panel2: rgba(15, 23, 42, 0.55);
  --border: rgba(148, 163, 184, 0.18);
  --text: #E5E7EB;
  --muted: #94A3B8;
  --accent: #38BDF8;
  --good: #22C55E;
  --bad: #EF4444;
  --warn: #F59E0B;
}

/* Ensure text stays readable on dark background */
h1, h2, h3, h4, h5, h6, p, span, div, label {
  color: var(--text);
}
.stCaption, [data-testid="stCaptionContainer"] {
  color: var(--muted) !important;
}

/* Page background */
[data-testid="stAppViewContainer"] {
  background: radial-gradient(1200px 500px at 20% 0%, rgba(56,189,248,.12), transparent 50%),
              radial-gradient(900px 400px at 80% 10%, rgba(34,197,94,.10), transparent 45%),
              var(--bg);
  color: var(--text);
}

/* Streamlit sometimes renders select popovers inside the sidebar layer.
   Make sidebar visually neutral/dark so dropdown menus remain readable. */
section.stSidebar,
.stSidebar,
[data-testid="stSidebar"] {
  background-color: #0B1220 !important;
  background: #0B1220 !important;
}

/* Hide the top-left sidebar toggle / arrow (collapsed control) */
[data-testid="collapsedControl"],
button[title="Open sidebar"],
button[title="Close sidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarNavCollapseButton"],
[data-testid="stSidebarNavExpandButton"] {
  display: none !important;
}

/* If Streamlit portals the dropdown into the sidebar, force its surfaces dark */
.stSidebar ul,
.stSidebar [role="list"],
.stSidebar [role="listbox"],
.stSidebar [data-baseweb="menu"],
[data-testid="stSidebar"] ul,
[data-testid="stSidebar"] [role="list"],
[data-testid="stSidebar"] [role="listbox"],
[data-testid="stSidebar"] [data-baseweb="menu"] {
  background-color: #0F172A !important;
  color: #E5E7EB !important;
}

.stSidebar li,
.stSidebar li *,
[data-testid="stSidebar"] li,
[data-testid="stSidebar"] li * {
  color: #E5E7EB !important;
  opacity: 1 !important;
}

/* Main container spacing */
div[data-testid="stMainBlockContainer"] {
  max-width: 100%;
  padding-left: 2rem;
  padding-right: 2rem;
  padding-top: 0.75rem;
}

.discovery-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 1rem;
}

/* Titles */
.discovery-title {
  font-size: 2.0rem;
  font-weight: 750;
  letter-spacing: -0.02em;
  margin: 0;
  line-height: 1.15;
}
.discovery-subtitle {
  color: var(--muted);
  margin-top: 0.25rem;
  margin-bottom: 1.0rem;
  font-size: 0.98rem;
}

/* Hero (no box) */
.hero {
  margin: 4px 0 16px 0;
  padding: 6px 2px 2px 2px;
}
.hero-eyebrow {
  color: rgba(56,189,248,.95);
  font-weight: 750;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-size: 0.78rem;
  margin-bottom: 10px;
}

/* Card */
.card {
  background: linear-gradient(180deg, rgba(15,23,42,.85), rgba(15,23,42,.65));
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 18px 14px 18px;
  box-shadow: 0 10px 28px rgba(0,0,0,.35);
}

/* Inputs */
[data-baseweb="select"] > div {
  background-color: rgba(15,23,42,.80) !important;
  border: 1px solid var(--border) !important;
  color: var(--text) !important;
}

div[role="listbox"] {
  background-color: #0F172A !important;
  color: var(--text) !important;
}

div[role="option"] {
  color: var(--text) !important;
}

div[role="option"]:hover {
  background-color: rgba(56,189,248,.10) !important;
}

/* Buttons */
.stButton button {
  border-radius: 12px !important;
  border: 1px solid var(--border) !important;
}

.stButton button:hover {
  transform: translateY(-1px);
}

/* Hide Streamlit "Made with" footer */
footer { visibility: hidden; }
</style>
"""


def apply_theme() -> None:
    """Apply the shared Stock Sentinel CSS theme."""
    st.markdown(THEME_CSS, unsafe_allow_html=True)
