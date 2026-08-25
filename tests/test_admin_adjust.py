#!/usr/bin/env python3
"""Prove admin_adjust_credits records what it changes and cannot lock the admin out.

pages/Admin.py used to set balances with a service-role UPDATE straight against
public.profiles, writing nothing to public.usage_events. On production that left
the admin account holding 9 scan / 5 deep while the ledger summed to 66 / 40 --
roughly 75/45 of credits that appeared from nowhere. With a gap like that no
reconciliation invariant can exist, so the ledger cannot be used to investigate
a suspected bug in consume_credit: "the balance is wrong" is unfalsifiable.

Two classes of failure are asserted here.

LEDGER: every credit movement writes exactly one row, with the sign convention
the rest of the ledger uses (cost = old - new), and a change that moves no
credits writes none.

LOCKOUT: the actor check requires an enabled admin, which makes demoting or
disabling yourself a one-way door -- the next call returns not_admin and the
admin page can no longer undo it. Those two moves are refused. This is the
scenario that motivated the guard, so it is tested from both sides: refused for
self, still permitted against another account.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_admin_adjust.py

Exit code 0 = every assertion held. Nonzero = do not apply the migration.
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
# Import by repo root rather than by whatever happens to be on sys.path
# when this file is invoked -- running it as a script puts tests/ first,
# running it under a runner from the repo root does not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.migrations import chain as _chain  # noqa: E402

# Discovered, not listed. Four suites each kept their own hand-typed
# chain and they had already drifted -- see tests/migrations.py for the
# failure that produces (an old function definition, asserted against,
# passing green and proving nothing).
MIGRATIONS = _chain()

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


def seed_user(cur, scan=1, deep=1, disabled=False, admin=False) -> str:
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid[:8]}@t.test"))
    # credits is the sum, and it is set EXPLICITLY -- the column carries a
    # DEFAULT of 2 (the signup grant, which is a default rather than a ledger
    # row), so omitting it would hand every seeded profile two credits nothing
    # asked for and make every absolute-set assertion below off by two.
    cur.execute(
        "insert into public.profiles (user_id, email, role, disabled, credits,"
        " scan_credits, deep_credits) values (%s, %s, %s, %s, %s, %s, %s)",
        (uid, f"{uid[:8]}@t.test", "admin" if admin else "user", disabled,
         (scan or 0) + (deep or 0), scan, deep),
    )
    return uid


def balance(cur, uid):
    cur.execute("select credits, role, disabled from public.profiles where user_id=%s", (uid,))
    return cur.fetchone()


def adjust_rows(cur, uid):
    cur.execute(
        "select cost_scan_credits, cost_deep_credits, metadata from public.usage_events"
        " where user_id=%s and event_type='adjustment' order by created_at",
        (uid,),
    )
    return cur.fetchall()


def rebuild(cur) -> None:
    cur.execute("drop schema if exists public cascade; create schema public; drop schema if exists auth cascade;")
    for p in MIGRATIONS:
        cur.execute(p.read_text())


# ── ledger ───────────────────────────────────────────────────────────────────

def test_grant_writes_negative_cost(cur):
    print("\nledger: a grant is recorded as negative cost")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=2, deep=1)

    out = rpc(cur, "admin_adjust_credits", admin, u, 18, 0, False, "user", "topped up")
    check("grant ok", out.get("ok") is True, str(out))
    check("balance updated", balance(cur, u)[0] == 18, str(balance(cur, u)))

    rows = adjust_rows(cur, u)
    check("exactly one ledger row", len(rows) == 1, f"{len(rows)} rows")
    if rows:
        cs, cd, meta = rows[0]
        # cost = old - new, so granting is negative -- same direction as a
        # purchase. The whole movement lands in cost_scan_credits: an admin
        # adjustment is not "scan work" or "deep work", and splitting it across
        # both columns would invent a composition the change never had.
        check("cost_scan = 3-18 = -15", cs == -15, f"got {cs}")
        check("cost_deep untouched", cd == 0, f"got {cd}")
        check("records the actor", meta.get("actor_id") == admin, str(meta))
        check("records previous balance", meta.get("previous_credits") == 3, str(meta))
        check("records the reason", meta.get("reason") == "topped up", str(meta))


def test_revocation_writes_positive_cost(cur):
    print("\nledger: a revocation is recorded as positive cost")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=10, deep=10)
    rpc(cur, "admin_adjust_credits", admin, u, 14, 0, False, "user", "clawback")
    rows = adjust_rows(cur, u)
    check("one row", len(rows) == 1, f"{len(rows)} rows")
    if rows:
        check("cost_scan = 20-14 = +6 (same sign as a debit)", rows[0][0] == 6, f"got {rows[0][0]}")
        check("deep unchanged -> cost 0", rows[0][1] == 0, f"got {rows[0][1]}")


def test_no_credit_change_writes_no_row(cur):
    print("\nledger: a change that moves no credits writes no row")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=5, deep=5)

    out = rpc(cur, "admin_adjust_credits", admin, u, 10, 0, True, "admin", "promote only")
    check("ok", out.get("ok") is True, str(out))
    check("no event_id returned", out.get("event_id") is None, str(out))
    check("no ledger row", len(adjust_rows(cur, u)) == 0, "row written for a zero-credit change")
    check("role/disabled still applied", balance(cur, u)[1:] == ("admin", True), str(balance(cur, u)))
    check("reports role_changed", out.get("role_changed") is True, str(out))


def test_double_click_is_idempotent(cur):
    print("\nledger: saving the same values twice writes one row, not two")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=1, deep=1)
    rpc(cur, "admin_adjust_credits", admin, u, 18, 0, False, "user", "first")
    rpc(cur, "admin_adjust_credits", admin, u, 18, 0, False, "user", "second")
    check("second save is a zero delta", len(adjust_rows(cur, u)) == 1, f"{len(adjust_rows(cur,u))} rows")
    check("balance settled", balance(cur, u)[0] == 18, str(balance(cur, u)))


def test_ledger_reconciles(cur):
    print("\nledger: balance is fully explained by the ledger")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=0, deep=0)   # opening balance 0/0

    rpc(cur, "admin_adjust_credits", admin, u, 20, 0, False, "user", "grant")
    rpc(cur, "consume_credit", u, "scan", "{}", str(uuid.uuid4()))
    rpc(cur, "consume_credit", u, "deep_analyze", "{}", str(uuid.uuid4()))
    rpc(cur, "admin_adjust_credits", admin, u, 13, 0, False, "user", "clawback")

    # BOTH cost columns, summed. The merge left usage_events alone -- a scan
    # still writes cost_scan_credits and a deep analysis still writes
    # cost_deep_credits -- so reading one column would compare a whole balance
    # against half a ledger and the invariant would stop meaning anything.
    cur.execute(
        "select coalesce(sum(cost_scan_credits),0) + coalesce(sum(cost_deep_credits),0)"
        " from public.usage_events where user_id=%s", (u,))
    ledger = cur.fetchone()[0]
    credits = balance(cur, u)[0]

    # opening was 0, so: balance == -sum(cost).
    check(f"credits: {credits} == -({ledger})", credits == -ledger,
          f"balance {credits}, ledger {-ledger}")


# ── lockout ──────────────────────────────────────────────────────────────────

def test_cannot_demote_self(cur):
    print("\nlockout: an admin cannot demote or disable themselves")
    admin = seed_user(cur, admin=True, scan=5, deep=5)

    out = rpc(cur, "admin_adjust_credits", admin, admin, 10, 0, False, "user", "oops")
    check("demote self refused", out.get("reason") == "cannot_demote_self", str(out))
    check("still admin", balance(cur, admin)[1] == "admin", str(balance(cur, admin)))

    out = rpc(cur, "admin_adjust_credits", admin, admin, 10, 0, True, "admin", "oops")
    check("disable self refused", out.get("reason") == "cannot_disable_self", str(out))
    check("still enabled", balance(cur, admin)[2] is False, str(balance(cur, admin)))

    # The guard must not block the legitimate case it sits next to.
    out = rpc(cur, "admin_adjust_credits", admin, admin, 100, 0, False, "admin", "self top-up")
    check("admin CAN still adjust own credits", out.get("ok") is True, str(out))
    check("own credits applied", balance(cur, admin)[0] == 100, str(balance(cur, admin)))


def test_can_still_manage_others(cur):
    print("\nlockout: the guard is scoped to self, not to everyone")
    admin = seed_user(cur, admin=True)
    other = seed_user(cur, scan=3, deep=3, admin=True)

    out = rpc(cur, "admin_adjust_credits", admin, other, 6, 0, True, "user", "demote+disable other")
    check("can demote another admin", out.get("ok") is True, str(out))
    check("other demoted and disabled", balance(cur, other)[1:] == ("user", True), str(balance(cur, other)))


def test_actor_must_be_enabled_admin(cur):
    print("\nauthz: the actor is checked in the database, not just the UI")
    plain = seed_user(cur)
    victim = seed_user(cur, scan=1, deep=1)
    check("non-admin actor refused",
          rpc(cur, "admin_adjust_credits", plain, victim, 1998, 0, False, "user", "x").get("reason") == "not_admin")
    check("victim balance untouched", balance(cur, victim)[0] == 2, str(balance(cur, victim)))

    dis_admin = seed_user(cur, admin=True, disabled=True)
    check("disabled admin refused",
          rpc(cur, "admin_adjust_credits", dis_admin, victim, 1998, 0, False, "user", "x").get("reason") == "not_admin")

    # The whole point of the actor check: holding the service-role key is not
    # enough, because the actor id is just an argument the caller supplies.
    check("no adjustment rows written by refused calls", len(adjust_rows(cur, victim)) == 0)


def test_input_validation(cur):
    print("\nvalidation: bad input is a reason code, not a constraint violation")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=5, deep=5)

    check("negative scan refused",
          rpc(cur, "admin_adjust_credits", admin, u, -1, 0, False, "user", "x").get("reason") == "invalid_credits")
    check("negative deep refused",
          rpc(cur, "admin_adjust_credits", admin, u, 5, -3, False, "user", "x").get("reason") == "deep_credits_retired")
    check("null credits refused",
          rpc(cur, "admin_adjust_credits", admin, u, None, 0, False, "user", "x").get("reason") == "invalid_credits")
    check("bogus role refused",
          rpc(cur, "admin_adjust_credits", admin, u, 10, 0, False, "superuser", "x").get("reason") == "invalid_role")
    check("missing profile refused",
          rpc(cur, "admin_adjust_credits", admin, str(uuid.uuid4()), 10, 0, False, "user", "x").get("reason")
          == "profile_not_found")
    check("balance untouched by every refusal", balance(cur, u)[0] == 10, str(balance(cur, u)))


def test_execute_is_service_role_only(cur):
    print("\nauthz: anon and authenticated cannot execute it")
    for role in ("anon", "authenticated"):
        cur.execute(
            "select has_function_privilege(%s,"
            " 'public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)', 'EXECUTE')",
            (role,))
        check(f"{role} has no EXECUTE", cur.fetchone()[0] is False)


def test_concurrent_adjust_vs_debit(cur):
    print("\nconcurrency: an admin save must not swallow a simultaneous debit")
    admin = seed_user(cur, admin=True)
    u = seed_user(cur, scan=5, deep=5)

    def do_adjust():
        c = conn()
        try:
            with c.cursor() as k:
                return rpc(k, "admin_adjust_credits", admin, u, 200, 0, False, "user", "concurrent")
        finally:
            c.close()

    def do_debit():
        c = conn()
        try:
            with c.cursor() as k:
                return rpc(k, "consume_credit", u, "scan", "{}", str(uuid.uuid4()))
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1, f2 = ex.submit(do_adjust), ex.submit(do_debit)
        f1.result(); f2.result()

    credits = balance(cur, u)[0]
    # Either order is legal, but the debit must be visible in the result:
    #   debit first  -> admin reads 9, sets 200          -> 200
    #   admin first  -> sets 200, debit applies on top   -> 199
    # 210 would mean the admin's absolute write was computed from a stale read.
    check(f"no lost update (got {credits}, expected 199 or 200)",
          credits in (199, 200), f"got {credits}")

    cur.execute("select coalesce(sum(cost_scan_credits),0) + coalesce(sum(cost_deep_credits),0)"
                " from public.usage_events where user_id=%s", (u,))
    total = cur.fetchone()[0]
    check(f"ledger explains it: 10 - ({total}) == {credits}", 10 - total == credits,
          f"10-{total} != {credits}")


def main() -> int:
    print("=" * 74)
    print("  admin_adjust_credits -- ledger completeness and lockout safety")
    print("=" * 74)

    c = conn()
    with c.cursor() as cur:
        rebuild(cur)
        for t in (test_grant_writes_negative_cost,
                  test_revocation_writes_positive_cost,
                  test_no_credit_change_writes_no_row,
                  test_double_click_is_idempotent,
                  test_ledger_reconciles,
                  test_cannot_demote_self,
                  test_can_still_manage_others,
                  test_actor_must_be_enabled_admin,
                  test_input_validation,
                  test_execute_is_service_role_only,
                  test_concurrent_adjust_vs_debit):
            try:
                t(cur)
            except Exception as e:
                FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    c.close()

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
