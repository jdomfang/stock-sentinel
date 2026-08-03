#!/usr/bin/env python3
"""Prove paid work that never finishes gets found and refunded.

On 2026-08-01 a Discovery scan was killed by the OOM killer mid-run. SIGKILL
runs no handler: no `except`, no `finally`, no refund. The credit was spent and
nothing delivered, and recovering it took a human noticing a blank page, then a
manual query, then a hand-issued refund.

bf401de moved refunds into try/finally, covering Streamlit aborting the script.
That is the common case and it is now handled. It cannot cover process death --
nothing inside a process can report its own kill.

work_runs makes the gap visible: a lifecycle row is opened in the SAME
TRANSACTION as the debit, so a debit without one is impossible rather than
merely discouraged, and reap_orphaned_work refunds anything still running well
past any legitimate runtime -- from a different process, which is the only place
it can work from.

NOTE ON SEEDING. Every user here starts at 0/0 and gets credits through
grant_credits, so the ledger explains the balance. Writing profiles.scan_credits
directly creates an unledgered opening balance and balance = -sum(cost) then
cannot hold by construction -- the discrepancy is exactly the seeded amount. I
made that mistake three separate times while writing these suites, and an
independent audit found the same flaw across the earlier ones. The helper below
exists so it cannot happen again.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_work_runs.py
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


def seed(cur, scan=5, deep=5) -> str:
    """Create a user whose balance is fully explained by the ledger."""
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id,email) values (%s,%s)", (uid, f"{uid[:8]}@t.test"))
    cur.execute(
        "insert into public.profiles (user_id,email,role,disabled,scan_credits,deep_credits)"
        " values (%s,%s,'user',false,0,0)", (uid, f"{uid[:8]}@t.test"))
    if scan or deep:
        rpc(cur, "grant_credits", uid, scan, deep, "test opening balance", f"seed_{uid[:8]}")
    return uid


def balance(cur, uid):
    cur.execute("select scan_credits,deep_credits from public.profiles where user_id=%s", (uid,))
    return cur.fetchone()


def reconciles(cur, uid) -> bool:
    cur.execute("select coalesce(sum(cost_scan_credits),0), coalesce(sum(cost_deep_credits),0)"
                " from public.usage_events where user_id=%s", (uid,))
    ls, ld = cur.fetchone()
    return balance(cur, uid) == (-ls, -ld)


def run_status(cur, event_id):
    cur.execute("select status from public.work_runs where event_id=%s", (event_id,))
    row = cur.fetchone()
    return row[0] if row else None


def age(cur, event_id, interval="1 hour"):
    cur.execute(f"update public.work_runs set started_at = now() - interval '{interval}'"
                " where event_id=%s", (event_id,))


def rebuild(cur) -> None:
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    for p in MIGRATIONS:
        cur.execute(p.read_text())


# ── the lifecycle row ────────────────────────────────────────────────────────

def test_debit_opens_a_run_atomically(cur):
    print("\nlifecycle: a debit and its run row commit together")
    uid = seed(cur)
    ev = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_a")["event_id"]
    check("run row exists", run_status(cur, ev) == "running")

    cur.execute("select kind, user_id::text, finished_at from public.work_runs where event_id=%s", (ev,))
    kind, ruid, fin = cur.fetchone()
    check("kind matches the debit", kind == "scan")
    check("user matches", ruid == uid)
    check("finished_at is null while running", fin is None)

    # The invariant that matters: no debit may exist without a run.
    cur.execute("""select count(*) from public.usage_events e
                    where e.user_id=%s and e.event_type in ('scan','deep_analyze')
                      and not exists (select 1 from public.work_runs w where w.event_id=e.id)""",
                (uid,))
    check("no debit lacks a run row", cur.fetchone()[0] == 0,
          "a charge with no lifecycle row is invisible to the reaper")


def test_replay_does_not_open_a_second_run(cur):
    print("\nlifecycle: an idempotent replay must not open a second run")
    uid = seed(cur)
    a = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_dup")
    b = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_dup")
    check("replay reports duplicate_request", b.get("reason") == "duplicate_request")
    cur.execute("select count(*) from public.work_runs where user_id=%s", (uid,))
    check("exactly one run row", cur.fetchone()[0] == 1,
          "two rows for one debit would make the reaper refund twice")
    check("same event id", a["event_id"] == b["event_id"])


def test_complete_work(cur):
    print("\nlifecycle: completing a run closes it, idempotently")
    uid = seed(cur)
    ev = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_c")["event_id"]
    r = rpc(cur, "complete_work", ev, "completed", "10 tickers")
    check("closes the run", run_status(cur, ev) == "completed", str(r))

    cur.execute("select finished_at, detail from public.work_runs where event_id=%s", (ev,))
    fin, detail = cur.fetchone()
    check("finished_at is set", fin is not None)
    check("detail recorded", detail == "10 tickers")

    r2 = rpc(cur, "complete_work", ev, "completed")
    check("re-closing is a no-op, not an error", r2.get("ok") is True and r2.get("reason") == "not_running",
          "callers close from a finally block; raising there would mask the original failure")
    check("unknown event is not an error",
          rpc(cur, "complete_work", str(uuid.uuid4()), "completed").get("ok") is True)
    check("bogus status refused",
          rpc(cur, "complete_work", ev, "banana").get("reason") == "invalid_status")


# ── the reaper ───────────────────────────────────────────────────────────────

def test_reaper_refunds_abandoned_work(cur):
    print("\nreaper: work killed mid-run is found and refunded")
    uid = seed(cur, 5, 5)
    before = balance(cur, uid)
    ev = rpc(cur, "consume_credit", uid, "deep_analyze", "{}", "rq_orphan")["event_id"]
    check("credit was taken", balance(cur, uid)[1] == before[1] - 1)

    age(cur, ev)  # simulate a process killed an hour ago
    res = rpc(cur, "reap_orphaned_work", "15 minutes")
    check("reaped exactly 1", res.get("reaped") == 1, str(res))
    check("no failures", res.get("failed") == 0, str(res))
    check("credit returned", balance(cur, uid) == before,
          "this is the OOM case -- no finally could have done it")
    check("run marked orphaned", run_status(cur, ev) == "orphaned")
    check("ledger reconciles", reconciles(cur, uid))


def test_reaper_leaves_live_work_alone(cur):
    print("\nreaper: a scan still running must NOT be refunded")
    uid = seed(cur)
    ev = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_live")["event_id"]
    after_debit = balance(cur, uid)
    res = rpc(cur, "reap_orphaned_work", "15 minutes")
    check("reaped nothing", res.get("reaped") == 0, str(res))
    check("still running", run_status(cur, ev) == "running")
    check("balance untouched", balance(cur, uid) == after_debit,
          "refunding slow work hands back credits for results about to arrive")


def test_reaper_does_not_double_refund(cur):
    print("\nreaper: running it twice must not refund twice")
    uid = seed(cur)
    before = balance(cur, uid)
    ev = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_twice")["event_id"]
    age(cur, ev)
    rpc(cur, "reap_orphaned_work", "15 minutes")
    mid = balance(cur, uid)
    res = rpc(cur, "reap_orphaned_work", "15 minutes")
    check("second pass reaps nothing", res.get("reaped") == 0, str(res))
    check("balance unchanged by the second pass", balance(cur, uid) == mid)
    check("net effect is one refund", balance(cur, uid) == before)


def test_reaper_ignores_completed_work(cur):
    print("\nreaper: delivered work is never refunded, however old")
    uid = seed(cur)
    ev = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_done")["event_id"]
    rpc(cur, "complete_work", ev, "completed")
    after = balance(cur, uid)
    age(cur, ev, "10 days")
    res = rpc(cur, "reap_orphaned_work", "15 minutes")
    check("reaped nothing", res.get("reaped") == 0, str(res))
    check("balance unchanged", balance(cur, uid) == after,
          "refunding delivered work would be free scans for anyone who waits")


def test_concurrent_reapers_do_not_collide(cur):
    print("\nreaper: two reapers at once split the work, never double it")
    uid = seed(cur, 8, 8)
    before = balance(cur, uid)
    evs = []
    for i in range(6):
        evs.append(rpc(cur, "consume_credit", uid, "scan", "{}", f"rq_conc_{i}")["event_id"])
    for ev in evs:
        age(cur, ev)

    def reap():
        c = conn()
        try:
            with c.cursor() as k:
                return rpc(k, "reap_orphaned_work", "15 minutes")
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(lambda _: reap(), range(3)))

    total = sum(r.get("reaped", 0) for r in results)
    check(f"exactly 6 reaped across all reapers (got {total})", total == 6,
          "for update skip locked should split them, not duplicate or drop")
    check("all six refunded once", balance(cur, uid) == before,
          "a double refund would show as a balance above the starting point")
    check("ledger reconciles", reconciles(cur, uid))


# ── access control ───────────────────────────────────────────────────────────

def test_execute_and_rls_locked_down(cur):
    print("\nauthz: clients may read their runs and nothing else")
    for sig, fn in (("public.complete_work(uuid,text,text)", "complete_work"),
                    ("public.reap_orphaned_work(interval)", "reap_orphaned_work")):
        for role in ("anon", "authenticated"):
            cur.execute("select has_function_privilege(%s,%s,'EXECUTE')", (role, sig))
            check(f"{role} cannot execute {fn}", cur.fetchone()[0] is False,
                  "a client that can close its own run defeats the reaper")
        cur.execute("select has_function_privilege('service_role',%s,'EXECUTE')", (sig,))
        check(f"service_role CAN execute {fn}", cur.fetchone()[0] is True)

    cur.execute("select relrowsecurity from pg_class where oid='public.work_runs'::regclass")
    check("RLS enabled on work_runs", cur.fetchone()[0] is True)

    cur.execute("select count(*) from pg_policies where tablename='work_runs' and cmd='SELECT'")
    check("has SELECT policies", cur.fetchone()[0] >= 1)
    cur.execute("select count(*) from pg_policies where tablename='work_runs' and cmd<>'SELECT'")
    check("has NO insert/update/delete policy", cur.fetchone()[0] == 0,
          "a writable lifecycle row could be marked completed to dodge the reaper")


def test_user_cannot_forge_a_run(cur):
    print("\nauthz: a user cannot write their own lifecycle rows")
    uid = seed(cur)
    ev = rpc(cur, "consume_credit", uid, "scan", "{}", "rq_forge")["event_id"]
    c = psycopg2.connect(DSN); c.autocommit = False
    try:
        with c.cursor() as k:
            k.execute("set local role authenticated")
            k.execute("set local request.jwt.claim.sub = %s", (uid,))
            k.execute("select count(*) from public.work_runs")
            check("can read own runs", k.fetchone()[0] >= 1)
            try:
                k.execute("update public.work_runs set status='completed' where event_id=%s", (ev,))
                check("cannot mark own run completed", k.rowcount == 0,
                      f"updated {k.rowcount} rows -- the reaper can be defeated")
            except psycopg2.Error:
                check("cannot mark own run completed", True)
        c.rollback()
    finally:
        c.close()
    check("run is still running", run_status(cur, ev) == "running")


def main() -> int:
    print("=" * 74)
    print("  work_runs -- detect and refund paid work that never finished")
    print("=" * 74)
    c = conn()
    with c.cursor() as cur:
        rebuild(cur)
        for t in (test_debit_opens_a_run_atomically,
                  test_replay_does_not_open_a_second_run,
                  test_complete_work,
                  test_reaper_refunds_abandoned_work,
                  test_reaper_leaves_live_work_alone,
                  test_reaper_does_not_double_refund,
                  test_reaper_ignores_completed_work,
                  test_concurrent_reapers_do_not_collide,
                  test_execute_and_rls_locked_down,
                  test_user_cannot_forge_a_run):
            try:
                t(cur)
            except Exception as e:
                FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    c.close()

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\n  DO NOT APPLY:")
        for n, d in FAILED:
            print(f"    - {n}: {d}")
    else:
        print("\n  All assertions held.")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
