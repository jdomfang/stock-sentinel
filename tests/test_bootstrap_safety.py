#!/usr/bin/env python3
"""The bootstrap must not reopen holes the migrations closed.

WHY THIS SUITE EXISTS

scripts/supabase_schema.sql is the documented setup step -- "Paste into Supabase
SQL Editor and run". It created two RLS policies that
20260801020000_credit_integrity.sql exists to DROP:

  profiles_update_own   any signed-in user may UPDATE their own profile row.
                        RLS is row-level, so it cannot restrict WHICH COLUMNS --
                        which makes it "users may set their own credits and
                        role". Self-service credits and self-promotion to admin.
  usage_insert_own      any signed-in user may INSERT ledger rows, so the audit
                        trail can be forged to match a stolen balance and
                        reconciliation agrees with the theft.

So the vulnerability was reachable by following the instructions. Nothing caught
it because every SQL suite applies the stub plus the migrations -- the migrations
drop the policies, the suites pass, and the bootstrap is never exercised.

This suite runs the REAL bootstrap file, then the full migration chain, and
asserts the effective grants. It is the only place either is checked.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_bootstrap_safety.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required:  pip install psycopg2-binary")
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.migrations import chain as _chain  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"

# Policies that must never exist, whatever created them.
FORBIDDEN = {
    "profiles_update_own": "lets a user set their own credits and role",
    "usage_insert_own": "lets a user forge the ledger that explains their balance",
    "usage_insert_admin": "lets an admin JWT write ledger rows outside the credit functions",
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


# ───────────────────────────────────────────────────── the file, statically

def test_the_bootstrap_file_creates_no_forbidden_policy():
    """Read as CODE, not as text.

    The file legitimately contains `drop policy if exists "profiles_update_own"`
    -- dropping it defensively is correct. A substring search for the policy
    name therefore matches the safe line as readily as the dangerous one, which
    is how a grep-based check would pass on a file that still creates it.
    """
    print("\nstatic: the bootstrap creates no policy the migrations drop")
    src = (REPO / "scripts" / "supabase_schema.sql").read_text()
    created = set(re.findall(r'create\s+policy\s+"([^"]+)"', src, re.I))
    for name, why in FORBIDDEN.items():
        check(f"bootstrap does not create {name}", name not in created, why)
    # ...and the defensive drop IS present, so a database created by an older
    # copy of this file is repaired by re-running the current one.
    dropped = set(re.findall(r'drop\s+policy\s+if\s+exists\s+"([^"]+)"', src, re.I))
    for name in ("profiles_update_own", "usage_insert_own"):
        check(f"bootstrap defensively drops {name}", name in dropped,
              "an older run of this file left the policy behind and nothing removes it")


# ────────────────────────────────────────── the database, after bootstrap

def apply_bootstrap_then_migrations(cur) -> None:
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    # Stub first: it supplies what SUPABASE provides -- the anon/authenticated/
    # service_role roles, the auth schema, auth.users, and the broad table
    # grants that make RLS the only gate. The bootstrap assumes all of it.
    #
    # Its `create table if not exists` statements then no-op against the stub's
    # tables, which is fine: what is under test here is the bootstrap's POLICY
    # statements, not its DDL.
    for p in _chain():
        if p.name == "00_supabase_stub.sql":
            cur.execute(p.read_text())
            break
    cur.execute((REPO / "scripts" / "supabase_schema.sql").read_text())
    for p in _chain():
        if p.name == "00_supabase_stub.sql":
            continue
        cur.execute(p.read_text())


def test_no_forbidden_policy_survives(cur):
    print("\nlive: the effective policy set after bootstrap + migrations")
    cur.execute("select policyname, tablename from pg_policies where schemaname='public'")
    live = {r[0]: r[1] for r in cur.fetchall()}
    for name, why in FORBIDDEN.items():
        check(f"{name} is not installed", name not in live, why)
    # The one that IS deliberate, and must survive: the owner created it so an
    # admin can adjust other accounts' credits.
    check("profiles_update_admin survives", "profiles_update_admin" in live,
          "the owner created this deliberately; do not drop it")


def test_credit_functions_are_service_role_only(cur):
    print("\nlive: EXECUTE on every credit function")
    sigs = {
        "consume_credit": "public.consume_credit(uuid,text,jsonb,text)",
        "refund_credit": "public.refund_credit(uuid,text,uuid,text)",
        "grant_credits": "public.grant_credits(uuid,integer,integer,text,text)",
        "admin_adjust_credits":
            "public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)",
    }
    for name, sig in sigs.items():
        for role in ("anon", "authenticated"):
            cur.execute("select has_function_privilege(%s,%s,'EXECUTE')", (role, sig))
            check(f"{role} cannot execute {name}", cur.fetchone()[0] is False,
                  "these take the target user as an ARGUMENT -- EXECUTE is the "
                  "only thing between a signed-in user and self-service credits")
        cur.execute("select has_function_privilege('service_role',%s,'EXECUTE')", (sig,))
        check(f"service_role CAN execute {name}", cur.fetchone()[0] is True)


def test_security_definer_functions_pin_search_path(cur):
    """A SECURITY DEFINER function without a pinned search_path is hijackable.

    Anything that can create a schema on the search path can shadow a table the
    function references and have it run as the owner.
    """
    print("\nlive: every SECURITY DEFINER function pins search_path")
    cur.execute("""
        select p.proname, p.proconfig
          from pg_proc p join pg_namespace n on n.oid = p.pronamespace
         where n.nspname = 'public' and p.prosecdef
         order by p.proname
    """)
    rows = cur.fetchall()
    check("there are SECURITY DEFINER functions to check", bool(rows))
    for name, config in rows:
        pinned = any(c.startswith("search_path=") for c in (config or []))
        check(f"{name} pins search_path", pinned,
              "a SECURITY DEFINER function with a mutable search_path can be "
              "made to run attacker-controlled objects as its owner")


def test_a_user_cannot_write_their_own_balance(cur):
    """The behaviour, not the policy list. RLS is enforced per role."""
    print("\nlive: RLS actually refuses a self-service credit grant")
    import uuid
    uid = str(uuid.uuid4())
    # The profile row is created by the handle_new_user TRIGGER on auth.users --
    # inserting it again here is a duplicate-key error, not a test.
    cur.execute("insert into auth.users (id,email) values (%s,%s)", (uid, "rls@t.test"))
    cur.execute("update public.profiles set credits = 1 where user_id = %s", (uid,))

    # `set role`, NOT `set local role`. This connection is in autocommit, so
    # there is no surrounding transaction for `local` to scope to and the
    # setting silently does nothing -- leaving the query running as the
    # superuser, which BYPASSES RLS entirely. The first version of this test
    # reported the database wide open when it had only failed to switch role.
    # Same for set_config: the third argument must be false outside a
    # transaction.
    def as_user(sql, params=()):
        # request.jwt.claim.sub -- the GUC the stub's auth.uid() actually reads
        # (tests/sql/00_supabase_stub.sql). Setting request.jwt.claims, a JSON
        # blob, leaves auth.uid() NULL, which makes every `auth.uid() = user_id`
        # predicate false and every assertion below pass whether the dangerous
        # policy exists or not. This test reported the database safe while a
        # re-created profiles_update_own let the same UPDATE through.
        cur.execute("set role authenticated")
        cur.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid,))
        try:
            cur.execute(sql, params)
            return cur.rowcount
        finally:
            cur.execute("reset role")
            cur.execute("select set_config('request.jwt.claim.sub', '', false)")

    # PROVE THE IMPERSONATION FIRST. Without this the rest is unfalsifiable:
    # a silent failure to become the user looks exactly like a database that
    # correctly refuses them.
    cur.execute("set role authenticated")
    cur.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid,))
    cur.execute("select auth.uid()")
    seen_uid = cur.fetchone()[0]
    cur.execute("reset role")
    cur.execute("select set_config('request.jwt.claim.sub', '', false)")
    check("the test can actually impersonate the user", str(seen_uid) == uid,
          f"auth.uid() = {seen_uid!r} -- every RLS assertion below is vacuous")

    moved = as_user("update public.profiles set credits = 9999 where user_id = %s", (uid,))
    cur.execute("select credits from public.profiles where user_id=%s", (uid,))
    after = cur.fetchone()[0]
    check("a user's UPDATE of their own credits affects no rows", moved == 0,
          f"{moved} row(s) updated")
    check("...and the balance is unchanged", after == 1, f"credits={after}")

    try:
        forged = as_user("insert into public.usage_events (user_id,event_type,"
                         "cost_scan_credits) values (%s,'purchase',-99)", (uid,)) > 0
    except psycopg2.Error:
        forged = False

    check("a user cannot forge a ledger row", not forged,
          "a forged grant row makes reconciliation agree with a stolen balance")


def main() -> int:
    print("=" * 74)
    print("  bootstrap safety: the documented setup step must not reopen a hole")
    print("=" * 74)
    test_the_bootstrap_file_creates_no_forbidden_policy()
    c = conn()
    with c.cursor() as cur:
        try:
            apply_bootstrap_then_migrations(cur)
        except Exception as e:
            check("bootstrap + migrations apply cleanly", False, f"{type(e).__name__}: {e}")
            c.close()
            print(f"\n  {len(PASSED)} passed, {len(FAILED)} failed")
            return 1
        check("bootstrap + migrations apply cleanly", True)
        for t in (test_no_forbidden_policy_survives,
                  test_credit_functions_are_service_role_only,
                  test_security_definer_functions_pin_search_path,
                  test_a_user_cannot_write_their_own_balance):
            try:
                t(cur)
            except Exception as e:
                FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    c.close()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
