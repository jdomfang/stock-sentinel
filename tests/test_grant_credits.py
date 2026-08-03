#!/usr/bin/env python3
"""Prove the money-IN path is atomic, idempotent, and locked down.

payments_api._apply_credit_delta was a select-then-update against
public.profiles -- verbatim the race that 20260801020000 removed from
consume_credit, left in place on the one path where the value being written was
paid for. Two grants racing both read N and both write N+1: one paid grant lost,
while BOTH audit rows were written, permanently breaking balance = -sum(cost).

It also had no idempotency guard on revocations, so a disputed charge -- which
emits charge.dispute.created AND charge.refunded, two distinct Stripe event ids
for one payment -- revoked twice. Clamped at zero, the second revocation
silently ate credits bought in a different, unrefunded transaction.

None of this was reachable: Stripe is in test mode and zero purchases and zero
webhook events have ever been processed. It is fixed and tested now because the
target architecture keeps payments as the existing Railway service through every
phase, so there is no later migration that would have cleaned it up.

ALSO CLOSES A GAP IN THE EXISTING SUITES. An independent audit mutation-tested
tests/test_credit_integrity.py by deleting the REVOKE/GRANT block from
20260801020000 -- leaving refund_credit, which MINTS credits, callable by every
logged-in user at Postgres's EXECUTE-TO-PUBLIC default -- and the suite stayed
green at 47/47. Nothing anywhere asserted EXECUTE on consume_credit or
refund_credit. That is not hypothetical: migration 3 shipped to production with
exactly that block accidentally omitted. test_execute_is_locked_down below
covers all four credit functions.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_grant_credits.py
"""

from __future__ import annotations

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required:  pip install psycopg2-binary")
    sys.exit(2)

DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"
REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = [
    REPO / "tests/sql/00_supabase_stub.sql",
    # The FULL production chain, in production order. Applying a subset used to
    # be fine, but 20260802010000 extracts consume_credit from 20260801060000 --
    # so it references public.work_runs, and a suite that skipped that migration
    # installed a function pointing at a table it had never created. An audit had
    # already flagged fixture-vs-production divergence as the thing that makes a
    # green suite meaningless; this keeps them identical by construction.
    REPO / "supabase/migrations/20260801010000_purchases.sql",
    REPO / "supabase/migrations/20260801020000_credit_integrity.sql",
    REPO / "supabase/migrations/20260801030000_admin_adjust_credits.sql",
    REPO / "supabase/migrations/20260801050000_grant_credits.sql",
    REPO / "supabase/migrations/20260801060000_work_runs.sql",
    REPO / "supabase/migrations/20260802010000_caller_identity.sql",
]

SIGS = {
    "consume_credit": "public.consume_credit(uuid,text,jsonb,text)",
    "refund_credit": "public.refund_credit(uuid,text,uuid,text)",
    "admin_adjust_credits": "public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)",
    "grant_credits": "public.grant_credits(uuid,integer,integer,text,text)",
}

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def rpc(cur, fn: str, *args):
    cur.execute(f"select public.{fn}(" + ",".join(["%s"] * len(args)) + ")", args)
    return cur.fetchone()[0]


def seed(cur, scan=0, deep=0) -> str:
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id,email) values (%s,%s)", (uid, f"{uid[:8]}@t.test"))
    cur.execute(
        "insert into public.profiles (user_id,email,role,disabled,scan_credits,deep_credits)"
        " values (%s,%s,'user',false,%s,%s)", (uid, f"{uid[:8]}@t.test", scan, deep))
    return uid


def balance(cur, uid):
    cur.execute("select scan_credits,deep_credits from public.profiles where user_id=%s", (uid,))
    return cur.fetchone()


def ledger(cur, uid):
    cur.execute(
        "select coalesce(sum(cost_scan_credits),0), coalesce(sum(cost_deep_credits),0),"
        " count(*) from public.usage_events where user_id=%s", (uid,))
    return cur.fetchone()


def rebuild(cur) -> None:
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    for p in MIGRATIONS:
        cur.execute(p.read_text())


# ── the actual bug ───────────────────────────────────────────────────────────

