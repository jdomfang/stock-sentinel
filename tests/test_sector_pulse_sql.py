#!/usr/bin/env python3
"""Prove sector_pulse is service-only on the table and browser-readable only through the narrow reader.

WHAT IS AT RISK

1. A BROWSER READING THE TABLE. The landing page shows the pulse to anonymous
   visitors. The moment anon can SELECT the table directly, every future column
   added to it is public by default -- which is how a "harmless" table grows
   into a leak. The reader function is the API; the table is not.
2. THE NIGHTLY JOB UNABLE TO WRITE. The sync upserts ten rows a night with the
   service key. If service_role lacks INSERT or UPDATE, the job fails green
   (its errors are logged and swallowed by design) and the strip silently
   freezes on the last good night.
3. A ROW THAT LIES. The state vocabulary, the 0..1 shares and the 0..5 day
   counts are CHECKed so a bug in the compute cannot store "accumulatng" or a
   breadth of 140% -- an unreadable row is better than a wrong one.

Impersonation uses `set role`, NOT `set local role`: this connection is in
autocommit, so `local` would silently no-op and every anon assertion below
would run as the admin and pass regardless (tests/test_bootstrap_safety.py
learned this the hard way).

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_sector_pulse_sql.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed. pip install psycopg2-binary")
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.migrations import chain as migration_chain  # noqa: E402

DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"
T = "public.sector_pulse"
F = "public.get_sector_pulse_recent(integer)"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def connect():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def rebuild(cur) -> None:
    cur.execute("drop schema if exists public cascade; create schema public; drop schema if exists auth cascade;")
    for m in migration_chain():
        cur.execute(m.read_text(encoding="utf-8"))


def row(sector, day, state="quiet", **kw):
    base = dict(sector=sector, trade_date=day, n_eligible=100, ud_ratio_5d=1.0, breadth=0.1,
                acc_days_5d=1, dist_days_5d=1, eq_return_5d=0.0, pct_up_5d=0.5, state=state,
                top_contrib=json.dumps([{"ticker": "XOM", "share_of_rise": 0.2, "ret_1d": 0.01, "rel_vol": 1.4}]),
                calendar_flag=None)
    base.update(kw)
    return base


UPSERT = """
insert into public.sector_pulse
  (sector, trade_date, n_eligible, ud_ratio_5d, breadth, acc_days_5d, dist_days_5d,
   eq_return_5d, pct_up_5d, state, top_contrib, calendar_flag)
values (%(sector)s, %(trade_date)s, %(n_eligible)s, %(ud_ratio_5d)s, %(breadth)s, %(acc_days_5d)s,
        %(dist_days_5d)s, %(eq_return_5d)s, %(pct_up_5d)s, %(state)s, %(top_contrib)s::jsonb, %(calendar_flag)s)
on conflict (sector, trade_date) do update set
  n_eligible = excluded.n_eligible, ud_ratio_5d = excluded.ud_ratio_5d, breadth = excluded.breadth,
  acc_days_5d = excluded.acc_days_5d, dist_days_5d = excluded.dist_days_5d, eq_return_5d = excluded.eq_return_5d,
  pct_up_5d = excluded.pct_up_5d, state = excluded.state, top_contrib = excluded.top_contrib,
  calendar_flag = excluded.calendar_flag, computed_at = now()
