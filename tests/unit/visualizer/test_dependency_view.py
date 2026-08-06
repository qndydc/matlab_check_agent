"""
Description: 验证终端依赖树、Graph JSON 契约和 Mermaid 导出。
References: dependency_view、Rich、domain.models。
Referenced By: pytest 测试发现。
"""

import json
from pathlib import Path

from rich.console import Console

from matlab_refactor_agent.capabilities.visualizer import (
    build_graph_document,
    render_dependency_tree,
    write_graph_json,
    write_mermaid,
)
from matlab_refactor_agent.domain.models import (
    AnalysisResult,
    DependencyEdge,
    FunctionInfo,
)


def _analysis_result() -> AnalysisResult:
    """作用：创建可视化测试结果；输入：无；输出：AnalysisResult；数据流：固定函数/边/指标 -> 导出器测试。"""

    return AnalysisResult(
        project_root="/project",
        functions=[
            FunctionInfo(
                name="main",
                qualified_name="main",
                file_path="main.m",
                kind="script",
            ),
            FunctionInfo(
                name="worker",
                qualified_name="worker",
                file_path="worker.m",
            ),
            FunctionInfo(
                name="loop",
                qualified_name="loop",
                file_path="loop.m",
            ),
            FunctionInfo(
                name="orphan",
                qualified_name="orphan",
                file_path="orphan.m",
            ),
        ],
        dependencies=[
            DependencyEdge(source="main", target="worker"),
            DependencyEdge(source="worker", target="loop"),
            DependencyEdge(source="loop", target="worker"),
        ],
        cycles=[["loop", "worker"]],
        orphans=["orphan"],
        core_functions=["worker"],
        entry_points=["main", "orphan"],
        unresolved_calls={"worker": ["disp"]},
    )


def test_graph_document_contains_web_flags_and_cycle_edges() -> None:
    """作用：验证 Web 图契约；输入：分析结果；输出：断言结果；数据流：分析模型 -> GraphDocument -> 节点标签/循环边。"""

    document = build_graph_document(_analysis_result())

    assert document.schema_version == "1.0"
    main = next(node for node in document.nodes if node.id == "main")
    assert main.is_entry_point is True
    worker = next(node for node in document.nodes if node.id == "worker")
    assert worker.is_core is True
    assert sum(edge.is_cycle for edge in document.edges) == 2


def test_visualization_exports_are_valid(tmp_path: Path) -> None:
    """作用：验证 JSON 和 Mermaid 导出；输入：分析结果与临时目录；输出：断言结果；数据流：图文档 -> 文件 -> 重新读取。"""

    graph_path = tmp_path / "graph.json"
    mermaid_path = tmp_path / "graph.mmd"

    write_graph_json(_analysis_result(), graph_path)
    write_mermaid(_analysis_result(), mermaid_path)

    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    mermaid = mermaid_path.read_text(encoding="utf-8")
    assert payload["directed"] is True
    assert len(payload["nodes"]) == 4
    assert "flowchart LR" in mermaid
    assert "linkStyle" in mermaid


def test_terminal_tree_marks_cycles_and_unresolved_calls() -> None:
    """作用：验证终端树语义标记；输入：分析结果；输出：断言结果；数据流：依赖边 -> Rich Tree -> 捕获文本。"""

    console = Console(record=True, color_system=None, width=120)

    render_dependency_tree(_analysis_result(), console)

    output = console.export_text()
    assert "main" in output
    assert "↻ cycle" in output
    assert "? disp" in output
    assert "[孤立]" in output
