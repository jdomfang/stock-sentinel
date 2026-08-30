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
| domain | `stock-sentinel-dev.streamlit.app` | `thestocksentinel.com`, `www.` |
| Stripe | **shared with prod — live keys** | **live** keys, live webhook + `whsec_` |
| payments-api | **shared with prod** | `stock-sentinel-production-2715` |
| `APP_BASE_URL` | n/a — the shared instance uses prod's | `https://thestocksentinel.com` |
| core-api, inference, worker, sync | shared | shared |
| Supabase | shared | shared |

### payments-api is NOT duplicated, and that costs something

An earlier draft of this file described prod's payments-api as "a second
service". It never was. There is **one** instance, and since 2026-08-30 it holds
**live** Stripe keys. Dev points at the same host.

So **the dev portal's "Buy 2 credits · $5" button creates a real charge on a real
card**, then redirects to `thestocksentinel.com` because that instance's
`APP_BASE_URL` is production's. There is no sandbox payment path anywhere.

That is a deliberate choice (one fewer service to maintain), not an oversight,
and the trade is named here so nobody rediscovers it by spending $5:

* **Testing checkout costs real money.** $5 a time. Recoverable — it is your own
  Stripe account — but it is a real charge, not a test one.
* **A dev click can move money.** The button looks identical in both
  environments. Treat dev checkout as production checkout, because it is.
* Splitting it later means a second Railway service with `sk_test_`, its own
  sandbox webhook, and `APP_BASE_URL` set to the Streamlit URL — same image,
  three different variables. Nothing in the code needs to change.

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

## Keeping dev awake

`.github/workflows/keepalive.yml` visits `stock-sentinel-dev.streamlit.app`
every 6 hours — half Community Cloud's 12-hour sleep timeout, leaving room for
GitHub's routinely-late schedules.

It targets **dev on purpose**. Production is on Railway and never sleeps; this
exists so a dev session does not start with the wake-up dance.

* **It uses a headless browser, not curl.** Both the sleep state and the wake
  action are JavaScript. The edge serves an identical React shell whether the
  container runs or not, and `/_stcore/health` returns that shell rather than
  `ok` — so an HTTP request cannot tell asleep from awake and does not reset the
  timer. `stock-sentinel-dev.streamlit.app` answers `303` to curl for the same
  reason: the auth handshake completes in JavaScript. That is not an outage.
* **It only runs from `master`.** GitHub schedules fire on the default branch
  only, so a change to this workflow on `develop` does nothing until merged.
* **It costs nothing.** An anonymous visitor renders demo data — no X, no
  Polygon, no credits.
* GitHub disables scheduled workflows after 60 days with no commits to the repo.
  You get an email; re-enable with one click.

## Where the domain takes effect

Exactly three places, none of them in application code:

1. **DNS** — CNAME `@` and `www` → the Railway targets. GoDaddy cannot CNAME an
   apex and has no ALIAS/flattening, so DNS moved to Cloudflare (free) on
   2026-08-30 while registration stays at GoDaddy. Nameservers are
   `ignacio`/`ingrid.ns.cloudflare.com`.
2. **Railway** — portal service → Networking → Custom Domain, for *both*
   hostnames, target port **8080**. TLS issues automatically once DNS resolves.
3. **`APP_BASE_URL`** on payments-api — it builds Stripe's success/cancel
   redirects.

### The two CNAME targets are different

Railway issues a **distinct** target per custom domain. They are not
interchangeable and neither equals the service's own `...up.railway.app` URL:

```
@     →  flacjo5o.up.railway.app
www   →  w29sq23q.up.railway.app
```

Read the value off Railway's own dialog when adding a domain; do not copy the
service URL from the Networking panel.

### Cloudflare is DNS-only. Do not enable the proxy.

Every record is grey-cloud (**DNS only**). The orange cloud is off deliberately:

* **Streamlit is a websocket.** Every interaction rides one long-lived
  connection. Cloudflare's free-tier proxy applies timeouts to those, and a
  dropped socket is a dead-looking UI mid-session.
* **Proxy + the wrong SSL mode is an infinite redirect loop**, and the symptom
  is a site that appears down with nothing in any log.
* **Railway already terminates TLS** and there is nothing to cache — every page
  is live, per-user data.

Cloudflare still flattens the apex CNAME in DNS-only mode, which is the *only*
reason DNS is here at all. The dashboard nags to enable proxying; that is an
advertisement, not a warning about this setup.

### Certificates are Railway's, not Cloudflare's

Two Let's Encrypt certificates, one per hostname, requested by Railway over ACME
and renewed automatically around day 60 of 90. Nothing to diarise, nothing to
install. Cloudflare's Universal SSL is not in play — it only applies to proxied
records.

It does **not** affect the Stripe webhook (an endpoint on payments-api at its
own hostname), Supabase (password auth only; no `emailRedirectTo` anywhere), or
any source file.

`APP_BASE_URL` is read at module import into a constant, so changing it needs a
payments-api **restart**, not just a variable save.

## Portal deployment

`portal/Dockerfile` builds the image. **`portal/railway.toml` is documentation
only — Railway does not read it.** Config-as-Code is closed to new services and
is deprecated with a 2026-12-01 cutoff, so pointing the "Railway Config File"
setting at it does nothing. Attempting to apply it produces a settings dialog
that re-offers the same changes forever.

Every setting therefore lives in the Railway UI, and `portal/railway.toml` is
the record of what those settings should be:

* **Root Directory** = blank (the repository root). The build needs `utils/`,
  `pages/`, `data/` and `assets/`, none of which are under `portal/`.
* **Builder** = Dockerfile, path `portal/Dockerfile`.
* **Healthcheck** = `/_stcore/health`.

If a deploy ever succeeds but nothing listens on `$PORT`, check whether Railway
fell back to **Nixpacks** — it runs `python app.py`, which exits immediately at
`st.switch_page`. The tell is `python@3.11.16`-style version labels in the build
log where Dockerfile stages should be.

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
