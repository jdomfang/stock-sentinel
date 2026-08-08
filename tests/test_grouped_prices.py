#!/usr/bin/env python3
"""Prove the grouped price sync finds the right trading day, and fails loudly.

WHY THIS SUITE EXISTS

The nightly sync used to ask Polygon about one ticker at a time. At the free
tier's 5 requests/minute the full universe would take 23.5 hours, so it settled
for 500 tickers chosen by position in an alphabetical file -- 643 cached, 387 of
them starting with "A". The grouped endpoint returns the whole market in one
request, which removes the cap entirely.

What it introduces is a date problem. Grouped takes ONE date and has no "latest"
mode, so the caller has to find the most recent trading day itself. Two things
about that are easy to get wrong and expensive to get wrong quietly:

  FINDING IT.  A weekday holiday is indistinguishable from a weekend -- both
               return zero rows. Memorial Day Monday 2026-05-25 returned 0
               exactly as the Sunday before it did (verified against the live
               API). Anything that special-cases weekends but not holidays will
               write nothing on those mornings.

  KNOWING WHEN TO STOP.  An unbounded walk-back is a silent-staleness machine:
               if Polygon starts returning empty for recent dates, it happily
               reaches back a week and writes stale prices while the dead-man
               switch pings green. That is the same failure as the 43 unnoticed
               sync failures and the 40x-degraded inference service, and the
               whole point of moving this job was to stop having it.

No network. Polygon and Supabase are both stubbed, so these run anywhere.

Usage:
    python3 tests/test_grouped_prices.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import prices  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


class Polygon:
    """A fake market. `open_days` are the only dates that return rows."""

    def __init__(self, open_days: set[str], tickers: int = 3,
                 refuse_days: set[str] | None = None, refuse_all: bool = False):
        self.open_days = open_days
        self.tickers = tickers
        self.refuse_days = refuse_days or set()
        self.refuse_all = refuse_all
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: int = 20):
        day = url.rsplit("/", 1)[-1].split("?")[0]
        self.asked.append(day)
        if self.refuse_all or day in self.refuse_days:
            # The live free-tier response for a date whose end-of-day data is
            # not published yet -- and also what a bad API key returns.
            return 403, {"status": "NOT_AUTHORIZED",
                         "message": "Attempted to request today's data before end of day."}
        if day not in self.open_days:
            # Exactly what the live API returns for a weekend or a holiday.
            return 200, {"status": "OK", "resultsCount": 0, "results": []}
        results = [{"T": f"TK{i}", "c": 10.0 + i} for i in range(self.tickers)]
        return 200, {"status": "OK", "resultsCount": len(results), "results": results}


def install(poly: Polygon, master: set[str] | None = None, master_raises: bool = False):
    """Point the module at the fake market and capture what it would write."""
    written: list[list[dict]] = []
    prices._config = lambda name, default="": "fake-key"
    prices._get_json = poly
    prices._upsert_stock_prices = lambda rows: written.append(list(rows))

    def _master():
        if master_raises:
            raise RuntimeError("simulated Supabase outage")
        return master if master is not None else set()

    prices._fetch_ticker_master_symbols = _master
    return written


def restore(saved: dict) -> None:
    for k, v in saved.items():
        setattr(prices, k, v)


SAVED = {
    "_config": prices._config,
    "_get_json": prices._get_json,
    "_upsert_stock_prices": prices._upsert_stock_prices,
    "_fetch_ticker_master_symbols": prices._fetch_ticker_master_symbols,
}


# ── finding the trading day ──────────────────────────────────────────────────

def test_it_lands_on_the_day_that_traded():
    print("\nwalk-back: an ordinary weeknight needs no stepping")
    poly = Polygon({"2026-08-04"})
    install(poly)
    try:
        day, n = prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 8, 4), restrict_to_master=False
        )
        check("returns the requested day", day == "2026-08-04", day)
        check("asks Polygon exactly once", poly.asked == ["2026-08-04"], str(poly.asked))
        check("writes every ticker", n == 3, str(n))
    finally:
        restore(SAVED)


def test_it_steps_over_a_weekend():
    print("\nwalk-back: Sunday and Saturday are skipped to reach Friday")
    poly = Polygon({"2026-07-31"})  # Friday
    install(poly)
    try:
        day, _ = prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 8, 2), restrict_to_master=False  # Sunday
        )
        check("lands on Friday", day == "2026-07-31", day)
        check("stepped one day at a time",
              poly.asked == ["2026-08-02", "2026-08-01", "2026-07-31"], str(poly.asked))
    finally:
        restore(SAVED)


def test_it_steps_over_a_weekday_holiday():
    print("\nwalk-back: a Monday holiday looks exactly like a weekend")
    # The real case, verified against the live API: Memorial Day Monday
    # 2026-05-25 returned 0 rows, the same as the Sunday before it, while
    # Friday 2026-05-22 returned 12,202. Nothing here knows it was a holiday.
    poly = Polygon({"2026-05-22"})
    install(poly)
    try:
        day, _ = prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 5, 25), restrict_to_master=False
        )
        check("lands on the Friday before the long weekend", day == "2026-05-22", day)
        check("cost four requests, not a calendar lookup", len(poly.asked) == 4,
              str(poly.asked))
    finally:
        restore(SAVED)


def test_it_steps_back_when_the_plan_cannot_serve_today_yet():
    print("\nwalk-back: 'not published yet' is a not-yet, not a failure")
    # Found by a dry run against the live API, which the stubs above could not
    # have caught: the free tier answers 403 NOT_AUTHORIZED -- "Attempted to
    # request today's data before end of day" -- for the most recent date. The
    # job runs at 01:00 UTC, still the same trading day in New York, so this is
    # the NORMAL answer to its very first question. Treating it as fatal meant
    # the sync could never run on schedule.
    poly = Polygon({"2026-08-04"}, refuse_days={"2026-08-05"})
    install(poly)
    try:
        day, n = prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 8, 5), restrict_to_master=False
        )
        check("steps past the refusal to the published day", day == "2026-08-04", day)
        check("still writes prices", n == 3, str(n))
    finally:
        restore(SAVED)


def test_a_bad_api_key_is_not_disguised_as_a_closed_market():
    print("\nwalk-back: a 403 on EVERY date must report the real reason")
    # An unentitled or wrong key also returns 403. Stepping back on 403 must not
    # turn that into "the market was closed all week", or a credentials problem
    # would be diagnosed as a holiday.
    poly = Polygon(set(), refuse_all=True)
    install(poly)
    try:
        raised = ""
        try:
            prices.fetch_and_cache_grouped_daily(
                start_date=date(2026, 8, 5), max_lookback_days=4,
                restrict_to_master=False,
            )
        except RuntimeError as e:
            raised = str(e)
        check("raises after exhausting the lookback", "No trading day found" in raised,
              raised[:90])
        check("carries the upstream 403 message", "NOT_AUTHORIZED" in raised, raised[:140])
    finally:
        restore(SAVED)


# ── refusing to succeed quietly ──────────────────────────────────────────────

def test_it_refuses_to_write_prices_that_are_too_old():
    print("\nguard: a gap wider than any real closure is an error, not a result")
    # Polygon still answers, but only for a date far in the past -- the shape of
    # an upstream problem rather than a holiday.
    poly = Polygon({"2026-07-20"})
    install(poly)
    try:
        raised = ""
        try:
            prices.fetch_and_cache_grouped_daily(
                start_date=date(2026, 8, 4), max_lookback_days=30,
                max_gap_days=5, restrict_to_master=False,
            )
        except RuntimeError as e:
            raised = str(e)
        check("raises rather than writing stale prices", "stale" in raised.lower(), raised[:90])
        check("names the date it would have written", "2026-07-20" in raised, raised[:90])
    finally:
        restore(SAVED)


def test_it_gives_up_instead_of_searching_forever():
    print("\nguard: a market that never opens is bounded, not infinite")
    poly = Polygon(set())  # nothing ever trades
    install(poly)
    try:
        raised = ""
        try:
            prices.fetch_and_cache_grouped_daily(
                start_date=date(2026, 8, 4), max_lookback_days=7,
                restrict_to_master=False,
            )
        except RuntimeError as e:
            raised = str(e)
        check("raises after the lookback is exhausted", "No trading day found" in raised,
              raised[:90])
        check("asked exactly max_lookback_days times", len(poly.asked) == 7,
              str(len(poly.asked)))
    finally:
        restore(SAVED)


# ── what gets written ────────────────────────────────────────────────────────

def test_it_keeps_only_symbols_the_app_scans():
    print("\nwrites: the grouped feed is filtered to ticker_master")
    poly = Polygon({"2026-08-04"}, tickers=5)
    written = install(poly, master={"TK0", "TK2", "TK4"})
    try:
        _, n = prices.fetch_and_cache_grouped_daily(start_date=date(2026, 8, 4))
        rows = [r for chunk in written for r in chunk]
        check("only master symbols are written", n == 3, str(n))
        check("the right ones", sorted(r["ticker"] for r in rows) == ["TK0", "TK2", "TK4"],
              str([r["ticker"] for r in rows]))
        # Pinned deliberately: an unexpected key means a PostgREST 400 on every
        # chunk, and a MISSING one means a column silently stops being written.
        # `volume` joined this set when it was added for sector-query ranking.
        check("rows carry exactly the columns stock_prices expects",
              all(set(r) == {"ticker", "close_price", "volume", "last_updated", "currency"}
                  for r in rows), str(set(rows[0]) if rows else {}))
    finally:
        restore(SAVED)


def test_an_unreadable_ticker_master_does_not_stop_the_sync():
    print("\nwrites: losing ticker_master costs precision, not the run")
    # Writing a few thousand extra instruments is harmless. Writing nothing,
    # because a secondary lookup failed, is not.
    poly = Polygon({"2026-08-04"}, tickers=5)
    written = install(poly, master_raises=True)
    try:
        _, n = prices.fetch_and_cache_grouped_daily(start_date=date(2026, 8, 4))
        check("still writes every ticker returned", n == 5, str(n))
        check("something was actually upserted", sum(len(c) for c in written) == 5)
    finally:
        restore(SAVED)


def test_it_writes_in_chunks_and_skips_junk():
    print("\nwrites: chunked upsert, and unusable rows are dropped")
    poly = Polygon({"2026-08-04"}, tickers=0)

    def with_junk(url, timeout=20):
        poly.asked.append("2026-08-04")
        return 200, {"resultsCount": 5, "results": [
            {"T": "AAA", "c": 1.0},
            {"T": "BBB", "c": None},      # no close
            {"T": "", "c": 2.0},          # no symbol
            {"T": "CCC", "c": 3.0},
            {"T": "DDD", "c": 4.0},
        ]}

    written = install(poly)
    prices._get_json = with_junk
    try:
        _, n = prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 8, 4), restrict_to_master=False, chunk_size=2,
        )
        check("junk rows are skipped", n == 3, str(n))
        check("written in chunk_size batches", [len(c) for c in written] == [2, 1],
              str([len(c) for c in written]))
    finally:
        restore(SAVED)


def test_duplicate_symbols_from_polygon_are_collapsed():
    print("\nupsert: Polygon returns some symbols twice, and that broke production")
    # 2026-08-07: the grouped feed returned BCPC and TPC twice out of 12,406.
    # PostgREST's merge-duplicates upsert is ON CONFLICT DO UPDATE, and Postgres
    # refuses when a key repeats in one statement -- HTTP 500. The first
    # duplicate sat at index 699, so chunk 1 committed and chunk 2 died,
    # discarding 5,480 good prices because of 2 bad rows.
    poly = Polygon({"2026-08-04"}, tickers=0)

    def dupes(url, timeout=20):
        poly.asked.append("2026-08-04")
        return 200, {"resultsCount": 4, "results": [
            {"T": "AAA", "c": 1.0, "v": 100},
            {"T": "BCPC", "c": 2.0, "v": 500},     # duplicate, higher volume
            {"T": "CCC", "c": 3.0, "v": 100},
            {"T": "BCPC", "c": 9.9, "v": 5},       # duplicate, thinner venue
        ]}

    written = install(poly)
    prices._get_json = dupes
    try:
        _, n = prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 8, 4), restrict_to_master=False,
        )
        rows = [r for c in written for r in c]
        syms = [r["ticker"] for r in rows]
        check("each symbol appears exactly once", len(syms) == len(set(syms)), str(syms))
        check("nothing else is dropped", n == 3, str(n))
        # The primary listing wins, not simply the last row seen.
        bcpc = [r for r in rows if r["ticker"] == "BCPC"][0]
        check("the higher-volume row survives", bcpc["close_price"] == 2.0,
              str(bcpc["close_price"]))
    finally:
        restore(SAVED)


def test_volume_is_captured_from_the_response_we_already_fetch():
    print("\nwrites: volume rides along in the same call as the close")
    # Ranking a sector's tickers by liquidity was previously called unbuildable
    # because no volume data existed. It was in the response all along, in `v`,
    # and being discarded. Nullable on purpose: a selector must distinguish
    # "unknown" from "did not trade".
    poly = Polygon({"2026-08-04"}, tickers=0)

    def with_volume(url, timeout=20):
        poly.asked.append("2026-08-04")
        return 200, {"resultsCount": 3, "results": [
            {"T": "AAA", "c": 10.0, "v": 1_500_000},
            {"T": "BBB", "c": 20.0, "v": 0},          # traded zero shares
            {"T": "CCC", "c": 30.0},                  # Polygon omitted volume
        ]}

    written = install(poly)
    prices._get_json = with_volume
    try:
        prices.fetch_and_cache_grouped_daily(
            start_date=date(2026, 8, 4), restrict_to_master=False)
        by = {r["ticker"]: r for r in (r for c in written for r in c)}
        check("volume is stored", by["AAA"]["volume"] == 1_500_000, str(by["AAA"]))
        check("zero volume is kept as zero, not dropped", by["BBB"]["volume"] == 0,
              str(by["BBB"]))
        check("a missing field becomes null, not zero", by["CCC"]["volume"] is None,
              str(by["CCC"]))
        check("close_price is unaffected", by["AAA"]["close_price"] == 10.0)
    finally:
        restore(SAVED)


def test_one_bad_chunk_does_not_discard_the_others():
    print("\nupsert: a failing chunk must not abandon the remaining ones")
    poly = Polygon({"2026-08-04"}, tickers=0)

    def many(url, timeout=20):
        poly.asked.append("2026-08-04")
        return 200, {"resultsCount": 5, "results":
                     [{"T": f"S{i}", "c": float(i), "v": 1} for i in range(5)]}

    prices._config = lambda name, default="": "fake-key"
    prices._get_json = many
    prices._fetch_ticker_master_symbols = lambda: set()

    attempted = []
    def flaky(chunk):
        attempted.append(len(chunk))
        if len(attempted) == 2:
            raise RuntimeError("simulated PostgREST 500")
    prices._upsert_stock_prices = flaky

    try:
        raised = ""
        try:
            prices.fetch_and_cache_grouped_daily(
                start_date=date(2026, 8, 4), restrict_to_master=False, chunk_size=2,
            )
        except RuntimeError as e:
            raised = str(e)
        check("every chunk was attempted despite the failure", len(attempted) == 3,
              str(attempted))
        check("the run still reports failure", "chunks failed" in raised, raised[:90])
        check("and says how much did get written", "/5 rows written" in raised, raised[:120])
    finally:
        restore(SAVED)


def test_a_day_with_no_usable_rows_is_an_error():
    print("\nwrites: 'returned data but none of it usable' must not report success")
    poly = Polygon({"2026-08-04"}, tickers=3)
    install(poly, master={"NOTHING_MATCHES"})
    try:
        raised = ""
        try:
            prices.fetch_and_cache_grouped_daily(start_date=date(2026, 8, 4))
        except RuntimeError as e:
            raised = str(e)
        check("raises instead of writing zero rows quietly", "none matched" in raised,
              raised[:90])
    finally:
        restore(SAVED)


def main() -> int:
    print("=" * 74)
    print("  grouped price sync: finding the trading day, and failing loudly")
    print("=" * 74)

    test_it_lands_on_the_day_that_traded()
    test_it_steps_over_a_weekend()
    test_it_steps_over_a_weekday_holiday()
    test_it_steps_back_when_the_plan_cannot_serve_today_yet()
    test_a_bad_api_key_is_not_disguised_as_a_closed_market()
    test_it_refuses_to_write_prices_that_are_too_old()
    test_it_gives_up_instead_of_searching_forever()
    test_it_keeps_only_symbols_the_app_scans()
    test_an_unreadable_ticker_master_does_not_stop_the_sync()
    test_it_writes_in_chunks_and_skips_junk()
    test_duplicate_symbols_from_polygon_are_collapsed()
    test_volume_is_captured_from_the_response_we_already_fetch()
    test_one_bad_chunk_does_not_discard_the_others()
    test_a_day_with_no_usable_rows_is_an_error()

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
