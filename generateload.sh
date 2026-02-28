#!/bin/bash
# Train Ticket load generator
# Usage: ./generateload.sh [mode]
#   no args  = low load (5 iterations, 0.2s sleep)
#   minimal  = gentle baseline for normal trace recording (~5 workers, light only, 0.5s sleep, 120s)
#   light    = read-only endpoints only – good for max RPS
#   high     = ~1000 req/s for 60s: 150 workers, mixed endpoints

cd "$(dirname "$0")"
if [ -f venv/bin/activate ]; then
  source venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
fi

BASE_URL="${BASE_URL:-http://localhost:18888}"

case "${1:-}" in
  minimal)
    # Gentle steady traffic for normal baseline recording
    # Target: ~5-10 req/s, read-only endpoints only, 0.5s sleep between iterations
    # Goal: healthy Apdex >0.9, P99 <300ms, no DB stress
    python3 generateload.py --url "$BASE_URL" \
      --workers 5 \
      --duration 120 \
      --scenarios light \
      --sleep 0.5 \
      -q
    ;;
  light)
    # Read-only endpoints only – good for moderate RPS
    python3 generateload.py --url "$BASE_URL" --workers 100 --duration 60 --scenarios light -q
    ;;
  high)
    # Target ~1000 scenarios/s: many workers, no sleep, mixed endpoints, 60s
    python3 generateload.py --url "$BASE_URL" --workers 150 --duration 60 --scenarios mixed -q
    ;;
  *)
    # Original low-load style (e.g. tracing / sanity check)
    python3 generateload.py --url "$BASE_URL" -n 5 --sleep 0.2
    ;;
esac