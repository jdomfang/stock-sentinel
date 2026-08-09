"""Persist every Deep Analyze verdict. Standard library only.

WHY THIS EXISTS

No verdict this product has ever produced has been written down. That makes
the central claim -- that the Buy/Watch/Avoid call is worth a credit --
unfalsifiable, not because it is wrong but because nothing was recorded.

The asymmetry that matters: X's recent-search index covers 7 days and cannot
be backfilled, and price history only accumulates forward. A verdict not
written today is a verdict that can never be scored, no matter how much
analysis is done later. Every other open question in this system can be
answered whenever we get to it. This one is the only one with a clock.

What it enables, once weeks of rows exist:
  - did Buy outperform Avoid over 5, 10, 30 days
  - is confidence calibrated, or is "High" just a large corpus
  - replaying a threshold change against real history instead of against the
    three corpora we happen to have retained

FAILURE POLICY

Nothing here may raise into a caller. The user has already paid and the panel
has already rendered; a logging failure must never turn a delivered analysis
into an error. Errors are logged at WARNING and swallowed -- but they ARE
logged, because a logger that silently stopped working looks exactly like one
with nothing to record.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "verdict_log"


def _config(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


def _num(v: Any) -> float | None:
    """Coerce to float, or None. Never raises on junk."""
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None  # drop NaN/inf
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def record(
    ticker: str,
    verdict: str,
    *,
    sector: str | None = None,
    confidence: str | None = None,
    avg_sentiment: Any = None,
    red_flag_rate: Any = None,
    disagreement: Any = None,
    total_mentions: Any = None,
    price_at_verdict: Any = None,
    projected_p10: Any = None,
    projected_p90: Any = None,
    suggested_hold_days: Any = None,
    success_rate: Any = None,
    event_id: str | None = None,
    corpus_key: str | None = None,
    model: str | None = None,
) -> bool:
    """Write one verdict. Returns True on success; never raises.

    price_at_verdict is the field most easily forgotten and the one without
    which none of the rest can be scored: forward return is meaningless if the
    starting price was not recorded at the moment of the call.
    """
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key or not ticker or not verdict:
        return False

    row = {
        "ticker": str(ticker).upper()[:16],
        "verdict": str(verdict)[:16],
        "sector": (str(sector)[:64] if sector else None),
        "confidence": (str(confidence)[:16] if confidence else None),
        "avg_sentiment": _num(avg_sentiment),
        "red_flag_rate": _num(red_flag_rate),
        "disagreement": _num(disagreement),
        "total_mentions": _int(total_mentions),
        "price_at_verdict": _num(price_at_verdict),
        "projected_p10": _num(projected_p10),
        "projected_p90": _num(projected_p90),
        "suggested_hold_days": _int(suggested_hold_days),
        "success_rate": _num(success_rate),
        "event_id": event_id or None,
        "corpus_key": (str(corpus_key)[:128] if corpus_key else None),
        "model": (str(model)[:64] if model else None),
    }

    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}",
        data=json.dumps([row]).encode(),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status not in (200, 201, 204):
                logger.warning("verdict_log HTTP %s", r.status)
                return False
    except urllib.error.HTTPError as e:
        logger.warning("verdict_log HTTP %s: %s", e.code,
                       (e.read() or b"")[:200].decode(errors="replace"))
        return False
    except Exception as e:
        logger.warning("verdict_log failed: %s: %s", type(e).__name__, str(e)[:160])
        return False

    logger.info("📓 verdict_log: %s %s/%s @ %s", row["ticker"], row["verdict"],
                row["confidence"], row["price_at_verdict"])
    return True
