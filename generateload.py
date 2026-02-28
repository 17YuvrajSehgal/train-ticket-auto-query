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
DEFAULT_ITERATIONS = 50

# Small sleep between iterations (single-worker); with workers>1 or --duration, default 0
DEFAULT_SLEEP_SECONDS = 0.2

# ---- Full scenarios (multi-step flows) ----
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

# ---- Light scenarios: single-endpoint or two-call read-only (hit different services) ----
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
# Uniform weight for light scenarios so we hit all endpoints
LIGHT_SCENARIO_WEIGHTS = [1.0] * len(LIGHT_SCENARIOS)

# Mixed mode: 40% full scenarios, 60% light (more endpoint variety at high RPS)
MIXED_FULL_WEIGHT = 0.4
MIXED_LIGHT_WEIGHT = 0.6


def choose_full_scenario():
    return random.choices(FULL_SCENARIOS, weights=FULL_SCENARIO_WEIGHTS, k=1)[0]


def choose_light_scenario():
    return random.choices(LIGHT_SCENARIOS, weights=LIGHT_SCENARIO_WEIGHTS, k=1)[0]


def choose_mixed_scenario():
    if random.random() < MIXED_FULL_WEIGHT:
        return choose_full_scenario()
    return choose_light_scenario()


def _run_worker(
    worker_id: int,
    base_url: str,
    end_time: float | None,
    iterations_per_worker: int | None,
    sleep_seconds: float,
    scenario_chooser,
    counters: dict,
    log_level: int,
) -> int:
    """Run a single worker loop. Returns number of completed scenarios."""
    logger = logging.getLogger("generateload")
    local_count = 0
    q = Query(base_url)
    for attempt in range(3):
        if q.login():
            break
        if attempt < 2:
            time.sleep(1.0 * (attempt + 1))  # 1s, then 2s backoff
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


def run_workload(
    base_url: str,
    iterations: int | None,
    sleep_seconds: float,
    workers: int = 1,
    duration_seconds: float | None = None,
    scenario_mode: str = "full",
) -> None:
    logger = logging.getLogger("generateload")

    if scenario_mode == "full":
        scenario_chooser = choose_full_scenario
        scenarios_desc = "full (preserve/pay/collect/execute/cancel/consign/rebook)"
    elif scenario_mode == "light":
        scenario_chooser = choose_light_scenario
        scenarios_desc = "light (route, trips, travelplan, admin, assurances, food)"
    else:
        scenario_chooser = choose_mixed_scenario
        scenarios_desc = "mixed (full + light endpoints)"

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
        )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _run_worker,
                    i, base_url, end_time, iterations_per_worker,
                    sleep_seconds, scenario_chooser, counters, logging.INFO,
                )
                for i in range(workers)
            ]
            for f in as_completed(futures):
                f.result()

    elapsed = time.monotonic() - start
    total = counters["total"]
    rps = total / elapsed if elapsed > 0 else 0
    logger.info(
        "Workload finished: %d scenarios in %.2fs => %.1f scenarios/s (target ~1000: increase --workers)",
        total, elapsed, rps,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Train Ticket auto-query workload generator. "
                    "Use --workers and --duration for high load (~1000 req/s).",
    )
    parser.add_argument(
        "--url",
        dest="url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL for Train Ticket gateway (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--iterations",
        "-n",
        dest="iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Total iterations across all workers (default: {DEFAULT_ITERATIONS}). Ignored if --duration is set.",
    )
    parser.add_argument(
        "--sleep",
        dest="sleep",
        type=float,
        default=None,
        help="Sleep between iterations in seconds (default: 0.2 for single worker, 0 with --workers or --duration)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        dest="workers",
        type=int,
        default=1,
        help="Number of concurrent workers (default: 1). Increase for ~1000 req/s (e.g. 100–200).",
    )
    parser.add_argument(
        "--duration",
        "-d",
        dest="duration",
        type=float,
        default=None,
        help="Run for this many seconds instead of fixed iterations (e.g. -d 60).",
    )
    parser.add_argument(
        "--scenarios",
        dest="scenarios",
        choices=("full", "light", "mixed"),
        default="mixed",
        help="full=multi-step flows only; light=single-endpoint reads; mixed=both (default: mixed for load).",
    )
    parser.add_argument(
        "-q", "--quiet",
        dest="quiet",
        action="store_true",
        help="Less per-iteration logging.",
    )
    args = parser.parse_args(argv)

    # Default sleep: 0 when doing high load (workers>1 or duration set), else 0.2
    if args.sleep is None:
        args.sleep = 0.0 if (args.workers > 1 or args.duration is not None) else DEFAULT_SLEEP_SECONDS

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
