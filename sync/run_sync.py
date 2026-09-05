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

RUNTIME. Seconds, not the ~100 minutes this took while it asked Polygon about
one ticker at a time. The grouped daily bars endpoint returns the whole market
for a date in a single request, so the job is now dominated by the chunked
upsert rather than by rate limiting. It stays its own Railway service because
its schedule (nightly, after the US close) has nothing to do with the worker's
5-minute tick.
"""

from __future__ import annotations

import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.sync_stock_prices import sync_prices  # noqa: E402

HC = os.environ.get("HEALTHCHECK_PRICE_SYNC_URL", "")
# Its own check, on purpose. The pulse and the price sync fail for unrelated
# reasons -- Polygon down versus a bad sector map -- and one dead-man switch for
# both would make whichever failed second invisible (worker/run_jobs.py says the
# same about the reaper and the inference probe).
HC_PULSE = os.environ.get("HEALTHCHECK_SECTOR_PULSE_URL", "")


def ping(suffix: str = "", url: str | None = None) -> None:
    """Best-effort. Monitoring must never be able to fail the job it monitors --
    the laptop wrapper once died before the sync ran because a grep for this
    very URL returned non-zero under `set -o pipefail`."""
    target = HC if url is None else url
    if not target:
        return
    try:
        urllib.request.urlopen(f"{target.rstrip('/')}{suffix}", timeout=10).read()
    except Exception as e:
        print(f"WARN healthcheck ping{suffix or ' (success)'} failed: {type(e).__name__}",
              flush=True)


def run_sector_pulse() -> bool:
    """Compute tonight's sector rows from the bars just written. Never raises.

    Runs AFTER the price sync has succeeded and pinged, so a broken pulse can
    only cost the pulse. Its result is the exit code's business, not the
    price sync's.
    """
    ping("/start", HC_PULSE)
    try:
        from utils import sector_pulse
        summary = sector_pulse.run()
    except Exception as e:  # noqa: BLE001 -- run() swallows; this is the import
        print(f"ERROR sector pulse could not start: {type(e).__name__}: {e}", flush=True)
        ping("/fail", HC_PULSE)
        return False
    if not summary.get("ok"):
        print(f"ERROR sector pulse failed: {summary.get('error')}", flush=True)
        ping("/fail", HC_PULSE)
        return False
    states = ", ".join(f"{r['sector']}={r['state']}" for r in summary.get("rows", []))
    print(f"sector pulse: {summary.get('written')} rows for {summary.get('trade_date')}  [{states}]",
          flush=True)
    ping("", HC_PULSE)
    return True


def main() -> int:
    lookback = int(os.environ.get("SYNC_MAX_LOOKBACK_DAYS", "7"))

    ping("/start")
    print("price sync starting: whole US market in one grouped request", flush=True)

    try:
        ok = sync_prices(max_lookback_days=lookback)
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
    # Prices are written and reported. Whatever happens next cannot un-succeed
    # that; it can only decide whether tonight's pulse rows exist.
    pulse_ok = run_sector_pulse()
    return 0 if pulse_ok else 1


if __name__ == "__main__":
    sys.exit(main())
