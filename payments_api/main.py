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
        # THE CODE FIRST, the message only as a fallback. Reading "already
        # processed" out of an error we merely failed to parse is the dangerous
        # direction: it makes the caller skip the grant and return 200, so a
        # paying customer gets nothing and Stripe never retries. The word
        # "unique" appearing in an unrelated message is enough to do that.
        # 23505 is the Postgres unique_violation code and PostgREST returns it
        # verbatim, so this almost never falls through.
        code = str(getattr(e, "code", "") or "")
        if code == "23505":
            return False
        if code:
            # A structured code we recognise as NOT a duplicate. Raising makes
            # Stripe retry, which is the recoverable failure.
            raise
        msg = str(e).lower()
        if "23505" in msg or "duplicate key" in msg or "unique constraint" in msg:
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
    credits_granted: int = 0,
) -> None:
    """Purchase audit record.

    `amount_total` is money (cents); the *_granted columns are credits. Both are
    needed: reconciliation is denominated in credits, and the credit counts live
    only in Stripe session metadata otherwise.
    """
    row = {
        "user_id": user_id,
        "event_id": event_id,
        "checkout_session_id": checkout_session_id,
        "payment_intent_id": payment_intent_id,
        "amount_total": amount_total,
        "currency": currency,
        "status": status,
    }
    try:
        sb = _supabase_admin_client()
        sb.table("purchases").insert(
            {
                **row,
                "credits_granted": int(credits_granted),
                # The legacy columns are still written, both zero. They are kept
                # so a chargeback on a PRE-merge purchase can still read what
                # that purchase actually granted; a new row has nothing to say
                # in the old denomination and must not pretend otherwise.
                "scan_credits_granted": 0,
                "deep_credits_granted": 0,
            }
        ).execute()
    except Exception as e:
        # DEPLOY-ORDER TOLERANCE. `credits_granted` is added by the merge
        # migration, which is applied by hand while this service deploys from a
        # git push -- so there is a window where the column does not exist yet.
        # Losing the row is not cosmetic: it is the row a chargeback reads to
        # decide how much to revoke, and without it the revocation falls back to
        # deriving from the amount, which is right only by coincidence.
        #
        # Retry once in the old shape, recording the grant in the columns that
        # DO exist. The migration's backfill later sums them into
        # credits_granted, so the row ends up correct either way.
        if "credits_granted" in str(e) or "PGRST204" in str(e):
            logger.error("purchases.credits_granted missing -- merge migration not "
                         "applied yet; recording this purchase in the legacy columns")
            try:
                _supabase_admin_client().table("purchases").insert(
                    {**row, "scan_credits_granted": int(credits_granted),
                     "deep_credits_granted": 0}
                ).execute()
                return
            except Exception:
                logger.exception("purchases fallback insert ALSO failed user_id=%s", user_id)

        # Log the actual error. This was previously a bare log line, which is how
        # the missing `purchases` table went unnoticed from launch until the
        # Phase 0 review: every insert failed with PGRST205 and said nothing.
        logger.exception(
            "purchases insert FAILED user_id=%s event_id=%s -- audit row lost",
            user_id,
            event_id,
        )


# WHAT A GIVEN AMOUNT BUYS. The single source of truth, used to price a
# Checkout Session AND to decide what a completed one grants.
#
# The grant used to read `metadata.scan_credits`, defaulting to 1. Metadata is
# a free-text dict that WE write at session creation -- but a session created
# from the Stripe dashboard, a payment link, or a future pack whose key drifts
# carries whatever metadata someone typed, and the `or 1` turned every one of
# those into a silent 1+1 grant against an unknown amount of money. Money paid
# is the one fact Stripe guarantees and nobody can typo, so credits are derived
# from it and an unrecognised amount is refused rather than guessed.
# HOW BIG A PACK IS. One number, because the pack size is a pricing decision
# and nothing structural depends on it.
#
# Two, not ten, and the reason is arithmetic rather than taste. X bills $0.005
# per post READ. A scan buys up to 300 posts ($1.50) and a deep analysis up to
# ~400 ($2.00), so two credits spent on the two most expensive actions cost
# about $4.00 to serve against $5.00 of revenue. At ten credits for $5 the same
# pack costs up to $20 to serve -- a loss on essentially every sale.
#
# This also makes the merge value-neutral: a user holding 1 scan + 1 deep paid
# $5 for two actions and now holds 2 credits worth two actions.
PACK_CREDITS = 2

