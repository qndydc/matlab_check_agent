"""
Description: 从分析与语义 artifacts 构建不含源码的规划 Agent 共享上下文。
References: ArtifactStore、domain.models、domain.planning、domain.semantics。
Referenced By: ModuleResponsibilityAgent 和 NamingDirectoryAgent。
"""

from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.planning import (
    PlanningContext,
    PlanningFunctionSummary,
)
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore


class PlanningContextBuilder:
    """作用：投影经验证的项目事实；输入：artifact 引用；输出：稳定有限上下文。"""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    def build(
        self,
        *,
        analysis_reference: str,
        semantic_reference: str,
    ) -> PlanningContext:
        analysis = self._artifacts.read_model(
            analysis_reference, AnalysisResult
        )
        semantic = self._artifacts.read_model(
            semantic_reference, SemanticIndex
        )
        return PlanningContext(
            project_root=semantic.project_root,
            project_purpose=semantic.project.purpose,
            entry_points=analysis.entry_points,
            functions=[
                PlanningFunctionSummary(
                    symbol_id=item.symbol_id,
                    file_path=item.file_path,
                    summary=item.summary,
                    inputs=item.inputs,
                    outputs=item.outputs,
                    risks=item.risks,
                    confidence=item.confidence,
                )
                for item in semantic.functions
            ],
            dependencies=[
                (edge.source, edge.target) for edge in analysis.dependencies
            ],
            cycles=analysis.cycle_clusters,
            semantic_conflicts=[
                f"{item.symbol_id}: {item.reason}"
                for item in semantic.conflicts
            ],
        )
