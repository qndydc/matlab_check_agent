"""
Description: 构建 MATLAB 内部调用图并计算循环、入口、孤立和核心指标。
References: NetworkX、domain.models。
Referenced By: workers.analyzer_agent 和 analyzer 单元测试。
"""

from __future__ import annotations

import math
from collections import defaultdict

import networkx as nx

from matlab_refactor_agent.domain.models import (
    AnalysisResult,
    DependencyEdge,
    FunctionInfo,
    ScanResult,
)


class DependencyAnalyzer:
    """作用：构建并度量 MATLAB 调用图；输入：ScanResult；输出：AnalysisResult；数据流：函数元数据 -> 名称解析/图算法 -> 分析结论。"""

    def analyze(
        self, scan: ScanResult, configured_entry_points: list[str] | None = None
    ) -> AnalysisResult:
        """作用：执行完整依赖分析；输入：扫描结果和可选入口；输出：AnalysisResult；数据流：节点/调用 -> 有向图 -> 循环/孤立/核心指标。"""

        functions = [function for item in scan.files for function in item.functions]
        graph = nx.DiGraph()
        diagnostics = [message for item in scan.files for message in item.diagnostics]

        by_qualified: dict[str, FunctionInfo] = {}
        by_simple: dict[str, list[str]] = defaultdict(list)
        by_file_simple: dict[tuple[str, str], list[str]] = defaultdict(list)
        for function in functions:
            if function.qualified_name in by_qualified:
                diagnostics.append(f"发现重复函数标识: {function.qualified_name}")
                continue
            by_qualified[function.qualified_name] = function
            by_simple[function.name].append(function.qualified_name)
            by_file_simple[(function.file_path, function.name)].append(function.qualified_name)
            graph.add_node(function.qualified_name)

        unresolved: dict[str, list[str]] = {}
        callers: dict[str, set[str]] = defaultdict(set)
        for function in functions:
            if by_qualified.get(function.qualified_name) is not function:
                continue
            missing: set[str] = set()
            for call in function.calls:
                target = _resolve_call(
                    call, function, by_qualified, by_simple, by_file_simple
                )
                if target is None:
                    missing.add(call)
                    continue
                graph.add_edge(function.qualified_name, target)
                callers[target].add(function.qualified_name)
            if missing:
                unresolved[function.qualified_name] = sorted(missing)

        normalized_functions = [
            function.model_copy(update={"called_by": sorted(callers[function.qualified_name])})
            for function in sorted(
                by_qualified.values(), key=lambda item: item.qualified_name
            )
        ]
        cycles = _stable_cycles(graph)
        orphans = sorted(node for node in graph if graph.degree(node) == 0)
        entry_points = _entry_points(
            graph, configured_entry_points or [], by_qualified, by_simple, by_file_simple
        )
        core_functions = _core_functions(graph, set(orphans))
        dependencies = [
            DependencyEdge(source=source, target=target)
            for source, target in sorted(graph.edges())
        ]
        return AnalysisResult(
            project_root=scan.project_root,
            functions=normalized_functions,
            dependencies=dependencies,
            cycles=cycles,
            orphans=orphans,
            core_functions=core_functions,
            entry_points=entry_points,
            unresolved_calls=unresolved,
            diagnostics=diagnostics,
        )


def _resolve_call(
    call: str,
    caller: FunctionInfo | None,
    by_qualified: dict[str, FunctionInfo],
    by_simple: dict[str, list[str]],
    by_file_simple: dict[tuple[str, str], list[str]],
) -> str | None:
    """作用：安全解析调用目标；输入：调用名、调用者及索引；输出：唯一节点名或空；数据流：完整名 -> 文件作用域 -> 类/包作用域 -> 全局短名。"""

    if call in by_qualified:
        return call
    if caller is not None:
        local_candidates = by_file_simple.get((caller.file_path, call), [])
        if len(local_candidates) == 1:
            return local_candidates[0]
        if "." in caller.qualified_name:
            scope = caller.qualified_name.rsplit(".", 1)[0]
            scoped_name = f"{scope}.{call}"
            if scoped_name in by_qualified:
                return scoped_name
    candidates = by_simple.get(call, [])
    return candidates[0] if len(candidates) == 1 else None


def _stable_cycles(graph: nx.DiGraph) -> list[list[str]]:
    """作用：生成确定顺序的循环列表；输入：有向图；输出：规范化环；数据流：simple_cycles -> 旋转归一化 -> 排序。"""

    normalized: set[tuple[str, ...]] = set()
    for cycle in nx.simple_cycles(graph):
        if not cycle:
            continue
        rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
        normalized.add(min(rotations))
    return [list(cycle) for cycle in sorted(normalized)]


def _entry_points(
    graph: nx.DiGraph,
    configured: list[str],
    by_qualified: dict[str, FunctionInfo],
    by_simple: dict[str, list[str]],
    by_file_simple: dict[tuple[str, str], list[str]],
) -> list[str]:
    """作用：确定入口点；输入：调用图、配置和名称索引；输出：入口节点列表；数据流：手工名称解析或零入度检测 -> 排序。"""

    if configured:
        resolved = {
            target
            for item in configured
            if (
                target := _resolve_call(
                    item, None, by_qualified, by_simple, by_file_simple
                )
            )
            is not None
        }
        return sorted(resolved)
    return sorted(node for node in graph if graph.in_degree(node) == 0)


def _core_functions(graph: nx.DiGraph, orphans: set[str]) -> list[str]:
    """作用：识别核心函数；输入：调用图与孤立节点；输出：核心节点列表；数据流：过滤孤立节点 -> PageRank -> 前 20%。"""

    candidates = [node for node in graph if node not in orphans]
    if not candidates:
        return []
    scores = nx.pagerank(graph)
    count = max(1, math.ceil(len(candidates) * 0.2))
    return sorted(candidates, key=lambda node: (-scores[node], node))[:count]
