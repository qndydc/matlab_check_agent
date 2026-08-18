"""
Description: 根据确定性验证证据生成受限、待人工复审的完整修复操作列表。
References: agents.base、StructuredLLMClient、domain.validation、domain.planning。
Referenced By: LangGraphWorkflow 和修复闭环测试。
"""

import json
import re
from pathlib import PurePosixPath

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.domain.agents import AgentProposal, AgentRequest, AgentResult
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.planning import RefactorPlan
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.domain.validation import RepairProposal, ValidationResult
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient

SYSTEM_PROMPT = (
    "你是 MATLAB 重构修复 Agent。只能根据验证证据和现有计划提出修复；"
    "返回下一次执行使用的完整 operations 列表，不得请求直接编辑源项目，"
    "不得绕过人工审批，不得提出验证失败无关的改动。"
)

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class RepairAgent(BaseAgent):
    """作用：将失败证据转成结构化修复候选；输入：计划和验证引用；输出：proposal artifact。"""

    def __init__(self, client: StructuredLLMClient) -> None:
        self._client = client

    @property
    def kind(self) -> AgentKind:
        return AgentKind.REPAIR

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        plan = context.artifact_store.read_model(
            request.artifact_refs["refactor_plan"], RefactorPlan
        )
        validation = context.artifact_store.read_model(
            request.artifact_refs["validation"], ValidationResult
        )
        semantic = context.artifact_store.read_model(
            request.artifact_refs["semantic_index"], SemanticIndex
        )
        attempt = int(request.inputs["attempt"])
        prompt = {
            "attempt": attempt,
            "plan": plan.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
        }
        response = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            response_model=RepairProposal,
        )
        self._validate(response, plan, validation, semantic, attempt)
        reference = context.artifact_store.write_model(
            request.job_id,
            f"repair-proposal-{attempt:03d}.json",
            response,
        )
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[
                AgentProposal(
                    agent_kind=self.kind,
                    payload={"repair_proposal_ref": reference},
                    evidence=response.addressed_checks,
                    confidence=response.confidence,
                )
            ],
            artifacts={f"repair_proposal_{attempt}": reference},
        )

    @staticmethod
    def _validate(
        response: RepairProposal,
        plan: RefactorPlan,
        validation: ValidationResult,
        semantic: SemanticIndex,
        attempt: int,
    ) -> None:
        if response.attempt != attempt:
            raise OrchestrationError("修复提议 attempt 与请求不一致")
        failed = {
            item.check_id
            for item in validation.checks
            if item.status == "failed"
        }
        if not set(response.addressed_checks) <= failed:
            raise OrchestrationError("修复提议引用了非失败检查")
        known = set(plan.symbol_to_module)
        source_paths = {
            item.symbol_id: item.file_path for item in semantic.functions
        }
        symbols = [item.symbol_id for item in response.operations]
        if len(symbols) != len(set(symbols)) or not set(symbols) <= known:
            raise OrchestrationError("修复操作包含重复或未知 symbol")
        for operation in response.operations:
            if operation.module_id != plan.symbol_to_module[operation.symbol_id]:
                raise OrchestrationError("修复操作的 module_id 与已审计划不一致")
            if operation.source_path != source_paths[operation.symbol_id]:
                raise OrchestrationError("修复操作必须从原始 symbol 文件开始")
            if not _IDENTIFIER.fullmatch(operation.proposed_name):
                raise OrchestrationError(
                    f"修复提议包含非法标识符: {operation.proposed_name}"
                )
            for value in (operation.source_path, operation.target_path):
                path = PurePosixPath(value)
                if (
                    not value
                    or "\\" in value
                    or path.is_absolute()
                    or ".." in path.parts
                    or any(":" in part for part in path.parts)
                    or path.suffix.lower() != ".m"
                ):
                    raise OrchestrationError(f"修复提议路径不安全: {value}")
