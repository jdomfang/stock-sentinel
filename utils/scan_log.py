"""Log every scan's per-ticker sentiment reading. Standard library only.

WHY

The open question behind the whole product is whether this sentiment
measurement carries any information about forward returns. Answering it needs
observations paired with the price at the time they were made, and neither can
be reconstructed afterwards -- X's index is 7 days deep, and a price snapshot
is only true on the day it is taken.

Deep Analyze cannot supply those observations. It has run four times, ever.
Discovery runs more often and yields ~10 valid tickers per run, so it produces
observations roughly 17x faster for posts that were bought anyway.

EVERY valid ticker is logged, not just the displayed top ten. The tail is
where a null result would surface first, and dropping it would bias the sample
toward whatever the ranking already favoured.

FAILURE POLICY

Never raises. This runs after the user's results have rendered; a logging
failure must not turn a delivered scan into an error. Errors are logged at
WARNING and swallowed -- but they ARE logged, because a logger that quietly
stopped is indistinguishable from one with nothing to record.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

TABLE = "scan_sentiment_log"

# PostgREST puts the ticker filter in the URL; a few hundred symbols stays well
# under any proxy limit.
_PRICE_CHUNK = 300


# Was a private copy of the same twelve lines in ten modules. Reaching into
# streamlit from analysis code is what kept this file inside the portal.
from utils.config import get as _config  # noqa: E402

def _endpoint() -> tuple[str, str] | None:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    return (base, key) if base and key else None


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def _fetch_prices(base: str, key: str, tickers: Sequence[str]) -> dict[str, dict]:
    """Latest close and volume for the tickers being logged.

    Best effort: a failure yields {} and the rows are written without
    stratification rather than not written at all.
    """
    out: dict[str, dict] = {}
    for i in range(0, len(tickers), _PRICE_CHUNK):
        chunk = tickers[i:i + _PRICE_CHUNK]
        qs = urllib.parse.urlencode({
            "select": "ticker,close_price,volume",
            "ticker": f"in.({','.join(chunk)})",
        })
        req = urllib.request.Request(
            f"{base}/rest/v1/stock_prices?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                for row in json.loads(r.read() or b"[]"):
                    out[str(row.get("ticker", "")).upper()] = row
        except Exception as e:
            logger.warning("scan_log price lookup failed: %s: %s",
                           type(e).__name__, str(e)[:160])
            return out
    return out


def record(
    sector: str,
    rows: Iterable[dict],
    displayed: Iterable[str],
    *,
    event_id: str | None = None,
    corpus_key: str | None = None,
    model: str | None = None,
) -> int:
    """Write one row per VALID ticker in the scan. Returns rows written.

    `rows` is the scan's per-ticker aggregate, before the top-N cut, each
    carrying Ticker / Mentions / Evidence / Avg Sentiment Score / Overall
    Sentiment / Valid. `displayed` is the shortlist that actually rendered.
    """
    ep = _endpoint()
    if ep is None:
        return 0
    base, key = ep

    shown = {str(t).upper() for t in (displayed or [])}
    # Only tickers in our universe: an unvalidated cashtag has no price to pair
    # the observation with, which is the entire point of recording it.
    valid = [r for r in (rows or []) if r.get("Valid") and r.get("Ticker")]
    if not valid:
        return 0

    symbols = [str(r["Ticker"]).upper()[:16] for r in valid]
    prices = _fetch_prices(base, key, symbols)

    payload = []
    for r in valid:
        t = str(r["Ticker"]).upper()[:16]
        p = prices.get(t) or {}
        payload.append({
            "sector": (str(sector)[:64] if sector else None),
            "ticker": t,
            "mentions": _int(r.get("Mentions")),
            "evidence_n": _int(r.get("Evidence")),
            "mean_margin": _num(r.get("Avg Sentiment Score")),
            "overall_sentiment": (str(r.get("Overall Sentiment"))[:32]
                                  if r.get("Overall Sentiment") else None),
            "displayed": t in shown,
            "price_at_scan": _num(p.get("close_price")),
            "volume_at_scan": _int(p.get("volume")),
            "event_id": event_id or None,
            "corpus_key": (str(corpus_key)[:128] if corpus_key else None),
            "model": (str(model)[:64] if model else None),
        })

    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}",
        data=json.dumps(payload).encode(),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status not in (200, 201, 204):
                logger.warning("scan_sentiment_log HTTP %s", r.status)
                return 0
    except urllib.error.HTTPError as e:
        logger.warning("scan_sentiment_log HTTP %s: %s", e.code,
                       (e.read() or b"")[:200].decode(errors="replace"))
        return 0
    except Exception as e:
        logger.warning("scan_sentiment_log failed: %s: %s",
                       type(e).__name__, str(e)[:160])
        return 0

    logger.info("📓 scan_sentiment_log: %d tickers (%d displayed) for %s",
                len(payload), sum(1 for p in payload if p["displayed"]), sector)
    return len(payload)
