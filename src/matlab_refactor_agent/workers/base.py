"""
Description: 定义统一 Worker 抽象接口和运行上下文。
References: abc、ArtifactStore、domain.orchestration。
Referenced By: 全部 Agent 实现和 WorkerPool。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore


@dataclass(frozen=True)
class WorkerContext:
    """作用：提供 Worker 运行期依赖；输入：ArtifactStore；输出：只读上下文；数据流：Orchestrator 装配 -> Worker 执行。"""

    artifact_store: ArtifactStore


class BaseWorker(ABC):
    """作用：定义所有 Worker 的统一接口；输入：TaskEnvelope 和上下文；输出：WorkerResult；数据流：WorkerPool -> Worker -> QualityGate。"""

    @property
    @abstractmethod
    def kind(self) -> WorkerKind:
        """作用：声明 Worker 类型；输入：Worker 实例；输出：WorkerKind；数据流：注册表 -> 任务路由。"""

    @abstractmethod
    def execute(self, task: TaskEnvelope, context: WorkerContext) -> WorkerResult:
        """作用：执行一个幂等任务；输入：任务与运行上下文；输出：WorkerResult；数据流：payload/artifact -> 能力实现 -> 新 artifact 引用。"""

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验调用参数；生产 Worker 应覆盖，测试 Worker 保持轻量兼容。"""

        return dict(payload)

    @property
    def risk_level(self) -> str:
        """确定性 Worker 只读取输入项目并写入隔离 artifact。"""

        return "write_artifact"
