"""
Description: 在 WCC Reason 后按 SCC DAG 生成有界 ActChunk，并持久化 chunk 级断点。
References: domain.migration、networkx、token_budget、ArtifactStore。
Referenced By: MigrationRuntime。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from threading import RLock

import networkx as nx

from matlab_refactor_agent.domain.migration import (
    ActChunk,
    ActChunkPlan,
    ActContext,
    CallChainContext,
    ConversionStratagem,
    MigrationWorkUnit,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm.token_budget import estimate_tokens
from matlab_refactor_agent.orchestration.call_lifecycle import sanitize_summary

_CHUNK_PLAN_LOCK = RLock()


@dataclass(frozen=True)
class ActChunkLimits:
    input_tokens: int = 20_000
    output_tokens: int = 8_000
    functions: int = 6
    modules: int = 4
    interface_reserve_tokens: int = 512


class ActChunkPlanner:
    """按反向拓扑和稳定优先级执行 greedy packing。"""

    def __init__(self, limits: ActChunkLimits | None = None) -> None:
        self.limits = limits or ActChunkLimits()

    def build(
        self, context: CallChainContext, stratagem: ConversionStratagem
    ) -> ActChunkPlan:
        sccs = context.unit.sccs or [[symbol] for symbol in context.unit.symbol_ids]
        dag, owner = self._scc_dag(sccs, context.internal_dependencies)
        order = list(reversed(list(nx.lexicographical_topological_sort(
            dag, key=lambda index: tuple(sccs[index])
        ))))
        remaining = list(order)
        groups: list[list[int]] = []
        while remaining:
            group = [remaining.pop(0)]
            while remaining:
                ranked = sorted(
                    remaining,
                    key=lambda index: self._priority(
                        index, group, sccs, dag, order, stratagem
                    ),
                    reverse=True,
                )
                selected = next(
                    (
                        index for index in ranked
                        if self._fits(
                            self._symbols([*group, index], sccs),
                            context,
                            stratagem,
                        )
                        and self._safe_merge(
                            index, group, groups, remaining, dag
                        )
                    ),
                    None,
                )
                if selected is None:
                    break
                group.append(selected)
                remaining.remove(selected)
            groups.append(group)

        group_owner = {
            scc_index: group_index
            for group_index, group in enumerate(groups)
            for scc_index in group
        }
        chunk_ids = [
            context.unit.unit_id
            if len(groups) == 1
            else f"{context.unit.unit_id}-chunk-{index + 1:03d}"
            for index in range(len(groups))
        ]
        chunks: list[ActChunk] = []
        for group_index, group in enumerate(groups):
            symbols = self._symbols(group, sccs)
            metrics = self.metrics(symbols, context, stratagem)
            dependencies = sorted({
                chunk_ids[group_owner[target]]
                for source, target in dag.edges
                if group_owner[source] == group_index
                and group_owner[target] != group_index
            })
            chunks.append(ActChunk(
                chunk_id=chunk_ids[group_index],
                wcc_id=context.unit.unit_id,
                symbol_ids=symbols,
                sccs=[list(sccs[index]) for index in group],
                depends_on_chunks=dependencies,
                module_paths=self._modules(symbols, stratagem),
                input_tokens=metrics[0],
                output_tokens=metrics[1],
                function_count=len(symbols),
                module_count=max(1, len(self._modules(symbols, stratagem))),
                oversized_atomic=(
                    len(group) == 1
                    and not self._within_limits(*metrics, len(symbols),
                                                len(self._modules(symbols, stratagem)))
                ),
            ))
        return ActChunkPlan(wcc_id=context.unit.unit_id, chunks=chunks)

    @staticmethod
    def _safe_merge(
        candidate: int,
        group: list[int],
        completed_groups: list[list[int]],
        remaining: list[int],
        dag: nx.DiGraph,
    ) -> bool:
        """禁止非连续 SCC 收缩后在 chunk DAG 中制造依赖环。"""

        components = [
            *completed_groups,
            [*group, candidate],
            *[[index] for index in remaining if index != candidate],
        ]
        owner = {
            node: component_index
            for component_index, component in enumerate(components)
            for node in component
        }
        contracted = nx.DiGraph()
        contracted.add_nodes_from(range(len(components)))
        contracted.add_edges_from(
            (owner[source], owner[target])
            for source, target in dag.edges
            if owner[source] != owner[target]
        )
        return nx.is_directed_acyclic_graph(contracted)

    def split(
        self,
        plan: ActChunkPlan,
        chunk_id: str,
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> ActChunkPlan:
        parent = next(item for item in plan.chunks if item.chunk_id == chunk_id)
        if len(parent.symbol_ids) < 2:
            return plan
        midpoint = (len(parent.symbol_ids) + 1) // 2
        halves = [parent.symbol_ids[:midpoint], parent.symbol_ids[midpoint:]]
        child_ids = [f"{parent.chunk_id}-a", f"{parent.chunk_id}-b"]
        edges = self._symbol_edges(context.internal_dependencies)
        cross_ab = any(source in halves[0] and target in halves[1]
                       for source, target in edges)
        cross_ba = any(source in halves[1] and target in halves[0]
                       for source, target in edges)
        children: list[ActChunk] = []
        for index, symbols in enumerate(halves):
            other = 1 - index
            depends = set(parent.depends_on_chunks)
            # caller -> callee；互相递归时两个子块同波执行，最终 WCC 再统一校验。
            if not (cross_ab and cross_ba):
                if (index == 0 and cross_ab) or (index == 1 and cross_ba):
                    depends.add(child_ids[other])
            metrics = self.metrics(symbols, context, stratagem)
            modules = self._modules(symbols, stratagem)
            children.append(ActChunk(
                chunk_id=child_ids[index],
                wcc_id=parent.wcc_id,
                symbol_ids=symbols,
                sccs=[
                    sorted(set(scc) & set(symbols))
                    for scc in parent.sccs if set(scc) & set(symbols)
                ],
                depends_on_chunks=sorted(depends),
                module_paths=modules,
                input_tokens=metrics[0],
                output_tokens=metrics[1],
                function_count=len(symbols),
                module_count=max(1, len(modules)),
                parent_chunk_id=parent.chunk_id,
                split_depth=parent.split_depth + 1,
                oversized_atomic=(
                    len(symbols) == 1
                    and not self._within_limits(*metrics, 1, len(modules))
                ),
            ))
        updated: list[ActChunk] = []
        for item in plan.chunks:
            if item.chunk_id == parent.chunk_id:
                updated.append(item.model_copy(update={"status": "superseded"}))
                updated.extend(children)
                continue
            dependencies = [
                dependency
                for value in item.depends_on_chunks
                for dependency in (
                    child_ids if value == parent.chunk_id else [value]
                )
            ]
            updated.append(item.model_copy(update={
                "depends_on_chunks": list(dict.fromkeys(dependencies))
            }))
        return plan.model_copy(update={"chunks": updated})

    def context_for(
        self,
        chunk: ActChunk,
        context: CallChainContext,
        stratagem: ConversionStratagem,
        dependency_interfaces: list,
    ) -> ActContext:
        symbols = set(chunk.symbol_ids)
        unit = MigrationWorkUnit(
            unit_id=chunk.chunk_id,
            symbol_ids=chunk.symbol_ids,
            entry_symbols=[
                item for item in context.unit.entry_symbols if item in symbols
            ],
            sccs=chunk.sccs,
        )
        strategy = stratagem.model_copy(update={
            "unit_id": chunk.chunk_id,
            "module_plan": {
                symbol: stratagem.module_plan[symbol]
                for symbol in chunk.symbol_ids
                if symbol in stratagem.module_plan
            },
        })
        return ActContext(
            unit=unit,
            stratagem=strategy,
            functions=[
                item for item in context.functions if item.symbol_id in symbols
            ],
            dependency_interfaces=dependency_interfaces,
        )

    def metrics(
        self,
        symbols: list[str],
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> tuple[int, int]:
        symbol_set = set(symbols)
        functions = [
            item for item in context.functions if item.symbol_id in symbol_set
        ]
        unit = MigrationWorkUnit(unit_id="estimate", symbol_ids=symbols)
        strategy = stratagem.model_copy(update={
            "unit_id": "estimate",
            "module_plan": {
                symbol: stratagem.module_plan[symbol]
                for symbol in symbols if symbol in stratagem.module_plan
            },
        })
        payload = ActContext(
            unit=unit, stratagem=strategy, functions=functions
        ).model_dump_json(indent=2)
        input_tokens = estimate_tokens(payload) + self.limits.interface_reserve_tokens
        source_tokens = sum(estimate_tokens(item.source) for item in functions)
        output_tokens = math.ceil(source_tokens * 1.25) + 128 * max(
            1, len(self._modules(symbols, stratagem))
        )
        return input_tokens, output_tokens

    def _fits(
        self,
        symbols: list[str],
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> bool:
        metrics = self.metrics(symbols, context, stratagem)
        return self._within_limits(
            *metrics, len(symbols), len(self._modules(symbols, stratagem))
        )

    def _within_limits(
        self, input_tokens: int, output_tokens: int,
        functions: int, modules: int,
    ) -> bool:
        return (
            input_tokens <= self.limits.input_tokens
            and output_tokens <= self.limits.output_tokens
            and functions <= self.limits.functions
            and max(1, modules) <= self.limits.modules
        )

    @staticmethod
    def _modules(
        symbols: list[str], stratagem: ConversionStratagem
    ) -> list[str]:
        return sorted({
            stratagem.module_plan.get(symbol, symbol)
            for symbol in symbols
        })

    @staticmethod
    def _symbols(indices: list[int], sccs: list[list[str]]) -> list[str]:
        return [symbol for index in indices for symbol in sorted(sccs[index])]

    @staticmethod
    def _symbol_edges(dependencies: list[str]) -> list[tuple[str, str]]:
        edges: list[tuple[str, str]] = []
        for dependency in dependencies:
            source, separator, target = dependency.partition(" -> ")
            if separator:
                edges.append((source, target))
        return edges

    def _scc_dag(
        self, sccs: list[list[str]], dependencies: list[str]
    ) -> tuple[nx.DiGraph, dict[str, int]]:
        owner = {
            symbol: index for index, scc in enumerate(sccs) for symbol in scc
        }
        dag = nx.DiGraph()
        dag.add_nodes_from(range(len(sccs)))
        for source, target in self._symbol_edges(dependencies):
            if source in owner and target in owner and owner[source] != owner[target]:
                dag.add_edge(owner[source], owner[target])
        return dag, owner

    @staticmethod
    def _priority(
        candidate: int,
        group: list[int],
        sccs: list[list[str]],
        dag: nx.DiGraph,
        order: list[int],
        stratagem: ConversionStratagem,
    ) -> tuple[int, int, int, tuple[str, ...]]:
        candidate_modules = {
            stratagem.module_plan.get(symbol, symbol)
            for symbol in sccs[candidate]
        }
        group_modules = {
            stratagem.module_plan.get(symbol, symbol)
            for index in group for symbol in sccs[index]
        }
        same_module = len(candidate_modules & group_modules)
        dependency_edges = sum(
            int(dag.has_edge(candidate, index))
            + int(dag.has_edge(index, candidate))
            for index in group
        )
        proximity = -min(abs(order.index(candidate) - order.index(index))
                         for index in group)
        return same_module, dependency_edges, proximity, tuple(sccs[candidate])


class ActChunkStore:
    """对同一 WCC 的 chunk 计划执行并发安全的读改写。"""

    def __init__(self, artifacts: ArtifactStore, job_id: str, wcc_id: str) -> None:
        self.artifacts = artifacts
        self.job_id = job_id
        self.wcc_id = wcc_id
        self.name = f"act-chunks-{wcc_id}.json"
        self.path = artifacts.root / job_id / self.name

    def load(self) -> ActChunkPlan:
        return self.artifacts.read_model(str(self.path), ActChunkPlan)

    def initialize(self, plan: ActChunkPlan) -> str:
        with _CHUNK_PLAN_LOCK:
            if self.path.is_file():
                return str(self.path)
            return self.artifacts.write_model(self.job_id, self.name, plan)

    def save(self, plan: ActChunkPlan) -> str:
        with _CHUNK_PLAN_LOCK:
            return self.artifacts.write_model(self.job_id, self.name, plan)

    def update(self, chunk_id: str, **changes: object) -> ActChunk:
        with _CHUNK_PLAN_LOCK:
            plan = self.load()
            for index, chunk in enumerate(plan.chunks):
                if chunk.chunk_id == chunk_id:
                    plan.chunks[index] = chunk.model_copy(update=changes)
                    self.artifacts.write_model(self.job_id, self.name, plan)
                    return plan.chunks[index]
        raise KeyError(chunk_id)

    def split(
        self,
        planner: ActChunkPlanner,
        chunk_id: str,
        context: CallChainContext,
        stratagem: ConversionStratagem,
    ) -> ActChunkPlan:
        with _CHUNK_PLAN_LOCK:
            updated = planner.split(self.load(), chunk_id, context, stratagem)
            self.artifacts.write_model(self.job_id, self.name, updated)
            return updated

    @staticmethod
    def ready(plan: ActChunkPlan) -> list[ActChunk]:
        frozen = {item.chunk_id for item in plan.chunks if item.status == "frozen"}
        return [
            item for item in plan.chunks
            if item.status == "pending"
            and set(item.depends_on_chunks) <= frozen
        ]

    @staticmethod
    def leaves(plan: ActChunkPlan) -> list[ActChunk]:
        return [item for item in plan.chunks if item.status != "superseded"]

    def fail(self, chunk_id: str, exc: BaseException) -> ActChunk:
        return self.update(
            chunk_id,
            status="failed",
            last_error=sanitize_summary(exc, limit=300),
        )


__all__ = ["ActChunkLimits", "ActChunkPlanner", "ActChunkStore"]
