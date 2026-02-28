"""
Train Ticket load generator: run scenario-based or light endpoint traffic
with optional concurrency and duration for ~1000+ requests/second.
"""
import argparse
import logging
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from queries import Query
from scenarios import (
    query_and_preserve,
    query_and_cancel,
    query_and_collect,
    query_and_execute,
    query_and_consign,
    query_and_pay,
    query_and_rebook,
)

# Default base URL: your Train Ticket gateway on the VM.
DEFAULT_BASE_URL = "http://localhost:30467"

# Default iterations (used when --duration is not set)
DEFAULT_ITERATIONS = 5

# Default sleep between iterations for single-worker runs.
# 2.0s keeps pressure very low on memory-constrained VMs.
DEFAULT_SLEEP_SECONDS = 2.0

# ---- Full scenarios (multi-step flows) ----
# WARNING: These are DB-heavy and involve order creation, payment, etc.
# Do NOT use on a memory-constrained VM (<64GB RAM) without JVM heap caps.
FULL_SCENARIOS = [
    query_and_preserve,
    query_and_pay,
    query_and_collect,
    query_and_execute,
    query_and_cancel,
    query_and_consign,
    query_and_rebook,
]
FULL_SCENARIO_WEIGHTS = [3.0, 3.0, 2.0, 2.0, 1.0, 1.0, 1.0]

# ---- Light scenarios: read-only, hits many services ----
def light_query_route(q: Query) -> None:
    q.query_route(routeId="")

def light_query_trips_left(q: Query) -> None:
    q.query_high_speed_ticket()

def light_query_trips_left_parallel(q: Query) -> None:
    q.query_high_speed_ticket_parallel()

def light_query_trips_normal(q: Query) -> None:
    q.query_normal_ticket()

def light_query_cheapest(q: Query) -> None:
    q.query_cheapest()

def light_query_quickest(q: Query) -> None:
    q.query_quickest()

def light_query_min_station(q: Query) -> None:
    q.query_min_station()

def light_query_admin_prices(q: Query) -> None:
    q.query_admin_basic_price()

def light_query_admin_config(q: Query) -> None:
    q.query_admin_basic_config()

def light_query_assurances(q: Query) -> None:
    q.query_assurances()

def light_query_food(q: Query) -> None:
    q.query_food()

LIGHT_SCENARIOS = [
    light_query_route,
    light_query_trips_left,
    light_query_trips_left_parallel,
    light_query_trips_normal,
    light_query_cheapest,
    light_query_quickest,
    light_query_min_station,
    light_query_admin_prices,
    light_query_admin_config,
    light_query_assurances,
    light_query_food,
]
LIGHT_SCENARIO_WEIGHTS = [1.0] * len(LIGHT_SCENARIOS)

# ---- Minimal scenarios: only fastest read-only endpoints ----
# All three require a valid Bearer token so the gateway forwards the request.
# Login is done ONCE per run and the token is injected into each worker's
# Query session, avoiding repeated hammering of ts-auth-service.
MINIMAL_SCENARIOS = [
    light_query_route,        # ts-route-service        — very cheap, no DB
    light_query_trips_left,   # ts-travel-service       — high speed trips
    light_query_trips_normal, # ts-travel2-service      — normal trips
]
MINIMAL_SCENARIO_WEIGHTS = [1.0] * len(MINIMAL_SCENARIOS)

# ---- Sanity scenario: single cheapest read, just to confirm system is alive ----
# query_route does NOT need auth (plain GET with no uid dependency).
# Safe to run immediately after restart.
SANITY_SCENARIOS = [
    light_query_route,
]
SANITY_SCENARIO_WEIGHTS = [1.0]

# Mixed mode: 40% full scenarios, 60% light
MIXED_FULL_WEIGHT  = 0.4
MIXED_LIGHT_WEIGHT = 0.6


def choose_full_scenario():
    return random.choices(FULL_SCENARIOS, weights=FULL_SCENARIO_WEIGHTS, k=1)[0]

def choose_light_scenario():
    return random.choices(LIGHT_SCENARIOS, weights=LIGHT_SCENARIO_WEIGHTS, k=1)[0]

def choose_minimal_scenario():
    return random.choices(MINIMAL_SCENARIOS, weights=MINIMAL_SCENARIO_WEIGHTS, k=1)[0]

