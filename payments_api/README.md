# Stock Sentinel Payments API (FastAPI)

This is a small FastAPI service intended to be deployed to Railway.

## Local run

```bash
cd payments_api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Visit: http://localhost:8000/health

## Railway

In Railway service settings:
- Root Directory: `payments_api`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Phase 2 will add Stripe + Supabase credit granting endpoints.