PACKS: dict[tuple[str, int], int] = {
    # (currency, amount in minor units): credits granted
    ("usd", 500): PACK_CREDITS,
}


def _credits_for(currency: str | None, amount_total: int | None) -> int | None:
    """How many credits this payment buys, or None if we do not recognise it.

    None is a refusal, never a default. The caller makes Stripe retry so the
    payment is visible and recoverable once the pack is added here, rather than
    granting an arbitrary quantity against real money.
    """
    if amount_total is None or not currency:
        return None
    return PACKS.get((str(currency).lower(), int(amount_total)))


def _purchase_by_payment_intent(payment_intent_id: str) -> dict[str, Any] | None:
    """The purchase row we wrote when we granted for this payment, if any.

    THE BEST SOURCE FOR A REVOCATION. Stripe metadata says what a session was
    *marked* as buying; this row says what we actually *granted*. Undoing the
    grant is the only definition of a revocation that cannot drift -- if the
    pack table changed between the purchase and the refund, deriving from the
    amount would revoke the wrong quantity, and metadata never knew.
    """
    try:
        sb = _supabase_admin_client()
        res = (sb.table("purchases")
                 .select("user_id,credits_granted,scan_credits_granted,"
                         "deep_credits_granted,amount_total,currency")
                 .eq("payment_intent_id", payment_intent_id)
                 .limit(1).execute())
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        logger.exception("purchases lookup failed for payment_intent=%s", payment_intent_id)
        return None


def _require_shared_secret(x_payments_shared_secret: str | None) -> None:
    # Simple shared-secret auth for the Streamlit -> API call.
    if not PAYMENTS_API_SHARED_SECRET:
        raise HTTPException(status_code=500, detail="Server not configured")
    if not x_payments_shared_secret or x_payments_shared_secret != PAYMENTS_API_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# -------- App --------

app = FastAPI(title="Stock Sentinel Payments API")

