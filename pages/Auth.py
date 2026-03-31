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
    if st.button("Continue", type="primary", use_container_width=True):
        _switch_to_next_page()
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

# ── Auth mode toggle ──
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    mode = st.radio("Auth Mode", ["Sign In", "Create Account"], horizontal=True, label_visibility="collapsed")

st.markdown("<div style='margin-top:-1.2rem;'></div>", unsafe_allow_html=True)

# ── Autofill-compatible HTML form ──
# Uses a real <form> with name/id/autocomplete attrs so Chrome/Safari/Firefox
# password managers can detect, save, and autofill credentials.
# Values are posted back to Streamlit via a hidden st.text_input bridge.
import streamlit.components.v1 as _auth_comp

_form_id = "clawd-auth-form"
_autocomplete_pw = "current-password" if mode == "Sign In" else "new-password"

_auth_comp.html(
    f"""
    <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:transparent; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
    form {{ display:flex; flex-direction:column; gap:12px; width:100%; max-width:420px; margin:0 auto; padding:4px 0; }}
    .field-label {{ font-size:0.92rem; font-weight:550; color:rgba(229,231,235,.90); margin-bottom:4px; display:block; }}
    input[type=email], input[type=password] {{
      width:100%; padding:12px 16px;
      background:rgba(2,6,23,.55);
      border:1px solid rgba(148,163,184,.25);
      border-radius:12px; color:#E5E7EB;
      font-size:0.95rem; outline:none;
      transition:border-color 0.15s ease;
    }}
    input[type=email]:focus, input[type=password]:focus {{
      border-color:rgba(56,189,248,.60);
    }}
    input::placeholder {{ color:rgba(148,163,184,.50); }}
    .remember {{ display:flex; align-items:center; gap:8px; font-size:0.88rem; color:rgba(148,163,184,.80); cursor:pointer; }}
    .remember input {{ width:16px; height:16px; accent-color:rgba(56,189,248,.90); }}
    .submit-btn {{
      width:100%; padding:12px;
      background:linear-gradient(180deg,rgba(56,189,248,.95),rgba(14,116,144,.95));
      border:1px solid rgba(56,189,248,.45); border-radius:10px;
      color:#001018; font-size:0.96rem; font-weight:700;
      cursor:pointer; margin-top:4px;
      transition:opacity 0.15s ease;
    }}
    .submit-btn:hover {{ opacity:0.88; }}
    </style>
    <form id="{_form_id}" autocomplete="on" onsubmit="handleSubmit(event)">
      <div>
        <label class="field-label" for="auth-email">Email address</label>
        <input type="email" id="auth-email" name="email"
          autocomplete="email" placeholder="you@example.com"
          required autofocus />
      </div>
      <div>
        <label class="field-label" for="auth-password">Password</label>
        <input type="password" id="auth-password" name="password"
          autocomplete="{_autocomplete_pw}" placeholder="••••••••"
          required />
      </div>
      {"" if mode != "Sign In" else '''<label class="remember"><input type="checkbox" id="auth-remember" name="remember" /> Remember me on this device</label>'''}
      <button type="submit" class="submit-btn">{"Sign In" if mode == "Sign In" else "Create Account"}</button>
    </form>
    <script>
    function handleSubmit(e) {{
      e.preventDefault();
      const email = document.getElementById('auth-email').value.trim();
      const pw = document.getElementById('auth-password').value;
      const rem = document.getElementById('auth-remember');
      const remember = rem ? rem.checked : false;
      // Post credentials to parent Streamlit frame
      const msg = {{ type: 'clawd_auth', email, password: pw, remember, mode: '{mode}' }};
      window.parent.postMessage(JSON.stringify(msg), '*');
    }}
    // Pre-fill from browser autofill after short delay
    setTimeout(() => {{
      const em = document.getElementById('auth-email');
      const pw = document.getElementById('auth-password');
      if (em && em.value) window.parent.postMessage(JSON.stringify({{type:'clawd_prefill',email:em.value}}), '*');
    }}, 800);
    </script>
    """,
    height=240 if mode == "Sign In" else 220,
    scrolling=False,
)

# ── Hidden bridge inputs — receive postMessage values ──
# These are invisible; the HTML form above drives the UX.
if "_auth_form_email" not in st.session_state:
    st.session_state["_auth_form_email"] = ""
if "_auth_form_password" not in st.session_state:
    st.session_state["_auth_form_password"] = ""
if "_auth_form_submitted" not in st.session_state:
    st.session_state["_auth_form_submitted"] = False
if "_auth_form_remember" not in st.session_state:
    st.session_state["_auth_form_remember"] = False

# JS listener to capture postMessage and write to hidden inputs via Streamlit
_auth_comp.html(
    """
    <script>
    window.addEventListener('message', function(ev) {
      try {
        const data = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data;
        if (data.type === 'clawd_auth') {
          // Write to hidden Streamlit inputs by simulating input events
          const setVal = (sel, val) => {
            const doc = window.parent.document;
            const el = doc.querySelector(sel);
            if (!el) return;
            const nativeInput = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, 'value');
            nativeInput.set.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
          };
          setVal('input[data-testid="stTextInput"][aria-label="__auth_email__"]', data.email || '');
          setVal('input[data-testid="stTextInput"][aria-label="__auth_password__"]', data.password || '');
          setVal('input[data-testid="stTextInput"][aria-label="__auth_submit__"]', data.mode === 'Sign In' ? 'signin' : 'signup');
        }
      } catch(e) {}
    });
    </script>
    """,
    height=0,
)

# Hidden Streamlit inputs that receive values from the HTML form
email = st.text_input("__auth_email__", key="_auth_email_bridge", label_visibility="collapsed")
password = st.text_input("__auth_password__", key="_auth_pw_bridge", label_visibility="collapsed", type="password")
_submit_signal = st.text_input("__auth_submit__", key="_auth_submit_bridge", label_visibility="collapsed")
remember_me = st.session_state.get("_auth_form_remember", False)

# Hide bridge inputs visually
st.markdown(
    """
    <style>
    div:has(> div > [aria-label="__auth_email__"]),
    div:has(> div > [aria-label="__auth_password__"]),
    div:has(> div > [aria-label="__auth_submit__"]) { display:none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Process submission
if _submit_signal == "signin":
    if not email or not password:
        st.error("Please enter both email and password.")
    else:
        ok, err = sign_in(email.strip(), password, remember_me=remember_me)
        if ok:
            st.success("✅ Signed in successfully!")
            _switch_to_next_page()
        else:
            st.error(err or "Sign in failed. Please check your credentials.")
elif _submit_signal == "signup":
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
