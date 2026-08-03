#!/usr/bin/env python3
"""Execute the Phase 0 credit-integrity migration and prove it under concurrency.

Three rounds of static review found real defects, two of which were reachable
ONLY through specific concurrent interleavings:

  * the exception block in consume_credit did not enclose the UPDATE, so a
    duplicate request committed a debit with no ledger row (a credit leak);
  * the duplicate handler keyed on the wrong value, returning ok=true with a
    null event_id -- a second action delivered against a single debit.

Neither is visible by reading. Both fail LOUDLY here.

Usage:
    docker compose -f docker-compose.test.yml up -d
    ./.venv/bin/python tests/test_credit_integrity.py

Exit code 0 = every assertion held. Nonzero = do not apply the migration.
"""

from __future__ import annotations

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 missing.  ./.venv/bin/pip install psycopg2-binary")

REPO = Path(__file__).resolve().parents[1]
DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"

STUB = REPO / "tests" / "sql" / "00_supabase_stub.sql"
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

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def run_sql(cur, path: Path) -> None:
    cur.execute(path.read_text())


def rpc(cur, fn: str, *args):
    """Call a migration function the way PostgREST does: one statement, autocommit."""
    cur.execute(f"select public.{fn}(" + ",".join(["%s"] * len(args)) + ")", args)
    return cur.fetchone()[0]


def seed_user(cur, scan=1, deep=1, disabled=False, admin=False) -> str:
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@t.test"))
    cur.execute(
        "insert into public.profiles (user_id, email, role, disabled, scan_credits, deep_credits)"
        " values (%s,%s,%s,%s,%s,%s)",
        (uid, f"{uid[:8]}@t.test", "admin" if admin else "user", disabled, scan, deep),
    )
    return uid


def balance(cur, uid, col="scan_credits") -> int:
    cur.execute(f"select {col} from public.profiles where user_id=%s", (uid,))
    return cur.fetchone()[0]


def ledger_rows(cur, uid) -> list[dict]:
    cur.execute(
        "select event_type, cost_scan_credits, cost_deep_credits, metadata"
        "  from public.usage_events where user_id=%s order by created_at",
        (uid,),
    )
    return [
        {"type": r[0], "scan": r[1], "deep": r[2], "meta": r[3]} for r in cur.fetchall()
    ]


# ─────────────────────────────────────────────────────────────────────────────
def setup() -> None:
    print("\n== setup ==")
    with conn() as c, c.cursor() as cur:
        cur.execute("drop schema if exists public cascade; create schema public;")
        cur.execute("drop schema if exists auth cascade;")
        run_sql(cur, STUB)
        print(f"  applied {STUB.name}")
        for m in MIGRATIONS:
            run_sql(cur, m)
            print(f"  applied {m.name}")


# ─────────────────────────────────────────────────────────────────────────────
def test_holes_closed() -> None:
    print("\n== RLS holes ==")
    with conn() as c, c.cursor() as cur:
        for pol in ("profiles_update_own", "usage_insert_own", "usage_insert_admin"):
            cur.execute("select 1 from pg_policies where schemaname='public' and policyname=%s", (pol,))
            check(f"policy dropped: {pol}", cur.fetchone() is None)

        uid = seed_user(cur, scan=5)

    # As an authenticated user: can they still grant themselves credits?
    c = psycopg2.connect(DSN); c.autocommit = False
    with c.cursor() as cur:
        cur.execute("set local role authenticated")
        cur.execute("set local request.jwt.claim.sub = %s", (uid,))
        cur.execute("update public.profiles set scan_credits = 9999 where user_id=%s", (uid,))
        cur.execute("select scan_credits from public.profiles where user_id=%s", (uid,))
        row = cur.fetchone()
        check("user cannot self-grant credits via RLS", row is None or row[0] != 9999,
              f"balance became {row[0] if row else '?'}")
        try:
            cur.execute(
                "insert into public.usage_events (user_id,event_type,cost_scan_credits,metadata)"
                " values (%s,'scan_refund',-99,'{\"refund_of\":\"squat\"}')", (uid,))
            forged = True
        except psycopg2.Error:
            forged = False
        check("user cannot forge ledger rows", not forged)
    c.rollback(); c.close()