"""


def as_role(cur, role, sql, params=None):
    """Run sql as a Supabase role. Returns (rows_or_None, error_or_None). Always resets the role."""
    cur.execute(f"set role {role}")
    try:
        cur.execute(sql, params)
        try:
            return cur.fetchall(), None
        except psycopg2.ProgrammingError:
            return None, None
    except psycopg2.Error as e:
        return None, e.pgcode or type(e).__name__
    finally:
        cur.execute("reset role")


def test_lockdown(cur):
    print("\nlockdown: RLS on, no policies, service-only table, browser-only reader")
    cur.execute("select relrowsecurity from pg_class where oid=%s::regclass", (T,))
    check("RLS is enabled", cur.fetchone()[0] is True)
    cur.execute("select count(*) from pg_policies where schemaname='public' and tablename='sector_pulse'")
    check("no RLS policies exist (nothing for a browser to satisfy)", cur.fetchone()[0] == 0)
    for role, verb, want in (("anon", "select", False), ("authenticated", "select", False),
                             ("service_role", "select", True), ("service_role", "insert", True),
                             ("service_role", "update", True), ("service_role", "delete", False)):
        cur.execute("select has_table_privilege(%s, %s, %s)", (role, T, verb))
        got = cur.fetchone()[0]
        check(f"{role} {'can' if want else 'cannot'} {verb} the table", got is want, f"got {got}")
    for role in ("anon", "authenticated", "service_role"):
        cur.execute("select has_function_privilege(%s, %s, 'execute')", (role, F))
        check(f"{role} can execute the reader", cur.fetchone()[0] is True)
    cur.execute("select p.prosecdef, p.proconfig from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
                "where n.nspname='public' and p.proname='get_sector_pulse_recent'")
    secdef, config = cur.fetchone()
    check("reader is SECURITY DEFINER", secdef is True)
    check("reader pins search_path", any(c.startswith("search_path=") for c in (config or [])), str(config))


def test_behaviour(cur):
    print("\nbehaviour: the job can upsert, browsers read only through the reader")
    for d in ("2026-09-01", "2026-09-02"):
        for s in ("energy", "tech"):
            _, err = as_role(cur, "service_role", UPSERT, row(s, d, "accumulating" if s == "energy" else "quiet"))
            check(f"service_role upserts {s} {d}", err is None, str(err))
    _, err = as_role(cur, "service_role", UPSERT, row("energy", "2026-09-02", "distributing", breadth=0.4))
    check("re-upserting the same (sector, date) updates rather than errors", err is None, str(err))
    cur.execute("select state, breadth from public.sector_pulse where sector='energy' and trade_date='2026-09-02'")
    st, br = cur.fetchone()
    check("...and the row now holds the new values", st == "distributing" and float(br) == 0.4, f"{st} {br}")
    cur.execute("select count(*) from public.sector_pulse")
    check("four rows, not five: the PK made the re-upsert an update", cur.fetchone()[0] == 4)

    rows, err = as_role(cur, "anon", "select * from public.sector_pulse")
    check("anon SELECT on the table is refused", err is not None and rows is None, f"rows={rows} err={err}")
    rows, err = as_role(cur, "authenticated", "select * from public.sector_pulse")
    check("authenticated SELECT on the table is refused", err is not None, f"err={err}")

    rows, err = as_role(cur, "anon", "select sector, trade_date::text, state from public.get_sector_pulse_recent(1)")
    check("anon reads the latest day through the reader", err is None and rows is not None, str(err))
    check("...only the most recent date comes back", rows is not None and {r[1] for r in rows} == {"2026-09-02"}, str(rows))
    check("...both sectors are present for it", rows is not None and {r[0] for r in rows} == {"energy", "tech"}, str(rows))
    rows, _ = as_role(cur, "anon", "select distinct trade_date::text from public.get_sector_pulse_recent(6)")
    check("asking for 6 days returns the 2 that exist", rows is not None and len(rows) == 2, str(rows))
    rows, _ = as_role(cur, "anon", "select count(distinct trade_date) from public.get_sector_pulse_recent(10000)")
    check("the window is clamped (cannot page out history)", rows is not None and rows[0][0] <= 30, str(rows))
    rows, _ = as_role(cur, "anon", "select count(*) from public.get_sector_pulse_recent(0)")
    check("a zero window still returns the latest day", rows is not None and rows[0][0] == 2, str(rows))

    _, err = as_role(cur, "service_role", "delete from public.sector_pulse where sector='tech'")
    check("service_role cannot delete (history is append/update only)", err is not None, "delete succeeded")
    _, err = as_role(cur, "anon", UPSERT, row("energy", "2026-09-03"))
    check("anon cannot write", err is not None, "insert succeeded")


def test_constraints(cur):
    print("\nconstraints: a row that would lie is refused")
    bad = [
        ("misspelled state", row("energy", "2026-09-05", "accumulatng")),
        ("breadth above 1", row("energy", "2026-09-05", breadth=1.4)),
        ("negative pct_up", row("energy", "2026-09-05", pct_up_5d=-0.1)),
        ("six accumulation days in a five-day window", row("energy", "2026-09-05", acc_days_5d=6)),
        ("top_contrib that is not an array", row("energy", "2026-09-05", top_contrib=json.dumps({"ticker": "XOM"}))),
        ("unknown calendar flag", row("energy", "2026-09-05", calendar_flag="fomc")),
    ]
    for label, r in bad:
        _, err = as_role(cur, "service_role", UPSERT, r)
        check(f"{label} is refused", err is not None, "row was accepted")
    _, err = as_role(cur, "service_role", UPSERT, row("energy", "2026-09-05", ud_ratio_5d=None, calendar_flag="opex"))
    check("NULL ud_ratio (all-up window) and a valid calendar flag are accepted", err is None, str(err))


def test_ledger(cur):
    print("\nledger: the migration recorded itself")
    cur.execute("select count(*) from public.schema_migrations where version='20260904010000_sector_pulse'")
    check("20260904010000_sector_pulse is in schema_migrations", cur.fetchone()[0] == 1)


def main() -> int:
    print("=" * 74)
    print("  sector_pulse: service-only table, narrow browser reader, honest rows")
    print("=" * 74)
    try:
        conn = connect()
    except psycopg2.OperationalError as exc:
        print(f"\nCannot reach the test database: {exc}")
        print("Start it with: docker compose -f docker-compose.test.yml up -d")
        return 2
    cur = conn.cursor()
    try:
        rebuild(cur)
    except Exception as e:  # noqa: BLE001
        check("migration chain applies cleanly", False, f"{type(e).__name__}: {e}")
        print(f"\n  {len(PASSED)} passed, {len(FAILED)} failed")
        return 1
    check("migration chain applies cleanly", True)
    for t in (test_lockdown, test_behaviour, test_constraints, test_ledger):
        try:
            t(cur)
        except Exception as e:  # noqa: BLE001
            FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    conn.close()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