def choose_sanity_scenario():
    return random.choices(SANITY_SCENARIOS, weights=SANITY_SCENARIO_WEIGHTS, k=1)[0]

def choose_mixed_scenario():
    if random.random() < MIXED_FULL_WEIGHT:
        return choose_full_scenario()
    return choose_light_scenario()


def _do_login(base_url: str, logger: logging.Logger) -> tuple[str, str] | None:
    """
    Perform login once and return (uid, token), or None on failure.
    This is called a single time per run so all workers share the same token,
    meaning ts-auth-service is hit exactly once regardless of worker count.
    """
    q = Query(base_url)
    for attempt in range(3):
        if q.login():
            return q.uid, q.token
        wait = 2.0 * (attempt + 1)
        logger.warning("Login attempt %d failed, retrying in %.0fs...", attempt + 1, wait)
        time.sleep(wait)
    logger.error("Login failed after 3 attempts. Check ts-auth-service and ts-verificationcode-service.")
    return None


def _run_worker(
    worker_id: int,
    base_url: str,
    end_time: float | None,
    iterations_per_worker: int | None,
    sleep_seconds: float,
    scenario_chooser,
    counters: dict,
    log_level: int,
    shared_uid: str | None,
    shared_token: str | None,
) -> int:
    """Run a single worker loop. Returns number of completed scenarios."""
    logger = logging.getLogger("generateload")
    local_count = 0
    q = Query(base_url)

    if shared_token:
        # Inject the pre-obtained token — no login call needed from this worker.
        q.uid = shared_uid
        q.token = shared_token
        q.session.headers.update({"Authorization": f"Bearer {shared_token}"})
    else:
        # Fallback: each worker logs in independently (full/mixed modes with
        # per-user state like order queries that need a real uid).
        for attempt in range(3):
            if q.login():
                break
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
        else:
            logger.error("Worker %d: login failed after retries.", worker_id)
            return 0

    while True:
        if end_time is not None and time.monotonic() >= end_time:
            break
        if iterations_per_worker is not None and local_count >= iterations_per_worker:
            break

        scenario = scenario_chooser()
        scenario_name = scenario.__name__
        try:
            scenario(q)
            local_count += 1
        except Exception:
            if log_level <= logging.DEBUG:
                logger.exception("Worker %d scenario %s failed", worker_id, scenario_name)

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    with counters["lock"]:
        counters["total"] += local_count
    return local_count


# Modes where all workers can share a single login token (read-only, no per-user state)
_SHARED_LOGIN_MODES = {"sanity", "minimal", "light"}