# WHICH BUILD ANSWERED. core-api has carried this since the migration for one
# reason: without it a deploy is unfalsifiable, and a test against the old image
# passes and proves nothing. Bump it whenever behaviour or the response contract
# changes.
SERVICE_VERSION = "2026.08.24-merged-credits"


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "payments_api",
        "version": SERVICE_VERSION,
        "env": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("ENV") or "unknown",
        # The pack table the webhook grants from, so a mismatch between what is
        # charged and what is granted is visible without reading logs.
        # `v` is a scalar credit count. It was list(v) while packs were tuples;
        # left unchanged that is TypeError: 'int' object is not iterable, and
        # /health -- the endpoint that exists to prove which build is live --
        # would 500 on the deploy that needed proving.
        "packs": {f"{c}:{a}": v for (c, a), v in PACKS.items()},
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

    # One pack for now: $5 -> +1 scan, +1 deep. Priced FROM the table the
    # webhook grants from, so the two can never drift apart.
    pack_currency, pack_amount = "usd", 500
    pack_credits = PACKS[(pack_currency, pack_amount)]

    # Metadata still carries user_id -- it is the only way to know WHO paid, and
    # nothing else supplies it. It no longer decides HOW MUCH: the credit counts
    # below are advisory, recorded for debugging, and the webhook ignores them.
    pack_meta = {
        "user_id": str(user_id),
        "pack": f"{pack_amount}_{pack_currency}_{pack_credits}_credits",
        "credits": str(pack_credits),
    }

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": pack_currency,
                        "unit_amount": pack_amount,
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
    # Hoisted. It used to be assigned inside the checkout branch only, while the
    # revocation branch below read it in its fallback revoke key -- so a refund
    # whose charge carried no payment_intent raised UnboundLocalError, returned
    # 500, and made Stripe retry a revocation that could never succeed.
    event_id = event.get("id")

    # Default: acknowledge (Stripe retries on non-2xx)
    try:
        # BOTH events, one handler. `completed` fires the moment the customer
        # finishes the Checkout flow -- which for an async method (SEPA, Bacs,
        # boleto, OXXO) is BEFORE the money moves: payment_status is 'unpaid'
        # and settlement arrives later as async_payment_succeeded, or never, as
        # async_payment_failed. Granting on `completed` alone hands out credits
        # for money that may never arrive. Gating on payment_status without
        # handling the async event would trade that for the opposite bug -- a
        # customer who really paid, waiting forever -- so neither is optional.
        #
        # A card payment (the only method enabled today) settles synchronously
        # and arrives 'paid' on `completed`, which is exactly why testing with
        # 4242 4242 4242 4242 cannot reveal any of this.
        if etype in ("checkout.session.completed",
                     "checkout.session.async_payment_succeeded"):
            session = event["data"]["object"]
            session_id = session.get("id")
            meta = session.get("metadata") or {}
            user_id = meta.get("user_id")
            pay_status = (session.get("payment_status") or "").lower()

            # Refuse rather than proceed unguarded. Stripe always sends an id, so
            # this is theoretical -- but the safe default on a money path is to
            # make Stripe retry, not to grant credits with no dedup at all.
            if not event_id:
                logger.error("Stripe event missing id; refusing to process unguarded")
                raise HTTPException(status_code=400, detail="Stripe event missing id")

            # NOT AN ERROR, and must not be retried: the customer completed
            # checkout and the bank has not settled yet. Acknowledge and wait for
            # async_payment_succeeded. Returning 500 here would bury a normal
            # async purchase under three days of failing retries.
            if pay_status not in ("paid", "no_payment_required"):
                logger.info(
                    "Checkout completed but not paid yet (payment_status=%s) "
                    "session_id=%s -- awaiting async settlement, granting nothing",
                    pay_status or "?", session_id,
                )
                return {"ok": True}

            # Validate BEFORE writing the idempotency guard. Previously the guard
            # row committed first and `if user_id:` was checked after, so a
            # session whose metadata lacked user_id -- a Stripe payment link, a
            # dashboard-created session, a future pack whose metadata key drifts
            # -- granted nothing, recorded nothing, logged nothing, returned 200,
            # and made every Stripe retry a permanent no-op. The customer is
            # charged and gets zero credits, discoverable only by diffing Stripe
            # against the purchases table by hand.
            if not user_id:
                logger.critical(
                    "PAID CHECKOUT WITH NO user_id IN METADATA -- no credits granted. "
                    "event_id=%s session_id=%s payment_intent=%s amount=%s. Reconcile manually.",
                    event_id, session_id, session.get("payment_intent"), session.get("amount_total"),
                )
                # 500 so Stripe retries (~3 days) and it is visible in the Stripe
                # dashboard rather than silently succeeding. No guard row was
                # written, so a retry after a fix will work.
                return JSONResponse(status_code=500, content={"ok": False, "error": "missing user_id"})

            # WHAT THE MONEY BUYS -- from the amount, not from metadata.
            amount_total = session.get("amount_total")
            currency = session.get("currency")
            grant = _credits_for(currency, amount_total)
            if grant is None:
                logger.critical(
                    "PAID CHECKOUT FOR AN UNRECOGNISED AMOUNT -- no credits granted. "
                    "event_id=%s session_id=%s user_id=%s amount=%s %s. Add it to PACKS "
                    "and let Stripe retry, or reconcile manually.",
                    event_id, session_id, user_id, amount_total, currency,
                )
                # Same reasoning as missing user_id: no guard row written, so a
                # retry after adding the pack grants correctly.
                return JSONResponse(status_code=500, content={"ok": False, "error": "unknown amount"})
            credits = grant

            # KEYED ON THE SESSION, not the event. One purchase can now reach us
            # as two different event ids -- `completed` (unpaid) followed by
            # `async_payment_succeeded` (paid) -- and an event-id guard would let
            # both through, granting twice for one payment. The session id is the
            # purchase. This is the same correction the revocation path below
            # already carries for payment_intent.
            grant_key = f"grant:{session_id}" if session_id else f"grant:evt:{event_id}"
            if not _mark_stripe_event_processed(event_id=grant_key, user_id=user_id, session_id=session_id):
                logger.info("Duplicate grant ignored %s (event_id=%s type=%s)",
                            grant_key, event_id, etype)
                return {"ok": True}

            try:
                # The whole grant goes in the scan slot and 0 in the deep slot.
                # grant_credits SUMS its two deltas now, so the split is
                # meaningless to it -- but the signature is unchanged on purpose
                # (a new one would be an overload, not a replacement).
                _apply_credit_delta(user_id=user_id, scan_delta=credits,
                                    deep_delta=0, reason=etype,
                                    request_id=grant_key)
            except Exception:
                # The guard row committed in its own transaction BEFORE this
                # ran. Leaving it in place would make Stripe's retry a no-op
                # and strand a paid customer with zero credits, recoverable
                # only by log archaeology. Release it so the retry can work.
                try:
                    _supabase_admin_client().table("stripe_events_processed") \
                        .delete().eq("event_id", grant_key).execute()
                except Exception:
                    logger.exception(
                        "CRITICAL: paid purchase not granted and idempotency guard "
                        "not released for %s user_id=%s -- MANUAL CREDIT "
                        "GRANT REQUIRED", grant_key, user_id,
                    )
                raise
            _record_purchase(
                user_id=user_id,
                event_id=event_id or None,
                checkout_session_id=session_id,
                payment_intent_id=session.get("payment_intent"),
                amount_total=amount_total,
                currency=currency,
                status=pay_status or session.get("status"),
                credits_granted=credits,
            )

        elif etype in {"charge.refunded", "charge.dispute.created"}:
            # Revoke credits on refund/dispute.
            charge = event["data"]["object"]
            payment_intent = charge.get("payment_intent")
            user_id = None
            # 0, not -1. These are overwritten by every path that learns the
            # real quantity below; as a DEFAULT, -1 was a guess about somebody
            # else's money that only ever ran when we knew the least.
            scan_delta = 0
            deep_delta = 0

            # A partial refund still revokes the whole pack. That is the
            # existing behaviour and it is deliberate: a credit is indivisible,
            # so there is no honest way to give back 40% of one. Logged below so
            # it is visible rather than assumed.
            if charge.get("amount_refunded") and charge.get("amount") and \
                    int(charge["amount_refunded"]) < int(charge["amount"]):
                logger.warning(
                    "Partial refund (%s of %s) revokes the full pack payment_intent=%s",
                    charge.get("amount_refunded"), charge.get("amount"), payment_intent,
                )

            # 1. What we actually granted. Authoritative when present.
            row = _purchase_by_payment_intent(payment_intent) if payment_intent else None
            if row:
                user_id = row.get("user_id")
                # BOTH ERAS, from one row. A post-merge purchase carries
                # credits_granted; a pre-merge one carries (1,1) in the legacy
                # columns and a zero here. Falling back to their sum revokes
                # exactly what that purchase granted -- 2 for an old pack, 10
                # for a new one -- without the caller knowing which era it is.
                #
                # Getting this wrong is not visible afterwards: grant_credits
                # clamps a revocation at zero and records only what it applied,
                # so an over-revocation empties a DIFFERENT, unrefunded pack and
                # the ledger still reconciles perfectly.
                granted = int(row.get("credits_granted") or 0)
                if granted <= 0:
                    granted = (int(row.get("scan_credits_granted") or 0)
                               + int(row.get("deep_credits_granted") or 0))
                scan_delta = -granted
                deep_delta = 0

            # 2. No purchase row -- the grant predates this table, or its insert
            #    failed. Fall back to Stripe: metadata for WHO (nothing else
            #    knows), the amount for HOW MUCH (metadata must not decide that,
            #    for the same reason it must not decide a grant).
            elif payment_intent and STRIPE_SECRET_KEY:
                try:
                    pi = stripe.PaymentIntent.retrieve(payment_intent)
                    pi = pi if isinstance(pi, dict) else {}
                    user_id = (pi.get("metadata") or {}).get("user_id")
                    derived = _credits_for(pi.get("currency"), pi.get("amount"))
                    if derived is None:
                        logger.critical(
                            "REFUND FOR AN UNRECOGNISED AMOUNT and no purchases row -- "
                            "nothing revoked. payment_intent=%s user_id=%s amount=%s %s. "
                            "Reconcile manually.",
                            payment_intent, user_id, pi.get("amount"), pi.get("currency"),
                        )
                        user_id = None
                    else:
                        scan_delta, deep_delta = -derived, 0
                except Exception:
                    logger.exception("PaymentIntent lookup failed for %s", payment_intent)
                    user_id = None

            if user_id and scan_delta == 0 and deep_delta == 0:
                logger.info("Refund revokes nothing (zero-credit purchase) payment_intent=%s",
                            payment_intent)
                return {"ok": True}

            if user_id:
                # Guard on the PAYMENT INTENT, not the event id. The event-id
                # guard only stops a retry of the SAME event; a disputed charge
                # emits charge.dispute.created AND charge.refunded -- two distinct
                # event ids for one payment -- and both would revoke, taking -2/-2
                # for a single purchase. Because the delta is clamped at zero, the
                # second revocation silently eats credits bought in a DIFFERENT,
                # unrefunded transaction, and the ledger records that as a
                # legitimate applied delta. A partial refund has the same shape.
                revoke_key = f"revoke:{payment_intent}" if payment_intent else f"revoke:evt:{event_id}"
                if not _mark_stripe_event_processed(event_id=revoke_key, user_id=user_id, session_id=None):
                    logger.info("Revocation already applied for %s; ignoring %s event_id=%s",
                                revoke_key, etype, event_id)
                    return {"ok": True}

                _apply_credit_delta(user_id=user_id, scan_delta=scan_delta,
                                    deep_delta=deep_delta, reason=etype,
                                    request_id=revoke_key)

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


