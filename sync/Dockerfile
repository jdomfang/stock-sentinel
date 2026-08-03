# Nightly price sync. BUILD CONTEXT IS THE REPO ROOT, not this directory --
# set the Railway service's Root Directory to the repo root and its
# Dockerfile Path to sync/Dockerfile.
#
# It needs utils/prices.py and data/tickers.json, which live outside this
# folder. The existing `worker` service builds from worker/ and is left
# untouched: it is green and running, and widening its context to fix a
# different service is how a working thing gets broken.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# No pip install. utils/prices.py is stdlib-only by design, which is the whole
# reason it was extracted from utils.finance -- that module pulls streamlit,
# numpy, the Polygon SDK and filelock, none of which this job needs.
COPY utils/prices.py            utils/
COPY utils/__init__.py          utils/
COPY scripts/sync_stock_prices.py scripts/
COPY data/tickers.json          data/
COPY sync/run_sync.py           ./

CMD ["python", "run_sync.py"]