def test_double_spend() -> None:
    print("\n== concurrency: double-spend ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=1)

    def attempt(_):
        cc = conn()
        try:
            with cc.cursor() as cur:
                return rpc(cur, "consume_credit", uid, "scan", "{}", None)
        finally:
            cc.close()

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(attempt, range(8)))

    ok = [r for r in results if r["ok"]]
    with conn() as c, c.cursor() as cur:
        bal = balance(cur, uid)
        rows = [r for r in ledger_rows(cur, uid) if r["type"] == "scan"]

    check("exactly one of 8 concurrent debits succeeds", len(ok) == 1, f"{len(ok)} succeeded")
    check("balance lands at 0", bal == 0, f"balance={bal}")
    check("exactly one ledger row written", len(rows) == 1, f"{len(rows)} rows")
    check("losers report no_scan_credits",
          all(r["reason"] == "no_scan_credits" for r in results if not r["ok"]),
          str({r["reason"] for r in results if not r["ok"]}))


def test_idempotent_replay() -> None:
    """The regression test for the credit leak: replay must not burn a credit."""
    print("\n== idempotency: sequential replay ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=5)
        req = f"req-{uuid.uuid4()}"

        first = rpc(cur, "consume_credit", uid, "scan", "{}", req)
        after_first = balance(cur, uid)
        second = rpc(cur, "consume_credit", uid, "scan", "{}", req)
        after_second = balance(cur, uid)
        rows = [r for r in ledger_rows(cur, uid) if r["type"] == "scan"]

    check("first call succeeds", first["ok"] and first["remaining"] == 4, str(first))
    check("REPLAY DOES NOT BURN A CREDIT", after_second == after_first,
          f"{after_first} -> {after_second}  (this is the leak)")
    check("replay reports duplicate_request", second.get("reason") == "duplicate_request", str(second))
    check("replay returns ok=true (retry is lossless)", second["ok"] is True, str(second))
    check("replay returns the ORIGINAL event_id", second.get("event_id") == first.get("event_id"),
          f"{second.get('event_id')} vs {first.get('event_id')}")
    check("replay returns a usable balance", second.get("remaining") == after_second, str(second))
    check("still exactly one ledger row", len(rows) == 1, f"{len(rows)} rows")


def test_concurrent_replay() -> None:
    print("\n== idempotency: concurrent replay ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=5)
    req = f"req-{uuid.uuid4()}"

    def attempt(_):
        cc = conn()
        try:
            with cc.cursor() as cur:
                return rpc(cur, "consume_credit", uid, "scan", "{}", req)
        except psycopg2.Error as e:
            return {"ok": False, "reason": f"RAISED:{e.pgcode}"}
        finally:
            cc.close()

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(attempt, range(6)))

    with conn() as c, c.cursor() as cur:
        bal = balance(cur, uid)
        rows = [r for r in ledger_rows(cur, uid) if r["type"] == "scan"]

    check("6 concurrent identical requests debit exactly once", bal == 4, f"balance={bal} (expected 4)")
    check("exactly one ledger row", len(rows) == 1, f"{len(rows)} rows")
    check("no caller receives a raw exception",
          not any(str(r.get("reason", "")).startswith("RAISED") for r in results),
          str([r.get("reason") for r in results]))


def test_refund_guards() -> None:
    print("\n== refund guards ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=5, deep=5)
        other = seed_user(cur, scan=5, deep=5)

        ev = rpc(cur, "consume_credit", uid, "scan", "{}", None)
        eid = ev["event_id"]

        # A purchase row: same shape as a debit except the cost is negative.
        cur.execute(
            "insert into public.usage_events (user_id,event_type,cost_scan_credits,metadata)"
            " values (%s,'purchase',-1,'{\"source\":\"stripe\"}') returning id", (uid,))
        purchase_id = cur.fetchone()[0]

        before = balance(cur, uid)
        r_purchase = rpc(cur, "refund_credit", uid, "scan", str(purchase_id), "should be refused")
        after = balance(cur, uid)
        check("CANNOT refund a purchase row (credit minting)",
              (not r_purchase["ok"]) and after == before,
              f"{r_purchase} balance {before}->{after}")
        check("  refusal is a guard, not an accident",
              r_purchase.get("reason") in ("original_event_not_a_debit", "original_event_mismatch"),
              str(r_purchase))

        # The cost guard's real target: a LEGACY grant row -- event_type='scan'
        # with a negative cost, indistinguishable from a debit by type alone.
        # This is what payments_api wrote before 'purchase' existed, and rows
        # like it are already in production.
        cur.execute(
            "insert into public.usage_events (user_id,event_type,cost_scan_credits,metadata)"
            " values (%s,'scan',-1,'{\"source\":\"stripe\"}') returning id", (uid,))
        legacy_grant = cur.fetchone()[0]
        b_pre = balance(cur, uid)
        r_legacy = rpc(cur, "refund_credit", uid, "scan", str(legacy_grant), "legacy grant")
        check("CANNOT refund a legacy negative-cost grant row",
              (not r_legacy["ok"]) and balance(cur, uid) == b_pre, str(r_legacy))
        check("  reason is original_event_not_a_debit",
              r_legacy.get("reason") == "original_event_not_a_debit", str(r_legacy))

        check("cannot refund another user's event",
              rpc(cur, "refund_credit", other, "scan", str(eid), "x").get("reason")
              == "original_event_mismatch")
        check("cannot refund with the wrong credit type",
              rpc(cur, "refund_credit", uid, "deep_analyze", str(eid), "x").get("reason")
              == "original_event_mismatch")
        check("cannot refund a nonexistent event",
              rpc(cur, "refund_credit", uid, "scan", str(uuid.uuid4()), "x").get("reason")
              == "original_event_not_found")
        check("cannot refund without an event id",
              rpc(cur, "refund_credit", uid, "scan", None, "x").get("reason")
              == "missing_original_event_id")

        b0 = balance(cur, uid)
        r1 = rpc(cur, "refund_credit", uid, "scan", str(eid), "genuine")
        b1 = balance(cur, uid)
        r2 = rpc(cur, "refund_credit", uid, "scan", str(eid), "again")
        b2 = balance(cur, uid)

        check("genuine refund restores exactly one credit", r1["ok"] and b1 == b0 + 1, f"{b0}->{b1}")
        check("second refund refused", (not r2["ok"]) and r2.get("reason") == "already_refunded", str(r2))
        check("second refund does not move the balance", b2 == b1, f"{b1}->{b2}")
        check("already_refunded returns a usable balance", r2.get("remaining") == b2, str(r2))


def test_concurrent_refund() -> None:
    print("\n== concurrency: refund ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=5)
        eid = rpc(cur, "consume_credit", uid, "scan", "{}", None)["event_id"]
        start = balance(cur, uid)

    def attempt(_):
        cc = conn()
        try:
            with cc.cursor() as cur:
                return rpc(cur, "refund_credit", uid, "scan", str(eid), "race")
        except psycopg2.Error as e:
            return {"ok": False, "reason": f"RAISED:{e.pgcode}"}
        finally:
            cc.close()

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(attempt, range(6)))

    with conn() as c, c.cursor() as cur:
        end = balance(cur, uid)
        refunds = [r for r in ledger_rows(cur, uid) if r["type"] == "scan_refund"]

    check("exactly one of 6 concurrent refunds succeeds",
          len([r for r in results if r["ok"]]) == 1,
          str([r.get("reason") for r in results]))
    check("balance restored exactly once", end == start + 1, f"{start}->{end}")
    check("exactly one refund row", len(refunds) == 1, f"{len(refunds)} rows")
    check("no caller receives a raw exception",
          not any(str(r.get("reason", "")).startswith("RAISED") for r in results),
          str([r.get("reason") for r in results]))


def test_failure_branches() -> None:
    print("\n== debit failure branches ==")
    with conn() as c, c.cursor() as cur:
        empty = seed_user(cur, scan=0)
        r = rpc(cur, "consume_credit", empty, "scan", "{}", None)
        check("zero balance -> no_scan_credits",
              (not r["ok"]) and r["reason"] == "no_scan_credits", str(r))
        check("  and reports remaining=0", r.get("remaining") == 0, str(r))

        off = seed_user(cur, scan=5, disabled=True)
        r = rpc(cur, "consume_credit", off, "scan", "{}", None)
        check("disabled account -> account_disabled",
              (not r["ok"]) and r["reason"] == "account_disabled", str(r))
        check("  disabled account is not debited", balance(cur, off) == 5)

        r = rpc(cur, "consume_credit", str(uuid.uuid4()), "scan", "{}", None)
        check("unknown user -> profile_not_found",
              (not r["ok"]) and r["reason"] == "profile_not_found", str(r))

        r = rpc(cur, "consume_credit", empty, "nonsense", "{}", None)
        check("bad event type -> invalid_event_type",
              (not r["ok"]) and r["reason"] == "invalid_event_type", str(r))

        cur.execute("select count(*) from public.usage_events where user_id=%s", (empty,))
        check("no ledger rows written on any failure branch", cur.fetchone()[0] == 0)


def test_foreign_unique_violation_not_swallowed() -> None:
    """A conflict from a DIFFERENT index must not be reported as success."""
    print("\n== unique_violation is not blanket-swallowed ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=5)
        eid = rpc(cur, "consume_credit", uid, "scan", "{}", None)["event_id"]
        rpc(cur, "refund_credit", uid, "scan", str(eid), "first")  # occupies refund_of

        before = balance(cur, uid)

    # Separate connection: the deliberate exception aborts the transaction, so
    # the follow-up read must not reuse the poisoned one.
    raised = False
    cc = conn()
    try:
        with cc.cursor() as cur2:
            # Collides with usage_events_refund_of_key, not the request-id key.
            rpc(cur2, "consume_credit", uid, "scan", f'{{"refund_of": "{eid}"}}', None)
    except psycopg2.Error:
        raised = True
    finally:
        cc.close()

    with conn() as c, c.cursor() as cur:
        after = balance(cur, uid)

    check("foreign conflict propagates instead of faking success", raised,
          "returned normally - a free action would have been granted")
    check("and the debit is rolled back", after == before, f"{before}->{after}")


def test_non_negative_floor() -> None:
    print("\n== balance floor ==")
    with conn() as c, c.cursor() as cur:
        uid = seed_user(cur, scan=0)
        try:
            cur.execute("update public.profiles set scan_credits=-1 where user_id=%s", (uid,))
            ok = False
        except psycopg2.Error:
            ok = True
        check("negative balance rejected by CHECK", ok)


def test_ownership_failure_mode() -> None:
    """Round 2's finding 6: mismatched owners = silent total outage.

    Proves the MECHANISM. Whether production actually has matching owners is
    answered by the query at the top of the migration's verify block.
    """
    print("\n== ownership / RLS exemption mechanism ==")
    with conn() as c, c.cursor() as cur:
        cur.execute("select rolname from pg_roles where rolname='low_owner'")
        if cur.fetchone() is None:
            cur.execute("create role low_owner nologin")
        cur.execute("grant usage on schema public to low_owner")
        cur.execute("""
            create or replace function public.__probe(p_user_id uuid)
            returns integer language plpgsql security definer set search_path = '' as $f$
            declare v integer;
            begin
              update public.profiles set scan_credits = scan_credits
               where user_id = p_user_id returning scan_credits into v;
              return v;
            end; $f$;""")
        uid = seed_user(cur, scan=3)

        cur.execute("alter function public.__probe(uuid) owner to postgres")
        cur.execute("select public.__probe(%s)", (uid,))
        as_owner = cur.fetchone()[0]

        cur.execute("alter function public.__probe(uuid) owner to low_owner")
        cur.execute("grant all on public.profiles to low_owner")
        cur.execute("select public.__probe(%s)", (uid,))
        as_other = cur.fetchone()[0]
        cur.execute("drop function public.__probe(uuid)")

    check("table owner sees rows through RLS", as_owner == 3, f"got {as_owner}")
    check("NON-owner is silently blocked by RLS (returns null, not an error)",
          as_other is None,
          f"got {as_other} - if this is 3, the failure mode differs from what was predicted")


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 74)
    print("Phase 0 credit-integrity migration — execution tests")
    print("=" * 74)
    try:
        setup()
    except psycopg2.OperationalError as e:
        return int(bool(sys.stderr.write(
            f"\nCannot reach Postgres on 127.0.0.1:5433.\n"
            f"  docker compose -f docker-compose.test.yml up -d\n\n{e}\n")) or 2)

    for t in (
        test_holes_closed,
        test_double_spend,
        test_idempotent_replay,
        test_concurrent_replay,
        test_refund_guards,
        test_concurrent_refund,
        test_failure_branches,
        test_foreign_unique_violation_not_swallowed,
        test_non_negative_floor,
        test_ownership_failure_mode,
    ):
        try:
            t()
        except Exception as e:  # a crashing test is a failing test
            FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\n  DO NOT APPLY THE MIGRATION:")
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    else:
        print("\n  All assertions held.")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
