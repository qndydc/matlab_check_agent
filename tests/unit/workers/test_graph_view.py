"""
Description: 验证大型项目渐进图视图的聚合、裁剪和函数邻域行为。
References: workers.graph_view 和 workers.graph_output。
Referenced By: pytest 回归测试套件。
"""

from matlab_refactor_agent.workers.graph_output import GraphDocument, GraphEdge, GraphNode
from matlab_refactor_agent.workers.graph_view import build_graph_view


def _node(node_id: str, file_path: str, **flags: bool) -> GraphNode:
    """作用：创建只包含图视图测试所需字段的函数节点。"""

    return GraphNode(
        id=node_id,
        label=node_id,
        qualified_name=node_id,
        kind="function",
        file_path=file_path,
        start_line=1,
        end_line=2,
        **flags,
    )


def _document() -> GraphDocument:
    """作用：构造含目录、跨文件聚合边和循环的确定性测试图。"""

    return GraphDocument(
        project_root="demo",
        nodes=[
            _node("main", "main.m", is_entry_point=True),
            _node("a", "src/large.m", is_core=True),
            _node("b", "src/large.m"),
            _node("c", "src/large.m"),
            _node("helper", "lib/helper.m"),
        ],
        edges=[
            GraphEdge(id="e0", source="main", target="a"),
            GraphEdge(id="e1", source="main", target="b"),
            GraphEdge(id="e2", source="a", target="b", is_cycle=True),
            GraphEdge(id="e3", source="b", target="a", is_cycle=True),
            GraphEdge(id="e4", source="b", target="helper"),
            GraphEdge(id="e5", source="c", target="helper"),
        ],
        cycle_clusters=[["a", "b"]],
    )


def test_project_view_aggregates_dependencies_and_counts() -> None:
    """作用：验证项目总览折叠目录并合并重复的跨组调用。"""

    view = build_graph_view(_document(), scope="project")
    assert {node.id for node in view.nodes} == {"file:main.m", "dir:src", "dir:lib"}
    assert next(node for node in view.nodes if node.id == "dir:src").member_count == 3
    assert next(node for node in view.nodes if node.id == "dir:src").cycle_count == 2
    edge = next(item for item in view.edges if item.source == "file:main.m")
    assert edge.target == "dir:src"
    assert edge.weight == 2


def test_file_view_prioritizes_core_and_obeys_limit() -> None:
    """作用：验证大文件视图优先保留核心函数且绝不超过节点上限。"""

    view = build_graph_view(_document(), scope="file", focus_id="src/large.m", limit=2)
    assert view.nodes[0].id == "a"
    assert view.visible_nodes == 2
    assert view.total_nodes == 3
    assert view.truncated is True


def test_function_view_respects_direction_and_depth() -> None:
    """作用：验证函数焦点视图能分别查询调用者和被调用者。"""

    callers = build_graph_view(
        _document(), scope="function", focus_id="a", direction="callers", depth=1
    )
    assert {node.id for node in callers.nodes} == {"main", "a", "b"}
    callees = build_graph_view(
        _document(), scope="function", focus_id="main", direction="callees", depth=1
    )
    assert {node.id for node in callees.nodes} == {"main", "a", "b"}


def test_query_jumps_to_matching_function() -> None:
    """作用：验证查询参数可把任意层级请求转换为函数聚焦视图。"""

    view = build_graph_view(_document(), scope="project", query="helper")
    assert view.scope == "function"
    assert view.focus_id == "helper"


def test_large_single_file_never_exceeds_default_limit() -> None:
    """作用：模拟 247 函数文件，验证初始画布严格限制为 80 个节点。"""

    nodes = [_node(f"function_{index:03d}", "src/huge.m") for index in range(247)]
    edges = [
        GraphEdge(id=f"e{index}", source=nodes[index].id, target=nodes[index + 1].id)
        for index in range(len(nodes) - 1)
    ]
    view = build_graph_view(
        GraphDocument(project_root="huge", nodes=nodes, edges=edges),
        scope="file",
        focus_id="src/huge.m",
    )
    assert view.visible_nodes == 80
    assert view.total_nodes == 247
    assert view.truncated is True
