#!/usr/bin/env python3
"""Compare Stripe against our own records. Read-only.

WHY

Nothing has ever checked that what Stripe collected matches what we granted.
The webhook is careful -- idempotency keyed on the checkout session, the guard
released if the grant fails so a retry works, 5xx on anything actionable so
Stripe keeps trying -- but every one of those is a control on a SINGLE event.
None of them answers the only question that matters at the end of a month:

    did every payment produce credits, and did every credit come from a payment?

Three ways that can be false, none of which raises anything at the time:

  PAID, NOT GRANTED   the webhook 5xx'd for three days and gave up, or the
                      event never arrived. The customer paid and has nothing.
                      This is the one that costs a customer.
  GRANTED, NOT PAID   a purchases row with no matching Stripe payment. Should be
                      impossible; if it happens, something can mint credits.
  AMOUNT MISMATCH     the row and the payment disagree on how much money moved.
  PACK DRIFT          the credits granted do not match what today's pack table
                      would grant for that amount -- which is what a PACKS
                      change underneath a retry actually looks like. Comparing
                      amount_total to Stripe's amount cannot see it: both are
                      copied from Stripe and a pack change moves neither.

Run it whenever you want a number you can trust:

    python3 scripts/reconcile_stripe.py [--days 30]

Reads Stripe and Supabase. Writes nothing, grants nothing, revokes nothing --
so it is safe to run against production at any time, and deliberately does not
"fix" anything it finds. A discrepancy needs a human deciding what happened.
"""

from __future__ import annotations

import argparse
import base64
import time
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _secrets() -> dict:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    p = REPO / ".streamlit" / "secrets.toml"
    if not p.exists():
        print(f"no {p} -- run this from a checkout that has secrets")
        sys.exit(2)
    return tomllib.loads(p.read_text())


def _stripe(cfg, path, params=""):
    key = cfg.get("STRIPE_SECRET_KEY", "")
    if not key:
        print("STRIPE_SECRET_KEY missing"); sys.exit(2)
    auth = base64.b64encode((key + ":").encode()).decode()
    url = f"https://api.stripe.com/v1/{path}" + (f"?{params}" if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=30) as f:
        return json.load(f)


