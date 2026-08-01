from __future__ import annotations

import json
import os
import logging
from typing import Any

import stripe
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("payments_api")
logging.basicConfig(level=logging.INFO)


def _init_sentry() -> None:
    """Report exceptions from the service that handles money.

    This is the only component where a silent failure costs a real payment: a
    webhook that raises returns 500, Stripe retries for ~3 days, and then stops.
    Until now nothing reported that, so a broken grant path would surface as a
    customer complaint days later. Never raises -- a misconfigured DSN must not
    stop the service taking webhooks.
    """
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.info("Sentry not configured (no SENTRY_DSN)")
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("ENV") or "production",
            traces_sample_rate=0.0,
            send_default_pii=False,  # card and customer data must not leave Stripe/Railway
        )
        logger.info("Sentry initialised")
    except Exception as e:
        logger.warning("Sentry init failed (%s); continuing without it", type(e).__name__)


_init_sentry()


# -------- Config --------

def _env(name: str, default: str | None = None) -> str:
    v = os.getenv(name)
    if v is None or v == "":
        if default is not None:
            return default
        raise RuntimeError(f"Missing required env var: {name}")
    return v


APP_BASE_URL = os.getenv("APP_BASE_URL", "")
PAYMENTS_API_SHARED_SECRET = os.getenv("PAYMENTS_API_SHARED_SECRET", "")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _configure_stripe() -> None:
    if STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY


def _supabase_admin_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Supabase service credentials are not configured")
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _mark_stripe_event_processed(*, event_id: str, user_id: str | None = None, session_id: str | None = None) -> bool:
    """Idempotency guard.

    Returns True if this is the first time we see the event.
    Returns False if it was already processed.

    Requires Supabase SQL table `stripe_events_processed`.
    """
    sb = _supabase_admin_client()
    try:
        sb.table("stripe_events_processed").insert(
            {"event_id": event_id, "user_id": user_id, "checkout_session_id": session_id}
        ).execute()
        return True
    except Exception as e:
        # Supabase/PostgREST client errors differ by version; detect unique violation defensively.
        msg = str(e).lower()
        if "duplicate" in msg or "23505" in msg or "unique" in msg:
            return False
        raise


def _record_purchase(
    *,
    user_id: str,
    event_id: str | None,
    checkout_session_id: str | None,
    payment_intent_id: str | None,
    amount_total: int | None,
    currency: str | None,
    status: str | None,
    scan_credits_granted: int = 0,
    deep_credits_granted: int = 0,
) -> None:
    """Purchase audit record.

    `amount_total` is money (cents); the *_granted columns are credits. Both are
    needed: reconciliation is denominated in credits, and the credit counts live
    only in Stripe session metadata otherwise.
    """
    try:
        sb = _supabase_admin_client()
        sb.table("purchases").insert(
            {
                "user_id": user_id,
                "event_id": event_id,
                "checkout_session_id": checkout_session_id,
                "payment_intent_id": payment_intent_id,
                "amount_total": amount_total,
                "currency": currency,
                "status": status,
                "scan_credits_granted": int(scan_credits_granted),
                "deep_credits_granted": int(deep_credits_granted),
            }
        ).execute()
    except Exception:
        # Log the actual error. This was previously a bare log line, which is how
        # the missing `purchases` table went unnoticed from launch until the
        # Phase 0 review: every insert failed with PGRST205 and said nothing.
        logger.exception(
            "purchases insert FAILED user_id=%s event_id=%s -- audit row lost",
            user_id,
            event_id,
        )


def _require_shared_secret(x_payments_shared_secret: str | None) -> None:
    # Simple shared-secret auth for the Streamlit -> API call.
    if not PAYMENTS_API_SHARED_SECRET:
        raise HTTPException(status_code=500, detail="Server not configured")
    if not x_payments_shared_secret or x_payments_shared_secret != PAYMENTS_API_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# -------- App --------

app = FastAPI(title="Stock Sentinel Payments API")


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "payments_api",
        "env": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("ENV") or "unknown",
    }


