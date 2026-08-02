# CPU-only torch. The default torch wheel drags ~2GB of nvidia CUDA packages
# that are pure waste on a CPU box -- and on a memory-constrained host, image
# size is also cold-start time on every deploy and restart.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    TRANSFORMERS_OFFLINE=0

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
    && pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Bake the weights into the image. Downloading 473MB on first request would make
# the first scan after every deploy time out, and a cold start under memory
# pressure is exactly when you least want a network dependency.
RUN python -c "from transformers import pipeline; pipeline('sentiment-analysis', model='ProsusAI/finbert', device=-1)"

EXPOSE 8000

# ONE worker, deliberately. Each worker loads its own ~473MB copy of the model,
# so a second worker doubles the memory for no throughput gain on a CPU box
# where inference is already the bottleneck. Scale by adding containers with
# their own limits, not workers inside one.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
