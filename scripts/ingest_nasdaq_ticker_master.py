"""Ingest Nasdaq screener ticker master (sector/industry) into Supabase.

Usage:
  SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/ingest_nasdaq_ticker_master.py

Notes:
- Stores Nasdaq `sector`/`industry` exactly as provided.
- Uses upserts on `ticker_master.symbol`.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import requests


NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"
USER_AGENT = os.getenv(
    "NASDAQ_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
)


def get_supabase_admin_client():
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY env vars")
    return create_client(url, key)


def fetch_nasdaq_page(limit: int, offset: int) -> List[Dict[str, Any]]:
    params = {
        "tableonly": "true",
        "download": "true",
        "limit": str(limit),
        "offset": str(offset),
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    }

    r = requests.get(NASDAQ_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    j = r.json() or {}
    data = (j.get("data") or {})
    rows = data.get("rows") or []
    return rows


def chunked(xs: List[Dict[str, Any]], n: int) -> List[List[Dict[str, Any]]]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    # Nasdaq schema fields (commonly): symbol, name, sector, industry, country, url, etc.
    sym = (row.get("symbol") or "").strip().upper()
    if not sym:
        return {}

    out = {
        "symbol": sym,
        "name": (row.get("name") or "").strip() or None,
        "sector": (row.get("sector") or "").strip() or None,
        "industry": (row.get("industry") or "").strip() or None,
        "country": (row.get("country") or "").strip() or None,
        # Nasdaq screener doesn't reliably include exchange in a single field.
        "exchange": None,
        "source": "nasdaq",
    }
    return out


def upsert_rows(sb, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    # supabase-py upsert
    sb.table("ticker_master").upsert(rows, on_conflict="symbol").execute()


def main() -> None:
    sb = get_supabase_admin_client()

    limit = int(os.getenv("NASDAQ_LIMIT", "4000"))
    sleep_s = float(os.getenv("NASDAQ_SLEEP_S", "0.35"))
    batch_size = int(os.getenv("SUPABASE_UPSERT_BATCH", "500"))

    total_in = 0
    total_out = 0

    offset = 0
    prev_fingerprint = None

    while True:
        rows = fetch_nasdaq_page(limit=limit, offset=offset)
        if not rows:
            break

        # Some Nasdaq API deployments ignore offset/limit and return the full set each time.
        # Detect that and stop to avoid infinite looping.
        fingerprint = None
        if rows:
            first_sym = (rows[0].get("symbol") or "")
            fingerprint = f"n={len(rows)}|first={first_sym}"
        if prev_fingerprint is not None and fingerprint == prev_fingerprint:
            print(f"Pagination appears ignored by Nasdaq API (fingerprint={fingerprint}). Stopping.")
            break
        prev_fingerprint = fingerprint

        total_in += len(rows)

        normalized: List[Dict[str, Any]] = []
        for r in rows:
            nr = normalize_row(r)
            if nr:
                normalized.append(nr)

        total_out += len(normalized)

        for batch in chunked(normalized, batch_size):
            upsert_rows(sb, batch)

        offset += limit
        print(f"Fetched offset={offset} rows_in={len(rows)} upserted={len(normalized)} total_upserted={total_out}")

        # polite pacing
        time.sleep(sleep_s)

    print(f"Done. total_rows_in={total_in} total_upserted={total_out}")


if __name__ == "__main__":
    main()
