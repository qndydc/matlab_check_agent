"""
Description: 验证确定性重构计划的路径循环和目标树生成。
References: PlanReconciler、domain.planning、domain.semantics。
Referenced By: pytest 测试发现和 P2 计划仲裁验收。
"""

from matlab_refactor_agent.domain.planning import RefactorPlanningCandidates
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.orchestration.plan_reconciler import PlanReconciler


def test_reconciler_detects_cyclic_file_moves() -> None:
    """作用：阻止相互交换路径的计划自动获批；输入：A/B 循环移动；输出：阻断冲突。"""

    functions = [
        {
            "symbol_id": name,
            "file_path": path,
            "start_line": 1,
            "end_line": 2,
            "summary": name,
            "evidence": [
                {
                    "file_path": path,
                    "start_line": 1,
                    "end_line": 2,
                    "source_hash": name,
                }
            ],
            "confidence": 0.9,
        }
        for name, path in [("a", "a.m"), ("b", "b.m")]
    ]
    semantic = SemanticIndex.model_validate(
        {
            "project_root": "project",
            "functions": functions,
            "project": {
                "purpose": "测试项目",
                "usage": "运行测试入口",
                "confidence": 0.9,
            },
        }
    )
    candidates = RefactorPlanningCandidates.model_validate(
        {
            "project_root": "project",
            "module_responsibility": {
                "project_root": "project",
                "modules": [
                    {
                        "module_id": "core",
                        "name": "Core",
                        "responsibility": "test",
                        "symbol_ids": ["a", "b"],
                        "rationale": ["test"],
                        "confidence": 0.9,
                    }
                ],
            },
            "naming_directory": {
                "project_root": "project",
                "changes": [
                    {
                        "symbol_id": "a",
                        "current_file_path": "a.m",
                        "proposed_name": "a",
                        "proposed_file_path": "b.m",
                        "reason": "swap",
                        "confidence": 0.9,
                    },
                    {
                        "symbol_id": "b",
                        "current_file_path": "b.m",
                        "proposed_name": "b",
                        "proposed_file_path": "a.m",
                        "reason": "swap",
                        "confidence": 0.9,
                    },
                ],
            },
        }
    )

    plan = PlanReconciler().reconcile(candidates, semantic)

    assert plan.target_tree == ["a.m", "b.m"]
    assert any(
        item.code == "cyclic_move" and item.blocking
        for item in plan.conflicts
    )
