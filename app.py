import streamlit as st
from utils.navigation import render_sidebar_navigation

# Page configuration
st.set_page_config(
    page_title="X-Stock Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Check for API keys
try:
    x_bearer_token = st.secrets["X_BEARER_TOKEN"]
    polygon_api_key = st.secrets["POLYGON_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Missing API keys – add them in .streamlit/secrets.toml")
    st.stop()

# Main page content
render_sidebar_navigation()

st.session_state.setdefault("selected_ticker", None)
st.session_state.setdefault("selected_sector", None)
st.session_state.setdefault("deep_analysis_results", None)
st.session_state.setdefault("df_valid", None)
st.session_state.setdefault("df_unvalidated", None)

# Reuse the Discovery page as the landing experience
st.switch_page("pages/Discovery.py")
