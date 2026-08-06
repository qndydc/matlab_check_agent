"""
Description: 为尚未实现的 Worker 提供安全预留基类和演示辅助函数。
References: workers.base、domain.enums、domain.orchestration、argparse。
Referenced By: Planner、Executor、Validator 和 Reporter Worker 模块。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence, TypeVar
from uuid import uuid4

from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

from .base import BaseWorker, WorkerContext


class _ReservedAgent(BaseWorker):
    """作用：为尚未实现的 Worker 保留稳定接口；输入：任务；输出：明确失败结果；数据流：WorkerPool -> 能力缺失诊断。"""

    worker_kind: WorkerKind
    description: str

    @property
    def kind(self) -> WorkerKind:
        """作用：返回预留 Worker 类型；输入：实例；输出：WorkerKind；数据流：注册表 -> 任务路由。"""

        return self.worker_kind

    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：安全拒绝未实现任务；输入：任务与上下文；输出：失败 WorkerResult；数据流：任务 -> 能力状态检查 -> 诊断。"""

        return WorkerResult(
            task_id=task.task_id,
            success=False,
            diagnostics=[f"{self.description} 尚未实现"],
        )


ReservedAgentT = TypeVar("ReservedAgentT", bound=_ReservedAgent)


def run_reserved_demo(
    agent_type: type[ReservedAgentT],
    label: str,
    argv: Sequence[str] | None = None,
) -> int:
    """作用：运行单个预留 Worker 的展示入口；输入：Agent 类型、标签和 CLI 参数；输出：进程退出码；数据流：CLI -> ReservedAgent -> 标准失败结果 -> stdout。"""

    parser = argparse.ArgumentParser(description=f"展示 {label} 预留接口")
    parser.add_argument(
        "--artifact-dir", type=Path, default=Path("var/worker-demos")
    )
    args = parser.parse_args(argv)
    worker = agent_type()
    task = TaskEnvelope(job_id=uuid4().hex, worker_kind=worker.kind)
    result = worker.execute(task, WorkerContext(ArtifactStore(args.artifact_dir)))
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0