@app.post("/create-checkout-session")
async def create_checkout_session(
    payload: dict[str, Any],
    x_payments_shared_secret: str | None = Header(default=None, alias="X-Payments-Shared-Secret"),
):
    """Create a Stripe Checkout session for the $5 credit pack.

    Security: protected by a shared secret header (MVP).

    Expected payload: {"user_id": "<supabase_user_id>"}
    """
    _require_shared_secret(x_payments_shared_secret)
    _configure_stripe()

    user_id = (payload or {}).get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")

    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="APP_BASE_URL not configured")

    # We keep the pack simple for now: $5 → +1 scan, +1 deep
    pack_meta = {
        "user_id": str(user_id),
        "pack": "5_usd_1_scan_1_deep",
        "scan_credits": "1",
        "deep_credits": "1",
    }

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": 500,
                        "product_data": {"name": "Sentinel Credits"},
                    },
                    "quantity": 1,
                }
            ],
            success_url=f"{APP_BASE_URL}/?payment=success",
            cancel_url=f"{APP_BASE_URL}/?payment=cancel",
            metadata=pack_meta,
            payment_intent_data={"metadata": pack_meta},
        )
    except Exception as e:
        # Log full error server-side, but keep client response generic.
        logger.exception("Failed to create Stripe Checkout Session")
        raise HTTPException(status_code=500, detail="Failed to create checkout session") from e

    return {"checkout_url": session.url, "id": session.id}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook receiver.

    Verifies signature using STRIPE_WEBHOOK_SECRET.
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Stripe webhook secret not configured")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    _configure_stripe()

    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig, secret=STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event.get("type")

    # Default: acknowledge (Stripe retries on non-2xx)
    try:
        if etype == "checkout.session.completed":
            event_id = event.get("id")
            session = event["data"]["object"]
            session_id = session.get("id")
            meta = session.get("metadata") or {}
            user_id = meta.get("user_id")
            scan_delta = int(meta.get("scan_credits") or 1)
            deep_delta = int(meta.get("deep_credits") or 1)

            # Refuse rather than proceed unguarded. Stripe always sends an id, so
            # this is theoretical -- but the safe default on a money path is to
            # make Stripe retry, not to grant credits with no dedup at all.
            if not event_id:
                logger.error("Stripe event missing id; refusing to process unguarded")
                raise HTTPException(status_code=400, detail="Stripe event missing id")

            if not _mark_stripe_event_processed(event_id=event_id, user_id=user_id, session_id=session_id):
                logger.info("Duplicate Stripe event ignored event_id=%s type=%s", event_id, etype)
                return {"ok": True}

            if user_id:
                try:
                    _apply_credit_delta(user_id=user_id, scan_delta=scan_delta, deep_delta=deep_delta, reason=etype)
                except Exception:
                    # The guard row committed in its own transaction BEFORE this
                    # ran. Leaving it in place would make Stripe's retry a no-op
                    # and strand a paid customer with zero credits, recoverable
                    # only by log archaeology. Release it so the retry can work.
                    try:
                        _supabase_admin_client().table("stripe_events_processed") \
                            .delete().eq("event_id", event_id).execute()
                    except Exception:
                        logger.exception(
                            "CRITICAL: paid purchase not granted and idempotency guard "
                            "not released for event_id=%s user_id=%s -- MANUAL CREDIT "
                            "GRANT REQUIRED", event_id, user_id,
                        )
                    raise
                _record_purchase(
                    user_id=user_id,
                    event_id=event_id or None,
                    checkout_session_id=session_id,
                    payment_intent_id=session.get("payment_intent"),
                    amount_total=session.get("amount_total"),
                    currency=session.get("currency"),
                    status=session.get("payment_status") or session.get("status"),
                    scan_credits_granted=scan_delta,
                    deep_credits_granted=deep_delta,
                )

        elif etype in {"charge.refunded", "charge.dispute.created"}:
            # Revoke credits on refund/dispute.
            charge = event["data"]["object"]
            payment_intent = charge.get("payment_intent")
            user_id = None
            scan_delta = -1
            deep_delta = -1

            if payment_intent and STRIPE_SECRET_KEY:
                try:
                    pi = stripe.PaymentIntent.retrieve(payment_intent)
                    meta = (pi.get("metadata") or {}) if isinstance(pi, dict) else {}
                    user_id = meta.get("user_id")
                    scan_delta = -int(meta.get("scan_credits") or 1)
                    deep_delta = -int(meta.get("deep_credits") or 1)
                except Exception:
                    user_id = None

            if user_id:
                _apply_credit_delta(user_id=user_id, scan_delta=scan_delta, deep_delta=deep_delta, reason=etype)

        # else: ignore other events

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error handling Stripe webhook event type=%s", etype)
        # 200 suppresses Stripe's retry. That is acceptable for events we merely
        # observe, but NOT for the one that grants credits against a completed
        # payment: swallowing it strands a paying customer at zero credits with
        # no retry and no alert. Let Stripe retry with backoff (~3 days) and
        # surface the failure in the dashboard.
        if etype == "checkout.session.completed":
            return JSONResponse(status_code=500, content={"ok": False})
        return JSONResponse(status_code=200, content={"ok": True})

    return {"ok": True}


