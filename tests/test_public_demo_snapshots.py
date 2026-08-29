#!/usr/bin/env python3
"""Prove durable public-demo publications are private and coherent.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_public_demo_snapshots.py
"""

from __future__ import annotations

import copy
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

DSN = (
    "host=127.0.0.1 port=5433 dbname=sentinel_test "
    "user=supabase_admin password=postgres"
)
MIGRATION = (
    REPO
    / "supabase"
    / "migrations"
    / "20260828010000_public_demo_snapshots.sql"
)
READER_MIGRATION = (
    REPO
    / "supabase"
    / "migrations"
    / "20260828020000_public_demo_reader.sql"
)
PUBLISHER = "11111111-1111-1111-1111-111111111111"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(
        f"  {'PASS' if condition else 'FAIL'}  {name}"
        f"{'' if condition else '  <- ' + detail}"
    )


def connect():
    connection = psycopg2.connect(DSN)
    connection.autocommit = True
    return connection


def rebuild(cursor) -> None:
    cursor.execute(
        "drop schema if exists public cascade; create schema public; "
        "drop schema if exists auth cascade;"
    )
    for migration in migration_chain():
        cursor.execute(migration.read_text(encoding="utf-8"))


def valid_bundle() -> dict:
    return {
        "scan": {
            "sector": "tech",
            "generated_at": "2026-08-28T21:00:00Z",
            "validated_rows": [
                {
                    "Ticker": "WAY",
                    "Overall Sentiment": "Neutral",
                    "Mentions": 21,
                },
                {
                    "Ticker": "SATA",
                    "Overall Sentiment": "Neutral",
                    "Mentions": 12,
                },
            ],
            "unvalidated_rows": [],
        },
        "deep_analysis": {
            "ticker": "WAY",
            "sector": "tech",
            "generated_at": "2026-08-28T21:01:00Z",
            "analysis_results": {"recommendation": "Watch"},
        },
    }


def insert(cursor, bundle: dict, version: int = 1) -> None:
    cursor.execute(
        """
        insert into public.public_demo_snapshots
            (schema_version, bundle, published_by)
        values (%s, %s::jsonb, %s::uuid)
        """,
        (version, json.dumps(bundle), PUBLISHER),
    )


def rejected(cursor, bundle: dict, version: int = 1) -> bool:
    try:
        insert(cursor, bundle, version)
        return False
    except psycopg2.Error:
        return True


def test_schema_and_security(cursor) -> None:
    print("\nschema: append-only history with server-only access")
    cursor.execute(
        "select relrowsecurity from pg_class "
        "where oid='public.public_demo_snapshots'::regclass"
    )
    check("RLS is enabled", cursor.fetchone()[0] is True)

    cursor.execute(
        "select count(*) from pg_policies "
        "where schemaname='public' and tablename='public_demo_snapshots'"
    )
    check("no browser-readable RLS policies exist", cursor.fetchone()[0] == 0)

    cursor.execute(
        """
        select
          has_table_privilege('anon', 'public.public_demo_snapshots', 'select'),
          has_table_privilege(
            'authenticated', 'public.public_demo_snapshots', 'select'
          ),
          has_table_privilege(
            'service_role', 'public.public_demo_snapshots', 'select'
          ),
          has_table_privilege(
            'service_role', 'public.public_demo_snapshots', 'insert'
          ),
          has_table_privilege(
            'service_role', 'public.public_demo_snapshots', 'update'
          )
        """
    )
    anon_read, user_read, service_read, service_insert, service_update = (
        cursor.fetchone()
    )
    check("anonymous browsers cannot read publications", not anon_read)
    check("authenticated browsers cannot read publications", not user_read)
    check("service role can read publications", service_read)
    check("service role can publish new versions", service_insert)
    check("service role cannot rewrite publication history", not service_update)

    cursor.execute(
        """
        select
          has_function_privilege(
            'anon', 'public.get_latest_public_demo()', 'execute'
          ),
          has_function_privilege(
            'authenticated', 'public.get_latest_public_demo()', 'execute'
          )
        """
    )
    anon_reader, user_reader = cursor.fetchone()
    check("anonymous visitors can call only the narrow reader", anon_reader)
    check("signed-in visitors can call the narrow reader", user_reader)


def test_bundle_constraints(cursor) -> None:
    print("\nintegrity: incomplete or mismatched demos cannot be published")
    bundle = valid_bundle()
    insert(cursor, bundle)
    check("a coherent scan and analysis bundle is accepted", True)

    cursor.execute("set role anon")
    cursor.execute("select bundle from public.get_latest_public_demo()")
    public_bundle = cursor.fetchone()[0]
    cursor.execute("reset role")
    check(
        "the narrow reader returns the reviewed public bundle",
        public_bundle["deep_analysis"]["ticker"] == "WAY",
    )

    missing_mentions = copy.deepcopy(bundle)
    del missing_mentions["scan"]["validated_rows"][0]["Mentions"]
    check(
        "every scan row must contain Social posts",
        rejected(cursor, missing_mentions),
    )

    fractional_mentions = copy.deepcopy(bundle)
    fractional_mentions["scan"]["validated_rows"][0]["Mentions"] = 1.5
    check(
        "Social posts must be a non-negative whole number",
        rejected(cursor, fractional_mentions),
    )

    wrong_ticker = copy.deepcopy(bundle)
    wrong_ticker["deep_analysis"]["ticker"] = "NVDA"
    check(
        "the analyzed ticker must belong to the published scan",
        rejected(cursor, wrong_ticker),
    )

    wrong_sector = copy.deepcopy(bundle)
    wrong_sector["deep_analysis"]["sector"] = "finance"
    check("scan and analysis sectors must match", rejected(cursor, wrong_sector))

    empty_analysis = copy.deepcopy(bundle)
    empty_analysis["deep_analysis"]["analysis_results"] = {}
    check("analysis results cannot be empty", rejected(cursor, empty_analysis))

    check("unsupported schema versions are rejected", rejected(cursor, bundle, 2))


def test_history_and_rerun(cursor) -> None:
    print("\nhistory: publications append and the migration is rerunnable")
    second = valid_bundle()
    second["scan"]["validated_rows"][0]["Mentions"] = 22
    insert(cursor, second)
    cursor.execute("select count(*) from public.public_demo_snapshots")
    check("publishing retains the previous version", cursor.fetchone()[0] == 2)

    try:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(READER_MIGRATION.read_text(encoding="utf-8"))
        reran = True
    except psycopg2.Error:
        reran = False
    check("the migration can be safely rerun", reran)


def main() -> int:
    print("=" * 74)
    print("  public_demo_snapshots: persistence, privacy, and bundle integrity")
    print("=" * 74)

    try:
        connection = connect()
    except psycopg2.OperationalError as exc:
        print(f"\nCannot reach the test database: {exc}")
        print("Start it with: docker compose -f docker-compose.test.yml up -d")
        return 2

    cursor = connection.cursor()
    rebuild(cursor)
    test_schema_and_security(cursor)
    test_bundle_constraints(cursor)
    test_history_and_rerun(cursor)
    connection.close()

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
