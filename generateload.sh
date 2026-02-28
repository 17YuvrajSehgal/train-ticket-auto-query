#!/bin/bash
# Train Ticket load generator
# Usage: ./generateload.sh [mode]
#
# Modes (lightest to heaviest):
#   sanity  — single route-service read only. Safe after restart/warm-up check.
#   minimal — route + trips read-only, no DB writes. DEFAULT for 40GB VMs.  (DEFAULT)
#   light   — all read endpoints incl. food, admin config, assurances
#   high    — ~1000 req/s, mixed full+light. Requires >=64GB RAM + JVM heap caps.
#
# WARNING: On 40GB RAM VMs running all ~40 microservices in kind, the 'full'
# and 'mixed' scenario modes trigger ts-order-service, ts-admin-basic-info-service,
# and MongoDB/MySQL writes, causing GC storms and 10,000ms+ latency spikes.
# Always use 'sanity' or 'minimal' for baseline/tracing work.

cd "$(dirname "$0")"
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
    source .venv/Scripts/activate
fi

BASE_URL="${BASE_URL:-http://localhost:18888}"

case "${1:-}" in
sanity)
    # Safest possible load: only hits ts-route-service, 1 worker, 3s sleep.
    # Use this to verify the system is up without triggering any JVM pressure.
    # Expected: P99 <100ms, Apdex ~1.0
    echo "[sanity] Single route-service reads — 1 worker, 3s sleep, 10 iterations"
    python3 generateload.py --url "$BASE_URL" \
        --scenarios sanity \
        -n 10 \
        --sleep 3.0
    ;;
minimal)
    # Gentle steady baseline: route + trips (read-only), no auth or DB writes.
    # Suitable for normal trace collection on 40GB VMs.
    # Expected: P99 <500ms, ~0.3 req/s
    echo "[minimal] Read-only route+trips — 1 worker, 2s sleep, 120s"
    python3 generateload.py --url "$BASE_URL" \
        --workers 1 \
        --duration 120 \
        --scenarios minimal \
        --sleep 2.0 \
        -q
    ;;
light)
    # All read-only endpoints. Includes food/admin — slightly more pressure.
    # Keep workers low on 40GB VMs.
    echo "[light] All read endpoints — 2 workers, 1s sleep, 60s"
    python3 generateload.py --url "$BASE_URL" \
        --workers 2 \
        --duration 60 \
        --scenarios light \
        --sleep 1.0 \
        -q
    ;;
high)
    # Heavy load: ~1000 scenarios/s. ONLY use with JVM heap caps and >=64GB RAM.
    echo "[high] WARNING: DB-heavy mixed load — 150 workers, 60s"
    python3 generateload.py --url "$BASE_URL" \
        --workers 150 \
        --duration 60 \
        --scenarios mixed \
        -q
    ;;
*)
    # Default: same as minimal mode — safe baseline
    echo "[default=minimal] Read-only route+trips — 1 worker, 2s sleep, 5 iterations"
    python3 generateload.py --url "$BASE_URL" \
        -n 5 \
        --sleep 2.0 \
        --scenarios minimal
    ;;
esac
