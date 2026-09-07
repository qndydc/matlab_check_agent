"""
Description: 定义可分级浏览的项目、目录、文件和符号代码树契约。
References: domain.models、Pydantic。
Referenced By: CodeTreeBuilder、AnalysisWorkflow 和后续多语言适配器。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import DomainModel

CodeTreeLevel = Literal["project", "directory", "file", "symbol"]


class CodeTreeNode(DomainModel):
    """一个稳定代码树节点，可递归包含下一级目录、文件或符号。"""

    node_id: str
    level: CodeTreeLevel
    name: str
    path: str = ""
    symbol_id: str | None = None
    kind: str | None = None
    summary: str | None = None
    child_count: int = Field(default=0, ge=0)
    children: list["CodeTreeNode"] = Field(default_factory=list)


class CodeTreeDocument(DomainModel):
    """完整分级代码树及其来源分析版本。"""

    schema_version: str = "1.0"
    project_root: str
    language: str = "matlab"
    file_count: int = Field(ge=0)
    symbol_count: int = Field(ge=0)
    root: CodeTreeNode

