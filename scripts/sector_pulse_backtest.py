#!/usr/bin/env python3
"""Does `accumulating` mean anything? Read-only test of the sector pulse over the backfilled year.

WHY

utils/sector_pulse.py labels each sector-day accumulating / distributing /
event / quiet using four thresholds that were guessed from three weeks of bars.
The strip built on them will tell a paying user where to spend a credit. Before
it may rank anything, the labels have to be shown to carry information -- or
the product has to stop claiming they do.

docs/SECTOR_PULSE.md holds the decision rule, and it was written and committed
BEFORE this script was run. That ordering is the point: a threshold chosen
because it flattered the result is not evidence, and the only defence against
choosing one is to fix the rule while the answer is still unknown.

WHAT IT MEASURES

For every sector-day in price_history it recomputes the pulse using the REAL
functions (P.eligible_names, P.compute_day, P.classify -- not a copy), then
measures the forward equal-weight return of exactly the names that were scored,
minus the median return of the whole eligible market over the same window.

TWO HORIZONS, AND ONLY ONE IS HONEST

  D  -> D+k    from the signal day's close. NOT achievable: the pulse is
               computed at 23:00 UTC, after that close. Reported to show how
               much of any edge lives in the gap you cannot trade.
  D+1-> D+1+k  from the next session's close: the first price a reader of the
               strip could actually get. THIS is what the decision rule uses.

WHAT IT DOES NOT DO

It writes nothing -- not to Supabase, not to sector_pulse. It costs no API
calls beyond reading price_history, and it does not touch X or Polygon.

Bars are cached to disk on first run (~1.59M rows, ~1,590 PostgREST requests,
about 11 minutes) so re-runs and threshold sweeps are instant.

Usage:
    python3 scripts/sector_pulse_backtest.py
    python3 scripts/sector_pulse_backtest.py --refresh      # re-fetch bars
    python3 scripts/sector_pulse_backtest.py --no-sweep     # rule only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import sector_pulse as P  # noqa: E402
from utils.config import get as _config  # noqa: E402

logger = logging.getLogger("sector_pulse_backtest")

DEFAULT_CACHE = os.environ.get(
    "PULSE_BACKTEST_CACHE",
    "/tmp/claude-1000/-home-jdomfang-stock-sentinel/"
    "30444001-6562-41ac-9ee9-b417680c5614/scratchpad/pulse_bars.pkl",
)

# The pre-registered values under test. Read from the module so this cannot
# silently disagree with what production runs.
REGISTERED = {
    "UD_ACCUMULATING": P.UD_ACCUMULATING,
    "UD_DISTRIBUTING": P.UD_DISTRIBUTING,
    "BREADTH_MIN": P.BREADTH_MIN,
    "EVENT_SHARE": P.EVENT_SHARE,
}
MIN_EVENTS = 100          # decision rule: at least this many `accumulating` days
HORIZONS = (3, 5)


# ------------------------------------------------------------------ loading --

def fetch_bars(cache: str, refresh: bool = False):
    """(sec, names, bars, dates). Cached on disk: the fetch is ~1,590 requests."""
    if not refresh and os.path.exists(cache):
        with open(cache, "rb") as f:
            d = pickle.load(f)
        print(f"  bars from cache: {len(d['dates'])} dates, {len(d['bars'])} tickers "
              f"({os.path.getsize(cache)/1e6:.0f} MB)  [--refresh to re-fetch]")
        return d["sec"], d["names"], d["bars"], d["dates"]

    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured")
    t0 = time.time()
    sec, names = P.load_sectors(base, key)
    print(f"  {len(sec)} tickers mapped to a sector", flush=True)

    # Page manually so progress is visible; PostgREST caps a page at 1000 rows
    # whatever limit is asked for, so a year is ~1,590 round trips.
    import urllib.request
    bars: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    offset, rows_seen = 0, 0
    while True:
        url = (f"{base}/rest/v1/price_history?select=ticker,trade_date,close,volume"
               f"&order=trade_date.asc,ticker.asc&limit=1000&offset={offset}")
        req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=90) as r:
            chunk = json.loads(r.read() or b"[]")
        for row in chunk:
            t = (row.get("ticker") or "").upper()
            c, v = row.get("close"), row.get("volume")
            # Only tickers that belong to a sector can ever be scored; dropping
            # the rest here keeps the cache and the working set small.
            if t in sec and c is not None and v:
                bars[t][row["trade_date"]] = (float(c), float(c) * float(v))
        rows_seen += len(chunk)
        offset += 1000
        if rows_seen % 100000 == 0:
            print(f"    {rows_seen:,} rows...", flush=True)
        if len(chunk) < 1000:
            break
    dates = sorted({d for s in bars.values() for d in s})
    print(f"  fetched {rows_seen:,} rows -> {len(bars)} tickers x {len(dates)} dates "
          f"in {time.time()-t0:.0f}s", flush=True)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "wb") as f:
        pickle.dump({"sec": sec, "names": names, "bars": dict(bars), "dates": dates}, f, protocol=4)
    return sec, names, dict(bars), dates


# ------------------------------------------------------------------- maths --

def eq_return(tickers, bars, d0: str, d1: str) -> float | None:
    """Equal-weight return of `tickers` from close d0 to close d1."""
    rs = [bars[t][d1][0] / bars[t][d0][0] - 1.0
          for t in tickers if d0 in bars.get(t, {}) and d1 in bars.get(t, {})]
    return statistics.mean(rs) if rs else None


def market_median(universe, bars, d0: str, d1: str) -> float | None:
    rs = [bars[t][d1][0] / bars[t][d0][0] - 1.0
          for t in universe if d0 in bars.get(t, {}) and d1 in bars.get(t, {})]
    return statistics.median(rs) if rs else None


def compute_all(sec, names, bars, dates):
    """Every sector-day the pulse can score, with the eligible set it scored.

    Returns {(sector, date): (row, eligible)}. States are NOT assigned here --
    classification is re-run per threshold set by the sweep.
    """
    out = {}
    first = P.BASELINE_SESSIONS + 2          # need a baseline and a 2-day-prior breadth
    total = (len(dates) - first) * len(P.UI_TO_NASDAQ)
    done = 0
    for i in range(first, len(dates)):
        day = dates[i]
        for sector in P.UI_TO_NASDAQ:
            row = P.compute_day(sector, day, dates, bars, sec, names)
            done += 1
            if row is None:
                continue
            elig, _ = P.eligible_names(sector, day, dates, bars, sec, names)
            out[(sector, day)] = (row, elig)
        if done % 500 < len(P.UI_TO_NASDAQ):
            print(f"    scored {done:,}/{total:,} sector-days...", flush=True)
    return out


def classify_all(scored, dates):
    """Assign a state to every scored sector-day using the module's CURRENT thresholds."""
    idx = {d: i for i, d in enumerate(dates)}
    states = {}
    for (sector, day), (row, _) in scored.items():
        i = idx[day]
        prior = scored.get((sector, dates[i - 2])) if i >= 2 else None
        states[(sector, day)] = P.classify(row, prior[0]["breadth"] if prior else None)
    return states


