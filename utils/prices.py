"""Polygon last-close fetch + Supabase cache write. Standard library only.

WHY THIS MODULE EXISTS SEPARATELY FROM utils.finance

The nightly price sync has been running from a laptop crontab and firing on 90
of 176 nights -- 51%. Half the price data the product displays simply was not
collected. Moving it to the worker container is the fix, but the worker's image
is deliberately dependency-free and utils.finance cannot be imported there: it
pulls in streamlit, numpy and the Polygon SDK at module scope.

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


def _fetch_ticker_master_symbols() -> set[str]:
    """Every symbol the app actually scans against. Empty set if unreadable."""
    base = _require("SUPABASE_URL").rstrip("/")
    key = _require("SUPABASE_SERVICE_ROLE_KEY")
    out: set[str] = set()
    offset = 0
    while True:
        qs = urllib.parse.urlencode({"select": "symbol", "offset": offset, "limit": 1000})
        req = urllib.request.Request(
            f"{base}/rest/v1/ticker_master?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read() or b"[]")
        out.update((row.get("symbol") or "").upper() for row in rows if row.get("symbol"))
        if len(rows) < 1000:
            return out
        offset += 1000


def fetch_and_cache_grouped_daily(
    start_date=None,
    max_lookback_days: int = 7,
    max_gap_days: int = 5,
    restrict_to_master: bool = True,
    chunk_size: int = 500,
) -> tuple[str, int]:
    """Fetch EVERY US ticker's last close in one request, then upsert.

    WHY THIS REPLACES THE PER-TICKER LOOP

    fetch_and_cache_last_close_prices asks Polygon one question per ticker. At
    the free tier's 5 requests/minute, 500 tickers takes ~100 minutes and the
    full 7,065-symbol universe would take 23.5 HOURS -- so a nightly full sync
    was arithmetically impossible, and the job settled for 500 chosen by
    position in a file. The result: 643 tickers cached, 387 of them starting
    with the letter "A".

    Polygon's grouped daily bars endpoint returns the whole market for one date
    in a SINGLE request. Measured 2026-08-05 against the production key: 1 call,
    0.5s, 12,408 tickers, covering 6,007 of ticker_master (85%). Same free tier.

    WHY THE WALK-BACK

    This endpoint takes one date and has no "latest" mode: ask for a Saturday
    and you get resultsCount=0, not Friday's closes. Crucially a weekday HOLIDAY
    is indistinguishable from a weekend -- verified, Memorial Day Monday
    2026-05-25 returned 0 exactly as the Sunday before it did, while Friday
    2026-05-22 returned 12,202.

    That is the whole reason this walks back instead of consulting a calendar.
    A hardcoded holiday list needs ten federal dates plus Good Friday plus
    irregular closures (the market shut for two days during Hurricane Sandy),
    must be maintained every year, and fails SILENTLY when it goes stale. A
    calendar library would add a dependency to a module that is deliberately
    stdlib-only so the sync container can import it. Asking Polygon and letting
    an empty response mean "closed" needs no maintenance and cannot go stale.

    The per-ticker path already does this, expressed as a 10-day range with
    sort=desc -- the stepping just moves to our side because grouped takes a
    single date.

    THE GUARD

    Walking back is safe; walking back silently is not. If Polygon starts
    returning empty for recent dates, an unguarded loop happily writes week-old
    prices while the dead-man switch pings green -- the exact shape of the 43
    unnoticed sync failures. So the search is bounded (max_lookback_days), the
    date landed on is always logged, and a gap wider than any real market
    closure (max_gap_days) raises instead of quietly succeeding.

    Returns (bar_date_iso, rows_written). Raises on any failure, because the
    caller turns an exception into a non-zero exit and a red dead-man switch.
    """
    api_key = _require("POLYGON_API_KEY")

    # The job runs at 01:00 UTC, which is the previous EVENING in New York --
    # after the 16:00 ET close. So the trading day we want is yesterday's date.
    if start_date is None:
        start_date = datetime.utcnow().date() - timedelta(days=1)

    day = start_date
    payload = None
    last_refusal = ""
    for _ in range(max_lookback_days):
        qs = urllib.parse.urlencode({"adjusted": "true", "apiKey": api_key})
        url = (
            "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/"
            f"{day.isoformat()}?{qs}"
        )

        refused = False
        for attempt in range(1, 4):
            status, body = _get_json(url, timeout=90)
            if status == 429:
                wait = 15.0 * attempt
                logger.warning("Polygon 429 on grouped %s (attempt %d/3). Sleeping %.0fs",
                               day, attempt, wait)
                time.sleep(wait)
                continue
            if status == 403:
                refused = True
                # The free tier refuses the CURRENT day until its end-of-day
                # data is published: "Attempted to request today's data before
                # end of day." That is a not-yet, not a failure, and it is the
                # normal answer for the first date this job asks about -- the
                # job runs at 01:00 UTC, which is still the same trading day in
                # New York. So it steps back exactly like a closed market.
                #
                # A bad or unentitled API key also returns 403. That is NOT
                # swallowed: every date will refuse, the loop exhausts its
                # lookback, and the final error carries this message rather
                # than a misleading "market was closed all week".
                last_refusal = f"HTTP 403 {str(body)[:160]}"
                break
            if status != 200:
                raise RuntimeError(
                    f"Polygon grouped HTTP {status} for {day}: {str(body)[:200]}"
                )
            break
        else:
            raise RuntimeError(f"Polygon grouped rate-limited for {day} after 3 attempts")

        if refused:
            logger.info("grouped: %s not available on this plan yet "
                        "(end-of-day data not published); stepping back", day)
            day = day - timedelta(days=1)
            continue

        count = int((body or {}).get("resultsCount") or 0)
        if count > 0:
            payload = body
            break

        # Zero rows means the market was closed. Weekend or holiday -- we do not
        # need to know which, only that there is nothing to collect.
        logger.info("grouped: %s was not a trading day (0 rows); stepping back", day)
        day = day - timedelta(days=1)

    if payload is None:
        raise RuntimeError(
            f"No trading day found in {max_lookback_days} days back from {start_date}. "
            + (f"Last upstream refusal: {last_refusal}"
               if last_refusal else "Polygon returned empty for every date.")
        )

    gap = (start_date - day).days
    if gap > max_gap_days:
        raise RuntimeError(
            f"Last trading day resolved to {day}, {gap} days before {start_date}. "
            f"No real market closure is that long -- refusing to write prices that "
            f"stale rather than reporting success."
        )
    if gap > 2:
        logger.warning("grouped: last trading day is %s, %d days back "
                       "(holiday weekend, or something is wrong)", day, gap)

    results = payload.get("results") or []
    logger.info("grouped: %s returned %d tickers in one request", day, len(results))

    keep: set[str] | None = None
    if restrict_to_master:
        try:
            keep = _fetch_ticker_master_symbols()
            logger.info("grouped: restricting to %d ticker_master symbols", len(keep))
        except Exception as e:
            # Not fatal. Writing the extra instruments is harmless; writing
            # nothing is not.
            logger.warning("grouped: ticker_master unreadable (%s); writing all tickers",
                           type(e).__name__)
            keep = None

    now_iso = datetime.utcnow().isoformat()

    # DEDUPE BY TICKER BEFORE BUILDING ROWS.
    #
    # Polygon's grouped feed returns a small number of symbols TWICE -- 2 of
    # 12,406 on 2026-08-05 (BCPC and TPC). PostgREST's merge-duplicates upsert
    # becomes ON CONFLICT DO UPDATE, and Postgres refuses when the same key
    # appears twice in one statement ("cannot affect row a second time"),
    # which surfaces as HTTP 500.
    #
    # That is exactly what killed the 2026-08-07 run: the first duplicate sits
    # at index 699, so chunk 1 committed and chunk 2 died. Two bad rows cost
    # 5,480 prices.
    #
    # The survivor is the higher-VOLUME row, which is the primary listing when
    # a symbol appears on more than one venue. Volume is already in the
    # response even though we do not store it.
    best: dict[str, dict] = {}
    dupes = 0
    for r in results:
        sym = (r.get("T") or "").upper()
        close = r.get("c")
        if not sym or not isinstance(close, (int, float)):
            continue
        if keep is not None and sym not in keep:
            continue
        prev = best.get(sym)
        if prev is None:
            best[sym] = r
        else:
            dupes += 1
            if (r.get("v") or 0) > (prev.get("v") or 0):
                best[sym] = r
    if dupes:
        logger.info("grouped: collapsed %d duplicate symbol row(s) from Polygon", dupes)

    rows: list[dict] = []
    for sym, r in best.items():
        close = r.get("c")
        rows.append({
            "ticker": sym,
            "close_price": float(close),
            # Deliberately the WRITE time, matching the per-ticker path. Mixing
            # semantics between the two writers would be worse than either
            # choice. The bar's own date is logged and returned instead; giving
            # it a column of its own is a schema change worth making separately.
            "last_updated": now_iso,
            "currency": "USD",
        })

    if not rows:
        raise RuntimeError(f"grouped {day} returned {len(results)} tickers but none matched")

    # Every chunk is attempted, even after one fails.
    #
    # The 2026-08-07 run raised on chunk 2 and abandoned chunks 3-12, so a
    # defect affecting 2 rows discarded 5,480 good ones. Chunks commit
    # independently, so there is no consistency argument for stopping -- the
    # only thing an early abort protects is the log.
    #
    # The run still FAILS if anything failed: a partial sync that reported
    # success is how this job ran broken 43 times unnoticed.
    written = 0
    failures: list[str] = []
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            _upsert_stock_prices(chunk)
            written += len(chunk)
            logger.info("stock_prices: wrote %d (%d/%d)", len(chunk), written, len(rows))
        except Exception as e:
            failures.append(f"rows {i}-{i + len(chunk) - 1}: {type(e).__name__}: {str(e)[:120]}")
            logger.warning("stock_prices chunk at %d failed: %s: %s",
                           i, type(e).__name__, str(e)[:160])

    if failures:
        raise RuntimeError(
            f"{len(failures)} of {(len(rows) + chunk_size - 1) // chunk_size} chunks failed "
            f"({written}/{len(rows)} rows written). First: {failures[0]}"
        )

    logger.info("✅ grouped sync complete: %d prices from %s", written, day)
    return day.isoformat(), written


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
