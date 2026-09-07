"""
Description: 按调用图和 token 预算生成函数簇，并构建只含目标源码与直接邻居摘要的上下文。
References: NetworkX、domain.models、domain.semantics、ArtifactStore。
Referenced By: SemanticPreparation、ClusterSemanticAnnotator 和单元测试。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import networkx as nx

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.models import AnalysisResult, FunctionInfo, ScanResult
from matlab_refactor_agent.domain.semantics import (
    FunctionSourceContext,
    NeighborSummary,
    SemanticClusterContext,
    SemanticWorkUnit,
    SemanticWorkUnits,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm.token_budget import estimate_tokens
from matlab_refactor_agent.workers.matlab_source import read_matlab_source


def finalize_context_tokens(
    context: SemanticClusterContext,
) -> SemanticClusterContext:
    """作用：按 Agent 实际发送的缩进 JSON 反复收敛并写入统一 token 估算值。"""

    current = context
    for _ in range(4):
        token_count = estimate_tokens(current.model_dump_json(indent=2))
        if (
            token_count == current.estimated_tokens
            and token_count == current.unit.estimated_tokens
        ):
            return current
        current = current.model_copy(
            update={
                "estimated_tokens": token_count,
                "unit": current.unit.model_copy(
                    update={"estimated_tokens": token_count}
                ),
            }
        )
    return current


class SemanticWorkUnitBuilder: #语义分组
    """作用：保持 SCC 原子性并按连通簇和预算分片；输入：分析结果；输出：稳定函数簇。"""

    # → 创建 NetworkX 图
    # → 找到 SCC
    # → 保持 SCC 内函数不拆分
    # → 根据依赖关系排序
    # → 按 token 预算组合
    # → 生成多个 SemanticWorkUnit

    def __init__(
        self,
        token_budget: int = 32_768,
        max_functions_per_unit: int = 8,
        hard_token_limit: int = 1_000_000,
    ) -> None:
        if token_budget <= 0:
            raise ValueError("token_budget 必须大于 0")
        if max_functions_per_unit <= 0:
            raise ValueError("max_functions_per_unit 必须大于 0")
        if hard_token_limit < token_budget:
            raise ValueError("hard_token_limit 不能小于 token_budget")
        self.token_budget = token_budget
        self.max_functions_per_unit = max_functions_per_unit
        self.hard_token_limit = hard_token_limit

    def build(
        self,
        analysis: AnalysisResult,
        context_estimator: Callable[[SemanticWorkUnit], int] | None = None,
    ) -> SemanticWorkUnits:
        """作用：按 SCC 拓扑顺序装箱，并可使用最终上下文的真实估算口径切簇。"""

        graph = nx.DiGraph()
        graph.add_nodes_from(item.qualified_name for item in analysis.functions)
        graph.add_edges_from((edge.source, edge.target) for edge in analysis.dependencies)
        estimates = {
            item.qualified_name: max(32, item.line_count * 12)
            for item in analysis.functions
        }
        units: list[SemanticWorkUnit] = []
        unit_number = 0
        components = sorted(
            nx.weakly_connected_components(graph), key=lambda items: min(items)
        )
        for component in components:
            subgraph = graph.subgraph(component)
            condensation = nx.condensation(subgraph)
            atomic_groups = [
                sorted(condensation.nodes[node]["members"])
                for node in nx.lexicographical_topological_sort(
                    condensation,
                    key=lambda item: min(condensation.nodes[item]["members"]),
                )
            ]
            current: list[str] = []
            current_tokens = 0
            for group in atomic_groups:
                group_tokens = self._estimate_unit(
                    unit_number,
                    group,
                    estimates,
                    context_estimator,
                )
                if group_tokens > self.hard_token_limit:
                    raise OrchestrationError(
                        "单个不可拆函数/SCC 超过模型上下文安全输入上限: "
                        f"{', '.join(group)} ({group_tokens} > "
                        f"{self.hard_token_limit})"
                    )
                candidate = [*current, *group]
                candidate_tokens = self._estimate_unit(
                    unit_number,
                    candidate,
                    estimates,
                    context_estimator,
                )
                exceeds_limit = (
                    candidate_tokens > self.token_budget
                    or len(candidate) > self.max_functions_per_unit
                )
                if current and exceeds_limit:
                    units.append(
                        self._unit(unit_number, current, current_tokens)
                    )
                    unit_number += 1
                    current = list(group)
                    current_tokens = group_tokens
                else:
                    current = candidate
                    current_tokens = candidate_tokens
            if current:
                units.append(self._unit(unit_number, current, current_tokens))
                unit_number += 1
        return SemanticWorkUnits(
            project_root=analysis.project_root,
            token_budget=self.token_budget,
            hard_token_limit=self.hard_token_limit,
            units=units,
        )

    @classmethod
    def _estimate_unit(
        cls,
        index: int,
        symbols: list[str],
        fallback_estimates: dict[str, int],
        context_estimator: Callable[[SemanticWorkUnit], int] | None,
    ) -> int:
        """作用：优先计算完整提示上下文，否则兼容使用旧的行数粗估。"""

        fallback = sum(fallback_estimates[symbol] for symbol in symbols)
        if context_estimator is None:
            return fallback
        unit = cls._unit(index, symbols, fallback)
        return context_estimator(unit)

    @staticmethod
    def _unit(index: int, symbols: list[str], tokens: int) -> SemanticWorkUnit:
        digest = hashlib.sha256("\0".join(symbols).encode("utf-8")).hexdigest()[:10]
        return SemanticWorkUnit(
            unit_id=f"cluster-{index:04d}-{digest}",
            symbol_ids=sorted(symbols),
            estimated_tokens=tokens,
        )


def split_semantic_work_unit(
    unit: SemanticWorkUnit,
    analysis: AnalysisResult,
) -> tuple[SemanticWorkUnit, SemanticWorkUnit] | None:
    """作用：沿 SCC 拓扑边界把输出超限工作单元近似二分；单一 SCC 返回空。"""

    symbols = set(unit.symbol_ids)
    graph = nx.DiGraph()
    graph.add_nodes_from(unit.symbol_ids)
    graph.add_edges_from(
        (edge.source, edge.target)
        for edge in analysis.dependencies
        if edge.source in symbols and edge.target in symbols
    )
    condensation = nx.condensation(graph)
    groups = [
        sorted(condensation.nodes[node]["members"])
        for node in nx.lexicographical_topological_sort(
            condensation,
            key=lambda item: min(condensation.nodes[item]["members"]),
        )
    ]
    if len(groups) < 2:
        return None
    cumulative = 0
    boundaries: list[tuple[float, int]] = []
    target = len(unit.symbol_ids) / 2
    for index, group in enumerate(groups[:-1], start=1):
        cumulative += len(group)
        boundaries.append((abs(cumulative - target), index))
    split_at = min(boundaries)[1]
    left_symbols = [symbol for group in groups[:split_at] for symbol in group]
    right_symbols = [symbol for group in groups[split_at:] for symbol in group]
    return (
        _split_unit(unit.unit_id, "a", left_symbols),
        _split_unit(unit.unit_id, "b", right_symbols),
    )


def _split_unit(
    parent_id: str,
    suffix: str,
    symbols: list[str],
) -> SemanticWorkUnit:
    """作用：为自动二分后的子工作单元生成稳定且可审计的标识。"""

    digest = hashlib.sha256("\0".join(symbols).encode("utf-8")).hexdigest()[:10]
    return SemanticWorkUnit(
        unit_id=f"{parent_id}-{suffix}-{digest}",
        symbol_ids=sorted(symbols),
        estimated_tokens=0,
    )


class SemanticContextBuilder: #构建源码上下文
    """作用：安全读取函数行范围并补充直接邻居摘要；输入：artifact 和工作单元；输出：预算内上下文。"""
    # → 找到函数所在文件
    # → 根据 start_line/end_line 读取源码片段
    # → 计算源码证据哈希
    # → 添加函数输入输出
    # → 添加调用者和被调用者摘要
    # → 组成 SemanticClusterContext

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._artifacts = artifact_store
        self._scan_cache: dict[str, ScanResult] = {}
        self._analysis_cache: dict[str, AnalysisResult] = {}
        self._source_cache: dict[tuple[str, str], FunctionSourceContext] = {}

    def build(
        self,
        *,
        scan_reference: str,
        analysis_reference: str,
        unit: SemanticWorkUnit,
        token_budget: int,
        hard_token_limit: int | None = None,
    ) -> SemanticClusterContext:
        context = self._context(
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            unit=unit,
        )
        finalized = finalize_context_tokens(context)
        effective_hard_limit = hard_token_limit or token_budget
        if finalized.estimated_tokens > effective_hard_limit:
            raise OrchestrationError(
                f"函数簇 {unit.unit_id} 上下文超过模型安全输入上限: "
                f"{finalized.estimated_tokens} > {effective_hard_limit}"
            )
        return finalized

    def estimate(
        self,
        *,
        scan_reference: str,
        analysis_reference: str,
        unit: SemanticWorkUnit,
    ) -> int:
        """作用：使用与 Agent 最终提示完全相同的上下文计算分组预算。"""

        context = self._context(
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            unit=unit,
        )
        return finalize_context_tokens(context).estimated_tokens

    def _context(
        self,
        *,
        scan_reference: str,
        analysis_reference: str,
        unit: SemanticWorkUnit,
    ) -> SemanticClusterContext:
        """作用：从缓存的分析 artifact 组装未校验预算的完整语义上下文。"""

        if scan_reference not in self._scan_cache:
            self._scan_cache[scan_reference] = self._artifacts.read_model(
                scan_reference, ScanResult
            )
        if analysis_reference not in self._analysis_cache:
            self._analysis_cache[analysis_reference] = self._artifacts.read_model(
                analysis_reference, AnalysisResult
            )
        scan = self._scan_cache[scan_reference]
        analysis = self._analysis_cache[analysis_reference]
        if scan.project_root != analysis.project_root:
            raise OrchestrationError("scan 与 analysis 的项目根目录不一致")
        functions = {item.qualified_name: item for item in analysis.functions}
        missing = sorted(set(unit.symbol_ids) - functions.keys())
        if missing:
            raise OrchestrationError(f"函数簇包含未知 symbol: {missing}")
        project_root = Path(scan.project_root).expanduser().resolve()
        source_contexts = []
        for symbol in unit.symbol_ids:
            cache_key = (str(project_root), symbol)
            if cache_key not in self._source_cache:
                self._source_cache[cache_key] = self._source_context(
                    project_root, functions[symbol]
                )
            source_contexts.append(self._source_cache[cache_key])
        neighbors = self._neighbors(analysis, set(unit.symbol_ids), functions)
        return SemanticClusterContext(
            project_root=str(project_root),
            unit=unit,
            functions=source_contexts,
            neighbors=neighbors,
            estimated_tokens=0,
        )

    @staticmethod
    def _source_context(root: Path, function: FunctionInfo) -> FunctionSourceContext:
        relative = Path(function.file_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise OrchestrationError(f"不安全的源码相对路径: {function.file_path}")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise OrchestrationError(f"源码路径越界: {function.file_path}") from exc
        try:
            lines = read_matlab_source(path).splitlines()
        except OSError as exc:
            raise OrchestrationError(f"无法读取源码 {path}: {exc}") from exc
        start = max(1, function.start_line)
        end = min(len(lines), max(start, function.end_line))
        source = "\n".join(lines[start - 1 : end])
        return FunctionSourceContext(
            symbol_id=function.qualified_name,
            file_path=function.file_path,
            start_line=start,
            end_line=end,
            inputs=function.inputs,
            outputs=function.outputs,
            source=source,
            source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _neighbors(
        analysis: AnalysisResult,
        symbols: set[str],
        functions: dict[str, FunctionInfo],
    ) -> list[NeighborSummary]:
        relations: set[tuple[str, str]] = set()
        for edge in analysis.dependencies:
            if edge.source in symbols and edge.target not in symbols:
                relations.add((edge.target, "callee"))
            if edge.target in symbols and edge.source not in symbols:
                relations.add((edge.source, "caller"))
        return [
            NeighborSummary(
                symbol_id=symbol,
                relation=relation,
                inputs=functions[symbol].inputs,
                outputs=functions[symbol].outputs,
            )
            for symbol, relation in sorted(relations)
            if symbol in functions
        ]
