# Working on Stock Sentinel

Read this before changing anything. Full detail lives in
[docs/ENVIRONMENTS.md](docs/ENVIRONMENTS.md); this file is only the list of
things that are expensive to rediscover.

## Branches

`master` is **production** — it deploys to `thestocksentinel.com` on Railway.
Nothing reaches it except through a pull request with green CI; that check is
the only gate in the system.

**Work on `develop`.** It deploys to `stock-sentinel-dev.streamlit.app`.

## Invariants

Each of these has already cost something. Several are enforced by tests.

* **Only `utils/config.py` may touch `st.secrets`.** `st.secrets` *raises*
  rather than returning a default when no `secrets.toml` exists, so a direct
  call kills every containerised page. Enforced by
  `test_only_utils_config_touches_st_secrets`.

* **`portal/railway.toml` is documentation only.** Railway does not read it —
  Config-as-Code is closed to new services. Real settings live in the Railway UI.

* **Cloudflare stays DNS-only (grey cloud).** Streamlit runs on websockets; the
  free-tier proxy times them out mid-session, and proxy plus the wrong SSL mode
  is an infinite redirect loop with nothing in any log.

* **The dev "Buy credits" button charges a real card.** There is one
  payments-api and it holds live Stripe keys. No sandbox payment path exists.
  Treat dev checkout as production checkout.

* **Dev must never run `worker`.** `reap_orphaned_work()` refunds and closes any
  `work_runs` row past its threshold — *including production's in-flight paid
  work*. Dev and prod share one Supabase project.

* **Do not drop `profiles_update_admin`.** It is deliberate: it is how an admin
  adjusts other accounts' credits.

* **Portal runs `numReplicas = 1`.** A Streamlit session is a websocket bound to
  one process holding that user's `st.session_state`, including the pending
  Stripe Checkout URL. A second replica silently loses mid-checkout purchases.

* **A scan spends real money.** X bills $0.005 per post returned, so a cold scan
  is $0.50–$1.50 and it comes out of the owner's pocket. Never run one to "check
  something works" — ask first.

* **Migrations have no undo.** `pg_dump` before touching `profiles`,
  `purchases`, `usage_events` or `work_runs`.

## Tests

```
python3 tests/run_all.py                              # everything
python3 tests/run_all.py --no-db                      # skip suites needing Postgres
docker compose -f docker-compose.test.yml up -d       # start Postgres for the SQL suites
```

Assertions here are expected to fail when behaviour changes. An assertion that
matches a comment, a source position, or a key's *name* rather than what the
code *does* is the recurring bug in this repo's own test suite — it passes while
the thing it claims to check is broken.
