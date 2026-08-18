"""
Description: 实现根据语义索引和依赖图提出模块职责与边界的结构化 Agent。
References: agents.base、agents.planning_context、StructuredLLMClient、domain.planning。
Referenced By: LangGraphWorkflow 和规划 Agent 测试。
"""

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.agents.planning_context import PlanningContextBuilder
from matlab_refactor_agent.domain.agents import AgentProposal, AgentRequest, AgentResult
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.planning import ModuleResponsibilityResponse
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient

SYSTEM_PROMPT = (
    "你是 MATLAB 项目模块职责规划 Agent。只使用给定语义摘要和依赖事实；"
    "每个函数必须恰好归属一个候选模块，模块依赖必须引用响应中的 module_id。"
    "只提出方案，不修改文件。"
)


class ModuleResponsibilityAgent(BaseAgent):
    """作用：生成可验证的模块职责候选；输入：语义和分析引用；输出：proposal artifact。"""

    def __init__(self, client: StructuredLLMClient) -> None:
        self._client = client

    @property
    def kind(self) -> AgentKind:
        return AgentKind.MODULE_RESPONSIBILITY

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        planning = PlanningContextBuilder(context.artifact_store).build(
            analysis_reference=request.artifact_refs["analysis_result"],
            semantic_reference=request.artifact_refs["semantic_index"],
        )
        response = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=planning.model_dump_json(indent=2),
            response_model=ModuleResponsibilityResponse,
        )
        self._validate_response(response, planning)
        reference = context.artifact_store.write_model(
            request.job_id, "module-responsibility.json", response
        )
        confidence = (
            min(item.confidence for item in response.modules)
            if response.modules
            else 1.0
        )
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[
                AgentProposal(
                    agent_kind=self.kind,
                    payload={"module_responsibility_ref": reference},
                    evidence=[item.symbol_id for item in planning.functions],
                    confidence=confidence,
                )
            ],
            artifacts={"module_responsibility": reference},
        )

    @staticmethod
    def _validate_response(response, context) -> None:
        if response.project_root != context.project_root:
            raise OrchestrationError("模块职责响应的 project_root 不匹配")
        known = {item.symbol_id for item in context.functions}
        module_ids = [item.module_id for item in response.modules]
        if len(module_ids) != len(set(module_ids)):
            raise OrchestrationError("模块职责响应包含重复 module_id")
        assigned = [
            symbol
            for module in response.modules
            for symbol in module.symbol_ids
        ]
        if len(assigned) != len(set(assigned)):
            raise OrchestrationError("同一 symbol 不能归属多个候选模块")
        unassigned = set(response.unassigned_symbols)
        if set(assigned) | unassigned != known or set(assigned) & unassigned:
            raise OrchestrationError("模块职责响应必须完整覆盖项目 symbol")
        module_id_set = set(module_ids)
        for module in response.modules:
            dependencies = set(module.depends_on_modules)
            if module.module_id in dependencies or not dependencies <= module_id_set:
                raise OrchestrationError("模块依赖包含自身或未知 module_id")
