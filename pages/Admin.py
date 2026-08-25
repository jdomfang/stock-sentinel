import json
from pathlib import Path

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
from utils.ui import apply_theme, close_page, safe_ui, ui_error
from utils.auth import is_logged_in, get_user
from utils.supabase_client import get_admin_client

st.set_page_config(page_title="Admin - Stock Sentinel", page_icon="🛠️", layout="wide", initial_sidebar_state="collapsed")
render_sidebar_navigation()
render_top_nav()
apply_theme()

st.markdown('<div class="clawd-app-wrapper">', unsafe_allow_html=True)


def _bail() -> None:
    """Stop the script WITHOUT leaving the page half-drawn.

    The same helper the two analysis pages use. st.stop() raises
    StopException, which unwinds past the close_page() at the bottom of this
    module -- so the wrapper div opened above stays open and the footer never
    renders. This page bails on seven paths (not an admin, no service key,
    lookup failed), so that was most of its exits.
    """
    close_page()
    st.stop()
st.markdown("# 🛠️ Admin Dashboard")

if not is_logged_in():
    st.warning("Please log in first.")
    if st.button("Go to Login", type="primary"):
        st.switch_page("pages/Auth.py")
    _bail()

admin_email = st.secrets.get("ADMIN_EMAIL", "").lower().strip()
user = get_user() or {}
user_email_raw = user.get("email") if isinstance(user, dict) else getattr(user, "email", None)
user_email = (user_email_raw or "").lower().strip()

# First guard: must match configured admin email
if not admin_email or user_email != admin_email:
    st.error("Not authorized.")
    _bail()

st.caption("Admin tools use the Supabase service role key (server-side).")

st.markdown("---")
st.subheader("📬 Messages")

# ABOVE EVERY st.stop() ON THIS PAGE, not just the one in "Manage a user".
# get_admin_client failing, the profiles query failing, or simply having zero
# users each stop the script -- so the queue vanished in exactly the situation
# where you most need to check whether contact writes are working.
#
# Expanders rather than st.dataframe. A dataframe truncates the one column that
# matters -- the message -- and the point of this section is reading them.
from utils import contact as _contact

_mc1, _mc2 = st.columns([1.0, 2.0])
with _mc1:
    _unhandled_only = st.checkbox("Unhandled only", value=True)
_messages = safe_ui(lambda: _contact.recent(200, unhandled_only=_unhandled_only),
                    context="admin.contact.recent")
# A HEAD count, not a second full fetch. The previous version re-ran the same
# query purely to length it -- two round trips pulling up to 200 full 4000-char
# bodies each, on a page Streamlit re-runs for every widget interaction.
_open_count = _contact.unhandled_count()

with _mc2:
    st.caption(f"{'?' if _open_count is None else _open_count} unhandled · "
               f"showing {0 if _messages is None else len(_messages)}")

if _messages is None:
    # NOT "no messages". A failed read that renders an empty inbox tells the
    # operator there is nothing to do while the queue is full -- the same silent
    # failure this whole table exists to end.
    st.error("Could not load messages — the queue may not be empty. Check logs.")
    _messages = []
elif not _messages:
    st.caption("Nothing unhandled." if _unhandled_only else "No messages yet.")

for _m in _messages:
    if not _m.get("id"):
        continue          # a malformed row must not take the whole page down
    _when = str(_m.get("created_at") or "")[:16].replace("T", " ")
    _done = bool(_m.get("handled_at"))
    # Escaped: the expander label renders markdown, and `email` comes from an
    # unauthenticated form whose validator permits brackets.
    _title = (f"{'✅' if _done else '🔵'}  "
              f"{_contact.md_escape(_m.get('topic', '?'))} · "
              f"{_contact.md_escape(_m.get('email', '?'))} · {_when}")
    with st.expander(_title, expanded=not _done and len(_messages) <= 5):
        # st.text, never st.markdown: the body is untrusted input from an
        # unauthenticated form, and this page renders with the service-role key
        # in scope. Markdown would let a sender inject links or HTML into the
        # one screen an operator trusts.
        st.text(_m.get("message") or "")
        # st.caption renders markdown too, and user_agent is a raw client
        # header. Unescaped, `![](https://attacker/p.png)` is a beacon that
        # fires when an admin opens the queue.
        _meta = [f"**Topic:** {_contact.md_escape(_m.get('topic', '?'))}",
                 f"**From:** {_contact.md_escape(_m.get('email', '?'))}"]
        if _m.get("user_id"):
            _meta.append(f"**Account:** `{_m['user_id']}`")
        else:
            # The address is self-asserted and never verified. Without this an
            # operator reads "From: victim@example.com — please reset my
            # access" with no cue that anyone could have typed it.
            _meta.append("**Unverified sender** (not logged in)")
        if _m.get("user_agent"):
            _meta.append("**Browser:** "
                         + _contact.md_escape(str(_m["user_agent"])[:160]))
        if _done:
            _meta.append(f"**Handled:** {str(_m['handled_at'])[:16].replace('T',' ')}")
            if _m.get("handled_note"):
                _meta.append(f"**Note:** {_m['handled_note']}")
        st.caption(" · ".join(_meta))

        _a, _b = st.columns([2.0, 1.0])
        if not _done:
            with _a:
                _note = st.text_input("Note (optional)", key=f"note_{_m.get('id')}",
                                      label_visibility="collapsed",
                                      placeholder="What you did about it")
            with _b:
                if st.button("Mark handled", key=f"done_{_m.get('id')}",
                             use_container_width=True):
                    if _contact.set_handled(_m["id"], True, _note):
                        st.rerun()
                    else:
                        st.error("Could not update — see logs.")
        else:
            with _b:
                if st.button("Reopen", key=f"open_{_m.get('id')}",
                             use_container_width=True):
                    if _contact.set_handled(_m["id"], False):
                        st.rerun()
                    else:
                        st.error("Could not update — see logs.")


