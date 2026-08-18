"""
Description: 验证依赖图循环、孤立、入口、调用者和局部函数消歧。
References: DependencyAnalyzer、domain.models。
Referenced By: pytest 测试发现。
"""

from matlab_refactor_agent.workers.dependency_analysis import DependencyAnalyzer
from matlab_refactor_agent.domain.models import FunctionInfo, MatlabFileInfo, ScanResult


def _function(name: str, calls: list[str]) -> FunctionInfo:
    """作用：创建分析测试节点；输入：名称和调用列表；输出：FunctionInfo；数据流：测试参数 -> 领域模型。"""

    return FunctionInfo(
        name=name,
        qualified_name=name,
        file_path=f"{name}.m",
        calls=calls,
    )


def test_analyzer_finds_cycles_orphans_and_callers() -> None:
    """作用：验证主要图指标；输入：内存调用图；输出：断言结果；数据流：函数模型 -> 分析器 -> 环/孤立/反向调用。"""

    functions = [
        _function("entry", ["worker"]),
        _function("worker", ["cycleA"]),
        _function("cycleA", ["cycleB"]),
        _function("cycleB", ["cycleA"]),
        _function("orphan", ["disp"]),
    ]
    scan = ScanResult(
        project_root="/project",
        files=[MatlabFileInfo(path="all.m", functions=functions)],
    )

    result = DependencyAnalyzer().analyze(scan)

    assert result.cycle_clusters == [["cycleA", "cycleB"]]
    assert result.cycles == [["cycleA", "cycleB"]]
    assert result.core_functions == []
    assert result.orphans == ["orphan"]
    assert result.entry_points == ["entry", "orphan"]
    worker = next(item for item in result.functions if item.name == "worker")
    assert worker.called_by == ["entry"]
    assert result.unresolved_calls == {"orphan": ["disp"]}
    assert [(edge.source, edge.target) for edge in result.dependencies] == [
        ("cycleA", "cycleB"),
        ("cycleB", "cycleA"),
        ("entry", "worker"),
        ("worker", "cycleA"),
    ]


def test_configured_entry_point_overrides_detection() -> None:
    """作用：验证手工入口优先；输入：配置入口；输出：断言结果；数据流：配置名称 -> 解析 -> 入口列表。"""

    functions = [_function("first", ["second"]), _function("second", [])]
    scan = ScanResult(
        project_root="/project",
        files=[MatlabFileInfo(path="all.m", functions=functions)],
    )

    result = DependencyAnalyzer().analyze(scan, ["second"])

    assert result.entry_points == ["second"]


def test_local_call_resolves_within_callers_file() -> None:
    """作用：验证同名局部函数消歧；输入：跨文件同名节点；输出：断言结果；数据流：调用名 -> 文件作用域索引 -> 唯一边。"""

    caller = FunctionInfo(
        name="primary",
        qualified_name="primary",
        file_path="primary.m",
        calls=["helper"],
    )
    local = FunctionInfo(
        name="helper",
        qualified_name="primary>helper",
        file_path="primary.m",
    )
    same_name_elsewhere = FunctionInfo(
        name="helper",
        qualified_name="other>helper",
        file_path="other.m",
    )
    scan = ScanResult(
        project_root="/project",
        files=[MatlabFileInfo(path="all.m", functions=[caller, local, same_name_elsewhere])],
    )

    result = DependencyAnalyzer().analyze(scan)

    resolved_local = next(
        item for item in result.functions if item.qualified_name == "primary>helper"
    )
    assert resolved_local.called_by == ["primary"]
    assert "primary" not in result.unresolved_calls


def test_scc_cycle_clusters_limit_representative_cycles() -> None:
    functions = [
        _function("a", ["b", "c", "d"]),
        _function("b", ["a", "c", "d"]),
        _function("c", ["a", "b", "d"]),
        _function("d", ["a", "b", "c"]),
    ]
    scan = ScanResult(
        project_root="/project",
        files=[MatlabFileInfo(path="dense.m", functions=functions)],
    )

    result = DependencyAnalyzer().analyze(scan)

    assert result.cycle_clusters == [["a", "b", "c", "d"]]
    assert 1 <= len(result.cycles) <= 3
