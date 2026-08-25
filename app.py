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
_sys.path.insert(0, str(_Path(__file__).resolve().parents[0]))


from utils.navigation import render_sidebar_navigation

# Page configuration
st.set_page_config(
    page_title="Stock Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Main page content
render_sidebar_navigation()

st.session_state.setdefault("selected_ticker", None)
st.session_state.setdefault("selected_sector", None)
st.session_state.setdefault("deep_analysis_results", None)
st.session_state.setdefault("df_valid", None)
st.session_state.setdefault("df_unvalidated", None)

# BEFORE the switch, and it has to be here. Stripe redirects a buyer back to
# APP_BASE_URL/?payment=success, and st.switch_page below raises immediately
# AND clears query params -- so Home can never see the parameter. Every design
# that reads it on the landing page fails silently.
from utils.billing import consume_payment_return  # noqa: E402
consume_payment_return()

# Route to a proper landing page that can load without API keys.
# The Discovery page will validate API keys when the user enters the app.
st.switch_page("pages/Home.py")
