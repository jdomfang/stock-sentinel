import streamlit as st

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page, safe_ui, ui_error
from utils.auth import is_logged_in, get_user
from utils.supabase_client import get_admin_client

st.set_page_config(page_title="Admin - Stock Sentinel", page_icon="🛠️", layout="wide", initial_sidebar_state="collapsed")
render_sidebar_navigation()
render_top_nav()
apply_theme()

st.markdown('<div class="clawd-app-wrapper">', unsafe_allow_html=True)
st.markdown("# 🛠️ Admin Dashboard")

if not is_logged_in():
    st.warning("Please log in first.")
    if st.button("Go to Login", type="primary"):
        st.switch_page("pages/Auth.py")
    st.stop()

admin_email = st.secrets.get("ADMIN_EMAIL", "").lower().strip()
user = get_user() or {}
user_email_raw = user.get("email") if isinstance(user, dict) else getattr(user, "email", None)
user_email = (user_email_raw or "").lower().strip()

# First guard: must match configured admin email
if not admin_email or user_email != admin_email:
    st.error("Not authorized.")
    st.stop()

st.caption("Admin tools use the Supabase service role key (server-side).")

sb = safe_ui(get_admin_client, context="admin.get_admin_client")
if not sb:
    st.stop()

st.subheader("👥 Users")

def _load_profiles():
    return (
        sb.table("profiles")
        .select("user_id,email,role,disabled,scan_credits,deep_credits,created_at")
        .order("created_at", desc=True)
        .execute()
    )

resp = safe_ui(_load_profiles, context="admin.load_profiles")
if not resp:
    st.stop()
rows = getattr(resp, "data", None) or []

if not rows:
    st.info("No users found yet.")
    st.stop()

st.dataframe(rows, use_container_width=True)

st.markdown("---")
st.subheader("⚙️ Manage a user")

email_options = [r.get("email") or r.get("user_id") for r in rows]
sel_email = st.selectbox("Select user", options=email_options)
selected = next((r for r in rows if (r.get("email") or r.get("user_id")) == sel_email), None)

if not selected:
    st.stop()

uid = selected.get("user_id")

c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.4])
with c1:
    disabled = st.checkbox("Disabled", value=bool(selected.get("disabled")))
with c2:
    role = st.selectbox("Role", options=["user", "admin"], index=0 if selected.get("role") == "user" else 1)
with c3:
    scan_credits = st.number_input("Scan credits", min_value=0, value=int(selected.get("scan_credits") or 0), step=1)
with c4:
    deep_credits = st.number_input("Deep credits", min_value=0, value=int(selected.get("deep_credits") or 0), step=1)

if st.button("💾 Save changes", type="primary"):
    def _save():
        return (
            sb.table("profiles")
            .update(
                {
                    "disabled": bool(disabled),
                    "role": role,
                    "scan_credits": int(scan_credits),
                    "deep_credits": int(deep_credits),
                }
            )
            .eq("user_id", uid)
            .execute()
        )

    ok = safe_ui(_save, context="admin.save_profile")
    if ok:
        st.success("✅ Saved.")
        st.rerun()

close_page()
