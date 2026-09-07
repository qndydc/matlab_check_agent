"""
Description: 管理 Worker 注册、容量和基于 worker_kind 的任务路由。
References: workers.base、domain.orchestration、domain.exceptions。
Referenced By: Orchestrator 和后续并发调度实现。
"""

from __future__ import annotations

from time import monotonic

from matlab_refactor_agent.domain.exceptions import WorkerNotFoundError
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.workers.base import BaseWorker, WorkerContext
from matlab_refactor_agent.orchestration.call_lifecycle import (
    CallLifecycle,
    CallObservationRecorder,
)


class WorkerPool:
    """作用：管理 Worker 注册与任务路由；输入：Worker 实例和 TaskEnvelope；输出：WorkerResult；数据流：Orchestrator -> kind 查找 -> Worker.execute。"""

    def __init__(self, max_workers: int = 4) -> None:
        """作用：初始化 Worker 注册表和并发容量；输入：最大活动 Worker 数；输出：WorkerPool；数据流：调度配置 -> 生命周期容量/注册表。"""

        self._workers: dict[str, BaseWorker] = {}
        self.max_workers = max_workers
        self._lifecycle = CallLifecycle(max_concurrent_calls=max_workers)

    def register(self, worker: BaseWorker) -> None:
        """作用：注册或替换 Worker；输入：BaseWorker；输出：无；数据流：worker.kind -> 生命周期注册表。"""

        self._workers[str(worker.kind)] = worker

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：将任务派发给匹配 Worker；输入：任务和上下文；输出：WorkerResult；数据流：worker_kind -> 实例 -> execute。"""

        worker = self._workers.get(str(task.worker_kind))
        if worker is None:
            observation = self._lifecycle.observe(
                tool=f"worker.{task.worker_kind}", status="failure",
                result=f"未注册 Worker: {task.worker_kind}",
                error_type="tool_not_found", retryable=False, attempt=1,
                started_at=monotonic(), next_action="stop",
                sink=CallObservationRecorder(context.artifact_store, task.job_id),
            )
            raise WorkerNotFoundError(
                f"未注册 Worker: {task.worker_kind}",
                error_type="tool_not_found", retryable=False,
                observation=observation,
            )
        recorder = CallObservationRecorder(context.artifact_store, task.job_id)
        return self._lifecycle.invoke(
            tool=f"worker.{task.worker_kind}",
            arguments=task.payload,
            argument_validator=worker.validate_payload,
            operation=lambda payload: worker.execute(
                task.model_copy(update={"payload": payload}), context,
            ),
            risk=worker.risk_level,
            summarize=lambda result: (
                f"success={result.success}, artifacts={sorted(result.artifacts)}"
            ),
            sink=recorder,
        )

    def registered_kinds(self) -> list[str]:
        """作用：列出可用 Worker；输入：注册表；输出：排序类型列表；数据流：注册表 keys -> 状态/诊断。"""

        return sorted(self._workers)
