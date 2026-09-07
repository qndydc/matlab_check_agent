"""
Description: 在不调用 LLM 的前提下读取分析图、生成 SCC 语义簇并持久化准备清单。
References: SemanticWorkUnitBuilder、SemanticContextBuilder、QualityGate、ArtifactStore。
Referenced By: MainWorkflow 和 SemanticAnnotationGraph。
"""

from __future__ import annotations

from matlab_refactor_agent.workers.semantic_context import (
    SemanticContextBuilder,
    SemanticWorkUnitBuilder,
)
from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.semantics import SemanticPreparationBundle
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

from .quality_gate import QualityGate


class SemanticPreparation:
    """把确定性 Analysis Bundle 转换成语义图可读取的不可变输入。"""

    def __init__(self, artifacts: ArtifactStore, quality_gate: QualityGate) -> None:
        self._artifacts = artifacts
        self._quality_gate = quality_gate

    def prepare(
        self,
        *,
        job_id: str,
        scan_reference: str,
        analysis_reference: str,
        code_tree_reference: str,
        token_budget: int,
        hard_token_limit: int,
        max_functions_per_unit: int,
    ) -> str:
        """生成 SCC 工作单元、预检上下文并返回 preparation artifact 引用。"""

        scan = self._artifacts.read_model(scan_reference, ScanResult)
        analysis = self._artifacts.read_model(analysis_reference, AnalysisResult)
        contexts = SemanticContextBuilder(self._artifacts)
        units = SemanticWorkUnitBuilder(
            token_budget=token_budget,
            max_functions_per_unit=max_functions_per_unit,
            hard_token_limit=hard_token_limit,
        ).build(
            analysis,
            context_estimator=lambda unit: contexts.estimate(
                scan_reference=scan_reference,
                analysis_reference=analysis_reference,
                unit=unit,
            ),
        )
        prepared_contexts = [
            contexts.build(
                scan_reference=scan_reference,
                analysis_reference=analysis_reference,
                unit=unit,
                token_budget=token_budget,
                hard_token_limit=hard_token_limit,
            )
            for unit in units.units
        ]
        self._quality_gate.validate_semantic_preflight(
            scan, analysis, units, prepared_contexts
        )
        units_reference = self._artifacts.write_model(
            job_id, "semantic-work-units.json", units
        )
        bundle = SemanticPreparationBundle(
            job_id=job_id,
            project_root=analysis.project_root,
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            code_tree_reference=code_tree_reference,
            work_units_reference=units_reference,
            token_budget=token_budget,
            hard_token_limit=hard_token_limit,
        )
        return self._artifacts.write_model(
            job_id, "semantic-preparation.json", bundle
        )
