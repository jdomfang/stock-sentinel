#!/usr/bin/env python3
"""Prove the shared X corpus cache is private, content-addressed, and safe to fail.

WHAT IS ACTUALLY AT RISK HERE

1. PRIVACY. x_corpus_cache holds the paid product's raw inputs. A single
   permissive RLS policy would let any signed-up user select every sector's
   tweet corpus without spending a credit -- which is the product, given away.
   ticker_master grants public read and that is correct for ticker_master; the
   same pattern here would be a giveaway. So "RLS is enabled" is not the
   assertion that matters. "There are ZERO policies" is.

2. DRIFT. The key hashes the query text. deep_analysis.py uses a hand-bumped
   CACHE_VERSION instead, which means the first person to edit a query and
   forget the bump silently serves corpora built by the old query -- for six
   hours, to everyone. These tests pin the property that a changed query can
   never collide with the old one.

3. FAILING OPEN. A cache is an optimisation. If it breaks, a scan must still
   run and return the right answer -- expensively, but correctly. Every entry
   point is asserted to swallow its errors and report a miss rather than raise
   into a paid code path.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_corpus_cache.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed. pip install psycopg2-binary")
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import corpus_cache  # noqa: E402

DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.migrations import chain as _chain  # noqa: E402

# Discovered, not listed. The list this replaces claimed to be "the full
# production chain, in production order" and had not been so for some time --
# it stopped at 20260805010000 and therefore installed the OLD credit
# functions, passing 31/31 while proving nothing about them.
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


def rebuild(cur) -> None:
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    for p in MIGRATIONS:
        cur.execute(p.read_text())


def rejects(cur, sql: str, params: tuple) -> bool:
    """True if the statement is refused by the database."""
    try:
        cur.execute(sql, params)
        return False
    except psycopg2.Error:
        return True


INSERT = """
insert into public.x_corpus_cache
  (cache_key, kind, subject, timeframe_h, query_hash, query_text,
   tweets, tweet_count, pages_fetched, expires_at, hard_expires_at)
values (%s, %s, %s, %s, 'deadbeef1234', 'q', '[]'::jsonb, %s, 1,
        now() + interval '6 hours', now() + interval '24 hours')
