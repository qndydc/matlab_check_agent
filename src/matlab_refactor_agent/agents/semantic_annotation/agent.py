"""
Description: 兼容 0.1 版 SemanticAnnotationAgent 协议并转发到语义流水线注释器。
References: ClusterSemanticAnnotator、agents.base、domain.agents。
Referenced By: 旧扩展；生产代码直接使用 semantics.ClusterSemanticAnnotator。
"""

from __future__ import annotations

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.domain.agents import (
    AgentProposal,
    AgentRequest,
    AgentResult,
)
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    SemanticAnnotationRequest,
    SemanticWorkUnit,
)
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.semantics.annotator import ClusterSemanticAnnotator


class SemanticAnnotationAgent(BaseAgent):
    """已弃用兼容适配器；不应作为新语义架构的公共入口。"""

    def __init__(self, client: StructuredLLMClient) -> None:
        self._client = client

    @property
    def kind(self) -> AgentKind:
        return AgentKind.SEMANTIC_ANNOTATION

    def run(
        self, request: AgentRequest, context: AgentContext
    ) -> AgentResult:
        unit = SemanticWorkUnit.model_validate(request.inputs["work_unit"])
        reference = ClusterSemanticAnnotator(
            self._client, context.artifact_store
        ).annotate(
            SemanticAnnotationRequest(
                job_id=request.job_id,
                scan_reference=request.artifact_refs["scan_result"],
                analysis_reference=request.artifact_refs["analysis_result"],
                unit=unit,
                token_budget=int(request.inputs.get("token_budget", 32_768)),
                hard_token_limit=int(
                    request.inputs.get("hard_token_limit", 1_000_000)
                ),
                attempt=int(request.inputs.get("attempt", 1)),
                quality_feedback=list(
                    request.inputs.get("quality_feedback", [])
                ),
                previous_annotation_reference=(
                    str(request.inputs["previous_annotation_ref"])
                    if request.inputs.get("previous_annotation_ref")
                    else None
                ),
            )
        )
        response = context.artifact_store.read_model(
            reference, ClusterAnnotationResponse
        )
        confidence = min(item.confidence for item in response.annotations)
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[
                AgentProposal(
                    agent_kind=self.kind,
                    payload={
                        "unit_id": unit.unit_id,
                        "annotation_ref": reference,
                    },
                    evidence=[
                        evidence.source_hash
                        for item in response.annotations
                        for evidence in item.evidence
                    ],
                    confidence=confidence,
                )
            ],
            artifacts={f"function_annotations_{unit.unit_id}": reference},
        )


__all__ = ["SemanticAnnotationAgent"]
