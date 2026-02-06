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

from polygon import RESTClient
from utils.supabase_client import get_admin_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
TICKER_MASTER_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'tickers.json')

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

def fetch_last_close(client: RESTClient, ticker: str) -> dict:
    """Fetch last close price using daily aggregates (more widely entitled than snapshot)."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=10)
    try:
        resp = client.get_aggs(
            ticker=ticker,
            multiplier=1,
            timespan="day",
            from_=start.isoformat(),
            to=end.isoformat(),
            limit=5,
            sort="desc",
        )
        results = getattr(resp, "results", None) or []
        if not results:
            return {"ticker": ticker, "close_price": None, "error": "No data"}
        close = getattr(results[0], "close", None)
        if isinstance(close, (int, float)):
            return {"ticker": ticker, "close_price": float(close), "error": None}
        return {"ticker": ticker, "close_price": None, "error": "Invalid close"}
    except Exception as e:
        return {"ticker": ticker, "close_price": None, "error": str(e)[:160]}

def sync_prices(limit: int = 500, workers: int = 10) -> None:
    """
    Sync prices for top stocks to Supabase.
    
    Args:
        limit: Number of tickers to sync
        workers: Number of concurrent API threads
    """
    logger.info(f"Starting price sync for top {limit} stocks...")
    
    # Load tickers
    all_tickers = load_all_tickers()
    if not all_tickers:
        logger.error("No tickers loaded, aborting")
        return
    
    tickers_to_fetch = get_top_tickers(all_tickers, limit)
    logger.info(f"Fetching prices for {len(tickers_to_fetch)} tickers")
    
    # Get Polygon client
    try:
        api_key = os.environ.get("POLYGON_API_KEY")
        if not api_key:
            logger.error("POLYGON_API_KEY not found in environment")
            return
        client = RESTClient(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to create Polygon client: {e}")
        return
    
    # Fetch prices (daily aggregates per ticker — more widely entitled than snapshot/batch endpoints)
    results = []
    failed = 0
    success = 0

    for idx, ticker in enumerate(tickers_to_fetch, start=1):
        r = fetch_last_close(client, ticker)
        results.append(r)
        if r.get("error"):
            failed += 1
        else:
            success += 1
        if idx % 50 == 0:
            logger.info(f"Fetched {idx}/{len(tickers_to_fetch)}...")

    logger.info(f"Polygon aggs fetch complete: {success} success, {failed} failed")
    
    # Update Supabase
    logger.info("Updating Supabase stock_prices table...")
    try:
        sb = get_admin_client()
        
        # Filter successful results
        valid_results = [r for r in results if (r.get("close_price") is not None and not r.get("error"))]
        
        if valid_results:
            # Upsert (insert or update)
            for batch_start in range(0, len(valid_results), 100):
                batch = valid_results[batch_start:batch_start + 100]
                sb.table("stock_prices").upsert(batch).execute()
                logger.info(f"Upserted {len(batch)} prices")
        
        logger.info(f"✅ Sync complete: {len(valid_results)} prices updated")
    except Exception as e:
        logger.error(f"Failed to update Supabase: {e}")

if __name__ == "__main__":
    sync_prices()
