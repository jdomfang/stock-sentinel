"""One-shot demo recorder (spends API credits once).

Records:
- data/demo/scan_tech.json
- data/demo/deep_NVDA_tech.json

Budget guidance (rough):
- X: 1 call for scan + 4 calls for deep analysis (collapsed) ~= 5 calls total
- Polygon: limited to TOP_N tickers for the scan table

Run:
  python3 scripts/record_demo.py

This script reads tokens from .streamlit/secrets.toml if present, otherwise env vars:
- X_BEARER_TOKEN
- POLYGON_API_KEY

"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List

# Ensure repo root is on sys.path so `utils.*` imports work when running as a script
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.sentiment import extract_tickers, analyze_sentiment
from utils.finance import validate_ticker, get_stock_data, get_ticker_master_list
from utils.projections import simple_projection
from utils.deep_analysis import run_deep_analysis

OUT_DIR = ROOT / "data" / "demo"

SECTOR = "tech"
DEEP_TICKER = "NVDA"

# Keep this small to control Polygon spend
TOP_N_VALIDATE = 6


def _load_secrets_toml() -> Dict[str, str]:
    p = ROOT / ".streamlit" / "secrets.toml"
    if not p.exists():
        return {}
    txt = p.read_text(encoding="utf-8")
    out: Dict[str, str] = {}
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def _get_token(name: str) -> str:
    s = _load_secrets_toml().get(name) or os.getenv(name)
    if not s:
        raise RuntimeError(f"Missing {name} in .streamlit/secrets.toml or env")
    return s


def x_recent_search(query: str, max_results: int, timeframe_h: int) -> List[Dict[str, Any]]:
    token = _get_token("X_BEARER_TOKEN")
    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {token}"}

    start_time = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - timeframe_h * 3600),
    )

    params = {
        "query": query,
        "max_results": min(int(max_results), 100),
        "tweet.fields": "text,created_at,public_metrics",
        "start_time": start_time,
    }

    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"X API error {r.status_code}: {r.text}")
    payload = r.json()
    return payload.get("data", []) or []


def build_sector_query(sector: str) -> str:
    sector_keywords = {
        "tech": "technology OR software OR AI OR chip OR semiconductor OR cloud OR internet",
        "healthcare": "healthcare OR medical OR pharma OR biotechnology OR drug OR clinical OR FDA",
        "energy": "energy OR oil OR gas OR renewable OR solar OR wind OR fossil OR petroleum",
        "finance": "finance OR bank OR financial OR investment OR lending OR credit OR wealth",
        "consumer": "consumer OR retail OR e-commerce OR shopping OR consumer goods OR discretionary",
        "utilities": "utilities OR electric OR power OR water OR gas OR infrastructure OR telecom",
        "real estate": "real estate OR property OR REIT OR housing OR commercial OR residential",
        "industrials": "industrials OR manufacturing OR industrial OR aerospace OR defense OR construction",
        "materials": "materials OR mining OR chemical OR steel OR cement OR commodity OR metals",
        "communication": "communication OR telecom OR media OR entertainment OR broadcasting OR wireless",
    }

    sector_terms = sector_keywords.get(sector.lower(), sector)
    return (
        f"({sector} OR {sector_terms}) stock "
        f"(bullish OR opportunity OR catalyst OR growth OR earnings) "
        f"-bearish lang:en -is:retweet"
    )


def record_scan_demo(sector: str) -> Dict[str, Any]:
    query = build_sector_query(sector)
    tweets = x_recent_search(query=query, max_results=100, timeframe_h=24)

    ticker_data = defaultdict(lambda: {"mentions": 0, "sentiment_scores": []})

    for tw in tweets:
        text = tw.get("text", "")
        tickers = extract_tickers(text)
        if not tickers:
            continue
        sent = analyze_sentiment(text)
        for t in tickers:
            ticker_data[t]["mentions"] += 1
            ticker_data[t]["sentiment_scores"].append(sent["score"])

    # Rank by mentions then avg sentiment
    rows = []
    for t, d in ticker_data.items():
        if d["mentions"] <= 0:
            continue
        avg = sum(d["sentiment_scores"]) / len(d["sentiment_scores"]) if d["sentiment_scores"] else 0.0
        rows.append((t.upper(), d["mentions"], avg))

    rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
    top = rows[:TOP_N_VALIDATE]

    # Use cached ticker master list for company names
    master = get_ticker_master_list() or {}

    validated_rows: List[Dict[str, Any]] = []

    for ticker, mentions, avg_sent in top:
        # Validate ticker (may hit Polygon depending on your current validate_ticker implementation)
        v = validate_ticker(ticker)
        if not v.get("valid", False):
            continue

        name = (master.get(ticker, {}) or {}).get("name") or v.get("name") or ticker

        # Pull market data (Polygon aggregates) and compute metrics
        sd = get_stock_data(ticker)
        if sd.get("error") is not None or not sd.get("prices"):
            continue

        prices = sd["prices"]
        current_price = prices[-1]

        # Volatility from finance module (already computed there as sd['volatility'] sometimes),
        # but compute a simple proxy if not present
        vol = sd.get("volatility")
        if vol is None:
            vol = 0.0

        proj = simple_projection(prices, avg_sent, days=30)
        projected_gain = proj.get("avg_gain", 0.0) if proj.get("error") is None else 0.0
        hold_days = proj.get("suggested_hold_days", 0) if proj.get("error") is None else 0

        overall = "Bullish" if avg_sent > 0.1 else ("Bearish" if avg_sent < -0.1 else "Neutral")

        validated_rows.append(
            {
                "Ticker": ticker,
                "Company Name": name,
                "Avg Sentiment Score": f"{avg_sent:.3f}",
                "Overall Sentiment": overall,
                "Volatility (%)": f"{vol:.2f}",
                "Projected Gain (%)": f"{projected_gain:.2f}",
                "Current Price ($)": f"{current_price:.2f}",
                "Suggested Hold (days)": str(int(hold_days)),
            }
        )

    return {
        "sector": sector,
        "generated_at": "demo",
        "notes": {
            "source": "one-shot snapshot",
            "x_query": query,
            "top_n_validated": TOP_N_VALIDATE,
        },
        "validated_rows": validated_rows,
    }


def record_deep_demo(ticker: str, sector: str) -> Dict[str, Any]:
    results = run_deep_analysis(ticker=ticker, sector=sector)
    return {
        "ticker": ticker,
        "sector": sector,
        "generated_at": "demo",
        "analysis_results": results,
    }


def main() -> None:
    # Make tokens available to modules that still use streamlit secrets.
    os.environ.setdefault("X_BEARER_TOKEN", _get_token("X_BEARER_TOKEN"))
    os.environ.setdefault("POLYGON_API_KEY", _get_token("POLYGON_API_KEY"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    scan_payload = record_scan_demo(SECTOR)
    (OUT_DIR / f"scan_{SECTOR}.json").write_text(json.dumps(scan_payload, indent=2), encoding="utf-8")
    print(f"Wrote demo scan: {OUT_DIR / f'scan_{SECTOR}.json'}")

    deep_payload = record_deep_demo(DEEP_TICKER, SECTOR)
    (OUT_DIR / f"deep_{DEEP_TICKER}_{SECTOR}.json").write_text(json.dumps(deep_payload, indent=2), encoding="utf-8")
    print(f"Wrote demo deep: {OUT_DIR / f'deep_{DEEP_TICKER}_{SECTOR}.json'}")


if __name__ == "__main__":
    main()
