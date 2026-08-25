#!/usr/bin/env python3
"""The spend ceiling's data source, tested as the role that actually calls it.

WHY

x_posts_billed_since returns `coalesce(sum(posts_billed), 0)`, which cannot
distinguish "nothing was spent" from "I cannot see the rows". x_call_metrics has
RLS enabled with no policies, so if the function's owner ever stops being able
to bypass it -- an ownership change, a restore, a rebuilt environment -- it
returns 0 forever and the ceiling is silently off. That is a fail-OPEN mode
inside a guard documented as fail-closed.

So this asserts a NON-ZERO answer with rows present, as service_role, which is
the role core-api uses. A superuser would pass either way and prove nothing.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_spend_budget.sql.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required:  pip install psycopg2-binary")
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.migrations import chain as _chain  # noqa: E402

DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def main() -> int:
    print("=" * 70)
    print("  x spend budget: the number the ceiling reads")
    print("=" * 70)
    c = psycopg2.connect(DSN); c.autocommit = True
    cur = c.cursor()
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    for p in _chain():
        cur.execute(p.read_text())

    def billed(window="24 hours"):
        cur.execute("select public.x_posts_billed_since(%s)", (window,))
        return cur.fetchone()[0]

    print("\nwhat it counts")
    check("an empty table reads 0", billed() == 0)

    cur.execute("insert into public.x_call_metrics (kind,subject,query_hash,posts_billed)"
                " values ('scan','tech','h1',260)")
    check("a scan's spend counts", billed() == 260, str(billed()))

    cur.execute("insert into public.x_call_metrics (kind,subject,query_hash,posts_billed)"
                " values ('deep','AAPL','h2',400)")
    check("a DEEP analysis counts too", billed() == 660, str(billed()))

    cur.execute("insert into public.x_call_metrics (kind,subject,query_hash,posts_billed,from_cache)"
                " values ('scan','energy','h3',0,true)")
    check("a cache hit adds nothing", billed() == 660, str(billed()))

    print("\nthe window")
    cur.execute("insert into public.x_call_metrics (kind,subject,query_hash,posts_billed,created_at)"
                " values ('scan','old','h4',999, now() - interval '25 hours')")
    check("spend older than the window is excluded", billed() == 660, str(billed()))
    check("...but a wider window includes it", billed("48 hours") == 1659, str(billed("48 hours")))

    print("\nthe role that actually calls it")
    # service_role, not the superuser this script connects as -- a superuser
    # bypasses RLS regardless of how anything is owned.
    #
    # WHAT THIS DOES AND DOES NOT PROVE. It catches a lost EXECUTE grant, a
    # renamed function, and a signature change. It does NOT prove the function
    # survives RLS on its own: service_role carries BYPASSRLS in Supabase (and
    # in the stub), so the rows are visible to it whether the function is
    # SECURITY DEFINER or not. Verified by mutation -- forcing row level
    # security on the table changes nothing here.
    #
    # So SECURITY DEFINER on this function is belt-and-braces, not the control.
    # The control is that only service_role may execute it, asserted below.
    cur.execute("set role service_role")
    try:
        cur.execute("select public.x_posts_billed_since('24 hours')")
        as_service = cur.fetchone()[0]
    finally:
        cur.execute("reset role")
    check("service_role sees the spend, not a filtered 0", as_service == 660,
          f"got {as_service} -- RLS is hiding rows from the ceiling, which then "
          f"reads 0 forever and silently stops capping anything")

    print("\nwho may ask")
    for role in ("anon", "authenticated"):
        cur.execute("select has_function_privilege(%s,"
                    "'public.x_posts_billed_since(interval)','EXECUTE')", (role,))
        check(f"{role} cannot execute it", cur.fetchone()[0] is False)
    cur.execute("select has_function_privilege('service_role',"
                "'public.x_posts_billed_since(interval)','EXECUTE')")
    check("service_role can", cur.fetchone()[0] is True)

    print("\nno duplicate index on the hot write path")
    cur.execute("select indexdef from pg_indexes where tablename='x_call_metrics'")
    defs = [r[0] for r in cur.fetchall()]
    # Only a STANDALONE (created_at DESC) index counts. The composite
    # (kind, query_hash, created_at DESC) serves a different query and is not a
    # duplicate of it -- matching on "created_at appears somewhere" flagged that
    # one too, which is a false positive, not a finding.
    import re as _re
    solo = [d for d in defs
            if _re.search(r"btree \(created_at DESC\)\s*$", d, _re.I)]
    check("exactly one standalone (created_at DESC) index", len(solo) == 1,
          f"{len(solo)}: {solo} -- a second identical index adds write "
          f"amplification on the paid path for no read benefit")

    c.close()
    print("\n" + "=" * 70)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