def test_concurrent_grants_do_not_lose_one(cur):
    print("\nrace: two paid grants at once must both land")
    uid = seed(cur, 0, 0)

    def grant(i):
        c = conn()
        try:
            with c.cursor() as k:
                return rpc(k, "grant_credits", uid, 1, 1, "checkout.session.completed", f"evt_{i}")
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(grant, range(8)))

    applied = sum(r.get("applied_scan", 0) for r in results)
    s, d = balance(cur, uid)
    ls, ld, n = ledger(cur, uid)

    check("all 8 grants reported ok", all(r.get("ok") for r in results))
    check(f"balance is 8/8, not fewer (got {s}/{d})", (s, d) == (8, 8),
          "a paid grant was lost to the read-modify-write race")
    check(f"applied deltas sum to 8 (got {applied})", applied == 8)
    check(f"ledger has exactly 8 rows (got {n})", n == 8)
    check(f"balance == -sum(cost): {s}/{d} vs {-ls}/{-ld}", (s, d) == (-ls, -ld),
          "ledger and balance disagree -- the invariant is broken")


def test_grant_racing_a_debit(cur):
    print("\nrace: a grant landing while the user spends must not overwrite it")
    # Seed 0/0 and establish the starting credit THROUGH the ledger. Seeding
    # profiles.scan_credits directly creates an unledgered opening balance, and
    # balance = -sum(cost) then cannot hold by construction -- the discrepancy is
    # exactly the seeded amount. That is the same flaw an audit found across the
    # existing suites, and the reason 20260801040000 had to backfill production.
    uid = seed(cur, 0, 0)
    rpc(cur, "grant_credits", uid, 1, 1, "opening", "seed_race")

    def do_grant():
        c = conn()
        try:
            with c.cursor() as k:
                return rpc(k, "grant_credits", uid, 5, 5, "purchase", "evt_race")
        finally:
            c.close()

    def do_spend():
        c = conn()
        try:
            with c.cursor() as k:
                return rpc(k, "consume_credit", uid, "scan", "{}", str(uuid.uuid4()))
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1, f2 = ex.submit(do_grant), ex.submit(do_spend)
        f1.result(); f2.result()

    s, d = balance(cur, uid)
    ls, ld, _ = ledger(cur, uid)
    # 1 + 5 - 1 = 5 either ordering. The old code could return 6 (debit lost).
    check(f"scan balance is 5, not 6 (got {s})", s == 5,
          "the spent credit reappeared -- the grant overwrote the debit")
    check(f"balance == -sum(cost): {s}/{d} vs {-ls}/{-ld}", (s, d) == (-ls, -ld))


# ── idempotency ──────────────────────────────────────────────────────────────

def test_replayed_webhook_grants_once(cur):
    print("\nidempotency: Stripe retries for ~3 days; a replay must grant nothing")
    uid = seed(cur, 0, 0)
    a = rpc(cur, "grant_credits", uid, 1, 1, "checkout.session.completed", "evt_x")
    b = rpc(cur, "grant_credits", uid, 1, 1, "checkout.session.completed", "evt_x")
    check("first grant applies", a.get("applied_scan") == 1)
    check("replay reports duplicate_request", b.get("reason") == "duplicate_request")
    check("replay applies nothing", b.get("applied_scan") == 0)
    check("replay returns the ORIGINAL event id", a.get("event_id") == b.get("event_id"),
          "a null or new id here would break refund tracing")
    check("balance moved once", balance(cur, uid) == (1, 1))
    check("one ledger row", ledger(cur, uid)[2] == 1)


def test_dispute_after_refund_revokes_once(cur):
    print("\nidempotency: dispute + refund for ONE payment must revoke once")
    uid = seed(cur, 0, 0)
    rpc(cur, "grant_credits", uid, 5, 5, "opening", "seed_dispute")  # ledgered
    # payments_api keys both on revoke:<payment_intent>, not the event id.
    r1 = rpc(cur, "grant_credits", uid, -1, -1, "charge.refunded", "revoke:pi_9")
    r2 = rpc(cur, "grant_credits", uid, -1, -1, "charge.dispute.created", "revoke:pi_9")
    check("first revocation applies", r1.get("applied_scan") == -1)
    check("second is a duplicate", r2.get("reason") == "duplicate_request")
    check("balance revoked once, not twice", balance(cur, uid) == (4, 4),
          "double revocation eats credits from an unrelated purchase")


