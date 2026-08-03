#!/usr/bin/env python3
"""Nightly price sync, on a server instead of a laptop.

The same job has been running from a laptop crontab and firing on 90 of 176
nights -- 51%. Half the price data the product displays was never collected,
and the failure looked like a monitoring problem rather than an architecture
one: a check running on the same laptop cannot report that the laptop is
asleep. healthchecks.io could see it only because it lives outside.

This is the same sync_prices() the laptop ran, unchanged. What changes is where
it runs and that it now reports to a dead-man switch that means something,
because the host does not sleep.

RUNTIME. ~500 tickers at Polygon's free-tier 5 req/min is roughly 100 minutes.
That is expected, not a hang -- and it is why this is its own Railway service
with a nightly cron rather than a job inside the 5-minute worker tick.
"""

from __future__ import annotations

import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.sync_stock_prices import sync_prices  # noqa: E402

HC = os.environ.get("HEALTHCHECK_PRICE_SYNC_URL", "")


def ping(suffix: str = "") -> None:
    """Best-effort. Monitoring must never be able to fail the job it monitors --
    the laptop wrapper once died before the sync ran because a grep for this
    very URL returned non-zero under `set -o pipefail`."""
    if not HC:
        return
    try:
        urllib.request.urlopen(f"{HC.rstrip('/')}{suffix}", timeout=10).read()
    except Exception as e:
        print(f"WARN healthcheck ping{suffix or ' (success)'} failed: {type(e).__name__}",
              flush=True)


def main() -> int:
    limit = int(os.environ.get("SYNC_TICKER_LIMIT", "500"))
    rate = float(os.environ.get("SYNC_RATE_PER_MIN", "5"))

    ping("/start")
    print(f"price sync starting: {limit} tickers at {rate}/min "
          f"(ETA ~{limit * (60.0 / max(rate, 1)) / 60:.0f} min)", flush=True)

    try:
        ok = sync_prices(limit=limit, rate_per_min=rate)
    except Exception as e:
        print(f"ERROR sync raised: {type(e).__name__}: {e}", flush=True)
        ping("/fail")
        return 1

    if not ok:
        # sync_prices already logged why. Non-zero so Railway records the run as
        # failed and the dead-man switch goes red -- the signal that was missing
        # while this ran broken 43 times printing "Sync completed".
        ping("/fail")
        return 1

    ping()
    return 0


if __name__ == "__main__":
    sys.exit(main())
