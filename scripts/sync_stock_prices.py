#!/usr/bin/env python3
"""
Sync last-close prices for the whole US market from Polygon to Supabase.

Runs nightly at 01:00 UTC, which is the previous evening in New York -- after
the 16:00 ET close.

Usage:
  python3 scripts/sync_stock_prices.py

WHAT CHANGED, AND WHY THE OLD SHAPE COULD NOT WORK

This used to walk a list of tickers and ask Polygon about each one. At the free
tier's 5 requests/minute that is ~12 seconds per ticker, so covering the
7,065-symbol universe would have taken 23.5 HOURS. A nightly full sync was not
slow -- it was arithmetically impossible.

So the job settled for the first 500 it could reach, and the selection was
worse than the cap: TOP_500_TICKERS was a hand-written list that had gone stale
(it contained "TripAdvisor" and "SEMITECH", which are not tickers, "ASANA"
instead of ASAN, AAPL and RBLX twice, and MXIM/SPLK/JCOM which were delisted or
renamed years ago). Entries that did not exist were silently dropped, and the
remainder of the 500 was filled from data/tickers.json IN FILE ORDER -- which is
alphabetical. The measured result was 643 tickers cached, 387 of them beginning
with the letter "A".

Polygon's grouped daily bars endpoint returns every US ticker's close for one
date in a SINGLE request. Measured against the production key on 2026-08-05:
1 call, 0.5 seconds, 12,408 tickers, covering 6,007 of ticker_master (85%).

That is the same free tier. The old approach was not limited by the plan; it
was limited by asking the wrong question 500 times.

NO FALLBACK TO THE PER-TICKER PATH -- ON PURPOSE

An earlier draft kept the old loop as a fallback. That is worse than useless
here: both paths call the same API with the same key, so anything that breaks
grouped breaks per-ticker too -- except per-ticker would spend 100 minutes
discovering it, then report a 9%-coverage run as a success. Falling back to a
quietly-degraded result is the failure mode this repo keeps paying for. A
failure here is loud and the dead-man switch goes red.

fetch_and_cache_last_close_prices is NOT deleted: Discovery still uses it
interactively when a scan hits a ticker the cache does not have.
"""

import os
import sys
import logging

# Add parent dir to path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prices import fetch_and_cache_grouped_daily

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def sync_prices(max_lookback_days: int = 7) -> bool:
    """Fetch the last trading day's closes for the whole market and cache them.

    Returns True only if prices were actually written. Every failure path used
    to `return` bare, so the process exited 0 and cron recorded a success --
    43 consecutive failures went unnoticed for six months. The caller turns
    False into a non-zero exit.
    """
    logger.info("Starting grouped price sync (whole market, one request)...")

    try:
        bar_date, written = fetch_and_cache_grouped_daily(
            max_lookback_days=max_lookback_days
        )
    except Exception as e:
        # Includes the guard tripping: a walk-back wider than any real market
        # closure raises rather than writing stale prices and reporting success.
        logger.error(f"Price sync failed: {type(e).__name__}: {e}")
        return False

    logger.info(f"✅ Sync complete: {written} prices from trading day {bar_date}")
    return True


if __name__ == "__main__":
    # How far back to search for the most recent trading day. Seven covers any
    # realistic closure -- a Monday holiday costs four steps, and the two-day
    # Hurricane Sandy shutdown stacked on a weekend would cost five.
    _lookback = int(os.environ.get("SYNC_MAX_LOOKBACK_DAYS", "7"))

    # Exit code is the only signal cron and Railway can see. Anything that
    # prevented prices from being written must be non-zero, or the failure is
    # invisible -- which is exactly how this ran broken from February to August.
    sys.exit(0 if sync_prices(max_lookback_days=_lookback) else 1)
