#!/usr/bin/env python3
"""
Sync latest stock prices from Polygon API to Supabase.
Runs nightly at 9 PM EST (after market close at 4 PM).

Usage:
  python3 scripts/sync_stock_prices.py
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta
from typing import List, Dict

# Add parent dir to path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prices import fetch_and_cache_last_close_prices

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
TICKER_MASTER_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'tickers.json')


def _get_polygon_key() -> str:
    """Resolve the Polygon key the same way the app does.

    Everything else in this process reads configuration from
    .streamlit/secrets.toml via utils.supabase_client, so reading the Polygon
    key from the environment alone made this script the one component with a
    second, separate source of truth -- and it was pointed at a path that does
    not exist (~/.streamlit/secrets.toml), so the nightly sync failed silently
    from 2026-02-06 onward. Environment still wins when set, which keeps CI and
    one-off overrides working.
    """
    env = os.environ.get("POLYGON_API_KEY")
    if env:
        return env
    try:
        import streamlit as st
        return st.secrets.get("POLYGON_API_KEY", "") or ""
    except Exception as e:
        logger.warning(f"Could not read secrets.toml: {type(e).__name__}: {str(e)[:120]}")
        return ""

# Top 500 US stocks (S&P 500 + most-traded)
TOP_500_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JNJ", "WMT",
    "XOM", "JPM", "MCD", "DIS", "ADBE", "NFLX", "BA", "CSCO", "INTC", "AMD",
    "PYPL", "QCOM", "IBM", "AVGO", "TXN", "BKNG", "UBER", "ABNB", "DXCM", "MRNA",
    "ZM", "SHOP", "CRM", "AZN", "NVR", "CMG", "PDD", "SE", "COIN", "DDOG",
    "CRWD", "NET", "OKTA", "SSNC", "SPLK", "MSTR", "RBLX", "U", "PINS", "SNAP",
    "TEAM", "FTNT", "WDAY", "CHWY", "VRSK", "ROKU", "RBLX", "ASML", "ASANA", "PTC",
    "ANET", "MXIM", "ILMN", "EXPE", "TripAdvisor", "SWKS", "JCOM", "ZION", "ETSY", "VEEV",
    "TTD", "NXPI", "MCHP", "LSCC", "MPWR", "CDNS", "SNPS", "CPRT", "HUBS", "PDCO",
    "SMCI", "ORCL", "AMAT", "LRCX", "KLAC", "ONTO", "AAPL", "TSM", "SEMITECH", "MU",
    # Add more as needed...
]

def load_all_tickers() -> Dict[str, Dict]:
    """Load all tickers from local JSON file."""
    if not os.path.exists(TICKER_MASTER_FILE):
        logger.error(f"Ticker file not found: {TICKER_MASTER_FILE}")
        return {}
    
    try:
        with open(TICKER_MASTER_FILE, 'r') as f:
            data = json.load(f)
            return data.get('tickers', {})
    except Exception as e:
        logger.error(f"Failed to load tickers: {e}")
        return {}

def get_top_tickers(all_tickers: Dict[str, Dict], limit: int = 500) -> List[str]:
    """Get top tickers (S&P 500 + most common)."""
    # Prefer hardcoded TOP_500 if available, fallback to first N from file
    tickers_to_fetch = []
    
    # Add TOP_500 (verified stocks)
    for t in TOP_500_TICKERS:
        if t in all_tickers:
            tickers_to_fetch.append(t)
    
    # If we need more, add from file (up to limit)
    if len(tickers_to_fetch) < limit:
        for ticker in all_tickers.keys():
            if ticker not in tickers_to_fetch:
                tickers_to_fetch.append(ticker)
            if len(tickers_to_fetch) >= limit:
                break
    
    return tickers_to_fetch[:limit]

def sync_prices(limit: int = 500, rate_per_min: float = 5.0) -> bool:
    """
    Sync prices for top stocks to Supabase.

    Returns True only if prices were actually written. Every failure path used
    to `return` bare, so the process exited 0 and cron recorded a success --
    43 consecutive failures went unnoticed for six months. The caller turns
    False into a non-zero exit.

    Args:
        limit: Number of tickers to sync
        rate_per_min: Polygon requests per minute. Free tier is ~5.
    """
    logger.info(f"Starting price sync for top {limit} stocks...")

    # Load tickers
    all_tickers = load_all_tickers()
    if not all_tickers:
        logger.error("No tickers loaded, aborting")
        return False

    if not _get_polygon_key():
        logger.error(
            "POLYGON_API_KEY not found in environment or .streamlit/secrets.toml"
        )
        return False

    tickers_to_fetch = get_top_tickers(all_tickers, limit)

    # Polygon's free tier allows ~5 requests/minute. Anything faster gets 429s
    # that the 3-attempt backoff cannot outlast, which is what the unpaced loop
    # here used to do. Derive the delay from the advertised rate.
    pace = 60.0 / max(1.0, rate_per_min)
    eta_min = (len(tickers_to_fetch) * pace) / 60.0
    logger.info(
        f"Fetching prices for {len(tickers_to_fetch)} tickers "
        f"at {rate_per_min}/min ({pace:.1f}s apart, ETA ~{eta_min:.0f} min)"
    )

    # Delegate to the app's implementation rather than keeping a second copy.
    # It paces, backs off on 429, and upserts the correct columns
    # (ticker, close_price, last_updated, currency). The copy that used to live
    # here upserted the raw fetch dicts -- including an "error" key that is not
    # a column -- so PostgREST rejected every batch and this table was never
    # written by this script even once.
    try:
        # strict=True: a failed write must raise, not be logged and swallowed.
        # Without it every upsert could be rejected and this function would still
        # return a non-empty dict, so the run reported SUCCESS and pinged the
        # dead-man switch green with nothing written.
        prices = fetch_and_cache_last_close_prices(
            tickers_to_fetch, pace_seconds=pace, strict=True
        )
    except Exception as e:
        logger.error(f"Price fetch/upsert failed: {type(e).__name__}: {e}")
        return False

    wanted, got = len(tickers_to_fetch), len(prices)
    if got == 0:
        logger.error(f"No prices fetched for any of {wanted} tickers, nothing written")
        return False

    logger.info(f"✅ Sync complete: {got}/{wanted} prices updated")
    return True

if __name__ == "__main__":
    # Tunable without editing code, so the free-tier rate limit can be raised
    # the day the Polygon plan changes.
    _limit = int(os.environ.get("SYNC_TICKER_LIMIT", "500"))
    _rate = float(os.environ.get("SYNC_RATE_PER_MIN", "5"))

    # Exit code is the only signal cron and run_sync.sh can see. Anything that
    # prevented prices from being written must be non-zero, or the failure is
    # invisible -- which is exactly how this ran broken from February to August.
    sys.exit(0 if sync_prices(limit=_limit, rate_per_min=_rate) else 1)
