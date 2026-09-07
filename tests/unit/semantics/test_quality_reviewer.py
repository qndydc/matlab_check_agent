"""
Description: 验证语义自检不会只依赖模型置信度，并能发现证据不一致。
References: SemanticQualityReviewer、domain.semantics。
Referenced By: pytest 测试发现。
"""

from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    FunctionAnnotation,
    SemanticWorkUnit,
    SourceEvidence,
)
from matlab_refactor_agent.semantics import SemanticQualityReviewer


def _response(*, confidence: float, evidence_path: str = "main.m"):
    return ClusterAnnotationResponse(
        unit_id="cluster-1",
        annotations=[
            FunctionAnnotation(
                symbol_id="main",
                file_path="main.m",
                start_line=1,
                end_line=4,
                summary="读取输入并返回归一化结果",
                confidence=confidence,
                evidence=[
                    SourceEvidence(
                        file_path=evidence_path,
                        start_line=1,
                        end_line=4,
                        source_hash="source-hash",
                    )
                ],
            )
        ],
    )


def test_reviewer_accepts_complete_evidence_bound_annotation() -> None:
    unit = SemanticWorkUnit(
        unit_id="cluster-1", symbol_ids=["main"], estimated_tokens=10
    )

    decision = SemanticQualityReviewer(0.6).review(
        unit, _response(confidence=0.9)
    )

    assert decision.status == "accepted"
    assert decision.reasons == []


def test_reviewer_rejects_high_confidence_with_invalid_evidence() -> None:
    unit = SemanticWorkUnit(
        unit_id="cluster-1", symbol_ids=["main"], estimated_tokens=10
    )

    decision = SemanticQualityReviewer(0.6).review(
        unit, _response(confidence=0.99, evidence_path="other.m")
    )

    assert decision.status == "incomplete"
    assert "源码证据缺失或定位不一致" in decision.reasons[0]
