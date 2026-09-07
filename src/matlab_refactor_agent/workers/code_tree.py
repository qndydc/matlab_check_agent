"""
Description: 将 MATLAB 分析结果确定性投影为项目、目录、文件和符号四级代码树。
References: domain.code_tree、domain.models、pathlib。
Referenced By: MainWorkflow、代码树单元测试和未来语言 Adapter。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from matlab_refactor_agent.domain.code_tree import CodeTreeDocument, CodeTreeNode
from matlab_refactor_agent.domain.models import AnalysisResult


class CodeTreeBuilder:
    """从项目级符号事实构建稳定排序的递归代码树。"""

    def build(
        self,
        analysis: AnalysisResult,
        *,
        summaries: dict[str, str] | None = None,
        file_summaries: dict[str, str] | None = None,
        project_summary: str | None = None,
    ) -> CodeTreeDocument:
        summary_lookup = summaries or {}
        files: dict[str, list] = {}
        for function in analysis.functions:
            path = function.file_path.replace("\\", "/").strip("/")
            files.setdefault(path, []).append(function)

        tree: dict[str, object] = {}
        for path in sorted(files):
            cursor = tree
            parts = PurePosixPath(path).parts
            for directory in parts[:-1]:
                cursor = cursor.setdefault(directory, {})  # type: ignore[assignment]
            cursor[parts[-1]] = files[path]

        root = CodeTreeNode(
            node_id="project:root",
            level="project",
            name=PurePosixPath(analysis.project_root.replace("\\", "/")).name
            or analysis.project_root,
            path="",
            summary=project_summary,
            children=self._children(tree, "", summary_lookup, file_summaries or {}),
        )
        root.child_count = len(root.children)
        return CodeTreeDocument(
            project_root=analysis.project_root,
            file_count=len(files),
            symbol_count=len(analysis.functions),
            root=root,
        )

    def _children(
        self,
        tree: dict[str, object],
        parent: str,
        summaries: dict[str, str],
        file_summaries: dict[str, str],
    ) -> list[CodeTreeNode]:
        children: list[CodeTreeNode] = []
        for name in sorted(tree):
            value = tree[name]
            path = f"{parent}/{name}".strip("/")
            if isinstance(value, dict):
                nested = self._children(value, path, summaries, file_summaries)
                children.append(
                    CodeTreeNode(
                        node_id=f"directory:{path}",
                        level="directory",
                        name=name,
                        path=path,
                        child_count=len(nested),
                        children=nested,
                    )
                )
                continue
            functions = sorted(value, key=lambda item: item.qualified_name)
            symbols = [
                CodeTreeNode(
                    node_id=f"symbol:{item.qualified_name}",
                    level="symbol",
                    name=item.name,
                    path=path,
                    symbol_id=item.qualified_name,
                    kind=str(item.kind),
                    summary=summaries.get(item.qualified_name),
                )
                for item in functions
            ]
            children.append(
                CodeTreeNode(
                    node_id=f"file:{path}",
                    level="file",
                    name=name,
                    path=path,
                    summary=file_summaries.get(path),
                    child_count=len(symbols),
                    children=symbols,
                )
            )
        return children