def _apply_credit_delta(
    *, user_id: str, scan_delta: int, deep_delta: int, reason: str, request_id: str
) -> None:
    """Apply a signed credit delta atomically, via public.grant_credits().

    Was a select-then-update against public.profiles -- verbatim the race that
    20260801020000 removed from consume_credit, left in place on the one path
    where the value being written was paid for. Two grants racing (two packs in
    quick succession, or a grant overlapping a revocation) both read N and both
    write N+1: one paid grant lost, while BOTH audit rows were written,
    permanently breaking the balance = -sum(cost) invariant established by
    20260801040000. A consume_credit landing between the read and the write was
    silently overwritten, handing back a credit the user had already spent.

    grant_credits() does the lock, the clamp, the balance change and the ledger
    row in one transaction, and is idempotent on request_id -- which matters
    because Stripe retries a failed webhook for roughly three days.

    request_id must be stable per business event, not per call -- and a Stripe
    EVENT id is not that. One purchase can arrive as two events (`completed`
    unpaid, then `async_payment_succeeded` paid), so the keys are per business
    object: grant:<checkout_session> and revoke:<payment_intent>.
    """
    sb = _supabase_admin_client()

    logger.info(
        "Applying credit delta user_id=%s scan_delta=%s deep_delta=%s reason=%s request_id=%s",
        user_id, scan_delta, deep_delta, reason, request_id,
    )

    res = sb.rpc(
        "grant_credits",
        {
            "p_user_id": user_id,
            "p_scan_delta": int(scan_delta),
            "p_deep_delta": int(deep_delta),
            "p_reason": reason,
            "p_request_id": request_id,
        },
    ).execute()
    data = getattr(res, "data", None)

    # Raise on anything unexpected: the caller releases the idempotency guard so
    # Stripe's retry can work. Returning quietly would strand a paid customer.
    if not isinstance(data, dict):
        raise RuntimeError(f"grant_credits returned {data!r}")
    if not data.get("ok"):
        raise RuntimeError(f"grant_credits refused: {data.get('reason')}")

    if data.get("reason") == "duplicate_request":
        logger.info("grant_credits replayed (already applied) user_id=%s request_id=%s",
                    user_id, request_id)
        return

    # Clamping hides how much of a revocation could not be applied because the
    # user had already spent the credits. Say so, loudly, rather than losing it.
    if data.get("clamped"):
        logger.warning(
            "Credit revocation clamped at zero user_id=%s reason=%s requested %s applied %s",
            user_id, reason, scan_delta + deep_delta, data.get("applied"),
        )

    logger.info(
        "Credits updated user_id=%s applied %s -> balance %s event=%s",
        user_id, data.get("applied"),
        data.get("credits"), data.get("event_id"),
    )
