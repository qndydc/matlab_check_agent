"""
Description: 对函数簇语义执行确定性完整性、置信度和证据一致性自检。
References: domain.semantics。
Referenced By: SemanticAnnotationPipeline 的 quality_check 节点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    SemanticWorkUnit,
)


QualityStatus = Literal["accepted", "low_quality", "incomplete"]


@dataclass(frozen=True)
class SemanticQualityDecision:
    status: QualityStatus
    reasons: list[str]


class SemanticQualityReviewer:
    """只依据结构化产物判断是否需要反馈重试，不相信模型的单一自评分。"""

    def __init__(self, confidence_threshold: float = 0.6) -> None:
        self._threshold = confidence_threshold

    def review(
        self,
        unit: SemanticWorkUnit,
        response: ClusterAnnotationResponse | None,
        *,
        annotation_error: str = "",
    ) -> SemanticQualityDecision:
        reasons: list[str] = []
        if annotation_error or response is None:
            return SemanticQualityDecision(
                status="incomplete",
                reasons=[annotation_error or "函数簇没有注释产物"],
            )

        expected = set(unit.symbol_ids)
        actual_symbols = [item.symbol_id for item in response.annotations]
        missing = sorted(expected - set(actual_symbols))
        unexpected = sorted(set(actual_symbols) - expected)
        duplicates = sorted(
            symbol
            for symbol in set(actual_symbols)
            if actual_symbols.count(symbol) > 1
        )
        if missing:
            reasons.append(f"缺少函数注释: {', '.join(missing)}")
        if unexpected:
            reasons.append(f"包含簇外函数注释: {', '.join(unexpected)}")
        if duplicates:
            reasons.append(f"函数注释重复: {', '.join(duplicates)}")

        empty_summaries = sorted(
            item.symbol_id
            for item in response.annotations
            if not item.summary.strip()
        )
        if empty_summaries:
            reasons.append(f"函数用途为空: {', '.join(empty_summaries)}")
        invalid_evidence = sorted(
            item.symbol_id
            for item in response.annotations
            if not item.evidence
            or any(
                evidence.file_path != item.file_path
                or evidence.start_line != item.start_line
                or evidence.end_line != item.end_line
                or not evidence.source_hash
                for evidence in item.evidence
            )
        )
        if invalid_evidence:
            reasons.append(
                f"源码证据缺失或定位不一致: {', '.join(invalid_evidence)}"
            )
        if missing or unexpected or duplicates or empty_summaries or invalid_evidence:
            return SemanticQualityDecision("incomplete", reasons)

        low = sorted(
            item.symbol_id
            for item in response.annotations
            if item.confidence < self._threshold
        )
        if low:
            reasons.append(
                f"置信度低于 {self._threshold:.2f}: {', '.join(low)}"
            )
            return SemanticQualityDecision("low_quality", reasons)
        return SemanticQualityDecision("accepted", [])


__all__ = [
    "QualityStatus",
    "SemanticQualityDecision",
    "SemanticQualityReviewer",
]
