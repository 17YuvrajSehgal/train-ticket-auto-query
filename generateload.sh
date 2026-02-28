#!/bin/bash
# Train Ticket load generator
# Usage: ./generateload.sh [mode]
#   no args = low load (5 iterations, 0.2s sleep)
#   high    = high load (~1000 req/s for 60s: 150 workers, mixed endpoints)

cd "$(dirname "$0")"
# Activate venv if present (Linux/Mac)
if [ -f venv/bin/activate ]; then
  source venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
fi

BASE_URL="${BASE_URL:-http://localhost:18888}"

case "${1:-}" in
  high)
    # Target ~1000 scenarios/s: many workers, no sleep, mixed endpoints, 60s
    python3 generateload.py --url "$BASE_URL" --workers 150 --duration 60 --scenarios mixed -q
    ;;
  light)
    # Read-only endpoints only (routes, trips, admin, etc.) – good for max RPS
    python3 generateload.py --url "$BASE_URL" --workers 100 --duration 60 --scenarios light -q
    ;;
  *)
    # Original low-load style (e.g. tracing / sanity check)
    python3 generateload.py --url "$BASE_URL" -n 5 --sleep 0.2
    ;;
esac