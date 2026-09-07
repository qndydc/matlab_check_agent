"""
Description: 根据调用图和 SCC 确定性生成依赖优先迁移计划；该步骤不是 Agent。
References: networkx、domain.migration、domain.models。
Referenced By: MatlabToPythonMigrationAgent 和迁移规划测试。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

import networkx as nx

from matlab_refactor_agent.domain.migration import (
    MatlabToPythonPlan,
    MigrationWorkUnit,
    PythonArchitecture,
)
from matlab_refactor_agent.domain.models import AnalysisResult


class MigrationPlanBuilder:
    """以 WCC 组织完整调用链上下文，并在其中保留 SCC 原子边界。"""

    def build(self, analysis: AnalysisResult) -> MatlabToPythonPlan:
        graph = nx.DiGraph()
        graph.add_nodes_from(
            item.qualified_name for item in analysis.functions
        )
        graph.add_edges_from(
            (edge.source, edge.target) for edge in analysis.dependencies
        )
        sccs = [
            tuple(sorted(items))
            for items in nx.strongly_connected_components(graph)
        ]
        sccs.sort()
        scc_owner = {
            symbol: component
            for component in sccs
            for symbol in component
        }
        entry_candidates = set(analysis.entry_points)
        wccs = [
            tuple(sorted(items))
            for items in nx.weakly_connected_components(graph)
        ]
        wccs.sort()
        units: list[MigrationWorkUnit] = []
        for symbols in wccs:
            symbol_set = set(symbols)
            roots = sorted(entry_candidates & symbol_set)
            if not roots:
                roots = sorted(
                    symbol for symbol in symbols
                    if graph.subgraph(symbol_set).in_degree(symbol) == 0
                )
            contained_sccs = sorted({scc_owner[symbol] for symbol in symbols})
            units.append(MigrationWorkUnit(
                unit_id=self._wcc_id(symbols),
                symbol_ids=list(symbols),
                entry_symbols=roots,
                sccs=[list(component) for component in contained_sccs],
            ))
        package = self._package_name(
            PurePosixPath(
                analysis.project_root.replace("\\", "/")
            ).name
        )
        paths = {
            item.qualified_name: item.file_path for item in analysis.functions
        }
        return MatlabToPythonPlan(
            project_root=analysis.project_root,
            architecture=PythonArchitecture(
                package_name=package,
                source_directory=f"src/{package}",
                rules=[
                    "保留公开入口的参数与返回值语义",
                    "数组运算优先使用 NumPy，科学计算优先使用 SciPy",
                    "不得依赖 MATLAB Runtime，除非迁移计划明确标记人工适配",
                    "所有生成代码必须通过目标环境的静态检查和差分测试",
                ],
            ),
            units=units,
            symbol_to_module={
                symbol: self._module_path(package, path)
                for symbol, path in sorted(paths.items())
            },
        )

    @staticmethod
    def _unit_id(symbols: tuple[str, ...]) -> str:
        digest = hashlib.sha256(
            "\0".join(symbols).encode("utf-8")
        ).hexdigest()[:10]
        return f"migration-{digest}"

    @staticmethod
    def _wcc_id(symbols: tuple[str, ...]) -> str:
        digest = hashlib.sha256(
            "\0".join(symbols).encode("utf-8")
        ).hexdigest()[:10]
        return f"call-chain-{digest}"

    @staticmethod
    def _package_name(value: str) -> str:
        normalized = re.sub(r"\W+", "_", value.casefold()).strip("_")
        if not normalized:
            return "converted_project"
        return (
            f"project_{normalized}"
            if normalized[0].isdigit()
            else normalized
        )

    @staticmethod
    def _module_path(package: str, matlab_path: str) -> str:
        path = PurePosixPath(matlab_path.replace("\\", "/"))
        parts = [
            part[1:] if part.startswith("+") else part
            for part in path.with_suffix("").parts
        ]
        safe = [
            re.sub(r"\W+", "_", part).strip("_").casefold()
            for part in parts
        ]
        return ".".join([package, *filter(None, safe)])


__all__ = ["MigrationPlanBuilder"]