# ── clamping and validation ──────────────────────────────────────────────────

def test_clamp_records_what_was_applied(cur):
    print("\nclamp: a revocation cannot go negative, and the ledger records reality")
    uid = seed(cur, 0, 0)
    rpc(cur, "grant_credits", uid, 1, 1, "opening", "seed_clamp")  # ledgered, see above
    r = rpc(cur, "grant_credits", uid, -5, -5, "charge.refunded", "revoke:pi_big")
    check("clamped flag is set", r.get("clamped") is True)
    check("applied is -1, not the requested -5", r.get("applied_scan") == -1,
          "recording the REQUESTED delta would put a number in the ledger the balance never moved by")
    check("balance floors at 0", balance(cur, uid) == (0, 0))
    ls, ld, _ = ledger(cur, uid)
    check(f"balance == -sum(cost) after clamping: 0/0 vs {-ls}/{-ld}", (0, 0) == (-ls, -ld))


def test_validation(cur):
    print("\nvalidation: refuse rather than guess on a money path")
    uid = seed(cur, 0, 0)
    rpc(cur, "grant_credits", uid, 1, 1, "opening", "seed_valid")
    check("missing request_id refused",
          rpc(cur, "grant_credits", uid, 1, 1, "x", None).get("reason") == "missing_request_id",
          "without a key every Stripe retry would grant again")
    check("blank request_id refused",
          rpc(cur, "grant_credits", uid, 1, 1, "x", "   ").get("reason") == "missing_request_id")
    check("null delta refused",
          rpc(cur, "grant_credits", uid, None, 1, "x", "k1").get("reason") == "invalid_delta")
    check("unknown profile refused",
          rpc(cur, "grant_credits", str(uuid.uuid4()), 1, 1, "x", "k2").get("reason")
          == "profile_not_found")
    check("balance untouched by every refusal", balance(cur, uid) == (1, 1))


def test_grant_is_distinguishable_from_a_debit(cur):
    print("\nledger: a grant must not look like a scan debit")
    uid = seed(cur, 0, 0)
    ev = rpc(cur, "grant_credits", uid, 1, 1, "checkout.session.completed", "evt_t")["event_id"]
    cur.execute("select event_type from public.usage_events where id=%s", (ev,))
    check("event_type is 'purchase', not 'scan'", cur.fetchone()[0] == "purchase",
          "a grant written as a scan debit could be 'refunded' into minted credits")
    r = rpc(cur, "refund_credit", uid, "scan", ev, "attempt to refund a purchase")
    check("refund_credit refuses to refund it",
          r.get("reason") in ("original_event_mismatch", "original_event_not_a_debit"),
          str(r))


# ── the gap the audit found in the OTHER suites ──────────────────────────────

def test_execute_is_locked_down(cur):
    print("\nauthz: EXECUTE on every credit function (the assertion that was missing)")
    for name, sig in SIGS.items():
        for role in ("anon", "authenticated"):
            cur.execute("select has_function_privilege(%s,%s,'EXECUTE')", (role, sig))
            check(f"{role} cannot execute {name}", cur.fetchone()[0] is False,
                  "these functions take the target user as an ARGUMENT -- "
                  "EXECUTE is the only thing stopping self-service credits")
        cur.execute("select has_function_privilege('service_role',%s,'EXECUTE')", (sig,))
        check(f"service_role CAN execute {name}", cur.fetchone()[0] is True,
              "the app cannot work without this")


def main() -> int:
    print("=" * 74)
    print("  grant_credits -- atomic, idempotent money-in; plus EXECUTE lockdown")
    print("=" * 74)
    c = conn()
    with c.cursor() as cur:
        rebuild(cur)
        for t in (test_concurrent_grants_do_not_lose_one,
                  test_grant_racing_a_debit,
                  test_replayed_webhook_grants_once,
                  test_dispute_after_refund_revokes_once,
                  test_clamp_records_what_was_applied,
                  test_validation,
                  test_grant_is_distinguishable_from_a_debit,
                  test_execute_is_locked_down):
            try:
                t(cur)
            except Exception as e:
                FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    c.close()

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\n  DO NOT APPLY THE MIGRATION / DEPLOY payments_api:")
        for n, d in FAILED:
            print(f"    - {n}: {d}")
    else:
        print("\n  All assertions held.")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
