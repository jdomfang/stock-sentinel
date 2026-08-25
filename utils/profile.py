from __future__ import annotations

import logging

import streamlit as st

from utils.supabase_client import get_client

_log = logging.getLogger(__name__)


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
    # `credits` only. The old per-kind columns are frozen pre-merge snapshots
    # kept for rollback -- selecting them here is how a stale reader ends up
    # rendering "5 scans left" against a merged balance of 0.
    try:
        res = sb.table("profiles").select(
            "user_id,email,role,disabled,credits"
        ).eq("user_id", user_id).maybe_single().execute()
        return res.data if getattr(res, "data", None) else None
    except Exception as e:
        # DEPLOY-ORDER TOLERANCE, and it is temporary by design.
        #
        # The merge migration is applied BY HAND in the Supabase editor while
        # this app deploys from a git push. Those cannot be simultaneous, and
        # without this the push-first ordering is a hard outage: PostgREST
        # answers 42703 for an unknown column, this raises, and
        # require_active_account() -- called at module scope on both spend
        # pages -- takes Discovery and Deep Analysis down for every user.
        #
        # The fallback computes the same sum the migration does, so the number
        # on screen is the one the user will have the moment the SQL lands.
        # It is not perfectly honest in the window: the OLD consume_credit is
        # still installed and still spends per-bucket, so a holder of
        # (0 scan, 5 deep) is shown 5 and refused a scan. That is a narrow,
        # minutes-long wrong promise against a total outage, and it is logged
        # loudly rather than swallowed.
        if "credits" not in str(e) and "42703" not in str(e) and "PGRST" not in str(e):
            raise
        _log.error("profiles.credits missing -- the merge migration has not been "
                   "applied yet. Falling back to scan+deep. APPLY "
                   "20260824030000_merge_credit_buckets.sql NOW.")
        res = sb.table("profiles").select(
            "user_id,email,role,disabled,scan_credits,deep_credits"
        ).eq("user_id", user_id).maybe_single().execute()
        row = res.data if getattr(res, "data", None) else None
        if row is not None:
            row["credits"] = int(row.get("scan_credits") or 0) + int(row.get("deep_credits") or 0)
        return row
