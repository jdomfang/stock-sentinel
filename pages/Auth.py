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
    page_title="Stock Sentinel - Account Access",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

apply_theme()
render_sidebar_navigation()

# --- Auth-specific presentation. Authentication behavior stays below. ---
st.markdown(
    """
    <style>
      div[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] {gap:.75rem!important;}
      .st-key-auth_topbar {border-bottom:1px solid rgba(148,163,184,.16);padding:.35rem 0 .55rem;margin-bottom:.25rem;}
      .st-key-auth_topbar [data-testid="stHorizontalBlock"] {align-items:center!important;}
      .st-key-auth_brand [data-testid="stPageLink"] a {color:var(--accent)!important;font-weight:800;letter-spacing:.07em;text-decoration:none!important;}
      .st-key-auth_home [data-testid="stPageLink"] a {justify-content:flex-end;color:#cbd5e1!important;text-decoration:none!important;}
      .st-key-auth_shell {margin:.25rem 0;border:1px solid var(--border);border-radius:18px;background:rgba(8,15,30,.76);overflow:hidden;box-shadow:var(--ss-shadow-panel);}
      .st-key-auth_shell [data-testid="stHorizontalBlock"]:has(.st-key-auth_form_panel):has(.st-key-auth_value) {gap:0!important;align-items:stretch!important;}
      .st-key-auth_shell [data-testid="stHorizontalBlock"]:has(.st-key-auth_form_panel):has(.st-key-auth_value) > [data-testid="stColumn"]:first-child {order:2;}
      .st-key-auth_shell [data-testid="stHorizontalBlock"]:has(.st-key-auth_form_panel):has(.st-key-auth_value) > [data-testid="stColumn"]:last-child {order:1;}
      .st-key-auth_value {height:100%;padding:clamp(1.25rem,2.5vw,1.8rem);background:linear-gradient(145deg,rgba(56,189,248,.075),rgba(15,23,42,.3));}
      .auth-value-kicker {color:#7dd3fc;font-size:.7rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.65rem;}
      .auth-value-title {font-size:clamp(1.75rem,3.3vw,2.25rem);font-weight:850;letter-spacing:-.04em;line-height:1.08;margin:0 0 .65rem;}
      .auth-value-copy {color:#a8b5c7;font-size:.9rem;line-height:1.55;margin:0;max-width:440px;}
      .auth-value-list {list-style:none;margin:1rem 0 0;padding:0;}
      .auth-value-list li {padding:.55rem 0;border-top:1px solid rgba(148,163,184,.12);color:#cbd5e1;font-size:.82rem;}
      .st-key-auth_form_panel {padding:clamp(1.25rem,2.5vw,1.8rem);height:100%;}
      .auth-form-heading {font-size:clamp(1.35rem,2.2vw,1.65rem);font-weight:820;letter-spacing:-.025em;margin:0 0 .3rem;}
      .auth-form-copy {color:#a8b5c7;font-size:.86rem;margin:0 0 .85rem;line-height:1.45;}
      .st-key-auth_form_panel [data-testid="stRadio"] {width:100%!important;margin:0 0 .8rem!important;padding:0!important;}
      .st-key-auth_form_panel [data-testid="stRadio"] > div {display:flex!important;flex-direction:row!important;gap:.5rem!important;width:100%!important;}
      .st-key-auth_form_panel [data-testid="stRadio"] label {flex:1 1 0;min-height:var(--ss-control-min-height);margin:0!important;padding:.55rem .7rem!important;border:1px solid var(--ss-color-border)!important;border-radius:var(--ss-radius-control)!important;background:#07101f!important;color:var(--ss-color-text)!important;justify-content:center!important;font-weight:700!important;cursor:pointer!important;}
      .st-key-auth_form_panel [data-testid="stRadio"] label:hover {border-color:var(--ss-color-action)!important;background:var(--ss-color-info-surface)!important;}
      .st-key-auth_form_panel [data-testid="stRadio"] label:has([aria-checked="true"]),
      .st-key-auth_form_panel [data-testid="stRadio"] label:has(input:checked) {border-color:var(--ss-color-action)!important;background:var(--ss-color-info-surface)!important;color:var(--ss-color-text)!important;box-shadow:inset 0 0 0 1px var(--ss-color-info-border)!important;}
      .st-key-auth_form_panel .stTextInput {max-width:none!important;margin:0 0 .5rem!important;}
      .st-key-auth_form_panel .stTextInput > label {color:#e2e8f0!important;font-size:.85rem!important;font-weight:650!important;margin-bottom:.32rem!important;}
      .st-key-auth_form_panel [data-baseweb="input"] {min-height:48px!important;background:#07101f!important;border:1px solid rgba(148,163,184,.28)!important;border-radius:10px!important;box-shadow:none!important;}
      .st-key-auth_form_panel [data-baseweb="input"]:focus-within {border-color:rgba(56,189,248,.82)!important;box-shadow:0 0 0 3px rgba(56,189,248,.12)!important;}
      .st-key-auth_form_panel [data-baseweb="input"] > div,
      .st-key-auth_form_panel [data-baseweb="input"] input {background:transparent!important;color:#f1f5f9!important;}
      .st-key-auth_form_panel input:-webkit-autofill {-webkit-text-fill-color:#f1f5f9!important;-webkit-box-shadow:0 0 0 1000px #07101f inset!important;caret-color:#f1f5f9!important;}
      .st-key-auth_form_panel [data-testid="stCheckbox"] {max-width:none!important;margin:.05rem 0 .55rem!important;}
      .st-key-auth_form_panel [data-testid="stCheckbox"] label {justify-content:flex-start!important;min-height:var(--ss-control-min-height);}
      .st-key-auth_form_panel [data-testid="stForm"] {border:0!important;padding:0!important;}
      .st-key-auth_form_panel [data-testid="stFormSubmitButton"] button {width:100%!important;min-height:48px!important;margin-top:.1rem!important;border-radius:10px!important;font-weight:760!important;}
      .auth-security-note {margin-top:.65rem;color:#94a3b8;font-size:.75rem;line-height:1.45;}
      .st-key-auth_topbar a:focus-visible,.st-key-auth_shell button:focus-visible,.st-key-auth_shell input:focus-visible {outline:3px solid var(--ss-color-focus)!important;outline-offset:3px!important;}
      footer {visibility:hidden;}
      @media (max-width:760px) {
        div[data-testid="stMainBlockContainer"] {
          padding-top:.2rem;
          padding-bottom:.75rem;
        }
        .st-key-auth_topbar [data-testid="stHorizontalBlock"] {flex-wrap:nowrap!important;}
        .st-key-auth_topbar [data-testid="stColumn"] {min-width:0!important;width:0!important;}
        .st-key-auth_topbar [data-testid="stColumn"]:first-child {flex:2.5 1 0!important;}
        .st-key-auth_topbar [data-testid="stColumn"]:last-child {flex:1 1 0!important;}
        .st-key-auth_shell [data-testid="stHorizontalBlock"]:has(.st-key-auth_form_panel):has(.st-key-auth_value) {flex-wrap:wrap!important;}
        .st-key-auth_shell [data-testid="stColumn"] {flex:1 1 100%!important;min-width:100%!important;}
        .st-key-auth_shell [data-testid="stHorizontalBlock"]:has(.st-key-auth_form_panel):has(.st-key-auth_value) > [data-testid="stColumn"]:first-child {order:1;}
        .st-key-auth_shell [data-testid="stHorizontalBlock"]:has(.st-key-auth_form_panel):has(.st-key-auth_value) > [data-testid="stColumn"]:last-child {display:none!important;}
        .st-key-auth_form_panel {padding:1.15rem;}
        .st-key-auth_form_panel [data-testid="stRadio"] label {min-width:0!important;padding:.5rem .35rem!important;font-size:.84rem!important;gap:.3rem!important;}
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
    # Preserve an explicit protected destination. With no saved intent, the
    # signed-in product opens at its primary workspace: Market Scan.
    nxt = (
        st.session_state.pop("_after_auth_page", None) or "Discovery"
    ).strip().lower()
    from utils.scan_intent import public_research_intent
    choice = public_research_intent()
    if choice:
        nxt = "discovery" if choice["kind"] == "scan" else "deep_analysis"
    if nxt in {"deep_analysis", "deep-analysis", "deep", "analysis"}:
        st.switch_page("pages/Deep_Analysis.py")
    elif nxt in {"analysis_result", "analysis-result", "result"}:
        st.switch_page("pages/Analysis_Result.py")
    elif nxt == "account":
        st.switch_page("pages/Account.py")
    elif nxt == "admin":
        st.switch_page("pages/Admin.py")
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

_create_mode = st.session_state["auth_mode"] == "Create Account"
_form_heading = "Create your free account" if _create_mode else "Welcome back"
_form_copy = (
    "Start with 2 free credits. No card required."
    if _create_mode else
    "Sign in to continue to your scans and analysis."
)

with st.container(key="auth_shell"):
    # Form comes first in source/reading order. CSS places the supporting value
    # panel on the left at desktop widths; mobile keeps this natural order.
    form_col, value_col = st.columns([1.08, .92])
    with value_col:
        with st.container(key="auth_value"):
            st.html(
                """
                <div class="auth-value-kicker">Short-term market intelligence</div>
                <h2 class="auth-value-title">Turn market noise into a decision-ready shortlist.</h2>
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
                f'<h1 class="auth-form-heading">{_form_heading}</h1>'
                f'<p class="auth-form-copy">{_form_copy}</p>',
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
            _btn_label = "Sign In" if mode == "Sign In" else "Create free account"

            # st.form handles Enter key submission natively.
            with st.form("auth_form", clear_on_submit=False):
                email = st.text_input(
                    "Email address", placeholder="you@example.com", key="auth_email"
                )
                password = st.text_input(
                    "Password", type="password",
                    placeholder=(
                        "Password" if mode == "Sign In"
                        else "Create a password"
                    ),
                    key="auth_password",
                )
                if mode == "Sign In":
                    remember_me = st.checkbox(
                        "Remember me on this device", value=False,
                        help="Use only on a private device.",
                    )
                else:
                    remember_me = False
                submitted = st.form_submit_button(
                    _btn_label, type="primary", use_container_width=True
                )
            _security_note = (
                "No card required · We’ll email you to confirm your account · "
                "Secure access powered by Supabase."
                if mode == "Create Account" else
                "Secure account access powered by Supabase."
            )
            st.markdown(
                f'<div class="auth-security-note">{_security_note}</div>',
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
