"""
Description: 将 AnalysisResult 投影为终端依赖树、Web 图契约和 Mermaid 图。
References: Rich、Pydantic、domain.models。
Referenced By: interfaces.cli.main 和 visualizer 测试。
"""

from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from pathlib import Path

from pydantic import Field
from rich.console import Console
from rich.text import Text
from rich.tree import Tree

from matlab_refactor_agent.domain.models import AnalysisResult, DomainModel, FunctionInfo


class GraphNode(DomainModel):
    """作用：定义 Web 可视化节点契约；输入：函数元数据与分析标签；输出：节点记录；数据流：AnalysisResult -> Graph JSON -> Web 图组件。"""

    id: str
    label: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    is_entry_point: bool = False
    is_core: bool = False
    is_orphan: bool = False


class GraphEdge(DomainModel):
    """作用：定义 Web 可视化边契约；输入：依赖边及循环信息；输出：有向边记录；数据流：AnalysisResult -> Graph JSON/Mermaid。"""

    id: str
    source: str
    target: str
    is_cycle: bool = False


class GraphDocument(DomainModel):
    """作用：封装稳定图数据契约；输入：节点、边及项目指标；输出：版本化图文档；数据流：分析结果 -> JSON -> 后续 API/Web。"""

    schema_version: str = "1.0"
    directed: bool = True
    project_root: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)
    unresolved_calls: dict[str, list[str]] = Field(default_factory=dict)


def build_graph_document(result: AnalysisResult) -> GraphDocument:
    """作用：构造前端无关图文档；输入：AnalysisResult；输出：GraphDocument；数据流：函数/依赖/指标 -> 节点边投影 -> 版本化契约。"""

    entries = set(result.entry_points)
    cores = set(result.core_functions)
    orphans = set(result.orphans)
    cycle_edges = _cycle_edges(result.cycles)
    nodes = [
        GraphNode(
            id=function.qualified_name,
            label=function.name,
            qualified_name=function.qualified_name,
            kind=str(function.kind),
            file_path=function.file_path,
            start_line=function.start_line,
            end_line=function.end_line,
            inputs=function.inputs,
            outputs=function.outputs,
            is_entry_point=function.qualified_name in entries,
            is_core=function.qualified_name in cores,
            is_orphan=function.qualified_name in orphans,
        )
        for function in sorted(result.functions, key=lambda item: item.qualified_name)
    ]
    edges = [
        GraphEdge(
            id=f"e{index}",
            source=edge.source,
            target=edge.target,
            is_cycle=(edge.source, edge.target) in cycle_edges,
        )
        for index, edge in enumerate(result.dependencies)
    ]
    return GraphDocument(
        project_root=result.project_root,
        nodes=nodes,
        edges=edges,
        entry_points=result.entry_points,
        cycles=result.cycles,
        unresolved_calls=result.unresolved_calls,
    )


def render_dependency_tree(result: AnalysisResult, console: Console) -> None:
    """作用：在终端渲染函数调用树；输入：分析结果与控制台；输出：无；数据流：依赖边 -> 邻接表 -> Rich 树。"""

    functions = {item.qualified_name: item for item in result.functions}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in result.dependencies:
        adjacency[edge.source].append(edge.target)
    for targets in adjacency.values():
        targets.sort()

    tree = Tree(Text("MATLAB 函数调用 / 依赖树", style="bold cyan"))
    expanded: set[str] = set()
    ordered_roots = [*result.entry_points]
    ordered_roots.extend(sorted(set(functions) - set(ordered_roots)))
    for root in ordered_roots:
        if root in expanded:
            continue
        branch = tree.add(_node_label(root, functions.get(root), result))
        expanded.add(root)
        _populate_tree(branch, root, {root}, expanded, adjacency, functions, result)
    console.print(tree)
    console.print(
        "[dim]图例：入口=候选入口，核心=PageRank 核心节点，"
        "↻=循环回边，↗=已在其他分支展开，?=未解析外部调用。[/dim]"
    )


def write_graph_json(result: AnalysisResult, destination: Path) -> None:
    """作用：写出 Web-ready 图数据；输入：分析结果与目标路径；输出：UTF-8 JSON 文件；数据流：AnalysisResult -> GraphDocument -> 文件。"""

    document = build_graph_document(result)
    payload = json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2)
    _write_text(destination, payload + "\n")


