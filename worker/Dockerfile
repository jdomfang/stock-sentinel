# No pip install, no requirements.txt. reap.py is stdlib only, so this image is
# just Python -- it builds in seconds and starts in milliseconds. On a cron
# service that runs ~1 second every 5 minutes, cold start IS the runtime.
#
# It also means this job cannot be broken by an unrelated dependency
# resolution, which matters for something whose entire value is running
# unattended when nobody is watching.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY reap.py probe_inference.py run_jobs.py .

# One tick runs every job. Each reports to its own healthcheck, and a
# failure in one does not stop the others -- the reaper is the safety net
# for paid work and must run even if the inference probe is red.
CMD ["python", "run_jobs.py"]
