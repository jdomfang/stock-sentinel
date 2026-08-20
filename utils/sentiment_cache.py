"""Cache FinBERT distributions by exact text. Standard library only.

WHY

FinBERT is deterministic -- the same string at the same model version always
yields the same distribution -- yet nothing stores the result. Dedup exists only
WITHIN one call, so identical text is re-scored across pages, across scans, and
across Deep Analyze's keyword buckets.

Measured: a repeat sector scan inside the 6h corpus-cache window costs ZERO X
API posts and still pays the full ~17 seconds of inference, re-scoring exactly
the posts it just replayed. Deep Analyze scores ~2.4x its corpus size for the
same reason.

That latency is not cosmetic. In Streamlit any new interaction stops the running
script, and the scan's own comments record that the likeliest way a scan dies is
a user clicking again because nothing looks like it is happening -- which spends
a credit and delivers nothing.

FAILURE POLICY

Nothing here may raise into a caller. A cache is an optimisation; a broken one
must degrade to "always miss", which is slower but identical in output. Errors
are logged at WARNING and swallowed -- but they ARE logged, because a cache that
silently stopped working looks exactly like one that is working.

Stdlib-only for the same reason as utils/prices.py and utils/corpus_cache.py:
one implementation the portal and any bare container can both import.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

logger = logging.getLogger(__name__)

TABLE = "sentiment_cache"

# Rows per PostgREST round trip. The `in.(...)` filter goes in the URL, and a
# sha256 hex is 64 characters, so a few hundred keys per request keeps the URL
# comfortably under any proxy limit.
_CHUNK = 200


# Was a private copy of the same twelve lines in ten modules. Reaching into
# streamlit from analysis code is what kept this file inside the portal.
from utils.config import get as _config  # noqa: E402

def _endpoint() -> tuple[str, str] | None:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return None
    return base, key


def text_key(text: str) -> str:
    """Hash of the EXACT string that will be sent to the model.

    Callers must pass the already-truncated text. Hashing anything else -- a
    normalised, lowercased or stripped variant -- would file the score under a
    string that is not the one scored, which is a silent correctness bug rather
    than a cache miss.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# Duplicated from utils.sentiment.score_finbert_output rather than imported:
# utils.sentiment imports THIS module, so importing back would be circular. The
# behaviour is pinned on both sides by tests/test_characterization.py, the same
# arrangement inference/main.py already uses.
NEUTRAL_THRESHOLD = 0.55


def _from_distribution(p_pos: float, p_neg: float, p_neu: float) -> dict[str, Any]:
    """Rebuild a full result from stored probabilities.

    EVERY field is derived, including `score`. An earlier version hardcoded
    score = 0.0, which was invisible to the sector scan (it reads `margin`) and
    silently destroyed Deep Analyze, which aggregates on `score` alone: one
    corpus is re-scored across 8 keyword buckets, so bucket 1 populated the
    cache and buckets 2-8 read back 0.0 and collapsed to Neutral. A paid
    analysis would have returned a fabricated verdict with no error anywhere.

    Deriving rather than storing also means a changed threshold cannot serve a
    stale label: the probabilities are the source of truth.
    """
    pairs = (("POSITIVE", p_pos), ("NEGATIVE", p_neg), ("NEUTRAL", p_neu))
    label, confidence = max(pairs, key=lambda kv: kv[1])

    if confidence < NEUTRAL_THRESHOLD or label == "NEUTRAL":
        signed, sentiment = 0.0, "Neutral"
    elif label == "POSITIVE":
        signed, sentiment = confidence, "Bullish"
    else:
        signed, sentiment = -confidence, "Bearish"

    return {
        "label": label,
        "confidence": confidence,
        "score": signed,
        "sentiment": sentiment,
        "p_positive": p_pos,
        "p_negative": p_neg,
        "p_neutral": p_neu,
        "margin": p_pos - p_neg,
    }


def get_many(texts: Iterable[str], model: str) -> dict[str, dict[str, Any]]:
    """Return {text: distribution} for whatever is already cached.

    Never raises. An unreachable or missing table yields {} and the caller
    scores everything, exactly as it does today.
    """
    ep = _endpoint()
    wanted = {text_key(t): t for t in texts}
    if ep is None or not wanted:
        return {}
    base, key = ep

    found: dict[str, dict[str, Any]] = {}
    keys = list(wanted)
    for i in range(0, len(keys), _CHUNK):
        chunk = keys[i:i + _CHUNK]
        qs = urllib.parse.urlencode({
            "select": "text_sha256,p_positive,p_negative,p_neutral,label,sentiment",
            "model": f"eq.{model}",
            "text_sha256": f"in.({','.join(chunk)})",
        })
        req = urllib.request.Request(
            f"{base}/rest/v1/{TABLE}?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                rows = json.loads(r.read() or b"[]")
        except Exception as e:
            logger.warning("sentiment_cache get failed: %s: %s",
                           type(e).__name__, str(e)[:160])
            return found  # partial is fine; the rest are scored normally

        for row in rows:
            t = wanted.get(row.get("text_sha256"))
            if t is None:
                continue
            p_pos = float(row.get("p_positive") or 0.0)
            p_neg = float(row.get("p_negative") or 0.0)
            p_neu = float(row.get("p_neutral") or 0.0)
            found[t] = _from_distribution(p_pos, p_neg, p_neu)
    if found:
        logger.info("sentiment_cache: %d/%d texts already scored",
                    len(found), len(wanted))
    return found


def put_many(scored: dict[str, dict[str, Any]], model: str) -> bool:
    """Store {text: distribution}. Returns True on success; never raises.

    Rows whose distribution is entirely zero are skipped: that is what a
    pre-distribution inference service (or the local dev fallback) returns, and
    caching it would poison every future lookup with a fabricated Neutral.
    """
    ep = _endpoint()
    if ep is None or not scored:
        return False
    base, key = ep

    rows = []
    for text, d in scored.items():
        p_pos = float(d.get("p_positive") or 0.0)
        p_neg = float(d.get("p_negative") or 0.0)
        p_neu = float(d.get("p_neutral") or 0.0)
        if p_pos == 0.0 and p_neg == 0.0 and p_neu == 0.0:
            continue
        rows.append({
            "text_sha256": text_key(text),
            "model": model,
            "p_positive": p_pos,
            "p_negative": p_neg,
            "p_neutral": p_neu,
            "label": d.get("label"),
            "sentiment": d.get("sentiment"),
        })
    if not rows:
        return False

    ok = True
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i:i + _CHUNK]
        req = urllib.request.Request(
            f"{base}/rest/v1/{TABLE}",
            data=json.dumps(chunk).encode(),
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status not in (200, 201, 204):
                    logger.warning("sentiment_cache put HTTP %s", r.status)
                    ok = False
        except urllib.error.HTTPError as e:
            logger.warning("sentiment_cache put HTTP %s: %s", e.code,
                           (e.read() or b"")[:200].decode(errors="replace"))
            ok = False
        except Exception as e:
            logger.warning("sentiment_cache put failed: %s: %s",
                           type(e).__name__, str(e)[:160])
            ok = False
    if ok:
        logger.info("sentiment_cache: stored %d distribution(s)", len(rows))
    return ok
