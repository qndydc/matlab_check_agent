"""
Description: 为 Reason 构建 WCC 调用链上下文，并为 Act 裁剪最小输入。
References: TranslationContextBuilder、domain.migration、ArtifactStore。
Referenced By: MatlabToPythonMigrationAgent 与调用链测试。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from matlab_refactor_agent.agents.matlab_to_python.context_builder import (
    TranslationContextBuilder,
)
from matlab_refactor_agent.domain.migration import (
    ActContext,
    CallChainContext,
    ConversionStratagem,
    MatlabToPythonPlan,
    MigrationWorkUnit,
)
from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore


class CallChainContextBuilder:
    """WCC 的完整结构只交给 Reason，Act 只获得策略和源码。"""

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
        requested_context: list[str] | None = None,
    ) -> CallChainContext:
        base = TranslationContextBuilder(self._artifacts).build(
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            semantic_reference=semantic_reference,
            plan=plan,
            unit=unit,
        )
        analysis = self._artifacts.read_model(
            analysis_reference, AnalysisResult
        )
        symbols = set(unit.symbol_ids)
        context = CallChainContext(
            project_root=base.project_root,
            unit=unit,
            architecture=base.architecture,
            functions=base.functions,
            project_structure=[
                f"{symbol} -> {module}"
                for symbol, module in sorted(plan.symbol_to_module.items())
            ],
            internal_dependencies=[
                f"{edge.source} -> {edge.target}"
                for edge in analysis.dependencies
                if edge.source in symbols and edge.target in symbols
            ],
            unresolved_calls={
                symbol: calls
                for symbol, calls in analysis.unresolved_calls.items()
                if symbol in symbols and calls
            },
            semantic_index_used=semantic_reference is not None,
            requested_context=requested_context or [],
            core_symbols=(self._artifacts.read_model(semantic_reference, SemanticIndex).core_functions
                          if semantic_reference else []),
        )
        # Only resolve symbols in the immutable project scan; never search parent directories.
        for requested in dict.fromkeys(requested_context or []):
            name = requested.replace("\\", "/").strip()
            matches = [item for item in analysis.functions if name in {
                item.qualified_name, item.file_path.replace("\\", "/"),
            }]
            if not matches and "/" not in name:
                matches = [item for item in analysis.functions if
                           PurePosixPath(name).stem == item.name or
                           name == PurePosixPath(item.file_path.replace("\\", "/")).name]
            paths = {item.file_path for item in matches}
            if matches and len(paths) == 1:
                extra_symbols = sorted({item.qualified_name for item in matches} - symbols -
                                       {item.symbol_id for item in context.dependency_functions})
                if extra_symbols:
                    extra = TranslationContextBuilder(self._artifacts).build(
                        scan_reference=scan_reference, analysis_reference=analysis_reference,
                        semantic_reference=semantic_reference, plan=plan,
                        unit=MigrationWorkUnit(unit_id=unit.unit_id, symbol_ids=extra_symbols),
                    )
                    context.dependency_functions.extend(extra.functions)
                context.context_notes.append(f"{requested}: 项目内源码已提供，不再重复请求")
            else:
                symbol = PurePosixPath(name).stem
                sites = [f"{item.symbol_id}: {line.strip()[:300]}"
                         for item in context.functions for line in item.source.splitlines()
                         if symbol in line][:12]
                context.external_dependencies[requested] = sites or ["调用接口未知，不得臆造签名或实现"]
                context.context_notes.append(
                    f"{requested}: {'存在多个同名定义，无法唯一解析' if matches else '当前项目扫描中没有源码'}；"
                    "仅保留调用点接口证据，不再请求项目外文件")
        return context

    @staticmethod
    def for_act(
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> ActContext:
        return ActContext(
            unit=context.unit,
            stratagem=stratagem,
            functions=context.functions,
            dependency_functions=context.dependency_functions,
        )


__all__ = ["CallChainContextBuilder"]
