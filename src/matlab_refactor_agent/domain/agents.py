"""
Description: 定义 Multi-Agent 请求、结构化提议和执行结果契约。
References: Pydantic、domain.enums、domain.models。
Referenced By: agents.base、agents.runtime 和各专责 Agent。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import Field

from .enums import AgentKind
from .models import DomainModel


class AgentRequest(DomainModel):
    """作用：携带单个 Agent 的有限上下文；输入：artifact 引用和轻量参数；输出：可路由请求；数据流：Orchestrator -> AgentRuntime。"""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    job_id: str
    agent_kind: AgentKind
    artifact_refs: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class AgentProposal(DomainModel):
    """作用：承载 Agent 的结构化建议而非直接文件写入；输入：Agent 推理结果；输出：可审计提议；数据流：Agent -> Reconciler/审批。"""

    proposal_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_kind: AgentKind
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AgentResult(DomainModel):
    """作用：统一 Agent 执行结果；输入：提议、诊断和 artifact；输出：运行时结果；数据流：Agent -> AgentRuntime -> Orchestrator。"""

    request_id: str
    success: bool
    proposals: list[AgentProposal] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)
