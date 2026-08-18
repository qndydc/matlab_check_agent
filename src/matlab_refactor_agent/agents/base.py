"""
Description: 定义专责 Agent 抽象接口和只含基础设施的运行上下文。
References: abc、domain.agents、infrastructure.artifacts。
Referenced By: agents.runtime 和具体 Agent 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from matlab_refactor_agent.domain.agents import AgentRequest, AgentResult
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore


@dataclass(frozen=True, slots=True)
class AgentContext:
    """作用：向 Agent 注入共享基础设施；输入：ArtifactStore；输出：不可变上下文；数据流：运行时装配 -> Agent。"""

    artifact_store: ArtifactStore


class BaseAgent(ABC):
    """作用：约束所有 LLM/工具驱动 Agent；输入：有限请求上下文；输出：结构化 AgentResult；数据流：Runtime -> Agent -> Proposal。"""

    @property
    @abstractmethod
    def kind(self) -> AgentKind:
        """返回当前 Agent 的唯一类型。"""

    @abstractmethod
    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        """执行一次无直接源码写入的 Agent 请求。"""
