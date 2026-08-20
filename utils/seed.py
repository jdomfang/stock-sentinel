"""Reuse posts a recent sector scan already paid for. Standard library only.

WHY THIS IS WORTH DOING

The two features are independent in data: Deep Analyze always runs its own
retrieval and never consults a scan. That is correct, and it leaves value on
the floor.

The measured reason is not "stop paying twice" -- the duplicate spend is a
handful of posts. It is that DIFFERENT QUERIES REACH DISJOINT PARTS OF THE
INDEX. Two arms run against the same ticker in the same 48-hour window shared
ZERO posts out of 198. So a sector corpus does not merely duplicate what Deep
Analyze would buy; it holds evidence its own query will probably never find.

For a thin ticker that matters. The materials scan retained five usable-shaped
posts about $MP against a Deep Analyze corpus of roughly fourteen.

WHY IT IS A SEPARATE CHANNEL, NEVER MERGED

A basket query matches any of ~55 cashtags, so it over-selects multi-ticker
list posts -- structurally biased against exactly the subject test the ledger
applies. The design therefore admits it as its own channel that may lift
confidence but may never, alone, produce a Buy or an Avoid. The verdict
enforces that; this module only supplies posts.

FAILURE POLICY

Never raises. No seed is the normal case, not an error.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")

# A scan older than this describes a different market. The corpus cache keeps
# sector entries for 24h, so this is the useful ceiling rather than an
# arbitrary tightening -- a stricter window would simply discard free evidence.
MAX_AGE_HOURS = 24

# Posts naming many tickers are what a basket query over-selects. The ledger
# would reject them on subject status anyway; dropping them here keeps the
# channel small and its provenance honest.
MAX_CASHTAGS = 3


# Was a private copy of the same twelve lines in ten modules. Reaching into
# streamlit from analysis code is what kept this file inside the portal.
from utils.config import get as _config  # noqa: E402

def _get(path: str, timeout: int = 15) -> list[dict]:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return []
    req = urllib.request.Request(
        f"{base}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"[]")


def _to_ui_slug(nasdaq_sector: str) -> str:
    """Nasdaq sector name -> Discovery's UI slug.

    TWO VOCABULARIES. ticker_master.sector holds Nasdaq's names ("Basic
    Materials", "Health Care", "Consumer Discretionary"); x_corpus_cache.subject
    holds the slug Discovery scans under ("materials", "healthcare",
    "consumer"). Querying one with the other matches nothing, silently, and it
    killed this channel for half the sectors -- including the $MP materials case
    used to justify building it.
    """
    from utils.sector_query import UI_TO_NASDAQ

    want = (nasdaq_sector or "").strip().lower()
    if not want:
        return ""
    for slug, names in UI_TO_NASDAQ.items():
        if any(want == str(n).strip().lower() for n in names):
            return slug
    return ""


def sector_of(ticker: str) -> str:
    """The ticker's sector as a Discovery SLUG, so the seed works when typed.

    pages/Deep_Analysis.py hardcodes sector="unknown" because the analysis does
    not need one, so every typed lookup comes through here.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    try:
        qs = urllib.parse.urlencode({"select": "sector", "symbol": f"eq.{t}", "limit": 1})
        rows = _get(f"ticker_master?{qs}", timeout=8)
        if not rows:
            return ""
        raw = str(rows[0].get("sector") or "")
        slug = _to_ui_slug(raw)
        if not slug:
            logger.info("seed: %s sector %r has no Discovery slug", t, raw)
        return slug
    except Exception as e:
        logger.warning("seed: sector lookup failed for %s: %s", t, type(e).__name__)
        return ""


def fetch(ticker: str, sector: str = "", *, max_age_hours: int = MAX_AGE_HOURS,
          exclude_ids: set[str] | None = None) -> list[dict]:
    """Posts about `ticker` from the most recent scan of its sector.

    Returns raw post dicts for the ledger, or [] -- which is the ordinary case
    and never an error.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return []
    from utils.sector_query import UI_TO_NASDAQ

    sec = (sector or "").strip().lower()
    if sec and sec != "unknown" and sec not in UI_TO_NASDAQ:
        # A caller handed us a Nasdaq name rather than a slug.
        sec = _to_ui_slug(sec)
    if not sec or sec == "unknown":
        sec = (sector_of(t) or "").strip().lower()
    if not sec:
        return []

    try:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=max_age_hours)).isoformat()
        qs = urllib.parse.urlencode({
            "select": "tweets,fetched_at,subject",
            "kind": "eq.sector",
            "subject": f"eq.{sec}",
            "fetched_at": f"gte.{cutoff}",
            # The cache's OWN view of death. Querying fetched_at alone served
            # rows that corpus_cache.get() refuses outright.
            "hard_expires_at": f"gt.{datetime.now(timezone.utc).isoformat()}",
            "order": "fetched_at.desc",
            "limit": 1,
        })
        rows = _get(f"x_corpus_cache?{qs}")
    except Exception as e:
        logger.warning("seed: corpus lookup failed for %s/%s: %s: %s",
                       t, sec, type(e).__name__, str(e)[:120])
        return []

    if not rows:
        return []

    raw = rows[0].get("tweets")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []

    skip = exclude_ids or set()
    out: list[dict] = []
    for post in raw:
        if not isinstance(post, dict):
            continue
        text = str(post.get("text") or "")
        tags = {m.upper() for m in CASHTAG.findall(text)}
        # The target cashtag must be present. A bare mention is the
        # Members-of-Parliament failure, and on this channel -- already biased
        # by a basket query -- there is no reason to accept one.
        if t not in tags or len(tags) > MAX_CASHTAGS:
            continue
        pid = post.get("id")
        if pid is not None and str(pid) in skip:
            continue
        out.append(post)

    if out:
        logger.info("🌱 seed: %d post(s) about %s reused from the %s scan of %s",
                    len(out), t, rows[0].get("subject"), str(rows[0].get("fetched_at"))[:16])
    return out
