#!/usr/bin/env python3
"""Did the verdicts work? Joins signal_log to price_history. Read-only, free.

WHY THIS EXISTS

Three of the four phases in the last release built measurement apparatus and
nothing read it. signal_log records the full adjudicator state for every
verdict; price_history accumulates daily closes for the whole US market. Until
something joins them, the product's central claim -- that Buy means the ticker
is likely to rise -- stays exactly as unfalsifiable as it was before any of it
was built.

WHAT IT ANSWERS, once enough rows exist

  does Buy beat Watch beat Avoid, over 1/3/5/10 sessions
  is the confidence tier calibrated, or is Moderate just a large corpus
  which BRANCH produced the returns -- buy_catalyst or buy_sentiment
  do the near misses (risk_high, channel_conflict, buy_downgraded_low_confidence)
    outperform the Buys that were issued
  is the quality bar in the right place
  is the sector exemption (market_wide) saving Buys or losing money

THE JOIN, AND WHY IT IS NOT created_at::date

decision_trade_date is the SESSION the recorded price belongs to. created_at is
when the row was written, which for a verdict issued at 02:00 UTC, on a weekend
or on a holiday is not a trading day at all -- so joining on it silently drops
roughly a third of the table and mis-anchors much of the rest. Rows written
before decision_trade_date existed cannot be scored and are reported as such
rather than quietly excluded.

FORWARD RETURN is measured from price_at_decision to the close N TRADING
sessions later, taken from price_history's own ordered dates for that ticker --
not from calendar arithmetic, which would land on holidays.

WHAT IT REFUSES TO DO

It does not report a hit rate on a handful of rows. Cohorts below MIN_COHORT
print their size and nothing else. A 60% hit rate on five observations is noise
with a decimal point, and the entire reason this table exists is that the
product has been run on numbers nobody checked.

COST: none. Two read-only Supabase queries.

Usage:
    python3 scripts/score_verdicts.py
    python3 scripts/score_verdicts.py --horizons 1,3,5,10,21
    python3 scripts/score_verdicts.py --min-cohort 1     # inspect early data
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DEFAULT_HORIZONS = (1, 3, 5, 10)

# Below this a cohort's number is not reported. See the module docstring.
MIN_COHORT = 20


def _config(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


def _get(path: str) -> list[dict]:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
    req = urllib.request.Request(
        f"{base}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"[]")


def fetch_signals() -> list[dict]:
    cols = ("created_at,ticker,feature,verdict,confidence,branch,model,"
            "adjudicator_version,price_at_decision,decision_trade_date,"
            "quality_score,quality_tier,eligible_clusters,social_direction,"
            "social_lean,catalyst_hard_clusters,catalyst_hard_direction,"
            "risk_high,price_status,price_excess_return_20d,seed_only")
    out, page = [], 0
    while True:
        qs = urllib.parse.urlencode({"select": cols, "order": "created_at.asc",
                                     "limit": 1000, "offset": page * 1000})
        rows = _get(f"signal_log?{qs}")
        out += rows
        if len(rows) < 1000:
            return out
        page += 1


def fetch_prices(tickers: set[str]) -> dict[str, list[tuple[str, float]]]:
    """ticker -> [(trade_date, close), ...] ascending. One query per 100 tickers."""
    series: dict[str, list[tuple[str, float]]] = defaultdict(list)
    tick = sorted(t for t in tickers if t)
    for i in range(0, len(tick), 100):
        chunk = tick[i:i + 100]
        qs = urllib.parse.urlencode({
            "select": "ticker,trade_date,close",
            "ticker": f"in.({','.join(chunk)})",
            "order": "trade_date.asc", "limit": 100000})
        for r in _get(f"price_history?{qs}"):
            try:
                series[r["ticker"]].append((str(r["trade_date"]),
                                            float(r["close"])))
            except (TypeError, ValueError):
                continue
    return series


def forward_return(series: list[tuple[str, float]], anchor_date: str,
                   entry: float, sessions: int) -> float | None:
    """Return from `entry` to the close `sessions` TRADING days after anchor.

    Trading days come from the series itself. Calendar arithmetic would land on
    weekends and holidays, where no bar exists, and silently drop the row.
    """
    if not series or not anchor_date or not entry or entry <= 0:
        return None
    idx = next((i for i, (d, _) in enumerate(series) if d >= anchor_date), None)
    if idx is None:
        return None
    # The anchor bar itself must exist at or before the target.
    tgt = idx + sessions
    if tgt >= len(series):
        return None            # not enough history has accumulated YET
    return (series[tgt][1] - entry) / entry


def summarise(name: str, rows: list[dict], horizons) -> None:
    n = len(rows)
    label = f"{name} (n={n})"
    if n < MIN_COHORT:
        print(f"  {label:<44} too few to report")
        return
    cells = []
    for h in horizons:
        vals = [r[f"fwd_{h}"] for r in rows if r.get(f"fwd_{h}") is not None]
        if len(vals) < MIN_COHORT:
            cells.append(f"{'n=' + str(len(vals)):>14}")
            continue
        hit = sum(1 for v in vals if v > 0) / len(vals)
        mean = sum(vals) / len(vals)
        cells.append(f"{hit:>6.0%} {mean:>+7.2%}")
    print(f"  {label:<44}" + " ".join(cells))


def main() -> int:
    args = sys.argv[1:]
    horizons = DEFAULT_HORIZONS
    if "--horizons" in args:
        horizons = tuple(int(x) for x in args[args.index("--horizons") + 1].split(","))
    global MIN_COHORT
    if "--min-cohort" in args:
        MIN_COHORT = int(args[args.index("--min-cohort") + 1])

    signals = fetch_signals()
    print("=" * 78)
    print("  DID THE VERDICTS WORK")
    print("=" * 78)
    if not signals:
        print("\n  signal_log is empty. Nothing to score yet.")
        return 0

    scoreable = [r for r in signals if r.get("decision_trade_date")
                 and r.get("price_at_decision")]
    unanchored = len(signals) - len(scoreable)
    print(f"\n  {len(signals)} verdict(s) logged, {str(signals[0]['created_at'])[:10]}"
          f" to {str(signals[-1]['created_at'])[:10]}")
    if unanchored:
        # Named, never silently dropped: these predate decision_trade_date and
        # can never be scored, which is a fact about the dataset.
        print(f"  {unanchored} cannot be scored (no anchor date or entry price)")

    prices = fetch_prices({r["ticker"] for r in scoreable})
    for r in scoreable:
        for h in horizons:
            r[f"fwd_{h}"] = forward_return(
                prices.get(r["ticker"], []), r["decision_trade_date"],
                float(r["price_at_decision"]), h)

    matured = [r for r in scoreable if any(r.get(f"fwd_{h}") is not None for h in horizons)]
    print(f"  {len(matured)} of {len(scoreable)} have enough forward history to score")
    print(f"\n  cohorts below n={MIN_COHORT} are not reported — a hit rate on a "
          "handful of rows is noise")

    hdr = " ".join(f"{str(h) + 'd hit  mean':>14}" for h in horizons)
    print("\n" + "-" * 78)
    print(f"  {'cohort':<44}{hdr}")
    print("-" * 78)

    def group(key, title):
        print(f"\n  ── by {title} ──")
        buckets = defaultdict(list)
        for r in matured:
            buckets[str(r.get(key))].append(r)
        for k in sorted(buckets):
            summarise(k, buckets[k], horizons)

    group("verdict", "verdict")
    group("branch", "cascade branch")
    group("confidence", "confidence")
    group("feature", "feature")
    group("price_status", "price status")
    group("quality_tier", "quality tier")

    # THE COMPARISON THE TABLE EXISTS FOR: issued Buys against the near misses.
    print("\n  ── Buy against the near misses ──")
    near = {"buy_downgraded_low_confidence", "channel_conflict", "no_alignment",
            "price_missing", "risk_high"}
    summarise("issued Buy", [r for r in matured if r["verdict"] == "Buy"], horizons)
    summarise("near miss (would-be Buy, blocked)",
              [r for r in matured if r["verdict"] != "Buy" and r.get("branch") in near],
              horizons)

    print("\n" + "=" * 78)
    print("  Nothing here is a claim until the cohorts clear their minimum.")
    print("  Every threshold in utils/modules.py and utils/verdict.py is")
    print("  provisional and stays provisional until this table says otherwise.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
