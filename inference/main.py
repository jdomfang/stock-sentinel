"""FinBERT sentiment scoring as a standalone service.

WHY THIS EXISTS

FinBERT is ~886 MB resident (473 MB of weights, measured) inside a Streamlit
process with a ~1 GB ceiling. That leaves ~138 MB for everything a scan actually
does -- 500 tweets, DataFrames, the ticker master -- and on 2026-08-01 a
healthcare scan crossed the line. The kernel sent SIGKILL, which runs no
handler: no traceback, no refund, a blank page, a spent credit.

No amount of batching or error handling fixes that, because the 886 MB is
resident weights held for the life of the process. It was measured: peak RSS is
identical at batch sizes 1, 8, 24 and 64.

Moving the model here changes the failure mode rather than just the numbers.
When inference is overloaded it dies alone, and the caller receives an HTTP
error -- an ordinary exception it can catch, log and refund. The user's session
survives. That conversion, from fatal-and-invisible to handled-and-recorded, is
the point; the freed memory is a bonus.

CONTRACT

POST /score  {"texts": ["...", ...]}
  -> {"results": [{"label","confidence","score","sentiment"}, ...]}

Exactly one result per input, in input order, with the same shape and the same
0.55 neutral threshold as utils.sentiment. Callers must be able to swap between
local and remote scoring without any behaviour change -- that is what makes the
rollout reversible.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

# MUST be set before torch/transformers are imported anywhere, because the
# OpenMP runtime reads these at load time and ignores later changes.
#
# torch sizes its thread pool from the VISIBLE cpu count. A container that can
# see 8 cores but is scheduled onto a fraction of one spawns 8 threads that
# thrash over that fraction. Measured on Railway: 6.0s to score a single text
# with the model already resident, against 0.15s for the same call locally --
# a ~40x slowdown that is pure contention, not compute.
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("TORCH_THREADS", "1"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("TORCH_THREADS", "1"))

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("inference")

MODEL_NAME = os.getenv("MODEL_NAME", "ProsusAI/finbert")
SHARED_SECRET = os.getenv("INFERENCE_SHARED_SECRET", "")
BATCH_SIZE = int(os.getenv("INFERENCE_BATCH_SIZE", "24"))
MAX_TEXTS = int(os.getenv("INFERENCE_MAX_TEXTS", "512"))
MAX_CHARS = 512  # matches utils.sentiment's truncation exactly

_pipeline = None


def _load():
    """Load once, lazily, and keep it. Loading costs seconds and ~473 MB."""
    global _pipeline
    if _pipeline is None:
        from transformers import pipeline as hf_pipeline

        # Belt and braces: the env vars above cover OpenMP, this covers torch's
        # own intra-op pool. Both are needed -- setting only one still leaves the
        # other defaulting to cpu_count().
        try:
            import torch
            torch.set_num_threads(int(os.getenv("TORCH_THREADS", "1")))
            logger.info("torch threads=%d (visible cpus=%s)",
                        torch.get_num_threads(), os.cpu_count())
        except Exception as e:
            logger.warning("could not pin torch threads: %s", type(e).__name__)

        t0 = time.time()
        _pipeline = hf_pipeline("sentiment-analysis", model=MODEL_NAME, device=-1)
        logger.info("loaded %s in %.1fs", MODEL_NAME, time.time() - t0)
    return _pipeline


def score_finbert_output(
    label: str, confidence: float, neutral_threshold: float = 0.55
) -> tuple[float, str, str]:
    """Byte-for-byte the mapping in utils.sentiment.score_finbert_output.

    Duplicated deliberately rather than imported: this service must not depend
    on the Streamlit app's package. The behaviour is pinned on both sides by
    tests/test_characterization.py, so a divergence fails loudly instead of
    silently shifting every recommendation.
    """
    label_norm = str(label).strip().upper() if label is not None else "UNKNOWN"
    conf = float(confidence or 0.0)

    if conf < neutral_threshold or label_norm == "NEUTRAL":
        return 0.0, "Neutral", "NEUTRAL" if label_norm != "UNKNOWN" else "UNKNOWN"
    if label_norm == "POSITIVE":
        return conf, "Bullish", "POSITIVE"
    if label_norm == "NEGATIVE":
        return -conf, "Bearish", "NEGATIVE"
    return 0.0, "Neutral", "UNKNOWN"


class ScoreRequest(BaseModel):
    texts: list[str] = Field(default_factory=list)


app = FastAPI(title="Stock Sentinel Inference")


def _require_secret(provided: str | None) -> None:
    # Unset means "not configured": refuse rather than serve an open model
    # endpoint. Anyone who can reach it can spend the box's memory budget.
    if not SHARED_SECRET:
        raise HTTPException(status_code=500, detail="Server not configured")
    if not provided or provided != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness only -- deliberately does NOT load the model.

    A health check that loads 473 MB would make every restart a slow, memory
    -spiking event, and an orchestrator probing it during startup could push the
    container over its limit and into a restart loop.
    """
    return {"ok": True, "service": "inference", "model": MODEL_NAME, "loaded": _pipeline is not None}


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Readiness: loads the model. Point the orchestrator here, not /health."""
    _load()
    return {"ok": True, "loaded": True, "model": MODEL_NAME}


@app.post("/score")
def score(
    req: ScoreRequest,
    x_inference_secret: str | None = Header(default=None, alias="X-Inference-Secret"),
) -> dict[str, Any]:
    _require_secret(x_inference_secret)

    texts = req.texts or []
    if not texts:
        return {"results": []}

    # Bound the request. An unbounded list is a memory-exhaustion vector against
    # the one service whose whole purpose is having a predictable memory profile.
    if len(texts) > MAX_TEXTS:
        raise HTTPException(
            status_code=413,
            detail=f"too many texts: {len(texts)} > {MAX_TEXTS}",
        )

    prepared = [(t or "")[:MAX_CHARS] for t in texts]

    try:
        pipe = _load()
        out: list[dict[str, Any]] = []
        for i in range(0, len(prepared), BATCH_SIZE):
            for r in pipe(prepared[i : i + BATCH_SIZE]):
                signed, sentiment, label_norm = score_finbert_output(
                    label=r.get("label"), confidence=float(r.get("score", 0.0))
                )
                out.append({
                    "label": label_norm,
                    "confidence": float(r.get("score", 0.0)),
                    "score": signed,
                    "sentiment": sentiment,
                })
    except Exception as e:
        # 503, not 500: this is "try again", and it is the signal that lets the
        # caller refund. Returning a partial or neutral result would silently
        # degrade every recommendation instead.
        logger.exception("scoring failed for %d texts", len(prepared))
        raise HTTPException(status_code=503, detail=f"inference failed: {type(e).__name__}") from e

    # A short response would misalign results with inputs at the caller, which is
    # worse than an error: every ticker after the gap gets another tweet's score.
    if len(out) != len(prepared):
        logger.error("length mismatch: %d results for %d texts", len(out), len(prepared))
        raise HTTPException(status_code=503, detail="inference length mismatch")

    return {"results": out}
