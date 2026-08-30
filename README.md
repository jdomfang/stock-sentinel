# Stock Sentinel

Sentiment analysis for publicly traded companies, built from posts on X.

Live at **[thestocksentinel.com](https://thestocksentinel.com)**.

A customer runs a **Market Scan** to find tickers a sector is talking about, or a
**Deep Analyze** on one ticker for a detailed read. Each costs one credit.
Credits are prepaid — $5 for 2 — and never expire.

It reports what people are saying and how the market moved. **It is not
financial advice**, gives no recommendations, holds no customer funds, and
executes no trades.

## Architecture

Six services, one repository. The portal is a UI; every paid operation happens
behind it.

```
                  browser
                     │
              ┌──────▼───────┐
              │    portal    │  Streamlit — pages/, no paid API calls
              └──────┬───────┘
                     │
        ┌────────────┼─────────────┐
        │            │             │
   ┌────▼─────┐ ┌────▼─────┐  ┌────▼───────┐
   │ core-api │ │ payments │  │  Supabase  │  Postgres + auth, RLS-gated
   │          │ │   -api   │  │            │  credits, ledger, work_runs
   └────┬─────┘ └────┬─────┘  └────────────┘
        │            │
   ┌────▼─────┐  ┌───▼────┐
   │inference │  │ Stripe │
   └──────────┘  └────────┘

   worker   scheduled jobs: the reaper, the inference canary
   sync     nightly price sync from Polygon
```

| service | what it does | stack |
|---|---|---|
| **portal** | the UI — 11 Streamlit pages, auth, credits, checkout hand-off | Streamlit |
| **core-api** | the analysis. `/scan`, `/analyze`. Spends credits, calls X and Polygon | FastAPI |
| **inference** | FinBERT sentiment scoring, `/score`. Separate because the model is ~886 MB resident | FastAPI, PyTorch, Transformers |
| **payments-api** | Stripe Checkout sessions and the signed webhook that grants credits | FastAPI, Stripe |
| **worker** | scheduled jobs — refunds orphaned paid work, probes inference for *correct and fast*, not merely up | — |
| **sync** | nightly price sync | Polygon |

**Why the split:** the portal used to do all of it, which meant a Streamlit
container holding FinBERT in memory and a page render that could spend money.
Paid work now lives behind an API that can be metered, retried and refunded on
its own.

### External services

**X API** — the sentiment corpus, billed **$0.005 per post returned**. A cold
scan is $0.50–$1.50. **Polygon** — prices and fundamentals. **Supabase** —
Postgres, auth, and the credit ledger, with row-level security as the gate.
**Stripe** — payments.

## Credits

One credit runs one Market Scan or one Deep Analyze. Balance lives in
`profiles.credits`; every movement is a row in `usage_events`, so a balance is
recomputable from purchases plus usage. Grants happen only inside
`SECURITY DEFINER` functions that `anon` and `authenticated` cannot execute —
RLS is row-level and cannot restrict *columns*, so "users may update their own
profile" would have meant "users may set their own credits".

`scripts/reconcile_stripe.py` compares Stripe against those records: every
payment should have produced credits, and every credit should trace to a payment.

## Running it

The portal alone, against your own Supabase and API keys:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Configuration is read through `utils/config.py`, which takes `os.environ` first
and falls back to `.streamlit/secrets.toml` — that whole directory is
gitignored, so nothing here is a template you can copy. At minimum you need
`SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY`; `X_BEARER_TOKEN`
and `POLYGON_API_KEY` for real data.

Scans additionally need `CORE_API_URL` and `INFERENCE_URL` pointing at running
instances. Without them the portal starts fine and every scan reports itself
unavailable — which is the failure people miss, because nothing looks broken.

Each service has its own `requirements.txt` and runs under `uvicorn`.

## Tests

```bash
python3 tests/run_all.py --no-db                   # 1,685 assertions, no Postgres
docker compose -f docker-compose.test.yml up -d    # then the ten SQL suites
python3 tests/run_all.py
```

The SQL suites apply the real migration chain to a throwaway Postgres and assert
the effective grants — that a signed-in user cannot write their own balance or
forge a ledger row.

## Before you change anything

**[AGENTS.md](AGENTS.md)** — the invariants, each with why. Several are enforced
by tests; several were learned by breaking them.

**[docs/ENVIRONMENTS.md](docs/ENVIRONMENTS.md)** — the two environments, what
they share, what that costs, and where the domain takes effect.

`master` is production and is reachable only through a pull request with green
CI. Work on `develop`.
