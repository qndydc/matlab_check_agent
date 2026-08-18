"""
Description: 实现根据语义事实提出 MATLAB 命名和目录调整方案的结构化 Agent。
References: agents.base、agents.planning_context、StructuredLLMClient、domain.planning。
Referenced By: LangGraphWorkflow 和规划 Agent 测试。
"""

import re
from pathlib import PurePosixPath

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.agents.planning_context import PlanningContextBuilder
from matlab_refactor_agent.domain.agents import AgentProposal, AgentRequest, AgentResult
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.planning import NamingDirectoryResponse
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient

SYSTEM_PROMPT = (
    "你是 MATLAB 命名与目录规划 Agent。只使用给定事实，遵守 MATLAB 主函数与文件名一致、"
    "+package、@class 和 private 目录语义。只返回必要的候选变更，不修改文件。"
)

_MATLAB_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class NamingDirectoryAgent(BaseAgent):
    """作用：生成安全可审计的命名目录候选；输入：语义和分析引用；输出：proposal artifact。"""

    def __init__(self, client: StructuredLLMClient) -> None:
        self._client = client

    @property
    def kind(self) -> AgentKind:
        return AgentKind.NAMING_DIRECTORY

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        planning = PlanningContextBuilder(context.artifact_store).build(
            analysis_reference=request.artifact_refs["analysis_result"],
            semantic_reference=request.artifact_refs["semantic_index"],
        )
        response = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=planning.model_dump_json(indent=2),
            response_model=NamingDirectoryResponse,
        )
        self._validate_response(response, planning)
        reference = context.artifact_store.write_model(
            request.job_id, "naming-directory.json", response
        )
        confidence = (
            min(item.confidence for item in response.changes)
            if response.changes
            else 1.0
        )
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[
                AgentProposal(
                    agent_kind=self.kind,
                    payload={"naming_directory_ref": reference},
                    evidence=[item.symbol_id for item in planning.functions],
                    confidence=confidence,
                )
            ],
            artifacts={"naming_directory": reference},
        )

    @staticmethod
    def _validate_response(response, context) -> None:
        if response.project_root != context.project_root:
            raise OrchestrationError("命名目录响应的 project_root 不匹配")
        known = {item.symbol_id: item for item in context.functions}
        changed = [item.symbol_id for item in response.changes]
        unchanged = response.unchanged_symbols
        if len(changed) != len(set(changed)) or len(unchanged) != len(set(unchanged)):
            raise OrchestrationError("命名目录响应包含重复 symbol")
        if set(changed) & set(unchanged) or set(changed) | set(unchanged) != set(known):
            raise OrchestrationError("命名目录响应必须将每个 symbol 标为变更或保持不变")
        proposed_paths: set[str] = set()
        for change in response.changes:
            source = known[change.symbol_id]
            if change.current_file_path != source.file_path:
                raise OrchestrationError(f"现有路径不匹配: {change.symbol_id}")
            if not _MATLAB_IDENTIFIER.fullmatch(change.proposed_name):
                raise OrchestrationError(f"非法 MATLAB 标识符: {change.proposed_name}")
            path = PurePosixPath(change.proposed_file_path)
            if (
                "\\" in change.proposed_file_path
                or path.is_absolute()
                or ".." in path.parts
                or any(":" in part for part in path.parts)
                or path.suffix.lower() != ".m"
            ):
                raise OrchestrationError(f"非法候选路径: {change.proposed_file_path}")
            normalized = path.as_posix()
            if normalized in proposed_paths:
                raise OrchestrationError(f"候选路径冲突: {normalized}")
            proposed_paths.add(normalized)