"""


# ── privacy ──────────────────────────────────────────────────────────────────

def test_the_corpus_is_not_readable_by_users(cur):
    print("\nprivacy: the paid product's inputs are service-role only")

    cur.execute("select relrowsecurity from pg_class where oid='public.x_corpus_cache'::regclass")
    check("RLS is enabled", cur.fetchone()[0] is True)

    # The assertion that actually protects the product. RLS with no policies
    # denies everything to anon and authenticated; the service role bypasses RLS
    # entirely, so the app still works. Any policy added here is a giveaway.
    cur.execute("select count(*), coalesce(string_agg(policyname, ', '), '')"
                " from pg_policies where tablename='x_corpus_cache'")
    n, names = cur.fetchone()
    check("zero RLS policies exist", n == 0, f"found {n}: {names}")

    # ticker_master is public-read on purpose. Prove we did not copy it.
    cur.execute("select count(*) from pg_policies where tablename='ticker_master'")
    tm = cur.fetchone()[0]
    check("did not inherit ticker_master's public-read pattern",
          tm == 0 or n == 0, "corpus cache must not be readable like ticker_master")


# ── the table's own guarantees ───────────────────────────────────────────────

def test_the_table_refuses_incoherent_rows(cur):
    print("\nintegrity: the table rejects rows that would mislead a reader")

    check("a well-formed row is accepted",
          not rejects(cur, INSERT, ("sector:x:24h:d", "sector", "x", 24, 0)))

    check("an unknown kind is rejected",
          rejects(cur, INSERT, ("k:y:24h:d", "obviously_wrong", "y", 24, 0)))

    check("a negative tweet_count is rejected",
          rejects(cur, INSERT, ("sector:z:24h:d", "sector", "z", 24, -1)))

    check("a non-positive timeframe is rejected",
          rejects(cur, INSERT, ("sector:w:24h:d", "sector", "w", 0, 0)))

    # If hard expiry preceded soft expiry there would be a window where a row is
    # simultaneously "refetch me" and "too old to use" -- the stale-serve path
    # would then be unreachable and an X outage would hard-fail instead of
    # degrading.
    check("hard expiry before soft expiry is rejected", rejects(cur, """
        insert into public.x_corpus_cache
          (cache_key, kind, subject, timeframe_h, query_hash, query_text,
           tweets, tweet_count, expires_at, hard_expires_at)
        values (%s,'sector','v',24,'d','q','[]'::jsonb,0,
                now() + interval '24 hours', now() + interval '1 hour')
    """, ("sector:v:24h:d",)))


def test_a_refetch_replaces_rather_than_accumulates(cur):
    print("\nintegrity: re-storing a corpus replaces it")
    cur.execute("delete from public.x_corpus_cache")
    cur.execute(INSERT, ("sector:dup:24h:d", "sector", "dup", 24, 5))
    # cache_key is the primary key, so the upsert the module sends
    # (resolution=merge-duplicates) updates in place. Without that, six hours of
    # refetches would pile up copies of the same sector.
    cur.execute("""
        insert into public.x_corpus_cache
          (cache_key, kind, subject, timeframe_h, query_hash, query_text,
           tweets, tweet_count, expires_at, hard_expires_at)
        values ('sector:dup:24h:d','sector','dup',24,'d','q','[]'::jsonb,9,
                now() + interval '6 hours', now() + interval '24 hours')
        on conflict (cache_key) do update set tweet_count = excluded.tweet_count
    """)
    cur.execute("select count(*), max(tweet_count) from public.x_corpus_cache"
                " where cache_key='sector:dup:24h:d'")
    rows, count = cur.fetchone()
    check("one row per cache key", rows == 1, f"got {rows}")
    check("the newer corpus wins", count == 9, f"got {count}")


def test_the_sweeper_has_an_index(cur):
    print("\nintegrity: expiry sweeps do not seq-scan")
    cur.execute("select indexdef from pg_indexes where tablename='x_corpus_cache'")
    defs = " ".join(r[0] for r in cur.fetchall())
    check("hard_expires_at is indexed", "hard_expires_at" in defs)


# ── the key is content-addressed ─────────────────────────────────────────────

def test_the_key_cannot_drift_from_its_query():
    print("\nkeys: a changed query can never reuse the old corpus")
    q1 = "(aerospace OR defense) (stock OR stocks) lang:en -is:retweet"
    q2 = "(aerospace OR defense OR machinery) (stock OR stocks) lang:en -is:retweet"

    k1 = corpus_cache.make_key("sector", "industrials", 24, q1)
    check("the key is deterministic",
          k1 == corpus_cache.make_key("sector", "industrials", 24, q1))

    # The whole point. deep_analysis.py's hand-bumped CACHE_VERSION would serve
    # q1's corpus for q2 until someone remembered to edit a constant.
    check("editing the query changes the key",
          k1 != corpus_cache.make_key("sector", "industrials", 24, q2))

    check("even a whitespace-only edit changes the key",
          k1 != corpus_cache.make_key("sector", "industrials", 24, q1 + " "))

    check("the timeframe is part of the key",
          k1 != corpus_cache.make_key("sector", "industrials", 48, q1))

    check("the kind is part of the key",
          k1 != corpus_cache.make_key("ticker", "industrials", 24, q1))

    # Subject is normalised so "Industrials" and "industrials" share a corpus
    # rather than paying twice for the same sector.
    check("the subject is case-insensitive",
          k1 == corpus_cache.make_key("sector", "  INDUSTRIALS ", 24, q1))

    check("the key is the documented shape",
          k1.startswith("sector:industrials:24h:") and len(k1.rsplit(":", 1)[-1]) == 12, k1)


# ── failing open ─────────────────────────────────────────────────────────────

def test_a_broken_cache_never_breaks_a_paid_scan():
    print("\nfailure policy: a cache fault degrades to a miss, never an exception")
    original = corpus_cache._config
    # Simulate "no credentials anywhere". The repo has a real
    # .streamlit/secrets.toml, so without this the module would happily reach
    # production and these assertions would test nothing.
    corpus_cache._config = lambda name, default="": ""
    try:
        check("get() returns a miss instead of raising",
              corpus_cache.get("sector", "industrials", 24, "q") is None)
        check("put() reports failure instead of raising",
              corpus_cache.put("sector", "industrials", 24, "q", []) is False)
        check("sweep() reports failure instead of raising",
              corpus_cache.sweep() == -1)
    finally:
        corpus_cache._config = original

    # An unreachable host must behave identically to an unconfigured one.
    corpus_cache._config = lambda name, default="": (
        "http://127.0.0.1:9" if name == "SUPABASE_URL" else "not-a-real-key"
    )
    try:
        check("an unreachable Supabase is a miss, not an exception",
              corpus_cache.get("sector", "industrials", 24, "q") is None)
        check("an unreachable Supabase fails the write quietly",
              corpus_cache.put("sector", "industrials", 24, "q", []) is False)
    finally:
        corpus_cache._config = original


def test_replaying_a_corpus_reproduces_the_original_pages():
    print("\nreplay: a cached corpus is served back as the pages it was bought as")
    corpus = [{"id": str(i)} for i in range(178)]  # the observed ACHR corpus size

    pages = corpus_cache.chunk_pages(corpus, 100)
    check("page count matches what was fetched", len(pages) == 2, f"got {len(pages)}")
    check("full pages are full", len(pages[0]) == 100, f"got {len(pages[0])}")
    check("the last page holds the remainder", len(pages[1]) == 78, f"got {len(pages[1])}")

    # The property that matters: replay must lose nothing and reorder nothing,
    # or a cache hit silently returns a different shortlist than the scan the
    # user paid for.
    check("no post is lost or reordered",
          [t for pg in pages for t in pg] == corpus)

    # Callers loop "fetch a page, break if empty". Zero pages would spin.
    check("an empty corpus replays as one empty page",
          corpus_cache.chunk_pages([], 100) == [[]])

    check("a single short page stays one page",
          len(corpus_cache.chunk_pages(corpus[:40], 100)) == 1)

    # An exact multiple must not produce a trailing empty page, which would cost
    # the caller an extra loop iteration and, on a miss, a real X request.
    exact = [{"id": str(i)} for i in range(200)]
    check("an exact multiple yields no trailing empty page",
          [len(p) for p in corpus_cache.chunk_pages(exact, 100)] == [100, 100],
          str([len(p) for p in corpus_cache.chunk_pages(exact, 100)]))


def test_an_empty_corpus_is_storable():
    print("\nnegative caching: 'no chatter' is an answer worth keeping")
    # The ACHR influencer query returned zero posts. Without a stored empty,
    # every subsequent user pays again to learn the same nothing. The table must
    # accept tweet_count = 0, and put() must not treat [] as a no-op.
    c = conn(); cur = c.cursor()
    cur.execute("delete from public.x_corpus_cache")
    cur.execute(INSERT, ("sector:empty:24h:d", "sector", "empty", 24, 0))
    cur.execute("select tweet_count from public.x_corpus_cache where cache_key='sector:empty:24h:d'")
    check("an empty corpus is a storable row", cur.fetchone()[0] == 0)
    c.close()


def main() -> int:
    print("=" * 74)
    print("  x_corpus_cache: privacy, content-addressing, and failing open")
    print("=" * 74)

    try:
        c = conn()
    except psycopg2.OperationalError as e:
        print(f"\nCannot reach the test database: {e}")
        print("Start it with: docker compose -f docker-compose.test.yml up -d")
        return 2

    cur = c.cursor()
    rebuild(cur)

    test_the_corpus_is_not_readable_by_users(cur)
    test_the_table_refuses_incoherent_rows(cur)
    test_a_refetch_replaces_rather_than_accumulates(cur)
    test_the_sweeper_has_an_index(cur)
    c.close()

    test_the_key_cannot_drift_from_its_query()
    test_replaying_a_corpus_reproduces_the_original_pages()
    test_a_broken_cache_never_breaks_a_paid_scan()
    test_an_empty_corpus_is_storable()

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
