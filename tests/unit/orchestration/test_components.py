"""
Description: 验证 TaskQueue、ArtifactStore 和 ConflictResolver 基础行为。
References: orchestration 组件、domain.orchestration、pytest。
Referenced By: pytest 测试发现。
"""

from pathlib import Path
from threading import Barrier

import pytest

from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.exceptions import ArtifactError, ConflictError
from matlab_refactor_agent.domain.models import ScanResult
from matlab_refactor_agent.domain.orchestration import (
    PathClaim,
    TaskEnvelope,
    WorkerResult,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration import ConflictResolver, TaskQueue, WorkerPool
from matlab_refactor_agent.workers.base import BaseWorker, WorkerContext


class _ConcurrentWorker(BaseWorker):
    """作用：验证 WorkerPool 并发容量；输入：同步屏障；输出：成功结果；数据流：execute_many -> 两线程 barrier -> WorkerResult。"""

    def __init__(self, barrier: Barrier) -> None:
        """作用：保存测试屏障；输入：Barrier；输出：测试 Worker；数据流：测试装配 -> execute。"""

        self._barrier = barrier

    @property
    def kind(self) -> WorkerKind:
        """作用：声明测试 Worker 类型；输入：实例；输出：SCANNER；数据流：WorkerPool 注册 -> 路由。"""

        return WorkerKind.SCANNER

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：等待另一个并发任务；输入：任务与上下文；输出：WorkerResult；数据流：线程 -> barrier -> 成功结果。"""

        self._barrier.wait(timeout=2)
        return WorkerResult(task_id=task.task_id, success=True)


def test_task_queue_orders_by_priority() -> None:
    """作用：验证任务优先级；输入：不同 priority 任务；输出：断言结果；数据流：TaskEnvelope -> heap -> 出队顺序。"""

    queue = TaskQueue()
    slow = TaskEnvelope(job_id="job", worker_kind=WorkerKind.ANALYZER, priority=20)
    fast = TaskEnvelope(job_id="job", worker_kind=WorkerKind.SCANNER, priority=10)

    queue.put(slow)
    queue.put(fast)

    assert queue.get().task_id == fast.task_id
    assert queue.get().task_id == slow.task_id
    assert queue.empty()


def test_artifact_store_round_trip_and_boundary(tmp_path: Path) -> None:
    """作用：验证 artifact 隔离和读取；输入：临时根目录与 ScanResult；输出：断言/越界异常；数据流：模型 -> Job JSON -> 模型。"""

    store = ArtifactStore(tmp_path / "artifacts")
    original = ScanResult(project_root="/project")

    reference = store.write_model("job1", "scan.json", original)

    assert store.read_model(reference, ScanResult) == original
    with pytest.raises(ArtifactError):
        store.read_model(str(tmp_path / "outside.json"), ScanResult)


def test_conflict_resolver_allows_reads_and_rejects_write_conflicts() -> None:
    """作用：验证路径仲裁；输入：读写 PathClaim；输出：断言/ConflictError；数据流：声明 -> 同路径兼容规则。"""

    resolver = ConflictResolver()
    resolver.claim(PathClaim(task_id="reader1", path="file.m", operation="read"))
    resolver.claim(PathClaim(task_id="reader2", path="file.m", operation="read"))

    with pytest.raises(ConflictError):
        resolver.claim(PathClaim(task_id="writer", path="file.m", operation="write"))

    resolver.release("reader1")
    resolver.release("reader2")
    resolver.claim(PathClaim(task_id="writer", path="file.m", operation="write"))


def test_worker_pool_executes_fanout_concurrently(tmp_path: Path) -> None:
    """作用：验证 execute_many 真并发；输入：两个屏障任务；输出：成功映射断言；数据流：WorkerPool(2) -> 两线程 -> fan-in。"""

    pool = WorkerPool(max_workers=2)
    pool.register(_ConcurrentWorker(Barrier(2)))
    tasks = [
        TaskEnvelope(job_id="job", worker_kind=WorkerKind.SCANNER)
        for _ in range(2)
    ]

    outcomes = pool.execute_many(
        tasks, WorkerContext(artifact_store=ArtifactStore(tmp_path / "artifacts"))
    )

    assert set(outcomes) == {task.task_id for task in tasks}
    assert all(
        isinstance(outcome, WorkerResult) and outcome.success
        for outcome in outcomes.values()
    )
