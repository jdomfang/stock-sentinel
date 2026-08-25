#!/usr/bin/env python3
"""The webhook that grants credits against real money.

WHY THESE PARTICULAR THINGS

Every defect pinned here is invisible to a card test. `4242 4242 4242 4242`
settles synchronously and arrives with payment_status='paid' on
checkout.session.completed, so a single card purchase exercises exactly one of
the branches below and reports success either way:

  unpaid      checkout.session.completed fires when the customer FINISHES the
              flow, not when the money moves. An async method (SEPA, Bacs,
              boleto, OXXO) is 'unpaid' at that moment and settles later -- or
              fails. Granting there gives credits away for nothing.
  async       ...and gating on payment_status without handling the settlement
              event is the mirror bug: a customer who really paid, waiting
              forever. Neither half is optional.
  two events  one purchase, two event ids -- an event-keyed guard grants twice.
  quantity    credits came from metadata, defaulting to 1. Metadata is free text
              on a session anyone with dashboard access can create.
  revocation  read `event_id` that was only ever assigned in the other branch.

No network, no Stripe, no database. stripe and supabase are stubbed.

Usage:
    python3 tests/test_payments.py
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


# --------------------------------------------------------------- the harness

class Unique(Exception):
    """What PostgREST raises on a unique violation."""
    code = "23505"


class FakeDB:
    """Just enough Supabase to see what the webhook decided."""

    def __init__(self) -> None:
        self.guard: set[str] = set()      # stripe_events_processed.event_id
        self.purchases: list[dict] = []
        self.grants: list[dict] = []      # grant_credits calls
        self.grant_raises: Exception | None = None
        self.guard_release_raises = False
        self.no_credits_granted_column = False

    # -- table(...) chain
    def table(self, name):
        return _Tbl(self, name)

    def rpc(self, fn, params):
        # Returns a BUILDER: production calls sb.rpc(...).execute(), and a fake
        # that skips the .execute() step tests a call chain nobody makes.
        if fn != "grant_credits":
            raise AssertionError(f"unexpected rpc {fn}")

        def _run():
            if self.grant_raises:
                raise self.grant_raises
            self.grants.append(dict(params))
            return _Res({"ok": True, "applied_scan": params["p_scan_delta"],
                         "applied_deep": params["p_deep_delta"],
                         "scan_credits": 9, "deep_credits": 9, "event_id": "e"})
        return types.SimpleNamespace(execute=_run)


class _Res:
    def __init__(self, data): self.data = data


class _Tbl:
    def __init__(self, db, name):
        self.db, self.name, self._filters, self._op = db, name, {}, None

    def insert(self, row):
        self._op, self._row = "insert", row
        return self

    def select(self, *a, **k): self._op = "select"; return self
    def delete(self): self._op = "delete"; return self
    def limit(self, n): return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        if self._op == "insert" and self.name == "stripe_events_processed":
            key = self._row["event_id"]
            if key in self.db.guard:
                raise Unique("duplicate key value violates unique constraint")
            self.db.guard.add(key)
            return _Res([self._row])
        if self._op == "insert" and self.name == "purchases":
            # Simulate the deploy window in which the merge migration has not
            # been applied yet and purchases.credits_granted does not exist.
            if self.db.no_credits_granted_column and "credits_granted" in self._row:
                raise RuntimeError(
                    "PGRST204 Could not find the 'credits_granted' column of "
                    "'purchases' in the schema cache")
            self.db.purchases.append(self._row)
            return _Res([self._row])
        if self._op == "delete" and self.name == "stripe_events_processed":
            if self.db.guard_release_raises:
                raise RuntimeError("guard release failed")
            self.db.guard.discard(self._filters.get("event_id"))
            return _Res([])
        if self._op == "select" and self.name == "purchases":
            pi = self._filters.get("payment_intent_id")
            return _Res([p for p in self.db.purchases if p.get("payment_intent_id") == pi])
        return _Res([])


def load(db: FakeDB | None = None):
    """payments_api.main with stripe and Supabase stubbed. Returns (client, M, db)."""
    import importlib
    import os

    db = db or FakeDB()

    st = types.ModuleType("stripe")
    st.api_key = None

    class _Sess:
        @staticmethod
        def create(**kw):
            _Sess.last = kw
            return types.SimpleNamespace(url="https://checkout.test/x", id="cs_test_1")
    st.checkout = types.SimpleNamespace(Session=_Sess)

    class _PI:
        registry: dict = {}
        @staticmethod
        def retrieve(pid): return _PI.registry[pid]
    st.PaymentIntent = _PI

    class _WH:
        @staticmethod
        def construct_event(payload, sig_header, secret):
            if sig_header != "good":
                raise ValueError("bad signature")
            return json.loads(payload)
    st.Webhook = _WH
    sys.modules["stripe"] = st

    for k, v in {"APP_BASE_URL": "https://app.test",
                 "PAYMENTS_API_SHARED_SECRET": "s3cret",
                 "STRIPE_SECRET_KEY": "sk_test_x",
                 "STRIPE_WEBHOOK_SECRET": "whsec_x",
                 "SUPABASE_URL": "https://db.test",
                 "SUPABASE_SERVICE_ROLE_KEY": "k"}.items():
        os.environ[k] = v

    import payments_api.main as M
    M = importlib.reload(M)
    M._supabase_admin_client = lambda: db
    from fastapi.testclient import TestClient
    return TestClient(M.app, raise_server_exceptions=False), M, db


def session_event(*, etype="checkout.session.completed", eid="evt_1",
                  sid="cs_1", user="u1", status="paid", amount=500,
                  currency="usd", meta=None):
    m = {"user_id": user} if meta is None else meta
    return {"id": eid, "type": etype, "data": {"object": {
        "id": sid, "metadata": m, "payment_status": status,
        "amount_total": amount, "currency": currency,
        "payment_intent": "pi_1", "status": "complete"}}}


def post(client, event):
    return client.post("/stripe/webhook", content=json.dumps(event),
                       headers={"stripe-signature": "good"})


# ------------------------------------------------- money moved, or it did not

def test_an_unsettled_checkout_grants_nothing():
    """checkout.session.completed does NOT mean paid."""
    print("\ncompleted-but-unpaid must grant nothing, and must not be retried")
    for status in ("unpaid", "", "processing"):
        client, M, db = load()
        r = post(client, session_event(status=status))
        check(f"payment_status={status or 'missing'}: no credits granted",
              db.grants == [], str(db.grants))
        check(f"payment_status={status or 'missing'}: acknowledged, not retried",
              r.status_code == 200, str(r.status_code))
        check(f"payment_status={status or 'missing'}: no guard row burned",
              db.guard == set(), str(db.guard))


def test_the_settlement_event_is_what_grants():
    """The other half. Gating without this strands a customer who really paid."""
    print("\nasync settlement must grant, or the gate above is just a new bug")
    client, M, db = load()
    post(client, session_event(status="unpaid"))
    check("still nothing after completed", db.grants == [])
    r = post(client, session_event(etype="checkout.session.async_payment_succeeded",
                                   eid="evt_2", status="paid"))
    check("settling grants once", len(db.grants) == 1, str(db.grants))
    check("...the right amount",
          db.grants and (db.grants[0]["p_scan_delta"], db.grants[0]["p_deep_delta"])
          == (M.PACK_CREDITS, 0), str(db.grants))
    check("...and returns 200", r.status_code == 200, str(r.status_code))


def test_one_purchase_two_events_grants_once():
    """The guard is keyed on the SESSION, because the event id is not the purchase."""
    print("\ntwo event ids, one payment: exactly one grant")
    client, M, db = load()
    post(client, session_event(eid="evt_1", sid="cs_9", status="paid"))
    post(client, session_event(etype="checkout.session.async_payment_succeeded",
                               eid="evt_2", sid="cs_9", status="paid"))
    check("granted exactly once", len(db.grants) == 1, str(len(db.grants)))
    check("a plain retry of the same event is also ignored",
          (post(client, session_event(eid="evt_1", sid="cs_9")).status_code == 200
           and len(db.grants) == 1), str(len(db.grants)))


# --------------------------------------------------- quantity comes from money

def test_metadata_cannot_decide_how_many_credits():
    print("\nthe amount paid decides, not a free-text field")
    client, M, db = load()
    post(client, session_event(meta={"user_id": "u1", "scan_credits": "999",
                                     "deep_credits": "999"}, amount=500))
    check("a $5 payment grants one pack however metadata is labelled",
          db.grants and db.grants[0]["p_scan_delta"] == M.PACK_CREDITS,
          str(db.grants))


def test_an_unrecognised_amount_is_refused_not_guessed():
    print("\nan amount we cannot price grants nothing and stays retryable")
    for amount, currency, label in ((100, "usd", "$1"), (50000, "usd", "$500"),
                                    (500, "eur", "€5"), (None, "usd", "no amount")):
        client, M, db = load()
        r = post(client, session_event(amount=amount, currency=currency))
        check(f"{label}: nothing granted", db.grants == [], str(db.grants))
        check(f"{label}: 500 so Stripe retries", r.status_code == 500, str(r.status_code))
        check(f"{label}: no guard row, so a retry can still work",
              db.guard == set(), str(db.guard))


def test_a_paid_session_with_no_user_is_refused_loudly():
    print("\nwe know money arrived but not whose it is")
    client, M, db = load()
    r = post(client, session_event(meta={}))
    check("nothing granted", db.grants == [])
    check("500 so it is visible in Stripe and retryable", r.status_code == 500,
          str(r.status_code))
    check("no guard row written", db.guard == set(), str(db.guard))


def test_a_failed_grant_releases_the_guard():
    print("\na paid customer must not be stranded by our own failure")
    client, M, db = load()
    db.grant_raises = RuntimeError("supabase down")
    r = post(client, session_event())
    check("guard released so Stripe's retry can work", db.guard == set(), str(db.guard))
    check("500, not a quiet 200", r.status_code == 500, str(r.status_code))
    check("no purchase recorded for a grant that did not happen",
          db.purchases == [], str(db.purchases))


def test_the_purchase_row_records_what_was_granted():
    print("\nthe audit row is what a revocation later reads")
    client, M, db = load()
    post(client, session_event())
    check("one purchase row", len(db.purchases) == 1, str(db.purchases))
    p = db.purchases[0] if db.purchases else {}
    check("...with the granted credits, not the metadata",
          p.get("credits_granted") == M.PACK_CREDITS, str(p))
    check("...and nothing in the retired columns",
          (p.get("scan_credits_granted"), p.get("deep_credits_granted")) == (0, 0),
          "a new purchase has nothing to say in the old denomination")
    check("...and the payment_intent a refund arrives on",
          p.get("payment_intent_id") == "pi_1", str(p))


# ------------------------------------------------------------------ refunds

def charge_event(*, etype="charge.refunded", eid="evt_r", pi="pi_1",
                 amount=500, refunded=500):
    return {"id": eid, "type": etype, "data": {"object": {
        "payment_intent": pi, "amount": amount, "amount_refunded": refunded}}}


def test_a_refund_revokes_exactly_what_was_granted():
    print("\nundo the grant, not a guess about it")
    client, M, db = load()
    post(client, session_event())
    db.grants.clear()
    r = post(client, charge_event())
    check("revoked", len(db.grants) == 1, str(db.grants))
    check("...the exact quantity from the purchases row",
          db.grants and (db.grants[0]["p_scan_delta"], db.grants[0]["p_deep_delta"])
          == (-M.PACK_CREDITS, 0), str(db.grants))
    check("200", r.status_code == 200, str(r.status_code))


def test_a_purchase_survives_the_deploy_window():
    """The merge migration is applied BY HAND while this service deploys on a
    git push, so there is a window where purchases.credits_granted does not
    exist yet.

    Losing the audit row in that window is not cosmetic. It is the row a
    chargeback reads to size a revocation; without it the revocation derives
    from the amount instead, which is correct only by coincidence and stops
    being correct the moment the pack size changes.
    """
    print("\na purchase must still be recorded before the migration lands")
    client, M, db = load()
    db.no_credits_granted_column = True
    r = post(client, session_event())

    check("the grant still happens", len(db.grants) == 1, str(db.grants))
    check("...and returns 200", r.status_code == 200, str(r.status_code))
    check("the purchase row is NOT lost", len(db.purchases) == 1, str(db.purchases))
    p = db.purchases[0] if db.purchases else {}
    check("...recorded in the columns that DO exist",
          p.get("scan_credits_granted") == M.PACK_CREDITS, str(p))
    check("...so the migration's backfill sums it to the right number",
          (int(p.get("scan_credits_granted") or 0)
           + int(p.get("deep_credits_granted") or 0)) == M.PACK_CREDITS, str(p))
    check("...and it carries the payment_intent a chargeback arrives on",
          p.get("payment_intent_id") == "pi_1", str(p))


def test_a_pre_merge_purchase_revokes_what_it_actually_granted():
    """The row shapes on either side of the merge are different, and a
    revocation must read both.

    A purchase made before the merge recorded scan_credits_granted=1,
    deep_credits_granted=1 and has no credits_granted. Revoking today's pack
    size against it would take back more than it ever gave -- and that is
    invisible afterwards, because grant_credits clamps at zero and records only
    what it applied, so the over-revocation quietly empties a DIFFERENT,
    unrefunded purchase and the ledger still reconciles.
    """
    print("\na chargeback on a purchase older than the merge")
    client, M, db = load()
    db.purchases.append({                       # the old shape, verbatim
        "user_id": "u_old", "payment_intent_id": "pi_old",
        "credits_granted": 0,
        "scan_credits_granted": 1, "deep_credits_granted": 1,
        "amount_total": 500, "currency": "usd"})
    post(client, charge_event(pi="pi_old"))
    check("revokes 2 -- what that purchase granted",
          db.grants and db.grants[0]["p_scan_delta"] == -2, str(db.grants))
    check("...for the right user",
          db.grants and db.grants[0]["p_user_id"] == "u_old", str(db.grants))


def test_a_dispute_after_a_refund_does_not_revoke_twice():
    print("\none payment, two revocation events")
    client, M, db = load()
    post(client, session_event())
    db.grants.clear()
    post(client, charge_event(etype="charge.refunded", eid="evt_r1"))
    post(client, charge_event(etype="charge.dispute.created", eid="evt_r2"))
    check("revoked once for one payment", len(db.grants) == 1, str(db.grants))


def test_a_refund_without_a_payment_intent_does_not_crash():
    """`event_id` was assigned only in the checkout branch; this branch read it."""
    print("\nthe UnboundLocalError path")
    client, M, db = load()
    r = post(client, charge_event(pi=None))
    check("no 500 from a NameError", r.status_code == 200, str(r.status_code))
    check("and nothing revoked, since we cannot tell whose it was",
          db.grants == [], str(db.grants))


def test_a_refund_falls_back_to_stripe_when_we_have_no_record():
    print("\nno purchases row: who from metadata, how much from the amount")
    client, M, db = load()
    import stripe
    stripe.PaymentIntent.registry["pi_7"] = {
        "metadata": {"user_id": "u9", "scan_credits": "999"},
        "amount": 500, "currency": "usd"}
    post(client, charge_event(pi="pi_7"))
    check("revoked for the right user",
          db.grants and db.grants[0]["p_user_id"] == "u9", str(db.grants))
    check("...deriving quantity from the amount, ignoring metadata's 999",
          db.grants and db.grants[0]["p_scan_delta"] == -M.PACK_CREDITS, str(db.grants))

    client, M, db = load()
    stripe.PaymentIntent.registry["pi_8"] = {
        "metadata": {"user_id": "u9"}, "amount": 12345, "currency": "usd"}
    post(client, charge_event(pi="pi_8"))
    check("an unpriceable refund revokes nothing rather than guessing",
          db.grants == [], str(db.grants))


# --------------------------------------------------- the idempotency guard

def test_only_a_real_unique_violation_means_already_processed():
    """Reading 'already done' out of an unparsed error skips a paid grant."""
    print("\nthe guard must not mistake an outage for a duplicate")
    client, M, db = load()

    class Coded(Exception):
        code = "08006"          # connection failure

    def boom(*a, **k):
        raise Coded("could not connect; unique constraint mentioned in passing")
    db.table = lambda n: types.SimpleNamespace(
        insert=lambda r: types.SimpleNamespace(execute=boom))
    raised = False
    try:
        M._mark_stripe_event_processed(event_id="e1", user_id="u1", session_id="s")
    except Coded:
        raised = True
    check("a non-23505 error propagates, so Stripe retries", raised)


def test_the_guard_reports_a_duplicate():
    print("\n...and a real one is reported, not raised")
    client, M, db = load()
    check("first insert is new",
          M._mark_stripe_event_processed(event_id="e1", user_id="u1", session_id="s") is True)
    check("second is a duplicate",
          M._mark_stripe_event_processed(event_id="e1", user_id="u1", session_id="s") is False)


def test_the_pack_size_is_what_we_think_it_is():
    """A LITERAL 2, deliberately.

    Every other assertion in this file reads M.PACK_CREDITS on both sides, so
    they all keep passing if the constant changes -- which is the assertion
    moving with the code rather than pinning it. Found by mutation testing:
    changing the pack to 10 broke nothing here.

    The number matters beyond bookkeeping. X bills $0.005 per post read, a scan
    buys up to 300 posts and a deep analysis up to ~400, so a $5 pack of 2
    costs at most ~$4.00 to serve. At 10 the same $5 pack costs up to $20 --
    a loss on essentially every sale. If this test fails, someone changed the
    price; make sure that was on purpose.
    """
    print("\n$5 buys 2 credits, and that is a business decision not a detail")
    client, M, db = load()
    check("PACK_CREDITS is 2", M.PACK_CREDITS == 2, str(M.PACK_CREDITS))
    check("the $5 pack grants 2", M._credits_for("usd", 500) == 2,
          str(M._credits_for("usd", 500)))
    post(client, session_event())
    check("a real $5 payment moves the balance by exactly 2",
          db.grants and db.grants[0]["p_scan_delta"] == 2, str(db.grants))
    check("...and the purchase row says 2",
          db.purchases and db.purchases[0].get("credits_granted") == 2,
          str(db.purchases))


def test_credits_for_refuses_what_it_does_not_know():
    print("\nthe pack table refuses rather than defaults")
    client, M, db = load()
    check("the one pack", M._credits_for("usd", 500) == M.PACK_CREDITS,
          str(M._credits_for("usd", 500)))
    check("case-insensitive currency", M._credits_for("USD", 500) == M.PACK_CREDITS)
    check("unknown amount -> None", M._credits_for("usd", 501) is None)
    check("unknown currency -> None", M._credits_for("gbp", 500) is None)
    check("no amount -> None", M._credits_for("usd", None) is None)
    check("no currency -> None", M._credits_for(None, 500) is None)


def test_the_session_is_priced_from_the_same_table():
    print("\nthe price charged and the credits granted cannot drift")
    client, M, db = load()
    r = client.post("/create-checkout-session", json={"user_id": "u1"},
                    headers={"X-Payments-Shared-Secret": "s3cret"})
    check("session created", r.status_code == 200, r.text[:120])
    import stripe
    kw = stripe.checkout.Session.last
    item = kw["line_items"][0]["price_data"]
    check("charged the amount the pack table prices",
          (item["currency"], item["unit_amount"]) in M.PACKS, str(item))
    check("the redirect carries the marker app.py reads",
          kw["success_url"].endswith("/?payment=success"), kw["success_url"])


def test_an_unsigned_webhook_is_rejected():
    print("\nsignature is the only thing making any of this trustworthy")
    client, M, db = load()
    r = client.post("/stripe/webhook", content="{}",
                    headers={"stripe-signature": "forged"})
    check("bad signature -> 400", r.status_code == 400, str(r.status_code))
    r = client.post("/stripe/webhook", content="{}")
    check("no signature -> 400", r.status_code == 400, str(r.status_code))
    check("nothing granted either way", db.grants == [], str(db.grants))


def main() -> int:
    for name, fn in [(k, v) for k, v in sorted(globals().items())
                     if k.startswith("test_")]:
        # A test that RAISES is a failure, not the end of the run. Found while
        # mutation-testing: breaking the duplicate guard made the guard raise
        # instead of returning False, which killed the runner mid-suite and
        # took every later test with it -- the mutant was caught, but silently.
        try:
            fn()
        except Exception as e:
            check(f"{name} raised", False, f"{type(e).__name__}: {e}")
    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
