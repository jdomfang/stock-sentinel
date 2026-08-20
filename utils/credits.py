from __future__ import annotations

import logging
import uuid
from typing import NamedTuple


from utils.obs import get_request_id
from utils.supabase_client import get_admin_client

logger = logging.getLogger(__name__)


class CreditResult(NamedTuple):
    """Outcome of a credit mutation.

    ok        -- the caller may proceed with the metered work
    message   -- human-readable text, safe to show a user
    event_id  -- usage_events id; REQUIRED to refund this debit later
    remaining -- balance after the change, or None when nothing moved
    reason    -- machine-readable code from the SQL function, for logging
    """

    ok: bool
    message: str
    event_id: str | None = None
    remaining: int | None = None
    reason: str = ""


# SQL reason codes -> text a user should see.
_MESSAGES = {
    "no_scan_credits": "No scan credits remaining",
    "no_deep_credits": "No Deep Analyze credits remaining",
    "account_disabled": "Account disabled",
    "profile_not_found": "Profile not found",
    "invalid_event_type": "Invalid event type",
    "missing_original_event_id": "Cannot refund without the original event",
    "original_event_not_found": "Original event not found",
    "original_event_mismatch": "Refund does not match the original event",
    "original_event_not_a_debit": "That event was not a charge",
    "already_refunded": "Already refunded",
}


def _get_user_id(user_id: str | None = None) -> str | None:
    """The acting user. Explicit if given, otherwise the portal's session.

    The argument exists so this module can be called from a service that
    received an identity on the request, rather than only from a Streamlit
    script that happens to have session_state. Passing it is how the credit
    engine stops depending on the UI framework.
    """
    if user_id:
        return user_id
    try:
        import streamlit as st
        user = st.session_state.get("auth.user")
    except Exception:
        # Outside the portal with no explicit user, there is nobody to charge.
        # Returning None makes the caller refuse, which is the safe direction
        # for anything that moves money.
        logger.warning("credits: no user_id supplied and no portal session")
        return None
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def consume_credit(event_type: str, metadata: dict | None = None,
                   user_id: str | None = None) -> CreditResult:
    """Debit one credit and write the ledger row, atomically.

    Delegates to public.consume_credit(), where the guard and the decrement are a
    single statement. The previous implementation read the balance and wrote
    balance-1 in a second round trip, so two browser tabs could both read N and
    both write N-1 -- one credit charged, two actions delivered.

    The idempotency key is the caller's request id when one is in scope, so the
    usage_events row and every log line from the same user action share it: a
    ledger row leads back to the logs that produced it, and a log line leads
    forward to the credit it moved. Without that link, "a credit was spent and
    nothing was delivered" could only be investigated by timestamp.

    Falls back to a fresh uuid when no scope is set, so a caller that forgets to
    open one still gets transport-retry idempotency -- but never reuses a STALE
    key for a genuinely new action, which the database would reject as a
    duplicate and the caller would read as success. That is a free scan.
    """
    uid = _get_user_id(user_id)
    if not uid:
        return CreditResult(False, "Not logged in", reason="not_logged_in")

    if event_type not in ("scan", "deep_analyze"):
        return CreditResult(False, "Invalid event type", reason="invalid_event_type")

    rid = get_request_id()
    request_id = rid if rid and rid != "-" else str(uuid.uuid4())

    try:
        res = (
            get_admin_client()
            .rpc(
                "consume_credit",
                {
                    "p_user_id": uid,
                    "p_event_type": event_type,
                    # consume_credit() merges p_request_id into the stored
                    # metadata itself, so passing it twice would be two places
                    # owning one fact.
                    "p_metadata": metadata or {},
                    "p_request_id": request_id,
                },
            )
            .execute()
            .data
        )
    except Exception:
        # Never charge on an error we cannot interpret. The debit and the ledger
        # row commit together or not at all, so a raised RPC means nothing moved.
        logger.exception("consume_credit RPC failed user=%s type=%s", uid, event_type)
        return CreditResult(False, "Could not verify your credits. Please try again.",
                            reason="rpc_error")

    if not isinstance(res, dict):
        logger.error("consume_credit returned %r", res)
        return CreditResult(False, "Could not verify your credits. Please try again.",
                            reason="bad_response")

    reason = res.get("reason") or ""

    # duplicate_request means this exact call already succeeded. The point of an
    # idempotency key is that a retry is lossless, so this IS success -- the
    # ledger row and the debit already exist.
    if res.get("ok"):
        if reason == "duplicate_request":
            logger.info("consume_credit replayed user=%s type=%s", uid, event_type)
        return CreditResult(True, "", res.get("event_id"), res.get("remaining"), reason)

    logger.info("consume_credit refused user=%s type=%s reason=%s", uid, event_type, reason)
    return CreditResult(False, _MESSAGES.get(reason, "Could not use a credit"),
                        None, res.get("remaining"), reason)


def complete_work(event_id: str | None, status: str = "completed", detail: str = "") -> bool:
    """Close the lifecycle row opened alongside a debit. Never raises.

    consume_credit() opens a work_runs row in the same transaction as the debit.
    Closing it here is what distinguishes "paid and delivered" from "paid and
    vanished" -- a distinction the ledger alone cannot make, because a debit
    looks identical either way.

    If this call fails, or never runs because the process was killed, the row
    stays 'running' and reap_orphaned_work refunds it on its next pass. That is
    the intended fallback, not a bug: the whole design assumes this call can be
    lost, since the failure it exists to record is the process dying.
    """
    if not event_id:
        return False
    try:
        res = (
            get_admin_client()
            .rpc(
                "complete_work",
                {"p_event_id": event_id, "p_status": status, "p_detail": (detail or "")[:300]},
            )
            .execute()
            .data
        )
    except Exception:
        # Downgraded to a warning: the reaper is the backstop, so a failure here
        # costs a delayed refund, not a lost one.
        logger.warning("complete_work failed event=%s status=%s -- reaper will handle it",
                       event_id, status)
        return False

    return bool(isinstance(res, dict) and res.get("ok"))


def refund_credit(event_type: str, event_id: str | None, reason: str,
                  user_id: str | None = None) -> bool:
    """Return a credit charged for work that failed. Safe to call twice.

    Called when a metered action is charged and then cannot be delivered -- the X
    API is down, returns 402 credits-depleted, or the analysis raises. Without
    this the user pays for an outage with no recourse.

    Never raises: a failed refund must not replace the user's original error with
    a second one. Returns False and logs instead.
    """
    uid = _get_user_id(user_id)
    if not uid or not event_id:
        return False

    try:
        res = (
            get_admin_client()
            .rpc(
                "refund_credit",
                {
                    "p_user_id": uid,
                    "p_event_type": event_type,
                    "p_original_event_id": event_id,
                    "p_reason": (reason or "")[:200],
                },
            )
            .execute()
            .data
        )
    except Exception:
        logger.exception("refund_credit RPC FAILED user=%s event=%s -- credit NOT returned",
                         uid, event_id)
        return False

    if isinstance(res, dict) and res.get("ok"):
        logger.info("refunded %s credit user=%s event=%s reason=%s",
                    event_type, uid, event_id, reason)
        return True

    got = res.get("reason") if isinstance(res, dict) else res
    # already_refunded is the idempotent path, not a failure.
    logger.log(logging.INFO if got == "already_refunded" else logging.ERROR,
               "refund_credit not applied user=%s event=%s reason=%s", uid, event_id, got)
    return got == "already_refunded"
