"""
APScheduler entry point. Stub — implemented in Phase 1.4.
Usage: uv run python -m backend.src.workers.scheduler
"""

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    log.info("FCPriceMaster scheduler starting (stub — no jobs configured yet)")
    log.info("Phase 1.4 will add FUT.GG hot-list and cold-list jobs here.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
