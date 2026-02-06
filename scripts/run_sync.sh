#!/bin/bash
# Sync stock prices nightly at 9 PM EST
# Add to crontab: 0 21 * * * cd /home/jdomfang/stock-sentinel && bash scripts/run_sync.sh

cd /home/jdomfang/stock-sentinel

# Activate venv if it exists
if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

# Set environment from .env or pass via crontab
export POLYGON_API_KEY="${POLYGON_API_KEY:-$(grep POLYGON_API_KEY ~/.streamlit/secrets.toml 2>/dev/null | cut -d'=' -f2 | tr -d ' \"')}"
export SUPABASE_URL="${SUPABASE_URL:-https://budwitscidqpnfhayjqd.supabase.co}"
export SUPABASE_SERVICE_ROLE_KEY="${SUPABASE_SERVICE_ROLE_KEY:-$(grep SUPABASE_SERVICE_ROLE_KEY ~/.streamlit/secrets.toml 2>/dev/null | cut -d'=' -f2 | tr -d ' \"')}"

# Run the sync
python3 scripts/sync_stock_prices.py >> logs/sync_prices.log 2>&1

echo "Sync completed at $(date)" >> logs/sync_prices.log
