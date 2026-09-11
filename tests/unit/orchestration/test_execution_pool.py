"""
Description: Verify global heavy-work and parent-job concurrency limits.
References: GlobalHeavyWorkPool and JobCoordinator.
Referenced By: Pytest orchestration regression suite.
"""

from __future__ import annotations

import time
from concurrent.futures import wait
from threading import Lock

from matlab_refactor_agent.orchestration.execution_pool import (
    GlobalHeavyWorkPool,
    JobCoordinator,
)


def test_heavy_pool_never_exceeds_global_capacity_and_nested_work_is_inline():
    pool = GlobalHeavyWorkPool(max_workers=2, max_pending=4)
    lock = Lock()
    active = 0
    peak = 0

    def work(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return pool.run(lambda: value + 1)
        finally:
            with lock:
                active -= 1

    futures = [pool.submit(work, value) for value in range(8)]
    assert [future.result() for future in futures] == list(range(1, 9))
    assert peak == 2
    assert pool.snapshot()["active_workers"] == 0


def test_job_coordinator_limits_active_parent_jobs():
    coordinator = JobCoordinator(max_active_jobs=2)
    lock = Lock()
    active = 0
    peak = 0

    def job() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    futures = [coordinator.submit(job) for _ in range(17)]
    wait(futures)
    assert peak == 2
    assert coordinator.snapshot() == {
        "max_active_jobs": 2,
        "active_jobs": 0,
        "queued_jobs": 0,
    }
