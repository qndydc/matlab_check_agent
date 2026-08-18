"""
Description: 定义 MATLAB 文件、函数、扫描结果和依赖分析结果模型。
References: Pydantic、domain.enums。
Referenced By: Parser、Analyzer、Visualizer、Workers 和 CLI。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .enums import MatlabObjectKind, ParseStatus


class DomainModel(BaseModel):
    """作用：提供严格领域模型基类；输入：模型字段；输出：校验后的模型；数据流：外部数据 -> Pydantic 校验 -> 领域对象。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class FunctionInfo(DomainModel):
    """作用：描述函数或脚本节点；输入：解析元数据；输出：标准函数记录；数据流：解析器 -> 分析器/JSON。"""

    name: str
    qualified_name: str
    file_path: str
    kind: MatlabObjectKind = MatlabObjectKind.FUNCTION
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    called_by: list[str] = Field(default_factory=list)
    line_count: int = 0
    start_line: int = 1
    end_line: int = 1


class DependencyEdge(DomainModel):
    """作用：描述已解析函数调用边；输入：调用者和被调用者标识；输出：稳定有向边；数据流：名称解析 -> 分析结果/可视化。"""

    source: str
    target: str


class MatlabFileInfo(DomainModel):
    """作用：描述单个 MATLAB 文件；输入：文件解析结果；输出：文件及函数集合；数据流：解析器 -> 扫描结果。"""

    path: str
    kind: MatlabObjectKind = MatlabObjectKind.UNKNOWN
    package: str | None = None
    functions: list[FunctionInfo] = Field(default_factory=list)
    line_count: int = 0
    parse_status: ParseStatus = ParseStatus.PARSED
    diagnostics: list[str] = Field(default_factory=list)


class MatlabFileManifest(DomainModel):
    """作用：记录 Worker-1 发现的 MATLAB 文件；输入：项目路径和过滤结果；输出：轻量文件清单；数据流：ScannerAgent -> ParserAgent fan-out。"""

    project_root: str
    files: list[str] = Field(default_factory=list)
    excluded_count: int = 0

    @property
    def file_count(self) -> int:
        """作用：统计待解析文件；输入：相对路径列表；输出：文件数；数据流：manifest.files -> Worker 指标。"""

        return len(self.files)


class ParseChunkResult(DomainModel):
    """作用：承载 Worker-2 单个解析分片；输入：分片索引和文件解析结果；输出：可聚合 artifact；数据流：文件批次 -> ParserAgent -> fan-in。"""

    project_root: str
    chunk_index: int
    files: list[MatlabFileInfo] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class ScanResult(DomainModel):
    """作用：汇总项目扫描结果；输入：文件记录与排除计数；输出：扫描快照；数据流：扫描器 -> 分析服务/CLI。"""

    project_root: str
    files: list[MatlabFileInfo] = Field(default_factory=list)
    excluded_count: int = 0

    @property
    def function_count(self) -> int:
        """作用：统计可分析对象；输入：当前文件集合；输出：对象总数；数据流：files -> 聚合计数 -> 报告。"""

        return sum(len(item.functions) for item in self.files)


class AnalysisResult(DomainModel):
    """作用：承载依赖分析结论；输入：调用图指标；输出：可序列化结果；数据流：分析器 -> CLI/JSON。"""

    project_root: str
    functions: list[FunctionInfo] = Field(default_factory=list)
    dependencies: list[DependencyEdge] = Field(default_factory=list)
    cycle_clusters: list[list[str]] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    core_functions: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    unresolved_calls: dict[str, list[str]] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)


def relative_posix(path: Path, root: Path) -> str:
    """作用：生成稳定相对路径；输入：文件路径与根目录；输出：POSIX 风格字符串；数据流：绝对路径 -> 相对化 -> 模型。"""

    return path.resolve().relative_to(root.resolve()).as_posix()
