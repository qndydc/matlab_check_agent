"""
Description: 为确定性 AnalyzerWorker 构建 MATLAB 调用图并计算结构指标。
References: NetworkX、domain.models。
Referenced By: workers.analyzer_agent 和 analyzer 单元测试。
"""

from __future__ import annotations

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
        # 1. 收集全部函数
        # 2. 建立名称索引
        # 3. 创建图节点
        # 4. 解析调用名称
        # 5. 创建图的边
        # 6. 计算图指标
        # 7. 生成 AnalysisResult

        # 1. 收集全部函数
        functions = [function for item in scan.files for function in item.functions]
        graph = nx.DiGraph()
        diagnostics = [message for item in scan.files for message in item.diagnostics]
        # 2. 建立名称索引
        by_qualified: dict[str, FunctionInfo] = {} #限定名称索引
        by_simple: dict[str, list[str]] = defaultdict(list) #简单名称索引
        by_file_simple: dict[tuple[str, str], list[str]] = defaultdict(list) #文件内名称索引
        for function in functions:
            if function.qualified_name in by_qualified:
                diagnostics.append(f"发现重复函数标识: {function.qualified_name}")
                continue
            by_qualified[function.qualified_name] = function
            by_simple[function.name].append(function.qualified_name)
            by_file_simple[(function.file_path, function.name)].append(function.qualified_name)
            graph.add_node(function.qualified_name) #先为所有函数添加节点，确保孤立函数也被包含在图中
        # 3. 创建图节点
        unresolved: dict[str, list[str]] = {}
        callers: dict[str, set[str]] = defaultdict(set)
        for function in functions:
            if by_qualified.get(function.qualified_name) is not function:
                continue
            missing: set[str] = set()
            # 4. 解析调用名称 当前函数中的调用名称，到底对应项目中的哪个函数？
            for call in function.calls:
                target = _resolve_call(
                    call, function, by_qualified, by_simple, by_file_simple
                )
                if target is None:
                    missing.add(call)
                    continue
            # 5. 创建图的边
                graph.add_edge(function.qualified_name, target) #正向调用
                callers[target].add(function.qualified_name) #记录反向调用，溯源caller
            if missing: #实在无法确认，就记录下来，后续可以在分析结果中报告
                unresolved[function.qualified_name] = sorted(missing)

        normalized_functions = [
            function.model_copy(update={"called_by": sorted(callers[function.qualified_name])})
            for function in sorted(
                by_qualified.values(), key=lambda item: item.qualified_name
            )
        ]
        cycle_clusters, cycles = _cycle_clusters(graph)
        orphans = sorted(node for node in graph if graph.degree(node) == 0)
        entry_points = _entry_points(
            graph, configured_entry_points or [], by_qualified, by_simple, by_file_simple
        )
        dependencies = [
            DependencyEdge(source=source, target=target)
            for source, target in sorted(graph.edges())
        ]
        return AnalysisResult(
            project_root=scan.project_root,
            functions=normalized_functions,
            dependencies=dependencies,
            cycles=cycles,
            cycle_clusters=cycle_clusters,
            orphans=orphans,
            # “算法核心”是语义概念，不能由图中心性可靠推断。
            core_functions=[],
            entry_points=entry_points,
            unresolved_calls=unresolved,
            diagnostics=diagnostics,
        )


def _resolve_call( #它负责回答：当前函数中的调用名称，到底对应项目中的哪个函数？
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


def _cycle_clusters(
    graph: nx.DiGraph, representative_limit: int = 3
) -> tuple[list[list[str]], list[list[str]]]:
    """先计算 SCC 循环簇，再为每簇提取少量确定性的代表环。"""

    clusters = sorted(
        (sorted(component) for component in nx.strongly_connected_components(graph) if len(component) > 1),
        key=lambda component: tuple(component),
    )
    representatives: list[list[str]] = []
    for cluster in clusters:
        subgraph = graph.subgraph(cluster)
        found: set[tuple[str, ...]] = set()
        for source, target in sorted(subgraph.edges()):
            try:
                return_path = nx.shortest_path(subgraph, target, source)
            except nx.NetworkXNoPath:
                continue
            cycle = [source, *return_path[:-1]]
            normalized = _normalize_cycle(cycle)
            found.add(normalized)
            if len(found) >= representative_limit:
                break
        representatives.extend(list(cycle) for cycle in sorted(found))
    return clusters, representatives


def _normalize_cycle(cycle: list[str]) -> tuple[str, ...]:
    """旋转环节点顺序，使同一有向环具有稳定表示。"""

    rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
    return min(rotations)


def _entry_points( #确认入口点 程序通常从哪里开始执行 （手工输入不稳定，大家写的代码不一定标准 0入口解析只能得到候选）
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
