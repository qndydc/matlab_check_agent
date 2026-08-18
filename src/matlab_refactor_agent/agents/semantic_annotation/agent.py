"""
Description: 实现按函数簇调用结构化 LLM 并产出可审计函数注解的 SemanticAnnotationAgent。
References: agents.base、context_builder、StructuredLLMClient、domain.semantics。
Referenced By: LangGraphWorkflow 和 Agent 测试。
"""

from __future__ import annotations

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.domain.agents import AgentProposal, AgentRequest, AgentResult
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    SemanticWorkUnit,
)
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient

from .context_builder import SemanticContextBuilder

SYSTEM_PROMPT = (
    "你是 MATLAB 代码语义分析 Agent。只能根据给定源码和邻居摘要回答；"
    "不得臆测未提供的实现。为每个目标 symbol 返回一条带源码哈希证据和置信度的注解。"
    "summary、data_flow、side_effects、risks 和 open_questions 等所有说明字段必须使用中文，"
    "函数名、变量名和 MATLAB 专有名词可以保留原文。"
    "只有实现项目关键领域算法或关键数值方法的函数，is_algorithm_core 才能为 true；"
    "入口、工具函数或被频繁调用本身不构成算法核心。"
)


class SemanticAnnotationAgent(BaseAgent):
    """作用：完成单函数簇的提示、调用、响应校验和 artifact 写入闭环。"""

    def __init__(self, client: StructuredLLMClient) -> None:
        self._client = client

    @property
    def kind(self) -> AgentKind:
        return AgentKind.SEMANTIC_ANNOTATION

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        unit = SemanticWorkUnit.model_validate(request.inputs.get("work_unit"))
        builder = SemanticContextBuilder(context.artifact_store)
        cluster_context = builder.build(
            scan_reference=request.artifact_refs["scan_result"],
            analysis_reference=request.artifact_refs["analysis_result"],
            unit=unit,
            token_budget=int(request.inputs.get("token_budget", 6000)),
        )
        response = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=cluster_context.model_dump_json(indent=2),
            response_model=ClusterAnnotationResponse,
        )
        self._validate_response(response, cluster_context)
        reference = context.artifact_store.write_model(
            request.job_id,
            f"function-annotations-{unit.unit_id}.json",
            response,
        )
        confidence = min(item.confidence for item in response.annotations)
        proposal = AgentProposal(
            agent_kind=self.kind,
            payload={"unit_id": unit.unit_id, "annotation_ref": reference},
            evidence=[item.source_hash for item in cluster_context.functions],
            confidence=confidence,
        )
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[proposal],
            artifacts={f"function_annotations_{unit.unit_id}": reference},
        )

    @staticmethod
    def _validate_response(response, context) -> None:
        if response.unit_id != context.unit.unit_id:
            raise OrchestrationError("LLM 响应 unit_id 与请求不一致")
        expected = {item.symbol_id: item for item in context.functions}
        actual = [item.symbol_id for item in response.annotations]
        if len(actual) != len(set(actual)) or set(actual) != set(expected):
            raise OrchestrationError(
                "LLM 响应必须为函数簇中的每个 symbol 返回且只返回一条注解"
            )
        for annotation in response.annotations:
            source = expected[annotation.symbol_id]
            if (
                annotation.file_path != source.file_path
                or annotation.start_line != source.start_line
                or annotation.end_line != source.end_line
            ):
                raise OrchestrationError(
                    f"LLM 响应源码定位不匹配: {annotation.symbol_id}"
                )
            if not any(
                evidence.source_hash == source.source_hash
                for evidence in annotation.evidence
            ):
                raise OrchestrationError(
                    f"LLM 响应缺少当前源码哈希证据: {annotation.symbol_id}"
                )
