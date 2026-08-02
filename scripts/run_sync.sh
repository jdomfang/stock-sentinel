#!/bin/bash
# Sync stock prices nightly at 9 PM EST
# Add to crontab: 0 21 * * * cd /home/jdomfang/stock-sentinel && bash scripts/run_sync.sh
#
# This wrapper deliberately does NOT parse secrets. It used to scrape
# ~/.streamlit/secrets.toml with grep/cut -- a file that does not exist, so
# POLYGON_API_KEY was exported empty and every run failed. It then printed
# "Sync completed" unconditionally, so the log claimed success 43 times in a
# row from 2026-02-06 onward. sync_stock_prices.py now reads
# .streamlit/secrets.toml itself, the same source the app uses.
#
# Override for CI or a one-off run:
#   POLYGON_API_KEY=... bash scripts/run_sync.sh   # env wins over secrets.toml

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

LOG_FILE="$REPO_DIR/logs/sync_prices.log"
mkdir -p "$REPO_DIR/logs"

# Activate venv if present. Activate scripts reference unset vars, so relax
# `set -u` across the source only.
if [ -d ".venv" ]; then
  set +u; source .venv/bin/activate; set -u
elif [ -d "venv" ]; then
  set +u; source venv/bin/activate; set -u
fi

# Dead-man switch. A greppable FAILED line only helps someone already reading
# the log -- this ran broken 43 times and nobody read it. healthchecks.io alerts
# on ABSENCE, which is the one failure class logs and error trackers structurally
# cannot catch: a job that silently stops running produces nothing to find.
# Read from the environment, else from secrets.toml, so the URL is never in git.
HC_URL="${HEALTHCHECK_PRICE_SYNC_URL:-}"
if [ -z "$HC_URL" ] && [ -f "$REPO_DIR/.streamlit/secrets.toml" ]; then
  # `|| true` is load-bearing. Under `set -o pipefail` a grep that matches
  # nothing makes the whole pipeline exit 1, the assignment inherits that
  # status, and `set -e` kills the script HERE -- before the sync runs, before
  # any ping, with nothing written to the log. That is silent failure: the exact
  # six-month outage this file was rewritten to end, reintroduced by the rewrite.
  # Monitoring config must never be able to stop the job it monitors.
  HC_URL=$(grep '^HEALTHCHECK_PRICE_SYNC_URL' "$REPO_DIR/.streamlit/secrets.toml" 2>/dev/null \
           | cut -d'=' -f2- | tr -d ' "'"'"'' || true)
fi
if [ -z "$HC_URL" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] WARN: no healthcheck URL; running unmonitored" >> "$LOG_FILE"
fi

# -m so a hung monitoring endpoint can never hold up or fail the sync itself.
hc_ping() {
  [ -n "$HC_URL" ] || return 0
  curl -fsS -m 10 --retry 3 "$HC_URL$1" >/dev/null 2>&1 || true
}

hc_ping /start

# `set -e` would abort before the failure could be logged, so capture the
# status explicitly rather than letting the shell exit here.
status=0
python3 scripts/sync_stock_prices.py >> "$LOG_FILE" 2>&1 || status=$?

if [ "$status" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] SUCCESS: sync completed" >> "$LOG_FILE"
  hc_ping ""
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] FAILED: sync exited $status" >> "$LOG_FILE"
  # /fail flips the check red immediately rather than waiting for the grace
  # period to lapse -- a known failure should not masquerade as "still running".
  hc_ping /fail
fi

# Propagate to cron, which mails output to the local user on non-zero exit.
# Until a real alerting channel exists, that plus a greppable FAILED line is
# the only signal a broken sync produces.
exit "$status"
