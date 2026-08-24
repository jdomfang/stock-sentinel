#!/usr/bin/env python3
"""Create a Stripe Checkout session through the DEPLOYED payments-api.

Costs nothing and charges nobody: a Checkout Session is just a URL. It is only
a payment once somebody completes it, and in a sandbox not even then.

WHY THIS EXISTS. Reproducing the payment path by hand meant pasting a shared
secret onto a command line, which puts it in shell history. This reads it from
.streamlit/secrets.toml, the same place the portal reads it from, so the secret
is never typed and never echoed.

    python3 scripts/new_checkout_session.py [user_id]

With no argument it uses the signed-in owner account.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config as c  # noqa: E402

DEFAULT_USER = "6d7c5148-964b-42ee-963e-bf44da4bfe5c"


def main() -> int:
    base = (c.get("PAYMENTS_API_BASE_URL") or "").rstrip("/")
    secret = c.get("PAYMENTS_API_SHARED_SECRET") or ""
    if not base or not secret:
        print("PAYMENTS_API_BASE_URL / PAYMENTS_API_SHARED_SECRET not configured")
        return 1

    user_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    req = urllib.request.Request(
        f"{base}/create-checkout-session",
        data=json.dumps({"user_id": user_id}).encode(),
        method="POST",
        headers={"Content-Type": "application/json",
                 "X-Payments-Shared-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = (e.read() or b"")[:300].decode(errors="replace")
        print(f"FAILED {e.code}: {body}")
        # The two that actually happen, and what each means.
        if e.code == 401:
            print("  -> PAYMENTS_API_SHARED_SECRET does not match the service's")
        if e.code == 500:
            print("  -> usually APP_BASE_URL or STRIPE_SECRET_KEY unset on Railway")
        return 1
    except Exception as e:
        print(f"FAILED {type(e).__name__}: {str(e)[:160]}")
        return 1

    print(f"session: {out.get('id')}")
    print("\nPay with test card 4242 4242 4242 4242, any future expiry, any CVC:\n")
    print(out.get("checkout_url"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