def _apply_credit_delta(*, user_id: str, scan_delta: int, deep_delta: int, reason: str) -> None:
    sb = _supabase_admin_client()

    logger.info(
        "Applying credit delta user_id=%s scan_delta=%s deep_delta=%s reason=%s",
        user_id,
        scan_delta,
        deep_delta,
        reason,
    )

    # Load current
    resp = sb.table("profiles").select("scan_credits,deep_credits").eq("user_id", user_id).maybe_single().execute()
    data = getattr(resp, "data", None) or {}

    cur_scan = int(data.get("scan_credits") or 0)
    cur_deep = int(data.get("deep_credits") or 0)

    # Clamp at zero. Revocation deltas (refund / dispute) are negative and were
    # previously applied unclamped, writing a negative balance. The
    # profiles_credits_non_negative CHECK added in migration
    # 20260801020000_credit_integrity.sql rejects that, and the webhook handler
    # swallows the error and still returns 200 to Stripe -- so an unclamped
    # revocation would be lost silently on a money path.
    #
    # THIS CLAMP MUST BE DEPLOYED BEFORE THAT MIGRATION IS APPLIED.
    raw_scan = cur_scan + int(scan_delta)
    raw_deep = cur_deep + int(deep_delta)
    new_scan = max(0, raw_scan)
    new_deep = max(0, raw_deep)

    # Clamping hides how much of a revocation could not be applied (the user had
    # already spent the credits). Say so, loudly, rather than losing it.
    if raw_scan < 0 or raw_deep < 0:
        logger.warning(
            "Credit revocation clamped at zero user_id=%s reason=%s "
            "scan %s->%s (unapplied %s) deep %s->%s (unapplied %s)",
            user_id, reason,
            cur_scan, new_scan, min(0, raw_scan),
            cur_deep, new_deep, min(0, raw_deep),
        )

    # What actually moved. The audit row below must record this, not the request.
    applied_scan = new_scan - cur_scan
    applied_deep = new_deep - cur_deep

    # If the profile row doesn't exist for some reason, create it.
    if not data:
        try:
            sb.table("profiles").insert({"user_id": user_id, "scan_credits": new_scan, "deep_credits": new_deep}).execute()
            logger.info("Inserted missing profile row for user_id=%s", user_id)
        except Exception:
            logger.exception("Failed to insert missing profile row for user_id=%s", user_id)
            raise
    else:
        try:
            sb.table("profiles").update({"scan_credits": new_scan, "deep_credits": new_deep}).eq("user_id", user_id).execute()
        except Exception:
            logger.exception("Failed updating profile credits for user_id=%s", user_id)
            raise

    logger.info("Credits updated user_id=%s scan=%s->%s deep=%s->%s", user_id, cur_scan, new_scan, cur_deep, new_deep)

    # Best-effort audit log (optional table). Schema may differ depending on your Supabase SQL.
    try:
        sb.table("usage_events").insert(
            {
                "user_id": user_id,
                # NOT "scan". A grant/revocation written as a scan debit is
                # indistinguishable from one, so refund_credit would happily
                # "refund" it and mint a credit nobody paid for -- invisibly,
                # because both rows carry the same negative cost. Requires the
                # widened CHECK in 20260801020000_credit_integrity.sql.
                "event_type": "purchase",
                # Record what was APPLIED, not what was requested. When the clamp
                # above bites, the balance moves by less than the requested delta;
                # writing the requested value here would make the ledger and the
                # balance disagree -- a revocation of 10 already-spent credits
                # would be recorded as 10 further credits consumed, double-counting
                # consumption that already has its own rows.
                "cost_scan_credits": -int(applied_scan),
                "cost_deep_credits": -int(applied_deep),
                "metadata": {
                    "reason": reason,
                    "source": "stripe",
                    "scan_delta": int(scan_delta),      # requested
                    "deep_delta": int(deep_delta),
                    "scan_applied": int(applied_scan),  # actually applied
                    "deep_applied": int(applied_deep),
                    "clamped": bool(raw_scan < 0 or raw_deep < 0),
                },
            }
        ).execute()
    except Exception:
        logger.info("usage_events insert skipped/failed (non-fatal)")