def forward_returns(scored, bars, dates):
    """{(sector, day): {label: excess}} -- computed ONCE.

    A forward return does not depend on any threshold: the eligible set and the
    prices are fixed by the day. Only the LABEL attached to it moves when a
    threshold moves. Recomputing these inside the sweep would repeat ~48 million
    operations 27 times to arrive at the same numbers.
    """
    idx = {d: i for i, d in enumerate(dates)}
    # The tradeable market on a given day: every name eligible in any sector.
    # Taken from day D only -- using a later day's universe would be look-ahead.
    universe_by_day = defaultdict(set)
    for (sector, day), (_, elig) in scored.items():
        universe_by_day[day].update(elig)

    # market_median over ~5,000 names repeats per sector; cache per (day, d0, d1).
    mkt_cache: dict[tuple, float | None] = {}

    def mkt(day, d0, d1):
        k = (day, d0, d1)
        if k not in mkt_cache:
            mkt_cache[k] = market_median(universe_by_day[day], bars, d0, d1)
        return mkt_cache[k]

    fwd: dict[tuple, dict[str, float]] = {}
    for (sector, day), (row, elig) in scored.items():
        i = idx[day]
        got: dict[str, float] = {}
        for k in HORIZONS:
            # optimistic: from the signal day's close (not achievable)
            if i + k < len(dates):
                s, m = eq_return(elig, bars, day, dates[i + k]), mkt(day, day, dates[i + k])
                if s is not None and m is not None:
                    got[f"D->D+{k}"] = s - m
            # realistic: k sessions starting from the next close
            if i + 1 + k < len(dates):
                s = eq_return(elig, bars, dates[i + 1], dates[i + 1 + k])
                m = mkt(day, dates[i + 1], dates[i + 1 + k])
                if s is not None and m is not None:
                    got[f"D+1->D+1+{k}"] = s - m
        fwd[(sector, day)] = got
    return fwd


