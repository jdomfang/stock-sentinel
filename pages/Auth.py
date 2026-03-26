import streamlit as st

from utils.navigation import render_sidebar_navigation
from utils.ui import apply_theme, render_footer
from utils.auth import sign_in, sign_up, is_logged_in, try_restore_cached_session

st.set_page_config(
    page_title="Stock Sentinel - Sign In",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
apply_theme()

# --- Auth-specific layout styling ---
st.markdown(
    """
    <style>
    /* Auth page keeps a centered, focused layout; global theme comes from utils.ui.apply_theme() */

    .stCaption, [data-testid="stCaptionContainer"] {
      text-align: center !important;
    }

    /* Main container spacing */
    div[data-testid="stMainBlockContainer"] {
      max-width: 100%;
      padding-left: 2rem;
      padding-right: 2rem;
      padding-top: 0.75rem;
    }

    .auth-wrapper {
      max-width: 900px;
      margin: 0 auto;
      padding: 0 1rem;
    }

    .auth-form-container {
      max-width: 420px;
      margin: 0 auto;
      position: relative;
    }

    /* Titles */
    .auth-title {
      font-size: 2.4rem;
      font-weight: 850;
      letter-spacing: -0.02em;
      margin: 0;
      line-height: 1.1;
      text-align: center;
    }
    .auth-subtitle {
      color: var(--muted);
      margin-top: 0.75rem;
      margin-bottom: 3rem;
      font-size: 1.0rem;
      text-align: center;
      line-height: 1.5;
    }

    /* Hero section for auth */
    .auth-hero {
      margin: 3rem 0 -5rem 0;
      padding: 2rem 0 1rem 0;
      text-align: center;
    }

    /* Auth mode tabs (prominent) */
    .auth-tabs {
      display: flex;
      justify-content: center;
      gap: 1rem;
      margin-bottom: 2.5rem;
      margin-top: 0.5rem;
    }

    /* HIDE THE DIVIDER BAR COMPLETELY */
    [data-testid="stRadio"] {
      all: revert !important;
      border: none !important;
      border-bottom: none !important;
      border-top: none !important;
      padding: 0 !important;
      margin: 0 !important;
      min-height: 0 !important;
      height: auto !important;
      width: 100% !important;
    }

    [data-testid="stRadio"]::before,
    [data-testid="stRadio"]::after,
    .stRadio::before,
    .stRadio::after {
      display: none !important;
      content: "" !important;
    }

    .stRadio {
      width: 100% !important;
      margin: 0 !important;
      padding: 0 !important;
      border: none !important;
    }

    .stRadio > div {
      flex-direction: row !important;
      justify-content: center !important;
      gap: 0.75rem !important;
      width: 100% !important;
      margin: 0 !important;
      padding: 0 !important;
      border: none !important;
    }
    
    .stRadio label {
      margin-bottom: 0 !important;
      padding: 0.6rem 1.5rem !important;
      border-radius: 999px !important;
      border: 2px solid var(--border) !important;
      background: rgba(2,6,23,.3) !important;
      color: var(--text) !important;
      font-weight: 600 !important;
      font-size: 0.95rem !important;
      cursor: pointer !important;
      transition: all 0.2s ease !important;
    }

    .stRadio label:hover {
      border-color: rgba(56,189,248,.6) !important;
      background: rgba(56,189,248,.1) !important;
    }

    .stRadio label {
      margin-bottom: 0 !important;
      padding: 0.6rem 1.5rem !important;
      border-radius: 999px !important;
      border: 2px solid var(--border) !important;
      background: rgba(2,6,23,.3) !important;
      color: var(--text) !important;
      font-weight: 600 !important;
      font-size: 0.95rem !important;
      cursor: pointer !important;
      transition: all 0.2s ease !important;
    }

    .stRadio label:hover {
      border-color: rgba(56,189,248,.6) !important;
      background: rgba(56,189,248,.1) !important;
    }

    /* Radio button checked state */
    .stRadio [role="radio"][aria-checked="true"] + label {
      border-color: rgba(56,189,248,.95) !important;
      background: rgba(56,189,248,.2) !important;
    }

    /* Hide radio button container div/spacing */
    .stRadio > div {
      margin: 0 !important;
      padding: 0 !important;
      gap: 0.5rem !important;
    }

    /* Collapse stRadio wrapper */
    [data-testid="stRadio"] {
      margin: 0 !important;
      padding: 0 !important;
      min-height: 0 !important;
    }
    
    /* Hide any HR/divider elements */
    hr {
      display: none !important;
    }
    
    /* Hide element dividers */
    [data-testid*="element"] > div {
      border-bottom: none !important;
    }
    
    /* Hide dividers between elements */
    .stVerticalBlock > [data-testid] {
      border-bottom: 0 !important;
    }

    /* Input fields - constrained width */
    .stTextInput {
      max-width: 420px !important;
      margin: 0 auto 0.8rem auto !important;
    }

    /* Center the Remember me checkbox row */
    div[data-testid="stCheckbox"] {
      max-width: 420px !important;
      margin: 0 auto 0.8rem auto !important;
    }
    div[data-testid="stCheckbox"] label {
      justify-content: center !important;
      width: 100% !important;
    }

    [data-baseweb="input"] > div {
      background-color: rgba(2,6,23,.55) !important;
      border-color: var(--border) !important;
      color: var(--text) !important;
      border-radius: 12px !important;
      padding: 0.75rem 1rem !important;
    }

    .stTextInput > label {
      color: var(--text) !important;
      font-weight: 550 !important;
      font-size: 0.95rem !important;
      margin-bottom: 0.5rem !important;
    }

    /* Primary buttons */
    button[data-testid="stBaseButton-primary"],
    .stButton > button[kind="primary"] {
      background: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 700 !important;
      padding: 0.65rem 2rem !important;
      font-size: 0.98rem !important;
      min-height: 44px !important;
      border-radius: 10px !important;
      margin-top: 0.5rem !important;
    }

    .stButton {
      text-align: center !important;
    }

    .stButton > button {
      width: 100% !important;
      max-width: 420px !important;
      margin: 0 auto !important;
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }
    </style>
    
    <script>
    // Remove the divider bar line next to radio buttons
    document.addEventListener('DOMContentLoaded', function() {
      setTimeout(function() {
        const radioElement = document.querySelector('[data-testid="stRadio"]');
        if (radioElement && radioElement.parentElement) {
          // Find and remove any border/line elements
          const parent = radioElement.parentElement;
          parent.style.border = 'none';
          parent.style.borderBottom = '0';
          parent.style.borderTop = '0';
          radioElement.style.borderWidth = '0';
          radioElement.style.borderBottomWidth = '0';
          radioElement.style.borderTopWidth = '0';
        }
      }, 100);
    });
    </script>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="auth-wrapper">', unsafe_allow_html=True)

# Try to restore cached session (Remember Me)
try_restore_cached_session()

def _switch_to_next_page() -> None:
    # Prefer an explicit destination set by a prior page (e.g. Home).
    nxt = (st.session_state.pop("_after_auth_page", None) or "Discovery").strip().lower()
    if nxt in {"deep_analysis", "deep-analysis", "deep", "analysis"}:
        st.switch_page("pages/Deep_Analysis.py")
    elif nxt in {"home"}:
        st.switch_page("pages/Home.py")
    else:
        st.switch_page("pages/Discovery.py")


# Check if already logged in
if is_logged_in():
    st.success("✅ You are already signed in.")

    # Preserve query params across the hop by using a link.
    from utils.scan_intent import get_query_params
    from urllib.parse import urlencode

    qp = get_query_params()
    nxt = (qp.pop("next", None) or "Discovery").strip()
    if nxt.lower() in {"deep_analysis", "deep-analysis", "deep", "analysis"}:
        dest = "/Deep_Analysis"
    elif nxt.lower() in {"home"}:
        dest = "/Home"
    else:
        dest = "/Discovery"

    url = dest + ("?" + urlencode(qp) if qp else "")
    st.link_button("Continue", url, type="primary", use_container_width=True)
    st.stop()

# --- Hero Section ---
st.markdown(
    """
    <div class="auth-hero">
      <div class="auth-title">Welcome to Stock Sentinel</div>
      <div class="auth-subtitle">AI-powered stock sentiment analysis and market intelligence</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- Form Container ---
st.markdown('<div class="auth-form-container">', unsafe_allow_html=True)

# --- Login Form Fields ---
email = st.text_input("Email address", placeholder="you@example.com")
password = st.text_input("Password", type="password", placeholder="••••••••")

# --- Remember Me Checkbox (Sign In only) ---
# Center-align to match the rest of the auth form
_c1, _c2, _c3 = st.columns([1, 2, 1])
with _c2:
    remember_me = st.checkbox("Remember me on this device", value=False)

# --- Auth Mode Toggle (Custom HTML + Hidden Radio) ---
st.markdown('<div style="margin: 0.5rem 0;"></div>', unsafe_allow_html=True)

# Create radio button but hide it completely
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    mode = st.radio("Auth Mode", ["Sign In", "Create Account"], horizontal=True, label_visibility="collapsed")

#st.markdown("<div style='height: 0.3rem;'></div>", unsafe_allow_html=True)
#st.markdown("<div style='margin-top: -0.8rem;'></div>", unsafe_allow_html=True)
st.markdown("<div style='margin-top: -1.2rem;'></div>", unsafe_allow_html=True)

if mode == "Sign In":
    if st.button("Sign In", type="primary", use_container_width=True):
        if not email or not password:
            st.error("Please enter both email and password.")
        else:
            ok, err = sign_in(email.strip(), password, remember_me=remember_me)
            if ok:
                st.success("✅ Signed in successfully!")

                # Preserve query params across the hop by using a link.
                from utils.scan_intent import get_query_params
                from urllib.parse import urlencode

                qp = get_query_params()
                nxt = (qp.pop("next", None) or "Discovery").strip()
                if nxt.lower() in {"deep_analysis", "deep-analysis", "deep", "analysis"}:
                    dest = "/Deep_Analysis"
                elif nxt.lower() in {"home"}:
                    dest = "/Home"
                else:
                    dest = "/Discovery"

                url = dest + ("?" + urlencode(qp) if qp else "")
                st.link_button("Continue", url, type="primary", use_container_width=True)
                st.stop()
            else:
                st.error(err or "Sign in failed. Please check your credentials.")
else:
    st.caption("We'll email you a confirmation link to verify your account.")
    if st.button("Create Account", type="primary", use_container_width=True):
        if not email or not password:
            st.error("Please enter both email and password.")
        else:
            ok, err = sign_up(email.strip(), password)
            if ok:
                st.success("✅ Account created! Check your email to confirm, then sign in.")
            else:
                st.error(err or "Account creation failed. Email may already be in use.")

st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

render_footer()
