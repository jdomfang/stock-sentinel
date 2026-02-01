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
            session = event["data"]["object"]
            meta = session.get("metadata") or {}
            user_id = meta.get("user_id")
            scan_delta = int(meta.get("scan_credits") or 1)
            deep_delta = int(meta.get("deep_credits") or 1)

            if user_id:
                _apply_credit_delta(user_id=user_id, scan_delta=scan_delta, deep_delta=deep_delta, reason=etype)

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

    except Exception:
        # Return 200 to prevent infinite retries; errors should be inspected in Railway logs.
        return JSONResponse(status_code=200, content={"ok": True})

    return {"ok": True}


def _apply_credit_delta(*, user_id: str, scan_delta: int, deep_delta: int, reason: str) -> None:
    sb = _supabase_admin_client()

    # Load current
    resp = sb.table("profiles").select("scan_credits,deep_credits").eq("user_id", user_id).maybe_single().execute()
    data = getattr(resp, "data", None) or {}

    cur_scan = int(data.get("scan_credits") or 0)
    cur_deep = int(data.get("deep_credits") or 0)

    new_scan = cur_scan + int(scan_delta)
    new_deep = cur_deep + int(deep_delta)

    sb.table("profiles").update({"scan_credits": new_scan, "deep_credits": new_deep}).eq("user_id", user_id).execute()

    # Best-effort audit log (optional table)
    try:
        sb.table("usage_events").insert(
            {
                "user_id": user_id,
                "event_type": "purchase" if scan_delta > 0 or deep_delta > 0 else "revoke",
                "reason": reason,
                "scan_delta": int(scan_delta),
                "deep_delta": int(deep_delta),
            }
        ).execute()
    except Exception:
        pass