def run_workload(
    base_url: str,
    iterations: int | None,
    sleep_seconds: float,
    workers: int = 1,
    duration_seconds: float | None = None,
    scenario_mode: str = "minimal",
) -> None:
    logger = logging.getLogger("generateload")

    if scenario_mode == "full":
        scenario_chooser = choose_full_scenario
        scenarios_desc = "full (preserve/pay/collect/execute/cancel/consign/rebook) [DB-HEAVY]"
    elif scenario_mode == "light":
        scenario_chooser = choose_light_scenario
        scenarios_desc = "light (route, trips, travelplan, admin, assurances, food)"
    elif scenario_mode == "minimal":
        scenario_chooser = choose_minimal_scenario
        scenarios_desc = "minimal (route + trips, read-only)"
    elif scenario_mode == "sanity":
        scenario_chooser = choose_sanity_scenario
        scenarios_desc = "sanity (route-service only)"
    else:
        scenario_chooser = choose_mixed_scenario
        scenarios_desc = "mixed (full + light endpoints) [DB-HEAVY]"

    # For read-only modes: login ONCE, share the token across all workers.
    # For full/mixed: each worker logs in independently (needs real per-user uid).
    shared_uid: str | None = None
    shared_token: str | None = None
    if scenario_mode in _SHARED_LOGIN_MODES:
        logger.info("Performing single shared login for all workers...")
        result = _do_login(base_url, logger)
        if result is None:
            logger.error("Aborting: cannot proceed without a valid auth token.")
            return
        shared_uid, shared_token = result
        logger.info("Shared login OK. Workers will reuse this token (no repeated auth hits).")

    if duration_seconds is not None:
        end_time = time.monotonic() + duration_seconds
        iterations_per_worker = None
        logger.info(
            "Starting workload: base_url=%s workers=%d duration=%.1fs sleep=%.3f scenarios=%s",
            base_url, workers, duration_seconds, sleep_seconds, scenarios_desc,
        )
    else:
        end_time = None
        total_iter = iterations or 0
        iterations_per_worker = (total_iter + workers - 1) // workers if workers else 0
        logger.info(
            "Starting workload: base_url=%s workers=%d iterations=%d sleep=%.3f scenarios=%s",
            base_url, workers, total_iter, sleep_seconds, scenarios_desc,
        )

    counters = {"total": 0, "lock": threading.Lock()}
    start = time.monotonic()

    if workers <= 1:
        _run_worker(
            0, base_url, end_time, iterations_per_worker,
            sleep_seconds, scenario_chooser, counters, logging.INFO,
            shared_uid, shared_token,
        )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _run_worker,
                    i, base_url, end_time, iterations_per_worker,
                    sleep_seconds, scenario_chooser, counters, logging.INFO,
                    shared_uid, shared_token,
                )
                for i in range(workers)
            ]
            for f in as_completed(futures):
                f.result()

    elapsed = time.monotonic() - start
    total = counters["total"]
    rps = total / elapsed if elapsed > 0 else 0
    logger.info(
        "Workload finished: %d scenarios in %.2fs => %.1f scenarios/s",
        total, elapsed, rps,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Train Ticket auto-query workload generator.\n"
            "Default mode is 'minimal' (read-only, single shared login).\n"
            "Use --scenarios full or mixed only on well-resourced VMs (>=64GB RAM).\n"
            "Use --workers and --duration for sustained load testing."
        ),
    )
    parser.add_argument(
        "--url", dest="url", default=DEFAULT_BASE_URL,
        help=f"Base URL for Train Ticket gateway (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--iterations", "-n", dest="iterations", type=int, default=DEFAULT_ITERATIONS,
        help=f"Total iterations across all workers (default: {DEFAULT_ITERATIONS}). Ignored if --duration is set.",
    )
    parser.add_argument(
        "--sleep", dest="sleep", type=float, default=None,
        help=(
            "Sleep between iterations in seconds "
            "(default: 2.0s for single-worker; 1.0s with --workers; 0 with --duration). "
            "Increase on memory-constrained VMs to reduce GC pressure."
        ),
    )
    parser.add_argument(
        "--workers", "-w", dest="workers", type=int, default=1,
        help="Number of concurrent workers (default: 1). Keep <=2 on 40GB VMs.",
    )
    parser.add_argument(
        "--duration", "-d", dest="duration", type=float, default=None,
        help="Run for this many seconds instead of fixed iterations (e.g. -d 60).",
    )
    parser.add_argument(
        "--scenarios", dest="scenarios",
        choices=("full", "light", "minimal", "mixed", "sanity"),
        default="minimal",
        help=(
            "sanity  = route-service GET only, no auth needed; "
            "minimal = route+trips read-only, 1 shared login (DEFAULT); "
            "light   = all read endpoints incl. food/admin, 1 shared login; "
            "full    = multi-step DB-heavy flows, per-worker login; "
            "mixed   = full+light DB-heavy, per-worker login. "
            "WARNING: full/mixed cause heavy DB and order-service pressure."
        ),
    )
    parser.add_argument(
        "-q", "--quiet", dest="quiet", action="store_true",
        help="Less per-iteration logging.",
    )
    args = parser.parse_args(argv)

    if args.sleep is None:
        if args.duration is not None:
            args.sleep = 0.0
        elif args.workers > 1:
            args.sleep = 1.0
        else:
            args.sleep = DEFAULT_SLEEP_SECONDS

    return args


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args(argv)
    if args.quiet:
        logging.getLogger("generateload").setLevel(logging.WARNING)
        logging.getLogger("auto-queries").setLevel(logging.WARNING)

    run_workload(
        base_url=args.url,
        iterations=args.iterations,
        sleep_seconds=args.sleep,
        workers=args.workers,
        duration_seconds=args.duration,
        scenario_mode=args.scenarios,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
