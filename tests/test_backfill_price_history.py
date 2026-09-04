#!/usr/bin/env python3
"""Prove the backfill writes history in the only safe order, and cannot exit green on a failure.

WHAT IS AT RISK

1. A STALE SNAPSHOT. fetch_and_cache_grouped_daily overwrites public.stock_prices
   -- the "latest close" Discovery and Deep Analyze read -- on EVERY call. If
   the loop ran newest-first, the last write would be the oldest day and the
   app would price everything at year-old closes with nothing red anywhere.
   The order is the whole safety property of this script.
2. A GREEN EXIT WITH HOLES. A holiday and a failed request both leave a day
   with no rows. One is correct and one is not; conflating them is how the
   nightly sync ran broken 43 times. The script must exit non-zero on the
   second and continue on the first.
3. RATE-LIMIT BLINDNESS. The free tier allows 5 requests a minute. Sleeping
   after a skip instead of between fetches would either waste an hour or
   hammer the API, depending on which way the bug went.

No network. FETCH, COUNT and sleep are stubbed; assertions are on the summary
the run returns and on what the stubs were asked to do -- never on printed text.

Usage:
    python3 tests/test_backfill_price_history.py
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import backfill_price_history as B  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


class Recorder:
    """A FETCH stub that records the order it was called in and can fail on demand."""

    def __init__(self, closed=(), broken=(), partial=(), resolve_wrong=()):
        self.calls: list[date] = []
        self.closed, self.broken, self.partial, self.resolve_wrong = set(closed), set(broken), set(partial), set(resolve_wrong)

    def __call__(self, start_date, max_lookback_days, max_gap_days):
        self.calls.append(start_date)
        if start_date in self.closed:
            raise RuntimeError(f"No trading day found in 1 days back from {start_date}. Polygon returned empty for every date.")
        if start_date in self.broken:
            raise RuntimeError(f"Polygon grouped HTTP 500 for {start_date}: boom")
        if start_date in self.resolve_wrong:
            return (start_date.replace(day=1).isoformat(), 6300)
        return (start_date.isoformat(), 120 if start_date in self.partial else 6300)


def _run(start, end, fetch, count=lambda d: 0, **kw):
    B.FETCH, B.COUNT = fetch, count
    sleeps: list[float] = []
    s = B.run(start, end, pace_s=13.0, sleep=sleeps.append, **kw)
    return s, sleeps


def test_order_and_weekends():
    print("\norder: oldest to newest, weekdays only")
    f = Recorder()
    s, sleeps = _run(date(2026, 8, 3), date(2026, 8, 14), f)   # Mon..Fri, two weeks
    check("every request was for a weekday", all(d.weekday() < 5 for d in f.calls), str(f.calls))
    check("ten weekdays were requested", len(f.calls) == 10, str(len(f.calls)))
    check("requests are strictly ascending", f.calls == sorted(f.calls) and len(set(f.calls)) == len(f.calls))
    check("the LAST write is the most recent day", f.calls[-1] == date(2026, 8, 14), str(f.calls[-1]))
    check("all ten recorded as written", len(s["written"]) == 10)
    check("sleeps happen BETWEEN fetches, not after the last", len(sleeps) == 9, str(len(sleeps)))
    check("sleep uses the pace value", all(x == 13.0 for x in sleeps))


def test_holiday_is_closed_not_failed():
    print("\nholiday: recorded as closed, loop continues, exit 0")
    labor_day = date(2026, 9, 7)
    f = Recorder(closed={labor_day})
    s, _ = _run(date(2026, 9, 3), date(2026, 9, 9), f)
    check("closed day recorded as closed", s["closed"] == [labor_day.isoformat()], str(s["closed"]))
    check("closed day is not a failure", not s["failed"], str(s["failed"]))
    check("days after the holiday were still fetched", date(2026, 9, 8) in f.calls and date(2026, 9, 9) in f.calls)
    check("exit code is 0", B.report(s) == 0)


def test_failure_is_non_zero_and_does_not_stop_the_loop():
    print("\nfailure: recorded, loop continues, exit 1")
    bad = date(2026, 8, 5)
    f = Recorder(broken={bad})
    s, _ = _run(date(2026, 8, 3), date(2026, 8, 7), f)
    check("the broken day is in failed", [e["date"] for e in s["failed"]] == [bad.isoformat()], str(s["failed"]))
    check("the other four days were written", len(s["written"]) == 4, str(len(s["written"])))
    check("the loop did not stop at the failure", date(2026, 8, 7) in f.calls)
    check("exit code is 1", B.report(s) == 1)


def test_closed_message_with_403_is_a_failure():
    print("\na 403 refusal is NOT a closed market")
    d = date(2026, 8, 5)

    def fetch(start_date, max_lookback_days, max_gap_days):
        raise RuntimeError("No trading day found in 1 days back from 2026-08-05. Last upstream refusal: HTTP 403 bad key")
    s, _ = _run(d, d, fetch)
    check("recorded as failed, not closed", s["failed"] and not s["closed"], f"failed={s['failed']} closed={s['closed']}")


def test_partial_and_misresolved_days_fail():
    print("\npartial day and wrong-date resolution are failures")
    f = Recorder(partial={date(2026, 8, 4)}, resolve_wrong={date(2026, 8, 6)})
    s, _ = _run(date(2026, 8, 3), date(2026, 8, 7), f)
    check("partial day recorded as partial", [e["date"] for e in s["partial"]] == ["2026-08-04"], str(s["partial"]))
    check("mis-resolved day recorded as failed", any(e["date"] == "2026-08-06" for e in s["failed"]), str(s["failed"]))
    check("exit code is 1", B.report(s) == 1)


def test_skip_existing_makes_no_request():
    print("\nskip-existing: a full day already present is never re-fetched")
    f = Recorder()
    have = {date(2026, 8, 4): 6300, date(2026, 8, 5): 120}   # one full, one partial
    s, sleeps = _run(date(2026, 8, 3), date(2026, 8, 6), f, count=lambda d: have.get(d, 0))
    check("the full day was skipped", date(2026, 8, 4) not in f.calls and s["skipped_existing"] == ["2026-08-04"], str(f.calls))
    check("the partial day was re-fetched", date(2026, 8, 5) in f.calls)
    check("no sleep was charged for the skip", len(sleeps) == 2, str(len(sleeps)))
    f2 = Recorder()
    _run(date(2026, 8, 3), date(2026, 8, 6), f2, count=lambda d: 6300, skip_existing=False)
    check("--no-skip-existing re-fetches everything", len(f2.calls) == 4, str(len(f2.calls)))


def test_dry_run_makes_no_requests():
    print("\ndry run: plan only")
    f = Recorder()
    s, sleeps = _run(date(2026, 8, 3), date(2026, 8, 7), f, dry_run=True)
    check("no fetches", f.calls == [] and s["fetches"] == 0)
    check("no sleeps", sleeps == [])
    check("plan counts the weekdays", s["planned"] == 5, str(s["planned"]))


def test_stale_snapshot_flag():
    print("\nstale snapshot: flagged when the range does not reach yesterday")
    f = Recorder()
    s_old, _ = _run(date(2026, 1, 5), date(2026, 1, 9), f)
    check("old range flags the snapshot as stale", s_old["snapshot_stale"] is True)
    from datetime import timedelta
    y = date.today() - timedelta(days=1)
    s_new, _ = _run(y - timedelta(days=4), y, Recorder())
    check("a range ending yesterday does not", s_new["snapshot_stale"] is False)
    # The flag means "this run overwrote stock_prices with old closes". A run
    # that wrote nothing cannot have done that, however old its range.
    s_dry, _ = _run(date(2026, 1, 5), date(2026, 1, 9), Recorder(), dry_run=True)
    check("a dry run over an old range does not flag it", s_dry["snapshot_stale"] is False)
    s_skip, _ = _run(date(2026, 1, 5), date(2026, 1, 9), Recorder(), count=lambda d: 6300)
    check("a run that skipped every day does not flag it", s_skip["snapshot_stale"] is False)
    s_closed, _ = _run(date(2026, 1, 5), date(2026, 1, 5), Recorder(closed={date(2026, 1, 5)}))
    check("a run that only met a closed market does not flag it", s_closed["snapshot_stale"] is False)


def test_end_before_start_is_refused():
    print("\nend before start is a usage error, not a silent no-op")
    try:
        B.run(date(2026, 8, 7), date(2026, 8, 3), sleep=lambda s: None)
        check("raises ValueError", False, "returned normally")
    except ValueError:
        check("raises ValueError", True)


def main() -> int:
    print("=" * 74)
    print("  backfill_price_history: order, holidays vs failures, pacing, exit codes")
    print("=" * 74)
    for t in (test_order_and_weekends, test_holiday_is_closed_not_failed,
              test_failure_is_non_zero_and_does_not_stop_the_loop, test_closed_message_with_403_is_a_failure,
              test_partial_and_misresolved_days_fail, test_skip_existing_makes_no_request,
              test_dry_run_makes_no_requests, test_stale_snapshot_flag, test_end_before_start_is_refused):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
