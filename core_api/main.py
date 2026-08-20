"""core-api: the analysis, as a service. The last piece of the migration.

WHAT THIS IS FOR

Phase 0 stabilised the credit engine and Phase 1 extracted inference, the
worker and price-sync. This is the remaining architecture piece: it moves the
business logic out of the Streamlit pages so the portal becomes a UI, and so the
same analysis can be called by anything -- a scheduled job, a second front end,
a backfill -- without a browser session.

IT SHARES utils/, IT DOES NOT COPY IT. Vendoring the analysis into this
directory would create a second copy of the thing the whole product depends on,
and two copies of one analysis is exactly how pages/Discovery.py and
pages/Deep_Analysis.py drifted apart. utils/ is now importable without
streamlit, which is what Step 1 was for; this service simply imports it.

WHAT IT RETURNS

The CARD, not raw data. The invariant that the explanation can never contradict
the decision is enforced on the Verdict object and NOT on the renderers -- three
of the four that draw the page never see it, which is where every contradiction
found in review came from. utils.analyze.card() builds the presentation beside
the state that justifies it, and a client renders what it is handed.

WHAT IT DELIBERATELY DOES NOT DO

Charge credits. That needs an authenticated identity, and identity belongs to
whoever owns the session. The portal charges before calling; a future
authenticated caller will charge from its own token. Putting the debit here
without an identity would mean either charging nobody or trusting a caller's
claim about who they are, and this endpoint is protected only by a shared
secret -- which authenticates the SERVICE, never a user.

AUTH: a shared secret in a header, matching inference/main.py. This service can
spend real money (X API posts are billed per post returned), so an unprotected
deployment is a bill, not just a leak. It refuses to start without one.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("core_api")

SHARED_SECRET = os.getenv("CORE_API_SHARED_SECRET", "")
SERVICE_VERSION = "2026.08.20-step2"

# THE BUDGET THE PORTAL HAS AND THIS SERVICE DOES NOT.
#
# pages/Deep_Analysis.py charges a credit before any work, atomically, against a
# per-user balance -- so the balance IS the spend limit. A shared secret
# authenticates a caller; it does not bound one. Without the three controls
# below, one analysis buys up to ~400 billed X posts and FastAPI runs a sync
# handler on a 40-slot threadpool, so fifty concurrent requests is ~16,000
# posts a minute. Fifty for the SAME ticker is worse: corpus_cache only stores
# after the fetch completes, so all fifty miss and all fifty pay.
MAX_CONCURRENT = int(os.getenv("CORE_API_MAX_CONCURRENT", "3"))
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)

# One in-flight analysis per ticker. The second caller waits for the first and
# then reads its corpus from cache, instead of buying a second copy.
_INFLIGHT: dict[str, threading.Lock] = {}
_INFLIGHT_GUARD = threading.Lock()

app = FastAPI(title="Stock Sentinel Core API")


def _ticker_lock(t: str) -> threading.Lock:
    with _INFLIGHT_GUARD:
        return _INFLIGHT.setdefault(t, threading.Lock())


def _config_blockers() -> list[str]:
    """What would make this analysis a waste of money, checked BEFORE spending.

    Every one of these has the same shape of failure: the posts are bought, the
    step that needed them fails, and the caller gets a 200 saying there was no
    evidence -- which reads exactly like a quiet market. Worse, retrieval raises
    before corpus_cache.put, so the corpus is not even kept and the retry buys
    it again.
    """
    from utils import config as _c
    _cfg = _c.get

    missing = []
    if not _cfg("INFERENCE_URL"):
        missing.append("INFERENCE_URL")          # nothing can be scored
    if not _cfg("X_BEARER_TOKEN"):
        missing.append("X_BEARER_TOKEN")         # nothing can be retrieved
    if not _cfg("POLYGON_API_KEY"):
        # The price veto fails closed, so no Buy is reachable and the answer is
        # wrong rather than merely absent.
        missing.append("POLYGON_API_KEY")
    if not _cfg("SUPABASE_URL") or not _cfg("SUPABASE_SERVICE_ROLE_KEY"):
        # No corpus cache: EVERY request pays full price. And no telemetry.
        missing.append("SUPABASE")
    return missing


def _authorise(provided: str | None) -> None:
    if not SHARED_SECRET:
        # Fail CLOSED. An analysis buys X posts, and X bills per post returned,
        # so an open endpoint is a spending endpoint.
        raise HTTPException(status_code=503,
                            detail="CORE_API_SHARED_SECRET is not configured")
    if not provided or not hmac.compare_digest(provided, SHARED_SECRET):
        raise HTTPException(status_code=401, detail="bad or missing secret")


class AnalyzeRequest(BaseModel):
    # PATTERNED, not merely length-checked. `t` is interpolated straight into
    # the billed X query -- `($TSLA OR "Tesla")` -- so six characters containing
    # a quote or a parenthesis rewrite a query that costs money, and fork the
    # corpus cache key so the result is never reused. The portal validates the
    # same shape on its URL path; the service was looser than the portal.
    ticker: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,5}$")
    sector: str = "unknown"
    # Correlates this analysis with the credit the caller already charged, so a
    # disputed call can be reconstructed end to end.
    event_id: str | None = None
    # A caller that only wants the answer -- a replay, a dry run, a comparison
    # against the portal's own result -- must be able to skip the telemetry.
    persist: bool = True


@app.get("/health")
async def health() -> dict:
    """Liveness plus what would actually change an answer."""
    from utils.evidence import directional_margin
    try:
        from utils.sentiment import MODEL_NAME
    except Exception:
        MODEL_NAME = "unavailable"
    blockers = _config_blockers()
    return {
        # NOT ok without inference. Without INFERENCE_URL this service falls to
        # the in-process scorer, which its image has no torch for -- so every
        # analysis raises and returns "no usable evidence", which reads exactly
        # like a quiet market. Reporting it here makes a misconfigured deploy
        # visible instead of merely unproductive.
        "ok": bool(SHARED_SECRET) and not blockers,
        "service": "core-api", "version": SERVICE_VERSION,
        "secret_configured": bool(SHARED_SECRET),
        "missing_config": blockers,
        # Reported because it is the pair that silently changes every verdict:
        # the model, and the gate its confidences are judged against.
        "model": MODEL_NAME,
        "directional_margin": directional_margin(),
    }


@app.get("/ready")
async def ready() -> dict:
    """async ON PURPOSE. /analyze is a sync def on a 40-slot threadpool, so a
    saturated service would queue its own probe behind forty minute-long
    analyses, blow the healthcheck timeout, and be restarted -- killing paid
    work that has already bought posts. The probes must not compete for the
    slots the analyses hold."""
    blockers = _config_blockers()
    if not SHARED_SECRET or blockers:
        # 503, NOT 200-with-a-sad-body. Railway keys its healthcheck on the
        # status code alone, so returning 200 here meant a deploy with no
        # secret and no inference went GREEN and started spending. The comment
        # claimed this made a bad deploy visible; it made it invisible.
        raise HTTPException(status_code=503, detail={
            "secret_configured": bool(SHARED_SECRET), "missing": blockers})
    return {"ok": True}


@app.post("/analyze")
def analyze(req: AnalyzeRequest,
            x_core_secret: str | None = Header(default=None,
                                               alias="X-Core-Secret")) -> dict:
    """One Deep Analyze. Returns the card the client should render."""
    _authorise(x_core_secret)

    # BEFORE SPENDING. /health already computed this and the paid endpoint did
    # not ask -- so a misconfigured deploy bought up to 400 posts, failed at the
    # step that needed them, and returned a 200 the caller would retry. The
    # corpus is not even cached on that path, so every retry re-buys it.
    blockers = _config_blockers()
    if blockers:
        raise HTTPException(status_code=503,
                            detail=f"refusing to spend: missing {blockers}")

    from utils.analyze import analyze as run, card, persist as write

    if not _SLOTS.acquire(blocking=False):
        # 429, never a queue. Queuing forty minute-long analyses is how the
        # healthcheck starves and the container is restarted mid-spend.
        raise HTTPException(status_code=429,
                            detail=f"at capacity ({MAX_CONCURRENT} concurrent)")
    t0 = time.time()
    try:
        # Single-flight per ticker: the second caller waits and then reads the
        # first one's corpus from cache rather than buying a second copy.
        with _ticker_lock(req.ticker.upper()):
            a = run(req.ticker, req.sector)
    except Exception as e:
        # A raise here happens AFTER the money is spent, and a bare 500 invites
        # the retry loop this endpoint is written to avoid.
        logger.exception("analyze %s crashed", req.ticker)
        return {"ok": False, "ticker": req.ticker.upper(),
                "error": f"{type(e).__name__}", "elapsed_s": round(time.time()-t0, 2)}
    finally:
        _SLOTS.release()
    elapsed = round(time.time() - t0, 2)

    if not a.ok:
        # 200 with an error field, not a 5xx: the caller has usually already
        # charged, and "no usable evidence about this company" is an ANSWER
        # rather than a fault. A 5xx would send it to a retry loop that spends
        # more money to learn the same thing.
        logger.info("analyze %s event=%s -> no verdict (%s) in %.2fs",
                    a.ticker, req.event_id, a.error, elapsed)
        return {"ok": False, "ticker": a.ticker, "error": a.error,
                # Whether money was spent is the one fact a refund decision
                # turns on, and the caller could not previously tell.
                "posts_billed": a.corpus.get("wire_billed"),
                "retrieved": len(a.corpus.get("ticker_corpus") or []),
                "elapsed_s": elapsed}

    if req.persist:
        write(a, feature="core_api", event_id=req.event_id)

    logger.info("analyze %s event=%s -> %s/%s branch=%s retrieved=%d in %.2fs",
                a.ticker, req.event_id, a.verdict.recommendation,
                a.verdict.confidence, a.verdict.branch,
                len(a.corpus.get("ticker_corpus") or []), elapsed)
    return {"ok": True, "elapsed_s": elapsed, "card": card(a)}
