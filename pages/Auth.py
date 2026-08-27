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


from utils.navigation import render_sidebar_navigation
from utils.ui import apply_theme, render_footer
from utils.auth import sign_in, sign_up, is_logged_in, try_restore_cached_session, restore_session_from_refresh_token

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
      font-size: clamp(2rem, 4vw, 2.65rem);
      font-weight: 850;
      letter-spacing: -0.02em;
      margin: 0;
      line-height: 1.1;
      text-align: center;
    }
    .auth-subtitle {
      color: var(--muted);
      margin-top: 0.75rem;
      margin-bottom: 1rem;
      font-size: 1.0rem;
      text-align: center;
      line-height: 1.5;
    }

    /* Hero section for auth */
    .auth-hero {
      margin: 0;
      padding: 0;
      text-align: left;
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

    /* Primary buttons + form submit button */
    button[data-testid="stBaseButton-primary"],
    button[data-testid="stFormSubmitButton"],
    .stButton > button[kind="primary"],
    .stFormSubmitButton > button {
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

    .stButton,
    .stFormSubmitButton {
      text-align: center !important;
    }

    .stButton > button,
    .stFormSubmitButton > button {
      width: 100% !important;
      max-width: 420px !important;
      margin: 0 auto !important;
    }

    /* Remove default form border/padding that Streamlit adds */
    [data-testid="stForm"] {
      border: none !important;
      padding: 0 !important;
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }
    </style>
    
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
      div[data-testid="stMainBlockContainer"] {max-width:1100px;margin:0 auto;padding-top:.25rem;}
      .st-key-auth_topbar {border-bottom:1px solid rgba(148,163,184,.16);padding:.45rem 0 .65rem;margin-bottom:1.25rem;}
      .st-key-auth_topbar [data-testid="stHorizontalBlock"] {align-items:center!important;}
      .st-key-auth_brand [data-testid="stPageLink"] a {color:var(--accent)!important;font-weight:800;letter-spacing:.07em;text-decoration:none!important;}
      .st-key-auth_home [data-testid="stPageLink"] a {justify-content:flex-end;color:#cbd5e1!important;text-decoration:none!important;}
      .st-key-auth_shell {border:1px solid var(--border);border-radius:18px;background:rgba(8,15,30,.66);overflow:hidden;box-shadow:var(--ss-shadow-panel);}
      .st-key-auth_shell > div > [data-testid="stHorizontalBlock"] {gap:0!important;align-items:stretch!important;}
      .st-key-auth_value {height:100%;padding:clamp(1.25rem,3vw,2.2rem);background:linear-gradient(145deg,rgba(56,189,248,.075),rgba(15,23,42,.3));}
      .auth-value-kicker {color:#7dd3fc;font-size:.72rem;font-weight:800;letter-spacing:.07em;text-transform:uppercase;margin-bottom:.7rem;}
      .auth-value-title {font-size:clamp(2rem,4vw,2.7rem);font-weight:850;letter-spacing:-.04em;line-height:1.07;margin:0 0 .75rem;}
      .auth-value-copy {color:#a8b5c7;line-height:1.55;margin:0;max-width:470px;}
      .auth-value-list {list-style:none;margin:1.2rem 0 0;padding:0;}
      .auth-value-list li {padding:.62rem 0;border-top:1px solid rgba(148,163,184,.12);color:#cbd5e1;font-size:.86rem;}
      .st-key-auth_form_panel {padding:clamp(1.25rem,3vw,2.2rem);height:100%;}
      .auth-form-heading {font-size:1.15rem;font-weight:800;margin:0 0 .25rem;}
      .auth-form-copy {color:#94a3b8;font-size:.84rem;margin:0 0 1rem;line-height:1.45;}
      .st-key-auth_form_panel [data-testid="stRadio"] {margin-bottom:.85rem!important;}
      .st-key-auth_form_panel .stTextInput {max-width:none!important;margin:0 0 .55rem!important;}
      .st-key-auth_form_panel [data-testid="stCheckbox"] {max-width:none!important;margin:.2rem 0 .65rem!important;}
      .st-key-auth_form_panel [data-testid="stCheckbox"] label {justify-content:flex-start!important;}
      .auth-security-note {margin-top:.8rem;color:#8192aa;font-size:.75rem;line-height:1.45;}
      @media (max-width:760px) {
        .st-key-auth_shell > div > [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;}
        .st-key-auth_shell [data-testid="column"] {flex:1 1 100%!important;min-width:100%!important;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.container(key="auth_topbar"):
    brand_col, home_col = st.columns([2.5, 1])
    with brand_col:
        with st.container(key="auth_brand"):
            st.page_link("pages/Home.py", label="STOCK SENTINEL")
    with home_col:
        with st.container(key="auth_home"):
            st.page_link("pages/Home.py", label="Back to home")

# ── Remember Me restore (two-stage) ──────────────────────────────────────────
# Stage 1: fast-path — same browser session (st.session_state survives navigation
#           between pages, but NOT a tab close / hard refresh).
# Stage 2: if stage 1 misses, read localStorage via JS → inject ?rt= query param
#           → Streamlit reruns → we call Supabase refresh_session with the token.
# ─────────────────────────────────────────────────────────────────────────────


if not try_restore_cached_session():
    # Check if we already have a refresh token in query params (second rerun)
    # ?rc= -- an opaque, SINGLE-USE code, not a Supabase refresh token. The
    # parameter is still a query string because Streamlit gives browser JS no
    # other channel to Python; what changed is that a leaked URL now carries a
    # credential that was spent the moment it was used. See utils/auth.py.
    _rc_param = st.query_params.get("rc", "")
    if _rc_param:
        # Clear the param immediately so it doesn't linger in the URL
        st.query_params.clear()
        from utils.auth import consume_remember_code
        _rt_param = consume_remember_code(_rc_param)
        if _rt_param and restore_session_from_refresh_token(_rt_param):
            pass  # session restored; is_logged_in() check below handles redirect
        else:
            # Token expired/invalid — clear it from localStorage so we don't loop
            from utils.auth import _clear_browser_cache
            _clear_browser_cache()
    else:
        # First visit (or hard refresh) -- ask JS to read localStorage and
        # redirect with the opaque CODE as a query param so Python can see it.
        # location.replace, not assign, so the URL carrying the code does not
        # become a history entry the back button can return to.
        import streamlit.components.v1 as _cmp_rt
        _cmp_rt.html(
            """<script>
            (function() {
              try {
                var rc = localStorage.getItem('ss_remember_code');
                if (rc) {
                  var url = new URL(window.parent.location.href);
                  url.searchParams.set('rc', rc);
                  window.parent.location.replace(url.toString());
                }
              } catch(e) {}
            })();
            </script>""",
            height=0,
        )

def _switch_to_next_page() -> None:
    # Prefer an explicit destination set by a prior page (e.g. Home).
    nxt = (st.session_state.pop("_after_auth_page", None) or "Home").strip().lower()
    if nxt in {"deep_analysis", "deep-analysis", "deep", "analysis"}:
        st.switch_page("pages/Deep_Analysis.py")
    elif nxt in {"home"}:
        st.switch_page("pages/Home.py")
    else:
        st.switch_page("pages/Discovery.py")


def _render_password_manager_adapter(password_autocomplete: str) -> None:
    """Current-renderer bridge for password-manager metadata.

    A future frontend puts these attributes directly on its inputs. The
    Streamlit adapter disconnects as soon as both generated inputs are patched
    and has a short timeout backstop, so observers do not accumulate.
    """
    import streamlit.components.v1 as components

    components.html(
        f"""
        <script>
        (function(){{
          try {{
            const doc = window.parent ? window.parent.document : document;
            let observer = null;
            let timer = null;
            const apply = () => {{
              const em = Array.from(doc.querySelectorAll('input')).find(i => i.getAttribute('aria-label') === 'Email address');
              const pw = Array.from(doc.querySelectorAll('input')).find(i => i.getAttribute('aria-label') === 'Password');
              if (em) {{ em.setAttribute('name','email'); em.setAttribute('autocomplete','username'); em.setAttribute('inputmode','email'); }}
              if (pw) {{ pw.setAttribute('name','password'); pw.setAttribute('autocomplete','{password_autocomplete}'); }}
              if (em && pw) {{
                if (observer) observer.disconnect();
                if (timer) window.clearTimeout(timer);
                return true;
              }}
              return false;
            }};
            if (!apply()) {{
              observer = new MutationObserver(apply);
              observer.observe(doc.body, {{ subtree:true, childList:true }});
              timer = window.setTimeout(() => observer && observer.disconnect(), 3000);
            }}
            window.addEventListener('pagehide', () => observer && observer.disconnect(), {{once:true}});
          }} catch(e) {{}}
        }})();
        </script>
        """,
        height=0,
    )


# Check if already logged in
if is_logged_in():
    st.success("✅ You are already signed in.")

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Continue", type="primary", use_container_width=True):
            _switch_to_next_page()
    with c2:
        # Mobile-safe escape hatch if we hide the top-nav auth button on small screens
        from utils.auth import sign_out
        if st.button("Log out", use_container_width=True):
            sign_out()
            st.switch_page("pages/Home.py")

    st.stop()

# Preserve an explicit Create Account intent from the landing CTA across form
# reruns. The auth implementation itself remains unchanged.
_requested_mode = st.session_state.pop("auth_initial_mode", None)
if _requested_mode in {"Sign In", "Create Account"}:
    st.session_state["auth_mode"] = _requested_mode
elif st.session_state.get("auth_mode") not in {"Sign In", "Create Account"}:
    st.session_state["auth_mode"] = "Sign In"

with st.container(key="auth_shell"):
    value_col, form_col = st.columns([.92, 1.08])
    with value_col:
        with st.container(key="auth_value"):
            st.html(
                """
                <div class="auth-value-kicker">Short-term market intelligence</div>
                <h1 class="auth-value-title">Move from market noise to a decision-ready shortlist.</h1>
                <p class="auth-value-copy">Scan sectors for unusual social attention, then evaluate a selected ticker with evidence, risk context, and a clear Buy, Watch, or Avoid recommendation.</p>
                <ul class="auth-value-list">
                  <li>Two free credits when you create an account</li>
                  <li>No card required and no subscription</li>
                  <li>Evidence context shown with every analysis</li>
                </ul>
                """
            )
    with form_col:
        with st.container(key="auth_form_panel"):
            st.markdown(
                '<h2 class="auth-form-heading">Access Stock Sentinel</h2>'
                '<p class="auth-form-copy">Choose an existing account or create a new one.</p>',
                unsafe_allow_html=True,
            )
            mode = st.radio(
                "Account access",
                ["Sign In", "Create Account"],
                horizontal=True,
                label_visibility="collapsed",
                key="auth_mode",
            )

            _pw_ac = "current-password" if mode == "Sign In" else "new-password"
            _btn_label = "Sign In" if mode == "Sign In" else "Create Account"

            # st.form handles Enter key submission natively.
            with st.form("auth_form", clear_on_submit=False):
                email = st.text_input(
                    "Email address", placeholder="you@example.com", key="auth_email"
                )
                password = st.text_input(
                    "Password", type="password", placeholder="Password",
                    key="auth_password",
                )
                remember_me = st.checkbox(
                    "Remember me on this device", value=False,
                    help="Use only on a private device.",
                )
                submitted = st.form_submit_button(
                    _btn_label, type="primary", use_container_width=True
                )
            st.markdown(
                '<div class="auth-security-note">Account access is protected by Supabase authentication. Payment details are entered only on Stripe.</div>',
                unsafe_allow_html=True,
            )

# Autofill / password-manager hints belong to the current renderer adapter.
_render_password_manager_adapter(_pw_ac)

if submitted:
    if not email or not password:
        st.error("Please enter both email and password.")
    elif mode == "Sign In":
        ok, err = sign_in(email.strip(), password, remember_me=remember_me)
        if ok:
            st.success("Signed in successfully!")
            _switch_to_next_page()
        else:
            st.error(err or "Sign in failed. Please check your credentials.")
    else:
        ok, err = sign_up(email.strip(), password)
        if ok:
            st.success("Account created! Check your email to confirm, then sign in.")
        else:
            st.error(err or "Account creation failed. Email may already be in use.")

render_footer()
