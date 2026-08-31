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
V2_MIGRATION = (
    REPO
    / "supabase"
    / "migrations"
    / "20260830010000_demo_snapshot_v2.sql"
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
                    "Company Name": "Waystar Holding Corp.",
                    "Overall Sentiment": "Neutral",
                    "Mentions": 21,
                    "Evidence": 2,
                    "Avg Sentiment Score": 0.06,
                },
                {
                    "Ticker": "SATA",
                    "Overall Sentiment": "Neutral",
                    "Mentions": 12,
                    "Evidence": 1,
                    "Avg Sentiment Score": 0.01,
                },
            ],
            "unvalidated_rows": [],
        },
        "deep_analysis": {
            "ticker": "WAY",
            "sector": "tech",
            "generated_at": "2026-08-28T21:01:00Z",
            "public_card": {
                "ticker": "WAY",
                "sector": "tech",
                "verdict": "Watch",
                "confidence": "Moderate",
                "avg_sentiment": 0.06,
                "reason": "Evidence is mixed.",
                "tiles": [{
                    "key": "range_30d",
                    "label": "30d range (vol)",
                    "value": "-8.0% to 9.0%",
                }],
                "evidence": {
                    "independent_voices": 2,
                    "mentions": 21,
                    "price_points": 25,
                },
                "movement": {"band_pct": 8.5, "horizon_days": 10},
            },
        },
    }


def valid_source() -> dict:
    return {
        "scan": {
            "sector": "tech",
            "generated_at": "2026-08-28T21:00:00Z",
            "rows": [
                {
                    "Ticker": "WAY",
                    "Overall Sentiment": "Neutral",
                    "Mentions": 21,
                    "Evidence": 2,
                    "Avg Sentiment Score": 0.06,
                    "Sample Tweets": ["private post"],
                },
                {
                    "Ticker": "SATA",
                    "Overall Sentiment": "Neutral",
                    "Mentions": 12,
                    "Evidence": 1,
                    "Avg Sentiment Score": 0.01,
                },
            ],
            "metadata": {"posts_seen": 42},
        },
        "deep_analysis": {
            "ticker": "WAY",
            "sector": "tech",
            "generated_at": "2026-08-28T21:01:00Z",
            "card": {
                "ticker": "WAY",
                "sector": "tech",
                "verdict": "Watch",
                "confidence": "Moderate",
                "avg_sentiment": 0.06,
                "reason": "Evidence is mixed.",
                "pillars": [{"name": "quality", "passed": True}],
                "tiles": [{
                    "key": "range_30d",
                    "label": "30d range (vol)",
                    "value": "-8.0% to 9.0%",
                }],
                "evidence": {
                    "independent_voices": 2,
                    "mentions": 21,
                    "price_points": 25,
                },
                "movement": {"band_pct": 8.5, "horizon_days": 10},
            },
            "analysis_results": {"raw": {"tweet_ids": ["123"]}},
            "metadata": {"elapsed_s": 4.2},
        },
    }


def insert(
    cursor, bundle: dict, source: dict | None = None, version: int = 2
) -> None:
    cursor.execute(
        """
        insert into public.public_demo_snapshots
            (schema_version, bundle, source_payload, published_by)
        values (%s, %s::jsonb, %s::jsonb, %s::uuid)
        """,
        (
            version,
            json.dumps(bundle),
            json.dumps(source) if source is not None else None,
            PUBLISHER,
        ),
    )


def rejected(
    cursor, bundle: dict, source: dict | None = None, version: int = 2
) -> bool:
    try:
        insert(cursor, bundle, source, version)
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
    source = valid_source()
    cursor.execute(
        "select public.is_coherent_demo_snapshot_v2('{}'::jsonb, '{}'::jsonb)"
    )
    check(
        "the coherence helper rejects empty payloads directly",
        cursor.fetchone()[0] is False,
    )
    insert(cursor, bundle, source)
    check("a coherent scan and analysis bundle is accepted", True)

    cursor.execute("set role anon")
    cursor.execute("select bundle from public.get_latest_public_demo()")
    public_bundle = cursor.fetchone()[0]
    cursor.execute("reset role")
    check(
        "the narrow reader returns the reviewed public bundle",
        public_bundle["deep_analysis"]["ticker"] == "WAY",
    )
    check(
        "the narrow reader never exposes the private source payload",
        "source_payload" not in public_bundle,
    )

    missing_mentions = copy.deepcopy(bundle)
    del missing_mentions["scan"]["validated_rows"][0]["Mentions"]
    check(
        "every scan row must contain Social posts",
        rejected(cursor, missing_mentions, source),
    )

    fractional_mentions = copy.deepcopy(bundle)
    fractional_mentions["scan"]["validated_rows"][0]["Mentions"] = 1.5
    check(
        "Social posts must be a non-negative whole number",
        rejected(cursor, fractional_mentions, source),
    )

    wrong_ticker = copy.deepcopy(bundle)
    wrong_ticker["deep_analysis"]["ticker"] = "NVDA"
    check(
        "the analyzed ticker must belong to the published scan",
        rejected(cursor, wrong_ticker, source),
    )

    wrong_sector = copy.deepcopy(bundle)
    wrong_sector["deep_analysis"]["sector"] = "finance"
    check(
        "scan and analysis sectors must match",
        rejected(cursor, wrong_sector, source),
    )

    missing_reason = copy.deepcopy(bundle)
    missing_reason["deep_analysis"]["public_card"]["reason"] = ""
    check(
        "the public recommendation reason is required",
        rejected(cursor, missing_reason, source),
    )

    check(
        "v2 requires a complete private source",
        rejected(cursor, bundle, None),
    )

    wrong_source = copy.deepcopy(source)
    wrong_source["deep_analysis"]["ticker"] = "NVDA"
    check(
        "private scan and analysis must be coherent",
        rejected(cursor, bundle, wrong_source),
    )

    check(
        "unsupported schema versions are rejected",
        rejected(cursor, bundle, source, 3),
    )


def test_history_and_rerun(cursor) -> None:
    print("\nhistory: publications append and the migration is rerunnable")
    second = valid_bundle()
    second["scan"]["validated_rows"][0]["Mentions"] = 22
    second_source = valid_source()
    second_source["scan"]["rows"][0]["Mentions"] = 22
    insert(cursor, second, second_source)
    cursor.execute("select count(*) from public.public_demo_snapshots")
    check("publishing retains the previous version", cursor.fetchone()[0] == 2)

    try:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(READER_MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(V2_MIGRATION.read_text(encoding="utf-8"))
        reran = True
    except psycopg2.Error:
        reran = False
    check("the migration can be safely rerun", reran)


def test_optional_migration_ledger(cursor) -> None:
    print("\nportability: production does not need the optional migration ledger")
    cursor.execute(
        "drop schema if exists public cascade; create schema public; "
        "drop schema if exists auth cascade;"
    )
    for migration in migration_chain():
        if migration.name == "20260829010000_schema_migrations.sql":
            continue
        cursor.execute(migration.read_text(encoding="utf-8"))

    cursor.execute("select to_regclass('public.schema_migrations')")
    check(
        "schema v2 installs when schema_migrations is absent",
        cursor.fetchone()[0] is None,
    )
    cursor.execute(
        "select exists ("
        "select 1 from information_schema.columns "
        "where table_schema='public' "
        "and table_name='public_demo_snapshots' "
        "and column_name='source_payload')"
    )
    check("the private source column is still installed", cursor.fetchone()[0])


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
    test_optional_migration_ledger(cursor)
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
