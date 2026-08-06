"""Shared cache for raw X (Twitter) corpora. Standard library only.

WHY THIS EXISTS

X bills per POST RETURNED, not per request, so the cost of a feature is the
number of posts it pulls. A Discovery scan pulls ~99 to display ten tickers,
and pages/Discovery.py caches none of it: the same sector scanned twice in a
minute is bought twice.

A scan is not user-specific -- it is a function of (sector, time window), and
there are exactly TEN sectors. Caching that turns the scan feature's bill from
"grows with every user" into "at most 10 corpora per TTL window, forever".
That is a change in shape, not a discount.

WHY STANDARD LIBRARY ONLY

Same reason as utils/prices.py: the Railway worker sweeps this table on its
cron tick, and the worker image has no Streamlit, no numpy and no Supabase SDK.
urllib and PostgREST work from both the portal and a bare python:3.11-slim
container, so one implementation serves both. A second copy in the worker is
exactly the drift that caused the six-month price-sync outage.

FAILURE POLICY -- THE IMPORTANT PART

Nothing here may raise into a caller. A cache is an optimisation; a broken one
must degrade to "always miss", which costs money but still returns the right
answer. Breaking a paid scan because a cache lookup failed would turn a saving
into an outage.

But silence is the other failure. This codebase has been bitten repeatedly by
components that degraded without erroring -- a nightly sync that failed 43
times unnoticed, an inference service running 40x slow with every health check
green. So every swallowed error is logged at WARNING with its type, and
callers get cache_hit/corpus_age telemetry to record in the ledger. A cache
that has silently stopped working looks exactly like a cache that is working
until someone reads the X bill.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "x_corpus_cache"

# Per-kind freshness. These are defaults; callers may override.
#
# Sector corpora get 6h because the scan analyses a 24-HOUR window of tweets --
# six hours of drift moves a minority of a day-long corpus, and the ceiling
# falls 6x versus an hourly TTL (40 refetches/day across 10 sectors instead of
# 240). The honest cost is a catalyst: after surprise earnings, scans keep
# showing pre-event sentiment until the entry expires. That is why callers are
# handed `age_s` and are expected to render it.
DEFAULT_TTL_S = {
    "sector": 6 * 60 * 60,
    "ticker": 30 * 60,
    "influencer": 6 * 60 * 60,
}

# How long past expiry a corpus may still be served when X itself is failing.
# Between expires_at and hard_expires_at the answer is "stale but labelled";
# past hard_expires_at there is no answer at all, which is the correct outcome.
DEFAULT_HARD_TTL_S = {
    "sector": 24 * 60 * 60,
    "ticker": 4 * 60 * 60,
    "influencer": 24 * 60 * 60,
}


def _config(name: str, default: str = "") -> str:
    """Environment first, then Streamlit secrets if Streamlit happens to exist."""
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


def _endpoint() -> tuple[str, str] | None:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        # Not configured is a miss, not a crash. Local dev without a service
        # role key should still be able to run a scan.
        logger.warning("corpus_cache: SUPABASE_URL/SERVICE_ROLE_KEY not set; caching disabled")
        return None
    return base, key


def _headers(key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def make_key(kind: str, subject: str, timeframe_h: int, query: str) -> str:
    """Content-addressed cache key.

    The query text is hashed into the key rather than versioned by hand. The
    existing cache in deep_analysis.py uses CACHE_VERSION = "v3", which means
    the first person to edit a query without remembering to bump the constant
    silently serves corpora built by the previous query. A hash cannot drift
    from the thing it names.

    `subject` must NOT carry anything the query does not. Ticker corpora, for
    instance, are built from the ticker alone, so folding `sector` into their
    key would split one corpus into N identical copies -- and worse, the two
    call sites disagree about sector already (pages/Deep_Analysis.py hardcodes
    "unknown" while Discovery passes the real one).
    """
    qh = hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{(subject or '').strip().lower()}:{int(timeframe_h)}h:{qh}"


def get(
    kind: str,
    subject: str,
    timeframe_h: int,
    query: str,
    allow_stale: bool = False,
) -> dict[str, Any] | None:
    """Return a cached corpus, or None on any miss, error or misconfiguration.

    With allow_stale=False (the default) an entry past expires_at is a miss.
    Pass allow_stale=True only on a live X failure, where serving a labelled
    older corpus beats serving nothing -- and only ever with the age shown.

    Returns {tweets, tweet_count, pages_fetched, stop_reason, age_s, stale}.
    """
    ep = _endpoint()
    if ep is None:
        return None
    base, key = ep

    cache_key = make_key(kind, subject, timeframe_h, query)
    qs = urllib.parse.urlencode({"cache_key": f"eq.{cache_key}", "select": "*", "limit": 1})
    req = urllib.request.Request(f"{base}/rest/v1/{TABLE}?{qs}", headers=_headers(key))

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read() or b"[]")
    except Exception as e:
        logger.warning("corpus_cache get failed (%s): %s: %s",
                       cache_key, type(e).__name__, str(e)[:200])
        return None

    if not rows:
        logger.info("corpus_cache MISS %s", cache_key)
        return None

    row = rows[0]
    now = datetime.now(timezone.utc)

    try:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        expires_at = datetime.fromisoformat(row["expires_at"])
        hard_expires_at = datetime.fromisoformat(row["hard_expires_at"])
    except Exception as e:
        logger.warning("corpus_cache: unparseable timestamps on %s: %s", cache_key, e)
        return None

    age_s = (now - fetched_at).total_seconds()

    if now >= hard_expires_at:
        # Past the point where this is an answer at all.
        logger.info("corpus_cache EXPIRED %s (age %.0fs)", cache_key, age_s)
        return None

    stale = now >= expires_at
    if stale and not allow_stale:
        logger.info("corpus_cache STALE %s (age %.0fs); refetching", cache_key, age_s)
        return None

    tweets = row.get("tweets") or []
    if not isinstance(tweets, list):
        logger.warning("corpus_cache: %s holds non-list tweets; ignoring", cache_key)
        return None

    logger.info(
        "corpus_cache HIT %s (%d posts, age %.0fs%s) -- 0 posts billed",
        cache_key, len(tweets), age_s, ", STALE" if stale else "",
    )
    return {
        "tweets": tweets,
        "tweet_count": int(row.get("tweet_count") or len(tweets)),
        "pages_fetched": int(row.get("pages_fetched") or 0),
        "stop_reason": row.get("stop_reason"),
        "age_s": age_s,
        "stale": stale,
        "fetched_at": fetched_at,
    }


def put(
    kind: str,
    subject: str,
    timeframe_h: int,
    query: str,
    tweets: list[dict],
    pages_fetched: int = 0,
    stop_reason: str | None = None,
    ttl_s: int | None = None,
    hard_ttl_s: int | None = None,
) -> bool:
    """Store a corpus. Returns True on success; never raises.

    An EMPTY corpus is cached too, deliberately. "This query returned nothing"
    is a real and repeatable answer that cost real money to learn -- the
    influencer query for ACHR returned zero posts, and without a negative entry
    the next user pays to learn the same thing. `tweet_count = 0` distinguishes
    a cached empty from an absent key.
    """
    ep = _endpoint()
    if ep is None:
        return False
    base, key = ep

    ttl = DEFAULT_TTL_S.get(kind, 30 * 60) if ttl_s is None else int(ttl_s)
    hard = DEFAULT_HARD_TTL_S.get(kind, 4 * 60 * 60) if hard_ttl_s is None else int(hard_ttl_s)
    if hard < ttl:
        hard = ttl  # the table's CHECK would reject the row otherwise

    now = datetime.now(timezone.utc)
    cache_key = make_key(kind, subject, timeframe_h, query)
    row = {
        "cache_key": cache_key,
        "kind": kind,
        "subject": (subject or "").strip().lower(),
        "timeframe_h": int(timeframe_h),
        "query_hash": cache_key.rsplit(":", 1)[-1],
        "query_text": query,
        "tweets": tweets,
        "tweet_count": len(tweets),
        "pages_fetched": int(pages_fetched),
        "stop_reason": stop_reason,
        "fetched_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "hard_expires_at": (now + timedelta(seconds=hard)).isoformat(),
    }

    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}",
        data=json.dumps(row).encode(),
        headers=_headers(key, {"Prefer": "resolution=merge-duplicates,return=minimal"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status not in (200, 201, 204):
                logger.warning("corpus_cache put HTTP %s for %s", r.status, cache_key)
                return False
    except urllib.error.HTTPError as e:
        body = (e.read() or b"")[:200].decode(errors="replace")
        logger.warning("corpus_cache put HTTP %s for %s: %s", e.code, cache_key, body)
        return False
    except Exception as e:
        logger.warning("corpus_cache put failed (%s): %s: %s",
                       cache_key, type(e).__name__, str(e)[:200])
        return False

    logger.info("corpus_cache STORE %s (%d posts, ttl %ds)", cache_key, len(tweets), ttl)
    return True


def sweep(grace_hours: int = 24) -> int:
    """Delete rows well past hard expiry. Returns rows deleted, or -1 on error.

    Bounded growth is not automatic even though there are only ten sectors:
    the key is content-addressed, so every query edit strands the corpora built
    by the previous wording. Without a sweep those accumulate forever.

    The grace period is deliberate -- an entry stays briefly past hard expiry so
    that an X outage spanning the boundary still has something to serve.
    """
    ep = _endpoint()
    if ep is None:
        return -1
    base, key = ep

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=grace_hours)).isoformat()
    qs = urllib.parse.urlencode({"hard_expires_at": f"lt.{cutoff}"})
    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}?{qs}",
        headers=_headers(key, {"Prefer": "return=representation"}),
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            deleted = json.loads(r.read() or b"[]")
    except Exception as e:
        logger.warning("corpus_cache sweep failed: %s: %s", type(e).__name__, str(e)[:200])
        return -1

    n = len(deleted) if isinstance(deleted, list) else 0
    logger.info("corpus_cache sweep: removed %d expired corpora", n)
    return n
