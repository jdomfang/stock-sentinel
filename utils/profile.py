from __future__ import annotations

import streamlit as st

from utils.supabase_client import get_client


def get_my_profile() -> dict | None:
    """Fetch the current user's profile row from public.profiles.

    Requires the current user to be authenticated (session restored in get_client()).
    """
    user = st.session_state.get("auth.user")
    user_id = None
    if isinstance(user, dict):
        user_id = user.get("id")
    else:
        user_id = getattr(user, "id", None)

    if not user_id:
        return None

    sb = get_client()
    res = sb.table("profiles").select("user_id,email,role,disabled,scan_credits,deep_credits").eq("user_id", user_id).maybe_single().execute()
    return res.data if getattr(res, "data", None) else None
