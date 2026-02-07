import json
from pathlib import Path

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
