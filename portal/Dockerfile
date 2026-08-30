# The Streamlit portal, containerised so it can run somewhere other than
# Streamlit Community Cloud.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory. The portal imports
# utils/ (which it shares with core-api), renders pages/, and reads data/ and
# assets/ at runtime -- none of which are reachable from portal/. So the Railway
# service's Root Directory must be BLANK.
#
# Set that, and the builder + Dockerfile path, in the RAILWAY UI. Railway does
# not read portal/railway.toml -- Config-as-Code is closed to new services and
# deprecated with a 2026-12-01 cutoff. That file is a record of what the UI
# settings should be, nothing more. See docs/ENVIRONMENTS.md.
#
# Python 3.11 because that is what Streamlit Community Cloud runs and what
# tests/test_runtime_compat.py asserts every source file parses on. Building on
# 3.12 would make the deploy target differ from the tested target, which is the
# gap that suite exists to close.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependencies first, so a change to a page does not reinstall pandas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# `pkg_resources.py` at the repo root is a SHIM, not dead code -- read its
# docstring before removing it. It exists because a dependency imported
# pkg_resources at module scope, which turned a missing module into a hard
# ImportError at portal startup. Not load-bearing on today's pins; kept so the
# image matches Community Cloud rather than diverging on a detail nobody would
# think to check after a dependency bump.
COPY pkg_resources.py ./
COPY app.py           ./
COPY utils/           utils/
COPY pages/           pages/
COPY assets/          assets/

# Only the data the portal actually reads. data/cache and data/corpora are
# working directories and captured corpora -- large, and rebuilt at runtime.
COPY data/influencers.json           data/
COPY data/influencers_validated.json data/
COPY data/labels.jsonl               data/
COPY data/demo/                      data/demo/
COPY data/education/                 data/education/

# The tracked theme and server settings. `.gitignore` ignores `.streamlit/`
# wholesale, so without this the container starts on Streamlit's defaults --
# full tracebacks in the browser, a first-run email prompt, and light widget
# chrome against the app's dark surfaces.
COPY portal/streamlit-config.toml .streamlit/config.toml

EXPOSE 8501

# NO secrets.toml is written, and none is needed. Every module now reads config
# through utils/config.py, which takes os.environ first -- so Railway variables
# are enough. That was not true before 41d6ebb: utils/navigation.py called
# st.secrets directly on every page, and st.secrets RAISES rather than
# defaulting when no file exists, so this image would have died on every render.
#
# Shell form so $PORT expands. Railway assigns a port per deploy and probes
# THAT port; a hardcoded 8501 starts fine and then fails the healthcheck
# because nothing is listening where the platform is looking.
CMD ["sh", "-c", "streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