def _rest(cfg, path):
    su = cfg["SUPABASE_URL"].rstrip("/")
    sk = cfg["SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{su}/rest/v1/{path}",
                                 headers={"apikey": sk, "Authorization": f"Bearer {sk}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        # SystemExit rather than a bare print: callers that have a fallback can
        # catch it, and callers that do not still stop instead of reconciling
        # against an empty list and reporting CLEAN.
        raise SystemExit(f"supabase {path}: HTTP {e.code} {body}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    cfg = _secrets()
    since = int(time.time()) - args.days * 86400

    # SUCCEEDED payments only. A PaymentIntent that never succeeded owes nothing.
    pis, params, page = [], f"limit=100&created[gte]={since}", None
    while True:
        d = _stripe(cfg, "payment_intents", params + (f"&starting_after={page}" if page else ""))
        pis += [p for p in d.get("data", []) if p.get("status") == "succeeded"]
        if not d.get("has_more"):
            break
        page = d["data"][-1]["id"]

    # credits_granted is added by 20260824030000_merge_credit_buckets.sql. A
    # reconciliation tool that cannot run until a migration lands is useless
    # exactly when you most want it -- during a migration -- so fall back to the
    # legacy columns, which credits() already sums.
    # THE SAME WINDOW AS STRIPE. Without this filter every purchase row older
    # than --days is absent from `pis` and gets reported as "GRANTED BUT NOT
    # PAID <- should be impossible" -- so the one class that should never fire
    # is the one guaranteed to fire, on the first run against any real history.
    _since_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
    _filter = f"created_at=gte.{_since_iso}&order=created_at.desc&limit=1000"
    _cols = ("payment_intent_id,amount_total,currency,credits_granted,"
             "scan_credits_granted,deep_credits_granted,user_id,created_at")
    try:
        rows = _rest(cfg, f"purchases?select={_cols}&{_filter}")
    except SystemExit as e:
        # ONLY the missing-column case. Any other error -- bad credentials, a
        # missing table -- must not be retried with fewer columns and reported
        # as a confusing second failure.
        if "42703" not in str(e):
            raise
        print("  (purchases.credits_granted not present -- reading legacy columns)")
        rows = _rest(cfg, "purchases?select=payment_intent_id,amount_total,currency,"
                          f"scan_credits_granted,deep_credits_granted,user_id,created_at&{_filter}")
    if len(rows) >= 1000:
        # A silent truncation would turn every dropped row's payment into a
        # false "PAID BUT NOT GRANTED".
        print("  WARNING: hit the 1000-row page limit; narrow --days or paginate")
    by_pi = {r["payment_intent_id"]: r for r in rows if r.get("payment_intent_id")}

    print("=" * 72)
    print(f"  Stripe reconciliation -- last {args.days} days")
    print("=" * 72)
    print(f"  succeeded payments in Stripe : {len(pis)}")
    print(f"  purchase rows on our side    : {len(rows)}")

    paid_not_granted, mismatched = [], []
    for p in pis:
        r = by_pi.get(p["id"])
        if r is None:
            paid_not_granted.append(p)
            continue
        if int(r.get("amount_total") or 0) != int(p.get("amount") or 0):
            mismatched.append((p, r))

    granted_not_paid = [r for pid, r in by_pi.items()
                        if pid not in {p["id"] for p in pis}]

    def credits(r):
        return (int(r.get("credits_granted") or 0)
                or int(r.get("scan_credits_granted") or 0)
                + int(r.get("deep_credits_granted") or 0))

    print(f"\n  PAID BUT NOT GRANTED : {len(paid_not_granted)}"
          "   <- a customer paid and has nothing")
    for p in paid_not_granted:
        print(f"      {p['id']}  {p['amount']/100:.2f} {p['currency']}  "
              f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(p['created']))}")

    print(f"\n  GRANTED BUT NOT PAID : {len(granted_not_paid)}"
          "   <- should be impossible")
    for r in granted_not_paid:
        print(f"      {r['payment_intent_id']}  {credits(r)} credits  user={r['user_id']}")

    print(f"\n  AMOUNT MISMATCH      : {len(mismatched)}")
    for p, r in mismatched:
        print(f"      {p['id']}  stripe={p['amount']}  ours={r.get('amount_total')}")

    # PACK DRIFT. Re-derive what today's table would grant and compare. Old
    # packs legitimately differ, so this is reported and not counted as a
    # discrepancy -- it is a prompt to check that the difference was intended.
    # PARSED, not imported. `from payments_api.main import _credits_for` pulls
    # in stripe and fastapi, which are installed in that service's image and not
    # in a shell running a reconciliation script -- so the import fails, the
    # except swallows it, and the whole check silently does not run. Which is
    # what happened the first time this was written.
    import re as _re
    _src = (REPO / "payments_api" / "main.py").read_text()
    _packs = {}
    _m = _re.search(r"PACKS[^=]*= \{(.*?)\n\}", _src, _re.S)
    _n = _re.search(r"^PACK_CREDITS = (\d+)", _src, _re.M)
    if _m and _n:
        for cur_, amt_, val_ in _re.findall(
                r'\("(\w+)",\s*(\d+)\):\s*(PACK_CREDITS|\d+)', _m.group(1)):
            _packs[(cur_, int(amt_))] = int(_n.group(1)) if val_ == "PACK_CREDITS" else int(val_)
    if not _packs:
        print("\n  PACK DRIFT           : could not read PACKS from payments_api/main.py")

    def _credits_for(currency, amount):
        if amount is None or not currency:
            return None
        return _packs.get((str(currency).lower(), int(amount)))

    if _packs:
        drift = []
        for pid, r in by_pi.items():
            want = _credits_for(r.get("currency"), r.get("amount_total"))
            got = credits(r)
            if want is not None and got and want != got:
                drift.append((pid, got, want))
        print(f"\n  PACK DRIFT           : {len(drift)}"
              "   (informational -- older packs legitimately differ)")
        for pid, got, want in drift:
            print(f"      {pid}  granted={got}  today's table would grant={want}")

    total_cents = sum(p["amount"] for p in pis)
    total_credits = sum(credits(r) for r in by_pi.values())
    print(f"\n  collected : {total_cents/100:.2f}")
    print(f"  granted   : {total_credits} credits")
    # Not an assertion -- pack sizes change over time, so this is a sanity
    # reading, not an invariant. A number wildly off is worth a look.
    if total_credits:
        print(f"  implied   : {total_cents/100/total_credits:.2f} per credit")

    bad = len(paid_not_granted) + len(granted_not_paid) + len(mismatched)
    print("\n" + "=" * 72)
    print("  CLEAN" if not bad else f"  {bad} DISCREPANC{'Y' if bad == 1 else 'IES'} -- investigate")
    print("=" * 72)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
