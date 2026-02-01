from __future__ import annotations

import streamlit as st

from utils.supabase_client import get_admin_client


def _get_user_id() -> str | None:
    user = st.session_state.get("auth.user")
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def consume_credit(event_type: str) -> tuple[bool, str]:
    """Consume 1 credit of the given event type for the current user.

    event_type: 'scan' | 'deep_analyze'

    Uses the service-role client so we can reliably update + record usage.
    """
    uid = _get_user_id()
    if not uid:
        return False, "Not logged in"

    if event_type not in ("scan", "deep_analyze"):
        return False, "Invalid event type"

    sb = get_admin_client()

    # Load current profile
    prof = sb.table("profiles").select("disabled,scan_credits,deep_credits").eq("user_id", uid).maybe_single().execute().data
    if not prof:
        return False, "Profile not found"
    if prof.get("disabled"):
        return False, "Account disabled"

    if event_type == "scan":
        if int(prof.get("scan_credits", 0)) <= 0:
            return False, "No scan credits remaining"
        new_scan = int(prof.get("scan_credits", 0)) - 1
        sb.table("profiles").update({"scan_credits": new_scan}).eq("user_id", uid).execute()
        sb.table("usage_events").insert({"user_id": uid, "event_type": "scan", "cost_scan_credits": 1, "cost_deep_credits": 0}).execute()
        return True, ""

    # deep_analyze
    if int(prof.get("deep_credits", 0)) <= 0:
        return False, "No Deep Analyze credits remaining"
    new_deep = int(prof.get("deep_credits", 0)) - 1
    sb.table("profiles").update({"deep_credits": new_deep}).eq("user_id", uid).execute()
    sb.table("usage_events").insert({"user_id": uid, "event_type": "deep_analyze", "cost_scan_credits": 0, "cost_deep_credits": 1}).execute()
    return True, ""
