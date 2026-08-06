"""
Description: 定义 Job、TaskEnvelope、WorkerResult、artifact 声明和流水线 Outcome。
References: Pydantic、domain.enums、domain.models。
Referenced By: Orchestrator、StateManager、WorkerPool 和 Workers。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import Field

from .enums import JobStatus, TaskStatus, WorkerKind
from .models import AnalysisResult, DomainModel, ScanResult


def utc_now() -> datetime:
    """作用：生成统一 UTC 时间；输入：系统时钟；输出：带时区 datetime；数据流：时钟 -> Job/Task 审计字段。"""

    return datetime.now(timezone.utc)


class TaskEnvelope(DomainModel):
    """作用：定义跨 Worker 的轻量任务信封；输入：Worker 类型、payload 和依赖；输出：可持久化任务；数据流：Orchestrator -> TaskQueue -> WorkerPool。"""

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    job_id: str
    worker_kind: WorkerKind
    payload: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 100
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkerResult(DomainModel):
    """作用：定义 Worker 标准输出；输入：执行状态、artifact 引用和指标；输出：轻量结果；数据流：Worker -> QualityGate/StateManager/Orchestrator。"""

    task_id: str
    success: bool
    artifacts: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)


class JobRecord(DomainModel):
    """作用：记录全局 Job 状态；输入：项目路径和状态事件；输出：可持久化记录；数据流：Orchestrator -> StateManager。"""

    job_id: str = Field(default_factory=lambda: uuid4().hex)
    project_root: str
    status: JobStatus = JobStatus.CREATED
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScanOutcome(DomainModel):
    """作用：返回扫描流水线结果；输入：Job、ScanResult 和 artifacts；输出：应用层结果；数据流：Orchestrator -> AnalysisService/CLI。"""

    job_id: str
    result: ScanResult
    artifacts: dict[str, str] = Field(default_factory=dict)


class AnalysisOutcome(DomainModel):
    """作用：返回分析流水线结果；输入：Job、AnalysisResult 和 artifacts；输出：应用层结果；数据流：Orchestrator -> AnalysisService/CLI。"""

    job_id: str
    result: AnalysisResult
    artifacts: dict[str, str] = Field(default_factory=dict)


class PathClaim(DomainModel):
    """作用：声明任务对路径的读写意图；输入：任务、路径和操作；输出：冲突检测记录；数据流：Worker 计划 -> ConflictResolver。"""

    task_id: str
    path: str
    operation: str
