"""
Description: 确定性合并模块职责与命名目录候选，生成可人工审查的重构计划。
References: domain.planning。
Referenced By: LangGraphWorkflow 和计划仲裁测试。
"""

from collections import defaultdict

from matlab_refactor_agent.domain.planning import (
    PlanConflict,
    RefactorOperation,
    RefactorPlan,
    RefactorPlanningCandidates,
)
from matlab_refactor_agent.domain.semantics import SemanticIndex


class PlanReconciler:
    """作用：执行无 LLM 的候选交叉校验；输入：候选集；输出：稳定 RefactorPlan。"""

    def reconcile(
        self,
        candidates: RefactorPlanningCandidates,
        semantic: SemanticIndex,
    ) -> RefactorPlan:
        modules = candidates.module_responsibility.modules
        naming = candidates.naming_directory
        symbol_to_module = {
            symbol: module.module_id
            for module in modules
            for symbol in module.symbol_ids
        }
        operations = [
            RefactorOperation(
                symbol_id=change.symbol_id,
                module_id=symbol_to_module.get(change.symbol_id, "unassigned"),
                source_path=change.current_file_path,
                target_path=change.proposed_file_path,
                proposed_name=change.proposed_name,
                reason=change.reason,
                confidence=change.confidence,
            )
            for change in naming.changes
        ]
        confidences = [module.confidence for module in modules] + [
            operation.confidence for operation in operations
        ]
        changed_targets = {
            operation.symbol_id: operation.target_path
            for operation in operations
        }
        desired_paths = {
            item.symbol_id: changed_targets.get(item.symbol_id, item.file_path)
            for item in semantic.functions
        }
        return RefactorPlan(
            project_root=candidates.project_root,
            modules=modules,
            symbol_to_module=symbol_to_module,
            operations=operations,
            unchanged_symbols=sorted(naming.unchanged_symbols),
            directory_rules=naming.directory_rules,
            target_tree=sorted(set(desired_paths.values())),
            conflicts=self._conflicts(
                operations,
                symbol_to_module,
                {item.symbol_id: item.file_path for item in semantic.functions},
                desired_paths,
            ),
            minimum_confidence=min(confidences, default=1.0),
        )

    def _conflicts(
        self,
        operations: list[RefactorOperation],
        symbol_to_module: dict[str, str],
        source_paths: dict[str, str],
        desired_paths: dict[str, str],
    ) -> list[PlanConflict]:
        conflicts: list[PlanConflict] = []
        for operation in operations:
            if operation.symbol_id not in symbol_to_module:
                conflicts.append(
                    PlanConflict(
                        code="unassigned_symbol",
                        message=f"符号未分配模块: {operation.symbol_id}",
                        symbol_ids=[operation.symbol_id],
                    )
                )
        targets_by_source: dict[str, set[str]] = defaultdict(set)
        sources_by_target: dict[str, set[str]] = defaultdict(set)
        for symbol, source in source_paths.items():
            target = desired_paths[symbol]
            targets_by_source[source].add(target)
            sources_by_target[target].add(source)
        for source, targets in targets_by_source.items():
            if len(targets) > 1:
                conflicts.append(
                    PlanConflict(
                        code="split_source_file",
                        message=f"同一源文件被规划到多个目标路径: {source}",
                        symbol_ids=sorted(
                            symbol
                            for symbol, path in source_paths.items()
                            if path == source
                        ),
                    )
                )
        for target, sources in sources_by_target.items():
            if len(sources) > 1:
                conflicts.append(
                    PlanConflict(
                        code="target_path_collision",
                        message=f"多个源文件映射到同一目标路径: {target}",
                        symbol_ids=sorted(
                            symbol
                            for symbol, path in desired_paths.items()
                            if path == target
                        ),
                    )
                )
        cycle = self._move_cycle(operations)
        if cycle:
            conflicts.append(
                PlanConflict(
                    code="cyclic_move",
                    message="路径移动形成循环: " + " -> ".join(cycle),
                    symbol_ids=sorted(
                        operation.symbol_id
                        for operation in operations
                        if operation.source_path in cycle
                    ),
                )
            )
        return conflicts

    @staticmethod
    def _move_cycle(operations: list[RefactorOperation]) -> list[str]:
        moves = {
            item.source_path: item.target_path
            for item in operations
            if item.source_path != item.target_path
        }
        for start in sorted(moves):
            order: list[str] = []
            positions: dict[str, int] = {}
            current = start
            while current in moves:
                if current in positions:
                    return order[positions[current] :] + [current]
                positions[current] = len(order)
                order.append(current)
                current = moves[current]
        return []
