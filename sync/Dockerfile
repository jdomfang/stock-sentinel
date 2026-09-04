# Nightly price sync. BUILD CONTEXT IS THE REPO ROOT, not this directory --
# set the Railway service's Root Directory to the repo root and its
# Dockerfile Path to sync/Dockerfile.
#
# It needs utils/prices.py, which lives outside this folder. The existing
# `worker` service builds from worker/ and is left untouched: it is green and
# running, and widening its context to fix a different service is how a working
# thing gets broken.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# No pip install. utils/prices.py is stdlib-only by design, which is the whole
# reason it was extracted from utils.finance -- that module pulls streamlit,
# numpy and the Polygon SDK, none of which this job needs.
#
# data/tickers.json is deliberately NOT copied any more. The old per-ticker
# sync read its work list from that file in alphabetical order, which is what
# made the cache 60% tickers starting with "A". The grouped endpoint returns the
# whole market, and the symbols worth keeping come from ticker_master in
# Supabase -- the same list the app scans against.
COPY utils/prices.py            utils/
COPY utils/__init__.py          utils/
# prices.py reads config through the shared helper now. config.py is
# stdlib-only, so this image keeps its "import it with nothing installed"
# property -- but it MUST ship, or the container dies at import.
COPY utils/config.py            utils/
# The sector pulse runs after the price write. sector_query is copied for its
# UI_TO_NASDAQ map only -- one source of truth for what "utilities" means --
# and is stdlib + utils.config, so the image stays dependency-free.
COPY utils/sector_query.py      utils/
COPY utils/sector_pulse.py      utils/
COPY scripts/sync_stock_prices.py scripts/
COPY sync/run_sync.py           ./

CMD ["python", "run_sync.py"]
