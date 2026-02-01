from __future__ import annotations

import os

from fastapi import FastAPI

app = FastAPI(title="Stock Sentinel Payments API")


@app.get("/health")
def health():
    """Basic health check for Railway."""
    return {
        "ok": True,
        "service": "payments_api",
        "env": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("ENV") or "unknown",
    }
