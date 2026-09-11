"""
Description: Provide process-wide bounded parent-job and heavy-work executors.
References: concurrent.futures, semaphores, and environment configuration.
Referenced By: Web job managers and heavy Analysis/Semantic/Migration leaves.
"""

from __future__ import annotations

import os
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore, Lock, local
from typing import Callable, TypeVar


T = TypeVar("T")
_worker_context = local()


def _positive_env(name: str, default: int, maximum: int = 256) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(1, min(value, maximum))


class GlobalHeavyWorkPool:
    """Bound heavy work and its pending submissions for the whole process."""

    def __init__(self, max_workers: int = 8, max_pending: int | None = None) -> None:
        self.max_workers = max(1, max_workers)
        self.max_pending = max_pending or self.max_workers * 2
        self._capacity = BoundedSemaphore(self.max_pending)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="global-heavy",
        )
        self._lock = Lock()
        self._active = 0
        self._submitted = 0

    @staticmethod
    def in_worker() -> bool:
        return bool(getattr(_worker_context, "heavy", False))

    def submit(self, function: Callable[..., T], *args, **kwargs) -> Future[T]:
        # Running inline is the safe behaviour for an accidental nested call:
        # a worker must never wait for another slot in its own bounded pool.
        if self.in_worker():
            future: Future[T] = Future()
            try:
                future.set_result(function(*args, **kwargs))
            except BaseException as exc:
                future.set_exception(exc)
            return future
        self._capacity.acquire()
        with self._lock:
            self._submitted += 1

        def invoke() -> T:
            _worker_context.heavy = True
            with self._lock:
                self._active += 1
            try:
                return function(*args, **kwargs)
            finally:
                with self._lock:
                    self._active -= 1
                _worker_context.heavy = False

        try:
            future = self._executor.submit(invoke)
        except BaseException:
            self._capacity.release()
            with self._lock:
                self._submitted -= 1
            raise

        def release(_future: Future[T]) -> None:
            with self._lock:
                self._submitted -= 1
            self._capacity.release()

        future.add_done_callback(release)
        return future

    def run(self, function: Callable[..., T], *args, **kwargs) -> T:
        return self.submit(function, *args, **kwargs).result()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            submitted = self._submitted
            active = self._active
        return {
            "max_workers": self.max_workers,
            "max_pending": self.max_pending,
            "active_workers": active,
            "queued_work": max(0, submitted - active),
        }


class JobCoordinator:
    """Keep parent orchestration threads bounded without occupying heavy slots."""

    def __init__(self, max_active_jobs: int = 16) -> None:
        self.max_active_jobs = max(1, max_active_jobs)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_active_jobs,
            thread_name_prefix="job-coordinator",
        )
        self._lock = Lock()
        self._pending: deque[tuple[Future, Callable, tuple, dict]] = deque()
        self._active = 0

    def submit(self, function: Callable[..., T], *args, **kwargs) -> Future[T]:
        public: Future[T] = Future()
        with self._lock:
            self._pending.append((public, function, args, kwargs))
            self._dispatch_locked()
        return public

    def _dispatch_locked(self) -> None:
        while self._pending and self._active < self.max_active_jobs:
            public, function, args, kwargs = self._pending.popleft()
            self._active += 1
            internal = self._executor.submit(function, *args, **kwargs)

            def finish(completed: Future, target: Future = public) -> None:
                try:
                    target.set_result(completed.result())
                except BaseException as exc:
                    target.set_exception(exc)
                finally:
                    with self._lock:
                        self._active -= 1
                        self._dispatch_locked()

            internal.add_done_callback(finish)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "max_active_jobs": self.max_active_jobs,
                "active_jobs": self._active,
                "queued_jobs": len(self._pending),
            }

    def shutdown(self, wait: bool = True) -> None:
        """Compatibility no-op: application managers do not own the singleton."""

        return None


global_heavy_pool = GlobalHeavyWorkPool(
    max_workers=_positive_env("MATLAB_GLOBAL_WORKERS", 8, 64),
    max_pending=_positive_env("MATLAB_GLOBAL_PENDING", 16, 512),
)
global_job_coordinator = JobCoordinator(
    max_active_jobs=_positive_env("MATLAB_ACTIVE_JOBS", 16, 128)
)


__all__ = [
    "GlobalHeavyWorkPool",
    "JobCoordinator",
    "global_heavy_pool",
    "global_job_coordinator",
]
