"""
Description: 按调用图和 token 预算生成函数簇，并构建只含目标源码与直接邻居摘要的上下文。
References: NetworkX、domain.models、domain.semantics、ArtifactStore。
Referenced By: LangGraphWorkflow、SemanticAnnotationAgent 和单元测试。
"""

from __future__ import annotations

import hashlib
import math
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


def estimate_tokens(text: str) -> int: #按长度/4估算token消耗
    """作用：提供不依赖特定 tokenizer 的保守预算；输入：文本；输出：估算 token 数。"""

    return math.ceil(len(text) / 4)


class SemanticWorkUnitBuilder: #语义分组
    """作用：保持 SCC 原子性并按连通簇和预算分片；输入：分析结果；输出：稳定函数簇。"""

    # → 创建 NetworkX 图
    # → 找到 SCC
    # → 保持 SCC 内函数不拆分
    # → 根据依赖关系排序
    # → 按 token 预算组合
    # → 生成多个 SemanticWorkUnit

    def __init__(self, token_budget: int = 6000) -> None:
        if token_budget <= 0:
            raise ValueError("token_budget 必须大于 0")
        self.token_budget = token_budget

    def build(self, analysis: AnalysisResult) -> SemanticWorkUnits:
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
                group_tokens = sum(estimates[symbol] for symbol in group)
                if current and current_tokens + group_tokens > self.token_budget:
                    units.append(
                        self._unit(unit_number, current, current_tokens)
                    )
                    unit_number += 1
                    current = []
                    current_tokens = 0
                current.extend(group)
                current_tokens += group_tokens
            if current:
                units.append(self._unit(unit_number, current, current_tokens))
                unit_number += 1
        return SemanticWorkUnits(
            project_root=analysis.project_root,
            token_budget=self.token_budget,
            units=units,
        )

    @staticmethod
    def _unit(index: int, symbols: list[str], tokens: int) -> SemanticWorkUnit:
        digest = hashlib.sha256("\0".join(symbols).encode("utf-8")).hexdigest()[:10]
        return SemanticWorkUnit(
            unit_id=f"cluster-{index:04d}-{digest}",
            symbol_ids=sorted(symbols),
            estimated_tokens=tokens,
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

    def build(
        self,
        *,
        scan_reference: str,
        analysis_reference: str,
        unit: SemanticWorkUnit,
        token_budget: int,
    ) -> SemanticClusterContext:
        scan = self._artifacts.read_model(scan_reference, ScanResult)
        analysis = self._artifacts.read_model(analysis_reference, AnalysisResult)
        if scan.project_root != analysis.project_root:
            raise OrchestrationError("scan 与 analysis 的项目根目录不一致")
        functions = {item.qualified_name: item for item in analysis.functions}
        missing = sorted(set(unit.symbol_ids) - functions.keys())
        if missing:
            raise OrchestrationError(f"函数簇包含未知 symbol: {missing}")
        project_root = Path(scan.project_root).expanduser().resolve()
        source_contexts = [
            self._source_context(project_root, functions[symbol])
            for symbol in unit.symbol_ids
        ]
        neighbors = self._neighbors(analysis, set(unit.symbol_ids), functions)
        context = SemanticClusterContext(
            project_root=str(project_root),
            unit=unit,
            functions=source_contexts,
            neighbors=neighbors,
            estimated_tokens=0,
        )
        token_count = estimate_tokens(context.model_dump_json())
        if token_count > token_budget:
            raise OrchestrationError(
                f"函数簇 {unit.unit_id} 上下文超出预算: "
                f"{token_count} > {token_budget}"
            )
        return context.model_copy(update={"estimated_tokens": token_count})

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
            lines = path.read_text(encoding="utf-8").splitlines()
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
