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

# `set -e` would abort before the failure could be logged, so capture the
# status explicitly rather than letting the shell exit here.
status=0
python3 scripts/sync_stock_prices.py >> "$LOG_FILE" 2>&1 || status=$?

if [ "$status" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] SUCCESS: sync completed" >> "$LOG_FILE"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] FAILED: sync exited $status" >> "$LOG_FILE"
fi

# Propagate to cron, which mails output to the local user on non-zero exit.
# Until a real alerting channel exists, that plus a greppable FAILED line is
# the only signal a broken sync produces.
exit "$status"
