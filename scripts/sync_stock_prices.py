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
from datetime import datetime
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def fetch_prices_batch(client: RESTClient, tickers: List[str]) -> List[Dict]:
    """Fetch latest close prices for multiple tickers in ONE batch API call."""
    try:
        # Polygon batch endpoint: comma-separated tickers
        ticker_str = ",".join(tickers)
        resp = client.get_aggs(
            ticker=ticker_str,
            timespan="day",
            limit=1
        )
        
        results = []
        if resp and resp.results:
            for agg in resp.results:
                results.append({
                    "ticker": agg.ticker,
                    "close_price": agg.close,
                    "error": None
                })
        
        # Check which tickers were found, mark missing ones as N/A
        found_tickers = {r["ticker"] for r in results}
        for ticker in tickers:
            if ticker not in found_tickers:
                results.append({"ticker": ticker, "close_price": None, "error": "No data"})
        
        return results
    except Exception as e:
        logger.warning(f"Batch fetch failed: {str(e)[:60]}")
        return [{"ticker": t, "close_price": None, "error": str(e)[:100]} for t in tickers]

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
    
    # Fetch prices in batches (100 tickers per batch = 1 API call)
    batch_size = 100
    results = []
    failed = 0
    success = 0
    
    for batch_start in range(0, len(tickers_to_fetch), batch_size):
        batch = tickers_to_fetch[batch_start:batch_start + batch_size]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (len(tickers_to_fetch) + batch_size - 1) // batch_size
        
        logger.info(f"Fetching batch {batch_num}/{total_batches} ({len(batch)} tickers)...")
        
        batch_results = fetch_prices_batch(client, batch)
        results.extend(batch_results)
        
        for result in batch_results:
            if result["error"]:
                failed += 1
            else:
                success += 1
    
    logger.info(f"Polygon batch fetch complete: {success} success, {failed} failed, {len(results)} total")
    
    # Update Supabase
    logger.info("Updating Supabase stock_prices table...")
    try:
        sb = get_admin_client()
        
        # Filter successful results
        valid_results = [r for r in results if r["close_price"] is not None]
        
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
