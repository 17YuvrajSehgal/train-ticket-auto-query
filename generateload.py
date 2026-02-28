import argparse
import logging
import random
import sys
import time

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
# You are port-forwarding svc/ts-gateway-service 30467 -> 18888 in the cluster,
# so from the VM host the correct URL is:
DEFAULT_BASE_URL = "http://localhost:30467"

# Default iterations:
#   - Use 50 for baseline (normal-full) traces
#   - Use 20 for anomaly traces, as in the LMAT plan
DEFAULT_ITERATIONS = 50

# Small sleep between iterations to avoid hammering the system unrealistically
DEFAULT_SLEEP_SECONDS = 0.2

# Scenario set and relative weights; adjust if you want a different mix.
SCENARIOS = [
    query_and_preserve,
    query_and_pay,
    query_and_collect,
    query_and_execute,
    query_and_cancel,
    query_and_consign,
    query_and_rebook,
]

# Heavier weight on preserve/pay/collect/execute as core flows
SCENARIO_WEIGHTS = [
    3.0,  # query_and_preserve
    3.0,  # query_and_pay
    2.0,  # query_and_collect
    2.0,  # query_and_execute
    1.0,  # query_and_cancel
    1.0,  # query_and_consign
    1.0,  # query_and_rebook
]


def choose_scenario() -> callable:
    """Pick one scenario function according to SCENARIO_WEIGHTS."""
    return random.choices(SCENARIOS, weights=SCENARIO_WEIGHTS, k=1)[0]


def run_workload(base_url: str, iterations: int, sleep_seconds: float) -> None:
    logger = logging.getLogger("generateload")

    logger.info("Starting workload: base_url=%s iterations=%d sleep=%.3f",
                base_url, iterations, sleep_seconds)

    # Create Query object and login once
    q = Query(base_url)
    if not q.login():
        logger.fatal("Login failed; aborting workload.")
        return

    logger.info("Login succeeded; starting scenario loop.")

    for i in range(iterations):
        scenario = choose_scenario()
        scenario_name = scenario.__name__

        logger.info("Iteration %d/%d: running scenario %s",
                    i + 1, iterations, scenario_name)

        try:
            scenario(q)
        except Exception:
            # Do not crash the whole workload if a single call fails
            logger.exception("Error while running scenario %s in iteration %d",
                             scenario_name, i + 1)

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    logger.info("Workload finished.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Train Ticket auto-query workload generator "
                    "using Query + scenarios."
    )
    parser.add_argument(
        "--url",
        dest="url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL for Train Ticket gateway "
             f"(default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--iterations",
        "-n",
        dest="iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Number of iterations to run (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument(
        "--sleep",
        dest="sleep",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Sleep time between iterations in seconds "
             f"(default: {DEFAULT_SLEEP_SECONDS})",
    )
    return parser.parse_args(argv)


def main(argv=None):
    # Basic logging setup
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = parse_args(argv)

    run_workload(
        base_url=args.url,
        iterations=args.iterations,
        sleep_seconds=args.sleep,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