def bucket(fwd, states):
    """{state: {label: [excess]}}. Cheap: a re-label, not a recomputation."""
    acc = defaultdict(lambda: defaultdict(list))
    for key, got in fwd.items():
        st = states[key]
        for label, x in got.items():
            acc[st][label].append(x)
    return acc


def summarise(acc, label: str) -> dict:
    """{state: (n, median, mean, hit_rate, p25, p75)} for one horizon label."""
    out = {}
    for st in list(P.STATES) + ["ALL"]:
        xs = acc[st][label] if st != "ALL" else [x for s in P.STATES for x in acc[s][label]]
        if xs:
            q = sorted(xs)
            out[st] = (len(xs), statistics.median(xs), statistics.mean(xs),
                       sum(1 for x in xs if x > 0) / len(xs),
                       q[len(q) // 4], q[(3 * len(q)) // 4])
    return out


# ------------------------------------------------------------------ report --

def print_table(title: str, s: dict) -> None:
    print(f"\n  {title}")
    print(f"    {'state':<15}{'n':>7}{'median':>9}{'mean':>9}{'positive':>10}"
          f"{'p25':>9}{'p75':>9}")
    for st in list(P.STATES) + ["ALL"]:
        if st in s:
            n, med, mean, hit, p25, p75 = s[st]
            print(f"    {st:<15}{n:>7}{med*100:>8.2f}%{mean*100:>8.2f}%{hit*100:>9.0f}%"
                  f"{p25*100:>8.1f}%{p75*100:>8.1f}%")
    print("    (p25/p75 are the spread of individual sector-days. An edge much smaller")
    print("     than that spread is inside the noise, whatever its sign.)")


def sweep(scored, fwd, dates):
    """Re-classify under 27 threshold combinations. DIAGNOSTIC ONLY -- see the decision rule.

    Forward returns are passed in already computed: only the labelling changes,
    so each combination is a re-bucket rather than a recomputation.
    """
    print("\n  THRESHOLD SWEEP -- diagnostic, not a selection procedure.")
    print("  27 combinations will produce a best one by chance; this shows whether the")
    print("  pre-registered values sit on a cliff or a plateau.\n")
    print(f"    {'U/D':>5}{'breadth':>9}{'event':>7}  |{'acc n':>7}{'acc med':>9}"
          f"{'quiet n':>9}{'quiet med':>11}{'edge':>8}")
    saved = {k: getattr(P, k) for k in REGISTERED}
    rows = []
    try:
        for ud in (1.2, 1.3, 1.5):
            for br in (0.15, 0.20, 0.25):
                for ev in (0.30, 0.40, 0.50):
                    P.UD_ACCUMULATING, P.BREADTH_MIN, P.EVENT_SHARE = ud, br, ev
                    s = summarise(bucket(fwd, classify_all(scored, dates)), "D+1->D+1+5")
                    a, q = s.get("accumulating"), s.get("quiet")
                    if not a or not q:
                        continue
                    edge = a[1] - q[1]
                    mark = "  <-- registered" if (ud, br, ev) == (
                        saved["UD_ACCUMULATING"], saved["BREADTH_MIN"], saved["EVENT_SHARE"]) else ""
                    print(f"    {ud:>5.1f}{br:>9.2f}{ev:>7.2f}  |{a[0]:>7}{a[1]*100:>8.2f}%"
                          f"{q[0]:>9}{q[1]*100:>10.2f}%{edge*100:>7.2f}%{mark}")
                    rows.append((ud, br, ev, a[0], a[1], q[0], q[1], edge))
    finally:
        for k, v in saved.items():
            setattr(P, k, v)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--refresh", action="store_true", help="re-fetch bars from Supabase")
    ap.add_argument("--no-sweep", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    print("=" * 78)
    print("  SECTOR PULSE BACKTEST -- read-only. Decision rule: docs/SECTOR_PULSE.md")
    print("=" * 78)
    print(f"  thresholds under test: {REGISTERED}")

    sec, names, bars, dates = fetch_bars(args.cache, args.refresh)
    print(f"  {len(dates)} trading days: {dates[0]} .. {dates[-1]}")

    t0 = time.time()
    scored = compute_all(sec, names, bars, dates)
    print(f"  scored {len(scored):,} sector-days in {time.time()-t0:.0f}s")

    states = classify_all(scored, dates)
    counts = defaultdict(int)
    for v in states.values():
        counts[v] += 1
    print(f"\n  state counts: " + "  ".join(f"{k}={counts[k]}" for k in P.STATES))

    t1 = time.time()
    fwd = forward_returns(scored, bars, dates)
    print(f"  forward returns for {len(fwd):,} sector-days in {time.time()-t1:.0f}s")
    acc = bucket(fwd, states)
    for k in HORIZONS:
        print_table(f"forward {k} sessions from the signal close (NOT achievable)",
                    summarise(acc, f"D->D+{k}"))
    for k in HORIZONS:
        print_table(f"forward {k} sessions from the NEXT close (the honest one)",
                    summarise(acc, f"D+1->D+1+{k}"))

    # ---- the decision rule, applied exactly as written ----
    s = summarise(acc, "D+1->D+1+5")
    a, q = s.get("accumulating"), s.get("quiet")
    print("\n" + "=" * 78)
    print("  DECISION RULE (docs/SECTOR_PULSE.md, committed before this ran)")
    print("    Ship the ranking IF accumulating beats quiet on median forward 5-session")
    print("    excess return from the NEXT close, with at least 100 accumulating events.")
    print("-" * 78)
    if not a or not q:
        print("    RESULT: INSUFFICIENT DATA -- one of the states never occurred.")
        verdict = 2
    else:
        enough, beats = a[0] >= MIN_EVENTS, a[1] > q[1]
        print(f"    accumulating: n={a[0]}  median {a[1]*100:+.2f}%")
        print(f"    quiet       : n={q[0]}  median {q[1]*100:+.2f}%")
        print(f"    edge        : {(a[1]-q[1])*100:+.2f} percentage points")
        print(f"    at least {MIN_EVENTS} events: {'YES' if enough else f'NO ({a[0]})'}")
        print(f"    beats quiet            : {'YES' if beats else 'NO'}")
        print("-" * 78)
        if enough and beats:
            print("    VERDICT: RULE MET -- the strip may rank sectors.")
            verdict = 0
        else:
            print("    VERDICT: RULE NOT MET -- ship the labels WITHOUT a ranking claim.")
            print("    The strip describes breadth, direction and who is driving it, and")
            print("    must not order sectors by implied opportunity or say 'candidate'.")
            verdict = 1
    print("=" * 78)

    if not args.no_sweep:
        sweep(scored, fwd, dates)
    return verdict


if __name__ == "__main__":
    sys.exit(main())