def write_mermaid(result: AnalysisResult, destination: Path) -> None:
    """作用：写出 Mermaid 调用图；输入：分析结果与目标路径；输出：`.mmd` 文本；数据流：GraphDocument -> Mermaid 节点/边/样式 -> 文件。"""

    document = build_graph_document(result)
    node_ids = {node.id: f"n{index}" for index, node in enumerate(document.nodes)}
    lines = ["flowchart LR"]
    for node in document.nodes:
        label = escape(node.qualified_name, quote=True)
        lines.append(f'    {node_ids[node.id]}["{label}"]')
    cycle_link_indexes: list[int] = []
    for index, edge in enumerate(document.edges):
        lines.append(f"    {node_ids[edge.source]} --> {node_ids[edge.target]}")
        if edge.is_cycle:
            cycle_link_indexes.append(index)
    lines.extend(
        [
            "    classDef entry fill:#dbeafe,stroke:#2563eb,stroke-width:2px;",
            "    classDef core fill:#fef3c7,stroke:#d97706,stroke-width:2px;",
            "    classDef orphan fill:#f3f4f6,stroke:#6b7280,stroke-dasharray:4 3;",
        ]
    )
    _append_mermaid_classes(lines, document.nodes, node_ids)
    if cycle_link_indexes:
        indexes = ",".join(str(index) for index in cycle_link_indexes)
        lines.append(f"    linkStyle {indexes} stroke:#dc2626,stroke-width:2px;")
    _write_text(destination, "\n".join(lines) + "\n")


def _populate_tree(
    branch: Tree,
    source: str,
    path: set[str],
    expanded: set[str],
    adjacency: dict[str, list[str]],
    functions: dict[str, FunctionInfo],
    result: AnalysisResult,
) -> None:
    """作用：递归展开终端树分支；输入：当前节点及图索引；输出：修改 Rich Tree；数据流：邻接节点 -> 循环/共享判断 -> 子分支。"""

    for target in adjacency.get(source, []):
        label = _node_label(target, functions.get(target), result)
        if target in path:
            label.append("  ↻ cycle", style="bold red")
            branch.add(label)
            continue
        if target in expanded:
            label.append("  ↗ shared", style="dim")
            branch.add(label)
            continue
        child = branch.add(label)
        expanded.add(target)
        _populate_tree(
            child,
            target,
            {*path, target},
            expanded,
            adjacency,
            functions,
            result,
        )
    for call in result.unresolved_calls.get(source, []):
        branch.add(Text(f"? {call}  [未解析/外部]", style="dim yellow"))


def _node_label(
    node_id: str, function: FunctionInfo | None, result: AnalysisResult
) -> Text:
    """作用：构造带状态标记的节点标签；输入：节点及分析指标；输出：Rich Text；数据流：函数属性/标签集合 -> 样式文本。"""

    label = Text(node_id, style="bold")
    kind = str(function.kind) if function is not None else "unknown"
    label.append(f"  ({kind})", style="dim")
    if node_id in result.entry_points:
        label.append("  [入口]", style="bold blue")
    if node_id in result.core_functions:
        label.append("  [核心]", style="bold yellow")
    if node_id in result.orphans:
        label.append("  [孤立]", style="dim")
    return label


def _cycle_edges(cycles: list[list[str]]) -> set[tuple[str, str]]:
    """作用：展开循环中的有向边；输入：循环节点列表；输出：边集合；数据流：每个环 -> 相邻节点及闭合边。"""

    edges: set[tuple[str, str]] = set()
    for cycle in cycles:
        if not cycle:
            continue
        for index, source in enumerate(cycle):
            edges.add((source, cycle[(index + 1) % len(cycle)]))
    return edges


def _append_mermaid_classes(
    lines: list[str], nodes: list[GraphNode], node_ids: dict[str, str]
) -> None:
    """作用：追加 Mermaid 节点分类；输入：输出行、节点和 ID 映射；输出：修改后的行列表；数据流：分析标签 -> class 指令。"""

    for class_name, predicate in (
        ("entry", lambda node: node.is_entry_point),
        ("core", lambda node: node.is_core),
        ("orphan", lambda node: node.is_orphan),
    ):
        members = [node_ids[node.id] for node in nodes if predicate(node)]
        if members:
            lines.append(f"    class {','.join(members)} {class_name};")


def _write_text(destination: Path, content: str) -> None:
    """作用：统一写出可视化文本；输入：目标路径和内容；输出：文件副作用；数据流：内存文本 -> 父目录创建 -> UTF-8 文件。"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
