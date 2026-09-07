"""
Description: 验证 MATLAB 到 Python 迁移计划的 SCC 原子性、依赖顺序和路径策略。
References: MigrationPlanBuilder、domain.models。
Referenced By: pytest 测试发现。
"""

from matlab_refactor_agent.migration import MigrationPlanBuilder
from matlab_refactor_agent.domain.models import AnalysisResult, DependencyEdge, FunctionInfo


def test_migration_plan_uses_wcc_context_and_keeps_scc_atoms() -> None:
    analysis = AnalysisResult(
        project_root="D:/Project Demo",
        functions=[
            FunctionInfo(name="main", qualified_name="main", file_path="main.m"),
            FunctionInfo(name="a", qualified_name="a", file_path="core/a.m"),
            FunctionInfo(name="b", qualified_name="b", file_path="core/b.m"),
        ],
        dependencies=[
            DependencyEdge(source="main", target="a"),
            DependencyEdge(source="a", target="b"),
            DependencyEdge(source="b", target="a"),
        ],
    )

    plan = MigrationPlanBuilder().build(analysis)

    assert plan.architecture.package_name == "project_demo"
    assert len(plan.units) == 1
    assert plan.units[0].symbol_ids == ["a", "b", "main"]
    assert plan.units[0].entry_symbols == ["main"]
    assert sorted(plan.units[0].sccs) == [["a", "b"], ["main"]]
    assert plan.symbol_to_module["a"] == "project_demo.core.a"