sb = safe_ui(get_admin_client, context="admin.get_admin_client")
if not sb:
    _bail()

st.subheader("👥 Users")

def _load_profiles():
    return (
        sb.table("profiles")
        .select("user_id,email,role,disabled,credits,created_at")
        .order("created_at", desc=True)
        .execute()
    )

resp = safe_ui(_load_profiles, context="admin.load_profiles")
if not resp:
    _bail()
rows = getattr(resp, "data", None) or []

if not rows:
    st.info("No users found yet.")
    _bail()

st.dataframe(rows, use_container_width=True)

st.markdown("---")
st.subheader("🎛️ Demo refresh tooling")
st.caption("These buttons save the *current session's* latest scan / deep analyze into data/education so Home can render updated demos.")

root = Path(__file__).resolve().parents[1]
edu_dir = root / "data" / "education"
edu_dir.mkdir(parents=True, exist_ok=True)

c_demo1, c_demo2 = st.columns([1, 1])

with c_demo1:
    if st.button("💾 Save current scan as demo", type="primary"):
        dfv = st.session_state.get("df_valid")
        dfn = st.session_state.get("df_unvalidated")
        if dfv is None or len(dfv) == 0:
            st.error("No scan results in this session. Run a scan on Discovery first.")
        else:
            try:
                # Enrich validated rows with Last Close prices (cache-first, fetch-on-miss)
                from utils.finance import get_last_close_prices_best_effort

                dfv2 = dfv.drop(columns=["Sample Tweets"], errors="ignore").copy()
                tickers = [str(t) for t in dfv2.get("Ticker", []).tolist()] if "Ticker" in dfv2.columns else []
                price_map = get_last_close_prices_best_effort(tickers)

                validated_rows = []
                for r in dfv2.to_dict("records"):
                    t = str(r.get("Ticker") or "").upper()
                    px = price_map.get(t)
                    r["Last Close"] = (None if px is None else float(px))
                    validated_rows.append(r)

                payload = {
                    "sector": st.session_state.get("selected_sector") or "",
                    "generated_at": "snapshot",
                    "validated_rows": validated_rows,
                    "unvalidated_rows": (dfn.to_dict("records") if dfn is not None else []),
                }
                out = edu_dir / "scan_latest.json"
                out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                st.success("Saved: data/education/scan_latest.json")
            except Exception as e:
                st.error(f"Failed to save scan demo: {str(e)[:200]}")

with c_demo2:
    if st.button("💾 Save current deep analyze as demo", type="primary"):
        ticker = st.session_state.get("selected_ticker") or ""
        sector = st.session_state.get("selected_sector") or ""
        results = st.session_state.get("deep_analysis_results")
        if not ticker or not results:
            st.error("No deep analysis results in this session. Run Deep Analyze first.")
        else:
            try:
                payload = {
                    "ticker": ticker,
                    "sector": sector,
                    "generated_at": "snapshot",
                    "analysis_results": results,
                }
                out = edu_dir / "deep_latest.json"
                out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                st.success("Saved: data/education/deep_latest.json")
            except Exception as e:
                st.error(f"Failed to save deep demo: {str(e)[:200]}")

st.markdown("---")
st.subheader("⚙️ Manage a user")

