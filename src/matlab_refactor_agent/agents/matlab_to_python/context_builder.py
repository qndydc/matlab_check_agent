"""
Description: 从分析、语义和源码 artifact 构建受控 MATLAB 到 Python 转换上下文。
References: SemanticContextBuilder、domain.migration、domain.semantics。
Referenced By: MatlabToPythonAgent 和 AnalysisWorkflow。
"""

from __future__ import annotations

from matlab_refactor_agent.workers.semantic_context import SemanticContextBuilder
from matlab_refactor_agent.domain.migration import (
    MatlabToPythonPlan,
    MigrationWorkUnit,
    TranslationContext,
    TranslationFunctionContext,
)
from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.semantics import SemanticIndex, SemanticWorkUnit
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore


class TranslationContextBuilder:
    """只加载当前迁移单元源码、语义和直接邻居接口摘要。"""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    def build(
        self,
        *,
        scan_reference: str,
        analysis_reference: str,
        semantic_reference: str | None,
        plan: MatlabToPythonPlan,
        unit: MigrationWorkUnit,
    ) -> TranslationContext:
        analysis = self._artifacts.read_model(analysis_reference, AnalysisResult)
        semantics = (
            self._artifacts.read_model(semantic_reference, SemanticIndex)
            if semantic_reference
            else None
        )
        annotations = {
            item.symbol_id: item for item in semantics.functions
        } if semantics else {}
        source_context = SemanticContextBuilder(self._artifacts).build(
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            unit=SemanticWorkUnit(unit_id=unit.unit_id, symbol_ids=unit.symbol_ids, estimated_tokens=0),
            token_budget=10**9,
        )
        functions = [
            TranslationFunctionContext(
                symbol_id=item.symbol_id,
                file_path=item.file_path,
                source=item.source,
                source_hash=item.source_hash,
                inputs=item.inputs,
                outputs=item.outputs,
                semantic_summary=(
                    annotations[item.symbol_id].summary
                    if item.symbol_id in annotations
                    else ""
                ),
                risks=(
                    annotations[item.symbol_id].risks
                    if item.symbol_id in annotations
                    else []
                ),
            )
            for item in source_context.functions
        ]
        summaries = {
            item.qualified_name: (
                f"{item.qualified_name}({', '.join(item.inputs)}) -> "
                f"{', '.join(item.outputs) or 'None'}"
            )
            for item in analysis.functions
        }
        return TranslationContext(
            project_root=analysis.project_root,
            unit=unit,
            architecture=plan.architecture,
            functions=functions,
            neighbor_contracts=[
                summaries[item.symbol_id]
                for item in source_context.neighbors
                if item.symbol_id in summaries
            ],
        )
