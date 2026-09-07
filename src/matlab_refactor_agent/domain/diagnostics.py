"""
Description: 定义差分验证和修复路由使用的结构化事实。
References: Pydantic、domain.models。
Referenced By: 执行器、验证器、失败分类器与迁移图。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field

from .models import DomainModel

FailureCategory = Literal[
    "translation", "assembly", "import", "shape", "dtype", "numeric",
    "complex", "exception", "side_effect", "environment", "unknown",
]

CallStatus = Literal["success", "failure"]
CallErrorType = Literal[
    "invalid_arguments",
    "invalid_response",
    "output_truncated",
    "timeout",
    "rate_limited",
    "service_unavailable",
    "remote_protocol_error",
    "empty_result",
    "bad_request",
    "unauthorized",
    "forbidden",
    "tool_not_found",
    "unsafe_action",
    "unknown",
]


class CallObservation(DomainModel):
    """一次模型或工具调用的安全、可持久化摘要。"""

    tool: str
    status: CallStatus
    result: str
    error_type: CallErrorType | None = None
    retryable: bool = False
    attempt: int = Field(default=1, ge=1)
    duration_ms: int = Field(default=0, ge=0)
    next_action: str = "finish"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class CallObservationLog(DomainModel):
    """保存一个 Job 的调用生命周期记录。"""

    job_id: str
    observations: list[CallObservation] = Field(default_factory=list)


class ValidationFact(DomainModel):
    kind: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""


class ExecutionResult(DomainModel):
    succeeded: bool
    outputs: list[Any] = Field(default_factory=list)
    output_shapes: list[list[int]] = Field(default_factory=list)
    output_dtypes: list[str] = Field(default_factory=list)
    exception_type: str | None = None
    exception_message: str | None = None
    file_side_effects: list[dict[str, str]] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


class DifferentialObservation(DomainModel):
    unit_id: str
    passed: bool
    facts: list[ValidationFact] = Field(default_factory=list)
    matlab_result_ref: str | None = None
    python_result_ref: str | None = None


class FailureDiagnostic(DomainModel):
    unit_id: str
    category: FailureCategory
    summary: str
    evidence: list[ValidationFact] = Field(default_factory=list)
    repairable: bool = True
    action: Literal["repair", "semantic_review", "manual_review"] = "repair"
