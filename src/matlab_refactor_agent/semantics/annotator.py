"""
Description: 使用有限源码上下文生成单个函数簇的结构化三级语义底稿。
References: StructuredLLMClient、SemanticContextBuilder、domain.semantics。
Referenced By: SemanticAnnotationPipeline 和兼容 SemanticAnnotationAgent。
"""

from __future__ import annotations

import json

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationDraftResponse,
    ClusterAnnotationResponse,
    FunctionAnnotation,
    SemanticAnnotationRequest,
    SourceEvidence,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.orchestration.call_lifecycle import CallObservationRecorder
from matlab_refactor_agent.workers.semantic_context import SemanticContextBuilder


SYSTEM_PROMPT = (
    "你是 MATLAB 代码语义分析器。只能根据给定源码和邻居摘要回答；"
    "不得臆测未提供的实现。为每个目标 symbol 返回一条带置信度的注解。"
    "源码证据由系统根据上下文附加，不要输出 evidence 或 source_hash。"
    "summary、data_flow、side_effects、risks 和 open_questions 必须使用中文；"
    "函数名、变量名和 MATLAB 专有名词可以保留原文。"
    "只有实现项目关键领域算法或关键数值方法的函数，is_algorithm_core 才能为 true；"
    "入口、工具函数或被频繁调用本身不构成算法核心。"
)


class ClusterSemanticAnnotator:
    """一次调用只把确定的源码上下文转换为结构化语义，不决定调度顺序。"""

    def __init__(
        self, client: StructuredLLMClient, artifacts: ArtifactStore,
        *, debug_model: bool = False,
    ) -> None:
        self._client = client
        self._artifacts = artifacts
        self._debug_model = debug_model

    def annotate(self, request: SemanticAnnotationRequest) -> str:
        """生成并持久化一个簇的注释，返回 artifact 引用。"""

        cluster_context = SemanticContextBuilder(self._artifacts).build(
            scan_reference=request.scan_reference,
            analysis_reference=request.analysis_reference,
            unit=request.unit,
            token_budget=request.token_budget,
            hard_token_limit=request.hard_token_limit,
        )
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][CONTEXT] unit={request.unit.unit_id} "
                f"attempt={request.attempt} functions={len(cluster_context.functions)} "
                f"neighbors={len(cluster_context.neighbors)} "
                f"estimated_tokens={cluster_context.estimated_tokens} "
                f"quality_feedback={len(request.quality_feedback)}",
                flush=True,
            )
        previous = None
        if request.previous_annotation_reference:
            previous = self._artifacts.read_model(
                request.previous_annotation_reference,
                ClusterAnnotationResponse,
            ).model_dump(mode="json")
        payload = cluster_context.model_dump(mode="json")
        payload.update(
            {
                "quality_feedback": request.quality_feedback,
                "previous_annotation": previous,
                "review_instruction": (
                    "首次分析请直接生成注释。若提供 quality_feedback，请根据源码证据修订问题，"
                    "不得仅通过提高 confidence 回避问题。"
                ),
            }
        )
        draft = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            response_model=ClusterAnnotationDraftResponse,
            tool_name=f"llm.semantic.annotate.{request.unit.unit_id}",
            observation_callback=CallObservationRecorder(
                self._artifacts, request.job_id
            ),
        )
        self._validate_response(draft, cluster_context)
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][RESPONSE] unit={request.unit.unit_id} "
                f"annotations={len(draft.annotations)} validation=passed",
                flush=True,
            )
        response = self._attach_evidence(draft, cluster_context)
        return self._artifacts.write_model(
            request.job_id,
            (
                f"function-annotations-{request.unit.unit_id}"
                f"-attempt-{request.attempt}.json"
            ),
            response,
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

    @staticmethod
    def _attach_evidence(draft, context) -> ClusterAnnotationResponse:
        expected = {item.symbol_id: item for item in context.functions}
        annotations = []
        for annotation in draft.annotations:
            source = expected[annotation.symbol_id]
            annotations.append(
                FunctionAnnotation(
                    **annotation.model_dump(mode="python"),
                    evidence=[
                        SourceEvidence(
                            file_path=source.file_path,
                            start_line=source.start_line,
                            end_line=source.end_line,
                            source_hash=source.source_hash,
                        )
                    ],
                )
            )
        return ClusterAnnotationResponse(
            unit_id=draft.unit_id, annotations=annotations
        )


__all__ = ["ClusterSemanticAnnotator"]
