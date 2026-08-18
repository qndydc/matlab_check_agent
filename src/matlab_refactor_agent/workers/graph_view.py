"""
Description: 从完整 GraphDocument 构建适合大型项目渐进浏览的裁剪视图。
References: workers.graph_output、NetworkX、Pydantic。
Referenced By: interfaces.api.app 和图视图单元测试。
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import PurePosixPath
from typing import Literal

import networkx as nx
from pydantic import Field

from matlab_refactor_agent.domain.models import DomainModel
from matlab_refactor_agent.workers.graph_output import GraphDocument, GraphNode

GraphScope = Literal["project", "directory", "file", "function"]
GraphDirection = Literal["both", "callers", "callees"]


class GraphViewNode(DomainModel):
    """作用：统一描述目录、文件和函数三类渐进视图节点。"""

    id: str
    node_type: Literal["directory", "file", "function"]
    label: str
    qualified_name: str = ""
    file_path: str = ""
    kind: str = ""
    start_line: int = 0
    end_line: int = 0
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    is_entry_point: bool = False
    is_core: bool = False
    is_orphan: bool = False
    member_count: int = 0
    internal_call_count: int = 0
    cycle_count: int = 0
    has_children: bool = False
    semantic_summary: str | None = None


class GraphViewEdge(DomainModel):
    """作用：描述当前视图中的调用边或聚合依赖边。"""

    id: str
    source: str
    target: str
    weight: int = 1
    is_cycle: bool = False


class GraphBreadcrumb(DomainModel):
    """作用：描述返回上一级图视图所需的面包屑信息。"""

    label: str
    scope: GraphScope
    focus_id: str | None = None


class GraphViewDocument(DomainModel):
    """作用：封装只包含当前可见节点的版本化图视图。"""

    schema_version: str = "1.0"
    scope: GraphScope
    focus_id: str | None = None
    nodes: list[GraphViewNode] = Field(default_factory=list)
    edges: list[GraphViewEdge] = Field(default_factory=list)
    breadcrumbs: list[GraphBreadcrumb] = Field(default_factory=list)
    total_nodes: int = 0
    visible_nodes: int = 0
    truncated: bool = False


def build_graph_view(
    document: GraphDocument,
    *,
    scope: GraphScope = "project",
    focus_id: str | None = None,
    depth: int = 1,
    limit: int = 80,
    direction: GraphDirection = "both",
    query: str | None = None,
    file_summaries: dict[str, str] | None = None,
) -> GraphViewDocument:
    """作用：按层级、焦点和节点上限从完整调用图生成渐进视图。"""

    limit = min(max(limit, 1), 150)
    depth = min(max(depth, 0), 5)
    normalized_query = (query or "").strip().casefold()
    if normalized_query:
        match = next(
            (
                node.id
                for node in document.nodes
                if normalized_query in node.qualified_name.casefold()
                or normalized_query in node.file_path.casefold()
            ),
            None,
        )
        if match:
            scope, focus_id = "function", match

    if scope in {"project", "directory"}:
        return _build_group_view(
            document,
            scope=scope,
            focus_id=focus_id,
            limit=limit,
            file_summaries=file_summaries or {},
        )
    if scope == "file":
        if not focus_id:
            raise ValueError("file 视图必须提供 focus_id")
        return _build_file_view(document, focus_id, limit)
    if not focus_id:
        raise ValueError("function 视图必须提供 focus_id")
    return _build_function_view(document, focus_id, depth, limit, direction)


def _clean_path(value: str) -> str:
    """作用：统一使用正斜杠表示图中的相对文件路径。"""

    return value.replace("\\", "/").strip("/")


def _group_key(path: str, scope: GraphScope, focus_id: str | None) -> tuple[str, str, str] | None:
    """作用：计算函数文件在项目或目录视图中的直接子级分组。"""

    clean = _clean_path(path)
    parts = PurePosixPath(clean).parts
    prefix = tuple(PurePosixPath(_clean_path(focus_id or "")).parts)
    if scope == "directory":
        if not prefix or parts[: len(prefix)] != prefix or len(parts) <= len(prefix):
            return None
        relative = parts[len(prefix) :]
    else:
        relative = parts
    if len(relative) == 1:
        file_path = "/".join((*prefix, relative[0]))
        return f"file:{file_path}", "file", file_path
    directory = "/".join((*prefix, relative[0]))
    return f"dir:{directory}", "directory", directory


def _build_group_view(
    document: GraphDocument,
    *,
    scope: GraphScope,
    focus_id: str | None,
    limit: int,
    file_summaries: dict[str, str],
) -> GraphViewDocument:
    """作用：聚合目录或文件节点，并合并它们之间的调用边。"""

    node_groups: dict[str, tuple[str, str, str]] = {}
    members: dict[str, list[GraphNode]] = defaultdict(list)
    for node in document.nodes:
        group = _group_key(node.file_path, scope, focus_id)
        if group:
            node_groups[node.id] = group
            members[group[0]].append(node)

    cycle_nodes = {item for cluster in document.cycle_clusters for item in cluster}
    internal_calls: Counter[str] = Counter()
    aggregated: Counter[tuple[str, str]] = Counter()
    aggregated_cycle: set[tuple[str, str]] = set()
    for edge in document.edges:
        source = node_groups.get(edge.source)
        target = node_groups.get(edge.target)
        if not source or not target:
            continue
        if source[0] == target[0]:
            internal_calls[source[0]] += 1
            continue
        key = (source[0], target[0])
        aggregated[key] += 1
        if edge.is_cycle:
            aggregated_cycle.add(key)

    ordered_ids = sorted(members, key=lambda item: (node_groups[next(n.id for n in members[item])][1], item))
    visible_ids = ordered_ids[:limit]
    visible = set(visible_ids)
    view_nodes: list[GraphViewNode] = []
    for group_id in visible_ids:
        group_prefix, group_path = group_id.split(":", 1)
        group_type = "directory" if group_prefix == "dir" else "file"
        group_members = members[group_id]
        summary = file_summaries.get(group_path) if group_type == "file" else None
        view_nodes.append(
            GraphViewNode(
                id=group_id,
                node_type=group_type,
                label=PurePosixPath(group_path).name,
                file_path=group_path,
                member_count=len(group_members),
                internal_call_count=internal_calls[group_id],
                cycle_count=sum(node.id in cycle_nodes for node in group_members),
                has_children=True,
                semantic_summary=summary,
            )
        )
    edges = [
        GraphViewEdge(
            id=f"aggregate:{source}->{target}",
            source=source,
            target=target,
            weight=weight,
            is_cycle=(source, target) in aggregated_cycle,
        )
        for (source, target), weight in sorted(aggregated.items())
        if source in visible and target in visible
    ]
    breadcrumbs = [GraphBreadcrumb(label="项目", scope="project")]
    if scope == "directory" and focus_id:
        current = ""
        for part in PurePosixPath(_clean_path(focus_id)).parts:
            current = f"{current}/{part}".strip("/")
            breadcrumbs.append(GraphBreadcrumb(label=part, scope="directory", focus_id=current))
    return GraphViewDocument(
        scope=scope,
        focus_id=focus_id,
        nodes=view_nodes,
        edges=edges,
        breadcrumbs=breadcrumbs,
        total_nodes=len(ordered_ids),
        visible_nodes=len(view_nodes),
        truncated=len(ordered_ids) > limit,
    )


def _topological_positions(document: GraphDocument) -> dict[str, int]:
    """作用：将 SCC 压缩为 DAG 后计算稳定拓扑位置，使相关函数相邻。"""

    graph = nx.DiGraph()
    graph.add_nodes_from(node.id for node in document.nodes)
    graph.add_edges_from((edge.source, edge.target) for edge in document.edges)
    components = [tuple(sorted(items)) for items in nx.strongly_connected_components(graph)]
    owner = {node: index for index, items in enumerate(components) for node in items}
    condensed = nx.DiGraph()
    condensed.add_nodes_from(range(len(components)))
    for edge in document.edges:
        source, target = owner[edge.source], owner[edge.target]
        if source != target:
            condensed.add_edge(source, target)
    ordered = list(nx.lexicographical_topological_sort(condensed, key=lambda idx: components[idx]))
    return {node: position for position, component in enumerate(ordered) for node in components[component]}


def _ranked_file_nodes(document: GraphDocument, file_path: str) -> list[GraphNode]:
    """作用：按重要标签、SCC 拓扑位置、度数和名称稳定排列文件内函数。"""

    clean = _clean_path(file_path)
    candidates = [node for node in document.nodes if _clean_path(node.file_path) == clean]
    degree: Counter[str] = Counter()
    for edge in document.edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    cycle_nodes = {item for cluster in document.cycle_clusters for item in cluster}
    positions = _topological_positions(document)
    return sorted(
        candidates,
        key=lambda node: (
            0 if node.is_entry_point or node.is_core else 1 if node.id in cycle_nodes else 2,
            positions.get(node.id, len(positions)),
            -degree[node.id],
            node.qualified_name,
        ),
    )


def _function_node(node: GraphNode) -> GraphViewNode:
    """作用：把完整图函数节点复制为视图函数节点。"""

    return GraphViewNode(node_type="function", **node.model_dump())


def _function_edges(document: GraphDocument, visible: set[str]) -> list[GraphViewEdge]:
    """作用：筛选两个端点都可见的函数调用边。"""

    return [
        GraphViewEdge(
            id=edge.id,
            source=edge.source,
            target=edge.target,
            is_cycle=edge.is_cycle,
        )
        for edge in document.edges
        if edge.source in visible and edge.target in visible
    ]


def _file_breadcrumbs(file_path: str) -> list[GraphBreadcrumb]:
    """作用：为文件和函数视图构造项目、目录、文件面包屑。"""

    parts = PurePosixPath(_clean_path(file_path)).parts
    crumbs = [GraphBreadcrumb(label="项目", scope="project")]
    current = ""
    for part in parts[:-1]:
        current = f"{current}/{part}".strip("/")
        crumbs.append(GraphBreadcrumb(label=part, scope="directory", focus_id=current))
    crumbs.append(GraphBreadcrumb(label=parts[-1], scope="file", focus_id=_clean_path(file_path)))
    return crumbs


def _build_file_view(document: GraphDocument, file_path: str, limit: int) -> GraphViewDocument:
    """作用：选择文件内重要函数，并补入其直接调用邻居直到达到上限。"""

    ranked = _ranked_file_nodes(document, file_path)
    same_file = {node.id for node in ranked}
    cycle_nodes = {item for cluster in document.cycle_clusters for item in cluster}
    priority = [
        node.id
        for node in ranked
        if node.is_entry_point or node.is_core or node.id in cycle_nodes
    ]
    if not priority and ranked:
        priority = [ranked[0].id]
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in document.edges:
        if edge.source in same_file and edge.target in same_file:
            neighbors[edge.source].add(edge.target)
            neighbors[edge.target].add(edge.source)
    ordered_ids: list[str] = []
    for node_id in [*priority, *(item for root in priority for item in sorted(neighbors[root])), *(node.id for node in ranked)]:
        if node_id not in ordered_ids:
            ordered_ids.append(node_id)
    visible_ids = ordered_ids[:limit]
    visible = set(visible_ids)
    lookup = {node.id: node for node in ranked}
    return GraphViewDocument(
        scope="file",
        focus_id=_clean_path(file_path),
        nodes=[_function_node(lookup[node_id]) for node_id in visible_ids],
        edges=_function_edges(document, visible),
        breadcrumbs=_file_breadcrumbs(file_path),
        total_nodes=len(ranked),
        visible_nodes=len(visible_ids),
        truncated=len(ranked) > limit,
    )


def _build_function_view(
    document: GraphDocument,
    focus_id: str,
    depth: int,
    limit: int,
    direction: GraphDirection,
) -> GraphViewDocument:
    """作用：使用限深 BFS 构建指定函数的调用者、被调用者或双向邻域。"""

    lookup = {node.id: node for node in document.nodes}
    if focus_id not in lookup:
        raise ValueError(f"函数不存在: {focus_id}")
    callers: dict[str, set[str]] = defaultdict(set)
    callees: dict[str, set[str]] = defaultdict(set)
    for edge in document.edges:
        callees[edge.source].add(edge.target)
        callers[edge.target].add(edge.source)
    queue = deque([(focus_id, 0)])
    ordered = [focus_id]
    seen = {focus_id}
    while queue and len(ordered) < limit:
        current, level = queue.popleft()
        if level >= depth:
            continue
        adjacent: set[str] = set()
        if direction in {"both", "callers"}:
            adjacent.update(callers[current])
        if direction in {"both", "callees"}:
            adjacent.update(callees[current])
        for item in sorted(adjacent):
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
            queue.append((item, level + 1))
            if len(ordered) >= limit:
                break
    reachable_total = _reachable_count(focus_id, callers, callees, depth, direction)
    visible = set(ordered)
    crumbs = _file_breadcrumbs(lookup[focus_id].file_path)
    crumbs.append(GraphBreadcrumb(label=lookup[focus_id].label, scope="function", focus_id=focus_id))
    return GraphViewDocument(
        scope="function",
        focus_id=focus_id,
        nodes=[_function_node(lookup[node_id]) for node_id in ordered],
        edges=_function_edges(document, visible),
        breadcrumbs=crumbs,
        total_nodes=reachable_total,
        visible_nodes=len(ordered),
        truncated=reachable_total > len(ordered),
    )


def _reachable_count(
    focus_id: str,
    callers: dict[str, set[str]],
    callees: dict[str, set[str]],
    depth: int,
    direction: GraphDirection,
) -> int:
    """作用：统计指定深度内真实可达节点数，用于判断视图是否截断。"""

    seen = {focus_id}
    frontier = {focus_id}
    for _ in range(depth):
        adjacent: set[str] = set()
        for item in frontier:
            if direction in {"both", "callers"}:
                adjacent.update(callers[item])
            if direction in {"both", "callees"}:
                adjacent.update(callees[item])
        frontier = adjacent - seen
        seen.update(frontier)
    return len(seen)