email_options = [r.get("email") or r.get("user_id") for r in rows]
sel_email = st.selectbox("Select user", options=email_options)
selected = next((r for r in rows if (r.get("email") or r.get("user_id")) == sel_email), None)

if not selected:
    _bail()

uid = selected.get("user_id")

c1, c2, c3 = st.columns([1.0, 1.0, 1.4])
with c1:
    disabled = st.checkbox("Disabled", value=bool(selected.get("disabled")))
with c2:
    role = st.selectbox("Role", options=["user", "admin"], index=0 if selected.get("role") == "user" else 1)
with c3:
    # ONE input. Two inputs against one wallet is a balance doubler: both would
    # default from the same merged number, so an admin opening a user with 10
    # credits sees 10 and 10, changes only a role, saves, and writes 20. The
    # ledger records that as a deliberate adjustment with an actor and a reason,
    # so reconciliation AGREES with it and nothing anywhere notices. Twice: 40.
    credits = st.number_input("Credits", min_value=0,
                              value=int(selected.get("credits") or 0), step=1)

adjust_reason = st.text_input(
    "Reason (recorded in the ledger)",
    placeholder="e.g. refund for failed scan, promo grant, correction",
    key="admin_adjust_reason",
)

# Reason codes from public.admin_adjust_credits -> text an admin should see.
_ADJUST_MESSAGES = {
    "not_admin": "Your account is not an enabled admin in the database.",
    "cannot_demote_self": "You cannot remove your own admin role — that would lock you out.",
    "cannot_disable_self": "You cannot disable your own account — that would lock you out.",
    "invalid_credits": "Credits must be zero or greater.",
    "deep_credits_retired": ("This page is out of date — scan and analysis "
                             "credits are now one balance. Reload the page."),
    "invalid_role": "Role must be 'user' or 'admin'.",
    "profile_not_found": "That profile no longer exists.",
}

if st.button("💾 Save changes", type="primary"):
    # The actor is recorded in the ledger row, so the audit trail answers "who"
    # and not merely "what". Without it an adjustment is anonymous.
    actor_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)

    if not actor_id:
        ui_error("Could not identify your account. Please log in again.")
        _bail()

    def _save():
        # Was a direct service-role UPDATE on profiles, which bypassed RLS and
        # wrote nothing to usage_events -- so every credit granted here was
        # invisible to the ledger (production drift was ~75 scan / 45 deep on
        # this account alone). The RPC applies the change and records it in one
        # transaction, and refuses the two edits that would lock the admin out.
        return sb.rpc(
            "admin_adjust_credits",
            {
                "p_actor_id": actor_id,
                "p_user_id": uid,
                # p_scan_credits carries the merged total; the signature is
                # unchanged because replacing it would create a second overload
                # rather than a new function. p_deep_credits MUST be 0 -- the
                # function rejects anything else as deep_credits_retired, so a
                # page still sending a real value fails loudly on the first save
                # instead of silently doubling somebody's balance.
                "p_scan_credits": int(credits),
                "p_deep_credits": 0,
                "p_disabled": bool(disabled),
                "p_role": role,
                "p_reason": (adjust_reason or "admin adjustment").strip()[:200],
            },
        ).execute()

    resp = safe_ui(_save, context="admin.save_profile")
    if resp is not None:
        result = getattr(resp, "data", None)
        if not isinstance(result, dict):
            ui_error("Unexpected response from the server. Nothing was changed.")
        elif not result.get("ok"):
            reason = result.get("reason") or "unknown"
            ui_error(_ADJUST_MESSAGES.get(reason, f"Could not save changes ({reason})."))
        else:
            # DISABLING AN ACCOUNT MUST KILL ITS SESSIONS.
            #
            # `disabled` stops consume_credit and gates every page, but a
            # "remember me" code stays valid for thirty days -- so a disabled
            # user keeps minting working Supabase sessions and keeps browsing.
            # Suspending someone who has to be suspended and leaving them
            # logged in is not a suspension.
            if bool(disabled) and result.get("disabled_changed"):
                try:
                    from utils.auth import revoke_remember_codes
                    revoke_remember_codes(uid)
                except Exception:
                    # Never fail the save over this: the account IS disabled,
                    # which is the thing that was asked for. Say so instead of
                    # implying it silently worked.
                    ui_error("Account disabled, but its saved sign-ins could not "
                             "be revoked. Re-save to retry.")

            delta = result.get("delta") or 0
            if delta:
                st.success(f"✅ Saved. Credits {delta:+d} — recorded in the ledger.")
            else:
                st.success("✅ Saved. No credit change, so no ledger entry.")
            st.rerun()

close_page()
