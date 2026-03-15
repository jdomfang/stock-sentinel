# Supabase ticker master cache setup (Stock Sentinel)

Goal: stop repeated Polygon bulk downloads by persisting `data/tickers.json` in **Supabase Storage**.

## 1) Rotate service role key (IMPORTANT)
If you pasted a key into chat/logs, assume it’s compromised.
- Supabase Dashboard → **Project Settings → API**
- Regenerate **service_role** key
- Update your deployment secrets.

## 2) Create Storage bucket
Supabase Dashboard → Storage → **Create bucket**
- **Name:** `cache` (or any name you prefer)
- **Privacy:** Private (recommended)

## 3) Add deployment secrets
In Streamlit secrets / your host’s secret manager:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Optional overrides:
- `TICKER_MASTER_BUCKET` (default `cache`)
- `TICKER_MASTER_OBJECT` (default `tickers/tickers.json`)

## 4) What will happen at runtime
- App tries to download `tickers/tickers.json` from Supabase first.
- If present + <30 days old → uses it.
- Otherwise → uses local file if fresh.
- Otherwise → downloads from Polygon, writes local file, then uploads to Supabase.

## Notes
- A local file lock (`/tmp/ticker_master_refresh.lock`) prevents concurrent refresh attempts within a single running instance.
- Supabase is the durable cache across app restarts/redeploys.
