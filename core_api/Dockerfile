FROM python:3.11-slim

# 3.11 to match runtime.txt. The portal has shipped f-strings that only parse on
# 3.12 twice; tests/test_runtime_compat.py exists because of it, and this image
# must not be the place the versions diverge.
WORKDIR /app

COPY core_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# utils/ is SHARED with the portal, not copied from it -- one analysis, one
# implementation. Everything here imports without streamlit, which is what
# Step 1 of the migration established and what tests/test_runtime_compat.py
# now guards.
COPY utils/            utils/
COPY data/influencers_validated.json data/influencers.json  data/
COPY core_api/main.py  core_api/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh","-c","uvicorn core_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
