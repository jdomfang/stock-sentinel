# Environments

Two environments, one repository, one branch each.

```
develop ──push──►  Streamlit Community Cloud      dev / preview
   │
   └──PR──►  CI (1,950+ assertions)  ──merge──►  master ──►  Railway  production
                                                              thestocksentinel.com
```

`master` is production. Nothing reaches it except through a pull request whose
CI is green — that check is the only gate in the system, and it is the reason
the split exists at all. Everything else here is informational.

## What differs, and what does not

| | dev (Streamlit Cloud, `develop`) | prod (Railway, `master`) |
|---|---|---|
| portal | Streamlit Community Cloud | Railway service, `portal/Dockerfile` |
| domain | `*.streamlit.app` | `thestocksentinel.com` |
| Stripe | sandbox keys | **live** keys, separate webhook + `whsec_` |
| payments-api | the existing Railway service | a **second** service, live keys |
| `APP_BASE_URL` | the Streamlit URL | `https://thestocksentinel.com` |
| core-api, inference, worker, sync | shared | shared |
| Supabase | shared | shared |

payments-api is duplicated and nothing else is, for one reason: it holds a
single `STRIPE_SECRET_KEY` and a single `APP_BASE_URL`, so one instance cannot
be both sandbox-and-Streamlit and live-and-thestocksentinel. Every other service
is stateless or environment-agnostic.

## What sharing Supabase actually costs

Dev and prod write to the same database. That is a deliberate trade — hand-applied
migrations mean two projects diverge within weeks, and the divergence is only
discovered when it *is* the bug — but it has consequences worth naming:

* **Do all dev testing signed in as one dedicated test account.** The blast
  radius is then that account's credits rather than a customer's.
* **A dev scan spends real money.** X bills $0.005 per post returned, so a cold
  scan is $0.50–$1.50. A sector prod scanned in the last six hours is a cache
  hit and costs nothing.
* **Dev must never run `worker`.** `reap_orphaned_work()` refunds and closes any
  `work_runs` row past its threshold — *including production's in-flight paid
  work*. This one is not a preference.
* **`reconcile_stripe.py` sees both.** Sandbox purchases land in the same
  `purchases` table, so reconciling against the live Stripe account reports them
  as `GRANTED BUT NOT PAID`. Filter by era before trusting the output.

## Where the domain takes effect

Exactly three places, none of them in application code:

1. **DNS** — CNAME `@` and `www` → the Railway target. GoDaddy cannot CNAME an
   apex and has no ALIAS/flattening, so DNS moves to Cloudflare (free) while
   registration stays at GoDaddy.
2. **Railway** — portal service → Networking → Custom Domain, for *both*
   hostnames. TLS issues automatically once DNS resolves.
3. **`APP_BASE_URL`** on production payments-api — it builds Stripe's
   success/cancel redirects.

It does **not** affect the Stripe webhook (an endpoint on payments-api at its
own hostname), Supabase (password auth only; no `emailRedirectTo` anywhere), or
any source file.

`APP_BASE_URL` is read at module import into a constant, so changing it needs a
payments-api **restart**, not just a variable save.

## Portal deployment

`portal/Dockerfile` and `portal/railway.toml`. Two settings live in the Railway
UI and cannot be expressed in the file:

* **Root Directory** = blank (the repository root). The build needs `utils/`,
  `pages/`, `data/` and `assets/`, none of which are under `portal/`.
* **Railway Config File** = `portal/railway.toml`.

`numReplicas = 1` is not a performance choice. A Streamlit session is a
websocket bound to one process holding that user's `st.session_state`, including
`billing.url` — the Stripe Checkout link, payable for ~24 hours. With two
replicas a reconnect can land on a process that has never seen the user, and a
mid-checkout refresh silently loses the pending purchase.

### Configuration is environment variables only

No `secrets.toml` is written into the image and none is needed: every module
reads config through `utils/config.py`, which takes `os.environ` first.

That was not true before `41d6ebb`. `utils/navigation.py` called `st.secrets`
directly and renders on every page, and `st.secrets` **raises** rather than
returning its default when no file exists — so this image would have died on
every render. `utils/billing.py` was worse: it swallowed the same failure and
returned `""`, which would have answered *"Payments are not configured yet"* to
every customer, silently, forever.

**Two variables are easy to miss** because they are absent from the local
`.streamlit/secrets.toml`: `CORE_API_URL` and `INFERENCE_URL`. Without them the
portal deploys green and every scan reports itself unavailable.

## Rollback

| what broke | how |
|---|---|
| portal or a service | Railway → Deployments → previous → **Redeploy**. Seconds, no rebuild. |
| something on Streamlit Cloud | `git revert <sha> && git push` |
| a migration | **No undo.** `pg_dump` first when touching `profiles`, `purchases`, `usage_events`, `work_runs`. |

Tag every production release (`git tag -a v2026.08.29`) — Railway's rollback
list is otherwise a wall of commit hashes at the moment you can least afford to
read one.

Stripe is the external source of truth for money, and `scripts/reconcile_stripe.py`
compares against it. Credits are recomputable from `purchases` + `usage_events`,
so a mangled `profiles.credits` is recoverable — provided `usage_events` itself
is intact.

## Verification

* `scripts/verify_security.sql` in the Supabase SQL editor — 8 checks, all
  should read PASS. This is the only way to verify RLS: `pg_policies` lives in
  `pg_catalog`, which PostgREST does not expose, so no script in this repo can
  check it.
* `python3 scripts/reconcile_stripe.py` — Stripe against `purchases`.
* `python3 tests/run_all.py` — the whole suite; needs
  `docker compose -f docker-compose.test.yml up -d` for the SQL suites.
