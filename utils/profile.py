from __future__ import annotations

import logging

import streamlit as st

from utils.supabase_client import get_client

_log = logging.getLogger(__name__)


def _is_missing_credits_column(exc: Exception) -> bool:
    """Is this the pre-migration 'no such column' error, and nothing else?

    Deliberately narrow. Treating any failure as "not migrated yet" would make a
    transient outage permanently serve a balance built from frozen pre-merge
    columns, with no error to notice.

    THE CODE FIRST. postgrest's APIError carries `.code`, and 42703 is the
    Postgres undefined_column code -- an exact fact, unlike a message that
    changes with the library version.
    """
    if str(getattr(exc, "code", "")) == "42703":
        return True
    msg = str(exc)
    return "42703" in msg or ("credits" in msg and "column" in msg.lower())


def _one(query):
    """Run a single-row query WITHOUT maybe_single(), and return the row or None.

    maybe_single() is not usable on any read that needs to distinguish one
    failure from another: it catches the real error and re-raises a generic one.
    Asked for a column that does not exist, PostgREST answers

        {"code": "42703", "message": "column profiles.credits does not exist"}

    and maybe_single() turns that into

        {"code": "204", "message": "Missing response"}

    -- no code, no column name, nothing to key on. That is what hid the credits
    balance on Home: the missing-column check above was correct and simply had
    no evidence left to match, so it re-raised and the caller fell back to
    showing nothing. limit(1) preserves the original error.
    """
    res = query.limit(1).execute()
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


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
        return _one(sb.table("profiles").select(
            "user_id,email,role,disabled,credits").eq("user_id", user_id))
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
        if not _is_missing_credits_column(e):
            raise
        _log.error("profiles.credits missing -- the merge migration has not been "
                   "applied yet. Falling back to scan+deep. APPLY "
                   "20260824030000_merge_credit_buckets.sql NOW.")
        row = _one(sb.table("profiles").select(
            "user_id,email,role,disabled,scan_credits,deep_credits").eq("user_id", user_id))
        if row is not None:
            row["credits"] = int(row.get("scan_credits") or 0) + int(row.get("deep_credits") or 0)
        return row


def fetch_credits(user_id: str) -> int | None:
    """The merged balance for a user, or None if it truly cannot be read.

    ONE IMPLEMENTATION, because there were two and the second one was wrong.
    pages/Home.py queried `profiles` directly instead of going through
    get_my_profile, so the deploy-order fallback added here did nothing for the
    landing page: before the merge migration is applied by hand, `credits` does
    not exist, PostgREST answers 42703, Home's own query returned None, and the
    page rendered neither the balance NOR the Buy button -- both sat behind the
    same `if`. The owner saw exactly that on the live site.

    Duplicating a fallback is how one copy silently goes stale. This is the copy.
    """
    if not user_id:
        return None
    sb = get_client()
    try:
        row = _one(sb.table("profiles").select("credits").eq("user_id", user_id))
        return int((row or {}).get("credits") or 0)
    except Exception as e:
        if not _is_missing_credits_column(e):
            # A real failure -- a network blip, an auth problem. Reading it as
            # "the migration has not run" would serve a frozen-column balance
            # forever and never raise again.
            raise
        _log.error("profiles.credits missing -- the merge migration has not been "
                   "applied yet. Falling back to scan+deep. APPLY "
                   "20260824030000_merge_credit_buckets.sql NOW.")
        d = _one(sb.table("profiles").select("scan_credits,deep_credits").eq(
            "user_id", user_id)) or {}
        return int(d.get("scan_credits") or 0) + int(d.get("deep_credits") or 0)
