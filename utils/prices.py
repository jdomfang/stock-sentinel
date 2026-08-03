"""Polygon last-close fetch + Supabase cache write. Standard library only.

WHY THIS MODULE EXISTS SEPARATELY FROM utils.finance

The nightly price sync has been running from a laptop crontab and firing on 90
of 176 nights -- 51%. Half the price data the product displays simply was not
collected. Moving it to the worker container is the fix, but the worker's image
is deliberately dependency-free and utils.finance cannot be imported there: it
pulls in streamlit, numpy, the Polygon SDK and filelock at module scope.

The function itself needed none of those -- only an HTTP client, a Supabase
write and the clock. So it lives here, using urllib and PostgREST directly,
which means BOTH the Streamlit app and a bare python:3.11-slim container can
import it with nothing installed.

The alternative was to duplicate ~40 lines of paced-fetch-and-upsert into the
worker. That is exactly what caused the six-month outage: scripts/
sync_stock_prices.py had its own copy that upserted a column which does not
exist, so every write was rejected and nobody noticed. One implementation,
imported twice, cannot drift.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _config(name: str, default: str = "") -> str:
    """Environment first, then Streamlit secrets if Streamlit happens to exist.

    Same precedence as utils.obs and utils.finance. The streamlit import is
    guarded so this module stays importable from a container that has never
    heard of Streamlit -- which is the entire point of the file.
    """
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


def _require(name: str) -> str:
    v = _config(name)
    if not v:
        raise RuntimeError(
            f"Missing {name} (set the env var or add it to .streamlit/secrets.toml)"
        )
    return v


def _get_json(url: str, timeout: int = 20) -> tuple[int, dict]:
    """GET returning (status, parsed_body). Never raises on HTTP status."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read() or b""
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"_raw": body[:200].decode(errors="replace")}


def _upsert_stock_prices(rows: list[dict]) -> None:
    """Upsert into public.stock_prices via PostgREST. Raises on failure.

    resolution=merge-duplicates is what the supabase client's .upsert() sends;
    doing it directly avoids pulling the SDK into the worker image.
    """
    base = _require("SUPABASE_URL").rstrip("/")
    key = _require("SUPABASE_SERVICE_ROLE_KEY")
    req = urllib.request.Request(
        f"{base}/rest/v1/stock_prices",
        data=json.dumps(rows).encode(),
        headers={
            "Content-Type": "application/json",
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"stock_prices upsert HTTP {r.status}")


def fetch_and_cache_last_close_prices(
    tickers: list[str], pace_seconds: float = 0.12, strict: bool = False
) -> dict[str, float]:
    """Fetch last close prices from Polygon (daily aggregates), then upsert them.

    Direct HTTP rather than the Polygon SDK's built-in retry, so a 429 is handled
    cleanly instead of becoming a MaxRetryError storm.

    pace_seconds is the delay between requests. The 0.12s default is tuned for an
    interactive scan of ~10 tickers, where the goal is only to avoid a burst. A
    batch job walking hundreds must pass a much larger value: Polygon's free tier
    allows ~5 requests/minute, and at 0.12s the backoff (3 attempts, 2/4/6s) is
    exhausted long before the window resets, so nearly every request fails.
    Callers, not this function, know which regime they are in.

    strict controls what happens when the write fails. Default False keeps the
    interactive path best-effort -- a scan must not die because a cache write
    did. Batch callers pass True: for them a silent write failure is the whole
    bug, since the return value is non-empty either way and the caller would
    otherwise report success. That exact hole made a fully-failed sync report
    SUCCESS and ping its dead-man switch green.

    Returns {TICKER: close_price} for any prices successfully fetched.
    """
    tickers_u = [t.upper().strip() for t in tickers if t]
    tickers_u = list(dict.fromkeys(tickers_u))
    if not tickers_u:
        return {}

    api_key = _require("POLYGON_API_KEY")
    out: dict[str, float] = {}

    # Write as we go rather than once at the end. A 500-ticker run at Polygon's
    # free-tier 5 req/min takes ~100 minutes, and the previous shape held every
    # row in memory until the loop finished -- so ANY interruption in that window
    # discarded 100% of the work after spending the entire fetch budget. That is
    # not hypothetical: a Railway redeploy killed an in-progress run on
    # 2026-08-03 and nothing was written.
    #
    # Flushing every 50 tickers caps the loss at ~10 minutes. The interactive
    # path is unaffected: a ~10-ticker scan still flushes once, at the end.
    flush_every = int(os.getenv("PRICE_FLUSH_EVERY", "50"))
    pending: list[dict] = []
    written = 0

    def _flush() -> None:
        nonlocal pending, written
        if not pending:
            return
        rows, pending = pending, []
        try:
            _upsert_stock_prices(rows)
            written += len(rows)
            logger.info("stock_prices: wrote %d (%d total)", len(rows), written)
        except Exception as e:
            logger.warning("stock_prices upsert failed: %s: %s", type(e).__name__, str(e)[:200])
            # Fail fast for a batch caller. A bad credential or a schema change
            # fails identically on row 1 and row 500, so raising at the first
            # flush saves ~90 minutes of fetching that cannot be written anyway.
            if strict:
                raise

    # A 10-day window so weekends and holidays still return a last close.
    end = datetime.utcnow().date()
    start = end - timedelta(days=10)

    for t in tickers_u:
        time.sleep(pace_seconds)

        qs = urllib.parse.urlencode(
            {"adjusted": "true", "sort": "desc", "limit": 1, "apiKey": api_key}
        )
        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{urllib.parse.quote(t)}"
            f"/range/1/day/{start.isoformat()}/{end.isoformat()}?{qs}"
        )

        for attempt in range(1, 4):
            try:
                status, payload = _get_json(url)
                if status == 429:
                    wait = 2.0 * attempt
                    logger.warning("Polygon 429 for %s (attempt %d/3). Sleeping %.1fs",
                                   t, attempt, wait)
                    time.sleep(wait)
                    continue
                if status != 200:
                    logger.warning("Polygon aggs HTTP %s for %s: %s",
                                   status, t, str(payload)[:200])
                    break

                results = (payload or {}).get("results") or []
                if not results:
                    break
                close = results[0].get("c")
                if isinstance(close, (int, float)):
                    out[t] = float(close)
                    pending.append({
                        "ticker": t,
                        "close_price": float(close),
                        "last_updated": datetime.utcnow().isoformat(),
                        "currency": "USD",
                    })
                break
            except Exception as e:
                logger.warning("Polygon aggs failed for %s (attempt %d/3): %s: %s",
                               t, attempt, type(e).__name__, str(e)[:160])
                time.sleep(1.0 * attempt)

        # Flush OUTSIDE the Polygon retry block. Called inside it, a strict
        # upsert failure was caught by `except Exception` above -- so it did not
        # fail fast, and worse, a Supabase 401 was logged as "Polygon aggs
        # failed", pointing at the wrong API entirely.
        if len(pending) >= flush_every:
            _flush()

    _flush()  # whatever is left below the flush threshold

    return out
