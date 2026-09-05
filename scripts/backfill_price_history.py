#!/usr/bin/env python3
"""Backfill public.price_history one trading day at a time. Standard library only.

WHY

price_history began on 2026-08-07, so every baseline the sector pulse needs --
a 20-day median volume, a robust "what is normal for this name" -- rests on a
fortnight of bars, and one event (MRNA, 2026-08-19, 90x volume) is enough to
poison a window that short. Polygon's free tier publishes end-of-day bars for
the trailing two years, so the history is there; nothing has asked for it.

MECHANICS

The nightly sync's own function does the work, unchanged:
fetch_and_cache_grouped_daily(start_date=<day>, max_lookback_days=1). One call
per trading day returns the whole market, and the rows land in price_history
keyed by the bar's own date. price_history upserts on (ticker, trade_date), so
the run is idempotent -- re-running over the same range rewrites identical rows.

OLDEST TO NEWEST, AND THAT ORDER IS LOAD-BEARING. The same function ALSO
overwrites public.stock_prices, the "latest close" snapshot Discovery and Deep
Analyze read. Whatever day is written LAST is what the app then believes is
current. Ascending order makes the final write the most recent day; any other
order leaves the snapshot pointing at a stale close until the next nightly run,
silently, with nothing red anywhere. The summary warns when the range does not
reach yesterday for the same reason.

FAILURE POLICY

A closed market is an answer, not a failure: with max_lookback_days=1 the sync
raises "No trading day found" for a holiday, and that exact case is recorded as
`closed` and the loop continues. Weekends are skipped without a request. Every
other exception is recorded as `failed`, the loop continues so one bad day does
not cost the other 249, and the exit code is non-zero if ANY day failed -- the
repo's rule that a job which could not do its work must not exit 0.

PACING

The free tier allows 5 requests a minute. The loop sleeps between real fetches
(never after a skip), default 13s, so ~250 trading days take about an hour.
The sync's own 429 handling remains underneath as a second line.

Usage:
    python3 scripts/backfill_price_history.py                       # last 365 days
    python3 scripts/backfill_price_history.py --start 2025-09-01 --end 2026-08-06
    python3 scripts/backfill_price_history.py --dry-run             # plan only, no requests
    python3 scripts/backfill_price_history.py --no-skip-existing    # rewrite days already present

Reads POLYGON_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY through
utils.config (environment first, then .streamlit/secrets.toml). Run it from a
machine that has them; it is a one-off and is not deployed anywhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import prices as _prices  # noqa: E402
from utils.config import get as _config  # noqa: E402

logger = logging.getLogger("backfill_price_history")

# Module attributes on purpose: tests replace these with stubs. Binding the
# function at call time (not import time) is what makes that work.
FETCH = _prices.fetch_and_cache_grouped_daily

DEFAULT_PACE_S = 13.0          # 5 req/min on the free tier, with headroom
CLOSED_PREFIX = "No trading day found"   # the sync's holiday message
ROWS_PER_DAY_FLOOR = 5000      # a full grouped day is ~6,300 rows; below this it is partial


def utc_today() -> date:
    """Today in UTC, which is the only 'today' this system has.

    NOT date.today(). Every date here is a MARKET date: Polygon's grouped
    endpoint takes one, price_history.trade_date stores one, and utils/prices.py
    derives its own start from utcnow(). date.today() is the LOCAL date, so
    running this in the evening in the Americas silently shifts both the default
    --end and the stale-snapshot check back a day -- which is exactly what
    happened on the first real run: at 22:32 EDT the local date was 09-03 while
    the market and the database had moved to 09-04, and the warning that the
    snapshot was left stale never printed.
    """
    return datetime.now(timezone.utc).date()


def count_rows_for_date(day: date) -> int:
    """How many price_history rows already exist for `day`. Returns -1 when unknown."""
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return -1
    qs = urllib.parse.urlencode({"select": "ticker", "trade_date": f"eq.{day.isoformat()}", "limit": 1})
    req = urllib.request.Request(
        f"{base}/rest/v1/price_history?{qs}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rng = r.headers.get("Content-Range", "")
            total = rng.split("/")[-1]
            return int(total) if total.isdigit() else -1
    except Exception as e:  # noqa: BLE001
        logger.warning("count for %s failed: %s: %s", day, type(e).__name__, str(e)[:120])
        return -1


COUNT = count_rows_for_date


def trading_weekdays(start: date, end: date) -> list[date]:
    """Every Monday..Friday in [start, end], ascending. Holidays are Polygon's call."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def run(start: date, end: date, *, pace_s: float = DEFAULT_PACE_S, dry_run: bool = False,
        skip_existing: bool = True, sleep=time.sleep) -> dict:
    """Backfill [start, end] ascending. Returns a summary dict; never raises for a single day."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    days = trading_weekdays(start, end)
    summary = {
        "start": start.isoformat(), "end": end.isoformat(), "planned": len(days),
        "written": [], "closed": [], "skipped_existing": [], "failed": [], "partial": [],
        "fetches": 0, "snapshot_stale": False,
    }
    if dry_run:
        for d in days:
            print(f"  plan  {d.isoformat()}")
        return summary
    # The snapshot is only stale if this run actually overwrote it: a dry run or
    # a run that skipped every day (all present already) touched nothing.
    def _finish() -> dict:
        wrote = bool(summary["written"] or summary["partial"])
        summary["snapshot_stale"] = wrote and end < (utc_today() - timedelta(days=1))
        return summary

    fetched_any = False
    for d in days:
        if skip_existing:
            have = COUNT(d)
            if have >= ROWS_PER_DAY_FLOOR:
                summary["skipped_existing"].append(d.isoformat())
                print(f"  skip  {d.isoformat()}  already has {have} rows", flush=True)
                continue
        if fetched_any and pace_s > 0:
            sleep(pace_s)
        fetched_any = True
        summary["fetches"] += 1
        try:
            bar_date, written = FETCH(start_date=d, max_lookback_days=1, max_gap_days=0)
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith(CLOSED_PREFIX) and "HTTP 403" not in msg:
                summary["closed"].append(d.isoformat())
                print(f"  closed {d.isoformat()}", flush=True)
                continue
            summary["failed"].append({"date": d.isoformat(), "error": msg[:200]})
            print(f"  FAIL  {d.isoformat()}  {msg[:160]}", flush=True)
            continue
        except Exception as e:  # noqa: BLE001
            summary["failed"].append({"date": d.isoformat(), "error": f"{type(e).__name__}: {str(e)[:180]}"})
            print(f"  FAIL  {d.isoformat()}  {type(e).__name__}: {str(e)[:160]}", flush=True)
            continue
        entry = {"date": d.isoformat(), "bar_date": bar_date, "rows": written}
        if bar_date != d.isoformat():
            # max_lookback_days=1 should make this impossible; record it rather than trust it.
            summary["failed"].append({"date": d.isoformat(), "error": f"resolved to {bar_date}, not the requested day"})
            print(f"  FAIL  {d.isoformat()}  resolved to {bar_date}", flush=True)
            continue
        if written < ROWS_PER_DAY_FLOOR:
            summary["partial"].append(entry)
            print(f"  PART  {d.isoformat()}  only {written} rows", flush=True)
        else:
            summary["written"].append(entry)
            print(f"  ok    {d.isoformat()}  {written} rows", flush=True)
    return _finish()


def report(summary: dict) -> int:
    """Print the outcome and return the process exit code."""
    w, c, s, f, p = (len(summary[k]) for k in ("written", "closed", "skipped_existing", "failed", "partial"))
    print("\n" + "=" * 66)
    print(f"  range {summary['start']} .. {summary['end']}   planned {summary['planned']} weekdays")
    print(f"  written {w}   closed {c}   skipped(existing) {s}   partial {p}   failed {f}   requests {summary['fetches']}")
    for e in summary["failed"]:
        print(f"    FAILED  {e['date']}  {e['error']}")
    for e in summary["partial"]:
        print(f"    PARTIAL {e['date']}  {e['rows']} rows -- re-run this day")
    if summary["snapshot_stale"]:
        print("\n  WARNING: the range ends before yesterday, so public.stock_prices -- the")
        print("  'latest close' the app reads -- now holds", summary["end"], "closes.")
        print("  It is corrected by the next nightly sync. Do not leave it like this over a")
        print("  weekend without knowing that.")
    print("=" * 66)
    return 1 if (f or p) else 0


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", type=_parse_date, default=utc_today() - timedelta(days=365))
    ap.add_argument("--end", type=_parse_date, default=utc_today() - timedelta(days=1))
    ap.add_argument("--pace-seconds", type=float, default=DEFAULT_PACE_S)
    ap.add_argument("--dry-run", action="store_true", help="print the plan; make no requests")
    ap.add_argument("--no-skip-existing", action="store_true", help="rewrite days already present")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # The sync's own logger is chatty per day; keep it to warnings here.
    logging.getLogger("utils.prices").setLevel(logging.WARNING)

    print(f"backfill {args.start} .. {args.end}  pace {args.pace_seconds}s  "
          f"{'DRY RUN' if args.dry_run else 'writing'}", flush=True)
    summary = run(args.start, args.end, pace_s=args.pace_seconds, dry_run=args.dry_run,
                  skip_existing=not args.no_skip_existing)
    code = report(summary)
    with open("backfill_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
