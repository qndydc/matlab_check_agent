"""
Description: 定义语义工作单元、有限源码上下文和函数/文件/项目三级注解。
References: Pydantic、domain.models。
Referenced By: SemanticAnnotationAgent、上下文构建器、聚合器和语义流水线。
"""

from __future__ import annotations

from pydantic import Field

from .models import DomainModel


class SourceEvidence(DomainModel):
    """作用：定位注解证据；输入：源码位置与哈希；输出：可审计证据；数据流：源码上下文 -> LLM 注解。"""

    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    source_hash: str


class SemanticWorkUnit(DomainModel):
    """作用：定义一次 Agent 调用处理的函数簇；输入：图分簇和预算；输出：稳定工作单元；数据流：AnalysisResult -> fan-out。"""

    unit_id: str
    symbol_ids: list[str] = Field(min_length=1)
    estimated_tokens: int = Field(ge=0)


class SemanticWorkUnits(DomainModel):
    """作用：持久化全部函数簇；输入：工作单元列表；输出：可复用 artifact；数据流：分簇器 -> Orchestrator。"""

    project_root: str
    token_budget: int = Field(gt=0)
    units: list[SemanticWorkUnit] = Field(default_factory=list)


class NeighborSummary(DomainModel):
    """作用：描述簇外直接依赖而不加载其源码；输入：调用边和函数签名；输出：轻量邻居上下文。"""

    symbol_id: str
    relation: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)


class FunctionSourceContext(DomainModel):
    """作用：承载单函数受控源码；输入：FunctionInfo 与源码切片；输出：LLM 上下文片段。"""

    symbol_id: str
    file_path: str
    start_line: int
    end_line: int
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    source: str
    source_hash: str


class SemanticClusterContext(DomainModel):
    """作用：封装一次语义调用的全部有限上下文；输入：工作单元、源码和邻居；输出：结构化提示负载。"""

    project_root: str
    unit: SemanticWorkUnit
    functions: list[FunctionSourceContext]
    neighbors: list[NeighborSummary] = Field(default_factory=list)
    estimated_tokens: int = Field(ge=0)


class FunctionAnnotation(DomainModel):
    """作用：描述函数用途、数据流、风险和证据；输入：结构化 LLM 响应；输出：语义索引函数节点。"""

    symbol_id: str
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    summary: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    data_flow: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    evidence: list[SourceEvidence] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    is_algorithm_core: bool = False


class ClusterAnnotationResponse(DomainModel):
    """作用：约束单函数簇 LLM 响应；输入：模型 JSON；输出：已校验函数注解集合。"""

    unit_id: str
    annotations: list[FunctionAnnotation] = Field(min_length=1)


class FileAnnotation(DomainModel):
    """作用：聚合同一文件内函数语义；输入：函数注解；输出：文件级说明。"""

    file_path: str
    role: str
    function_symbols: list[str]
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class FileAnnotationDraft(DomainModel):
    """作用：约束文件级 LLM 只生成语义字段；输入：函数注解；输出：不含路径和符号集合的草稿。"""

    role: str
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ProjectAnnotation(DomainModel):
    """作用：汇总项目入口、主要数据流和风险；输入：分析结果与文件注解；输出：项目级说明。"""

    purpose: str
    usage: str = ""
    entry_points: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ProjectAnnotationDraft(DomainModel):
    """作用：约束项目级 LLM 只生成语义字段；输入：聚合上下文；输出：不含确定性结构字段的草稿。"""

    purpose: str
    usage: str = ""
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SemanticConflict(DomainModel):
    """作用：记录重复、缺失或低置信度语义结论；输入：聚合校验；输出：人工复核项。"""

    symbol_id: str
    reason: str


class SemanticConflicts(DomainModel):
    """作用：包装冲突列表以便持久化；输入：冲突项；输出：semantic-conflicts artifact。"""

    conflicts: list[SemanticConflict] = Field(default_factory=list)


class SemanticIndex(DomainModel):
    """作用：提供后续 Agent 共用的版本化语义事实源；输入：三级注解与冲突；输出：semantic-index artifact。"""

    schema_version: str = "1.0"
    project_root: str
    functions: list[FunctionAnnotation] = Field(default_factory=list)
    core_functions: list[str] = Field(default_factory=list)
    files: list[FileAnnotation] = Field(default_factory=list)
    project: ProjectAnnotation
    conflicts: list[SemanticConflict] = Field(default_factory=list)


class SemanticAnnotationOutcome(DomainModel):
    """作用：返回语义流水线结果和 artifact 引用；输入：语义索引；输出：应用层结果。"""

    job_id: str
    index: SemanticIndex
    artifacts: dict[str, str] = Field(default_factory=dict)
