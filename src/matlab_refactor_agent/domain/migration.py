"""
Description: 定义 MATLAB 到 Python 规划、工作单元、生成结果和工作流 Outcome。
References: domain.models、domain.semantics、Pydantic。
Referenced By: matlab_to_python Agent、MigrationPlanBuilder 和 AnalysisWorkflow。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import DomainModel


class PythonArchitecture(DomainModel):
    """一次迁移任务共享的 Python 工程约束。"""

    package_name: str
    source_directory: str
    test_directory: str = "tests"
    dependencies: list[str] = Field(default_factory=lambda: ["numpy", "scipy"])
    rules: list[str] = Field(default_factory=list)


class MigrationWorkUnit(DomainModel):
    """以 WCC 为主要上下文、保留 SCC 原子边界的转换批次。"""

    unit_id: str
    symbol_ids: list[str] = Field(min_length=1)
    entry_symbols: list[str] = Field(default_factory=list)
    sccs: list[list[str]] = Field(default_factory=list)
    depends_on_units: list[str] = Field(default_factory=list)
    status: Literal["pending", "ready", "generated", "verified", "frozen", "failed", "manual_review"] = "pending"


class MigrationUnitState(DomainModel):
    """可持久化的 WCC 迁移状态；详细产物只保存引用。"""

    unit_id: str
    depends_on_units: list[str] = Field(default_factory=list)
    status: Literal[
        "pending", "ready", "generated", "verified", "frozen", "failed",
        "manual_review",
    ] = "pending"
    translation_ref: str | None = None
    assembly_ref: str | None = None
    observation_ref: str | None = None
    retry_count: int = Field(default=0, ge=0)


MigrationCheckpointStatus = Literal[
    "running", "completed", "interrupted", "manual_review"
]


class MigrationCheckpoint(DomainModel):
    """定位同一迁移 Job 的 WCC 状态和确定性分析输入。"""

    schema_version: str = "1.0"
    job_id: str
    project_root: str
    source_fingerprint: str
    scan_reference: str
    analysis_reference: str
    semantic_index_reference: str | None = None
    plan_reference: str
    migration_state_reference: str
    status: MigrationCheckpointStatus = "running"
    completed_unit_ids: list[str] = Field(default_factory=list)


class MatlabToPythonPlan(DomainModel):
    """完整 MATLAB 到 Python 迁移计划。"""

    schema_version: str = "1.0"
    project_root: str
    architecture: PythonArchitecture
    units: list[MigrationWorkUnit] = Field(default_factory=list)
    symbol_to_module: dict[str, str] = Field(default_factory=dict)
    unsupported_symbols: list[str] = Field(default_factory=list)


class TranslationFunctionContext(DomainModel):
    """单个 MATLAB 函数的源码、语义和边界接口。"""

    symbol_id: str
    file_path: str
    source: str
    source_hash: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    semantic_summary: str = ""
    risks: list[str] = Field(default_factory=list)


class TranslationContext(DomainModel):
    """Translation Agent 单次调用所需的有限上下文。"""

    project_root: str
    unit: MigrationWorkUnit
    architecture: PythonArchitecture
    functions: list[TranslationFunctionContext]
    neighbor_contracts: list[str] = Field(default_factory=list)


ReasonAction = Literal[
    "convert", "finish", "rebuild_context", "replan", "manual_review"
]


class CallChainContext(DomainModel):
    """Reason 使用的 WCC 上下文，包含结构；详细内容仍持久化为 artifact。"""

    project_root: str
    unit: MigrationWorkUnit
    architecture: PythonArchitecture
    functions: list[TranslationFunctionContext]
    project_structure: list[str] = Field(default_factory=list)
    internal_dependencies: list[str] = Field(default_factory=list)
    unresolved_calls: dict[str, list[str]] = Field(default_factory=dict)
    semantic_index_used: bool = False
    requested_context: list[str] = Field(default_factory=list)

    dependency_functions: list[TranslationFunctionContext] = Field(default_factory=list)
    external_dependencies: dict[str, list[str]] = Field(default_factory=dict)
    context_notes: list[str] = Field(default_factory=list)
    core_symbols: list[str] = Field(default_factory=list)


class ConversionStratagem(DomainModel):
    """Reason 的决策和转换策略；Act 必须严格遵守。"""

    unit_id: str
    action: ReasonAction = "convert"
    module_plan: dict[str, str] = Field(default_factory=dict)
    conversion_steps: list[str] = Field(default_factory=list)
    matlab_semantic_risks: list[str] = Field(default_factory=list)
    validation_plan: list[str] = Field(default_factory=list)
    requested_context: list[str] = Field(default_factory=list)
    rationale: str = ""
    external_dependencies: dict[str, list[str]] = Field(default_factory=dict)


ActChunkStatus = Literal["pending", "running", "frozen", "failed", "superseded"]


class FrozenChunkInterface(DomainModel):
    """后续 chunk 可引用的已冻结接口，不携带依赖 chunk 的完整源码。"""

    chunk_id: str
    symbol_ids: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    declarations: list[str] = Field(default_factory=list)
    summary: str = ""


class ActContext(DomainModel):
    """Act 的最小输入：转换策略与当前 chunk 源码，不包含项目结构。"""

    unit: MigrationWorkUnit
    stratagem: ConversionStratagem
    functions: list[TranslationFunctionContext]
    dependency_functions: list[TranslationFunctionContext] = Field(default_factory=list)
    dependency_interfaces: list[FrozenChunkInterface] = Field(default_factory=list)


class ActChunk(DomainModel):
    """一次有界 Act 调用及其可恢复状态。"""

    chunk_id: str
    wcc_id: str
    symbol_ids: list[str] = Field(min_length=1)
    sccs: list[list[str]] = Field(default_factory=list)
    depends_on_chunks: list[str] = Field(default_factory=list)
    module_paths: list[str] = Field(default_factory=list)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    function_count: int = Field(ge=1)
    module_count: int = Field(ge=1)
    status: ActChunkStatus = "pending"
    attempts: int = Field(default=0, ge=0)
    translation_ref: str | None = None
    interface_ref: str | None = None
    parent_chunk_id: str | None = None
    split_depth: int = Field(default=0, ge=0)
    oversized_atomic: bool = False
    last_error: str | None = None


class ActChunkPlan(DomainModel):
    """Reason 后确定性生成的 WCC chunk DAG 和断点。"""

    schema_version: str = "1.0"
    wcc_id: str
    chunks: list[ActChunk] = Field(default_factory=list)


class GeneratedPythonFile(DomainModel):
    """Agent 提议写入隔离 Python 工程的单个文件。"""

    path: str
    content: str
    symbol_ids: list[str] = Field(default_factory=list)


class TranslationResponse(DomainModel):
    """约束单个迁移工作单元的结构化模型响应。"""

    unit_id: str
    files: list[GeneratedPythonFile] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    manual_review: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class MatlabToPythonOutcome(DomainModel):
    """主 Workflow 返回的迁移计划和生成 artifact。"""

    job_id: str
    plan: MatlabToPythonPlan
    translations: list[TranslationResponse] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
