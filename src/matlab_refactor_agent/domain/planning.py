"""
Description: 定义模块职责、命名目录和后续计划仲裁使用的结构化候选模型。
References: Pydantic、domain.models、domain.semantics。
Referenced By: ModuleResponsibilityAgent、NamingDirectoryAgent 和 LangGraphWorkflow。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import DomainModel


class PlanningFunctionSummary(DomainModel):
    """作用：向规划 Agent 提供无源码的函数事实；输入：语义索引；输出：有限函数摘要。"""

    symbol_id: str
    file_path: str
    summary: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class PlanningContext(DomainModel):
    """作用：封装两个规划 Agent 的共享事实；输入：分析与语义 artifacts；输出：受控提示负载。"""

    project_root: str
    project_purpose: str
    entry_points: list[str] = Field(default_factory=list)
    functions: list[PlanningFunctionSummary] = Field(default_factory=list)
    dependencies: list[tuple[str, str]] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)
    semantic_conflicts: list[str] = Field(default_factory=list)


class ProposedModule(DomainModel):
    """作用：描述一个候选模块边界；输入：语义与依赖事实；输出：可仲裁模块提议。"""

    module_id: str
    name: str
    responsibility: str
    symbol_ids: list[str] = Field(min_length=1)
    depends_on_modules: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ModuleResponsibilityResponse(DomainModel):
    """作用：约束模块职责 Agent 的完整响应；输入：规划上下文；输出：模块候选集合。"""

    project_root: str
    modules: list[ProposedModule] = Field(default_factory=list)
    unassigned_symbols: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class NamingChange(DomainModel):
    """作用：描述单个符号的重命名或目录迁移建议；输入：现有标识；输出：候选路径变更。"""

    symbol_id: str
    current_file_path: str
    proposed_name: str
    proposed_file_path: str
    reason: str
    matlab_constraints: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class NamingDirectoryResponse(DomainModel):
    """作用：约束命名目录 Agent 响应；输入：规划上下文；输出：变更及目录规则。"""

    project_root: str
    changes: list[NamingChange] = Field(default_factory=list)
    directory_rules: list[str] = Field(default_factory=list)
    unchanged_symbols: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class RefactorPlanningCandidates(DomainModel):
    """作用：汇合两个并行 Agent 的原始候选；输入：两份响应；输出：PlanReconciler 唯一输入。"""

    schema_version: str = "1.0"
    project_root: str
    module_responsibility: ModuleResponsibilityResponse
    naming_directory: NamingDirectoryResponse


class PlanConflict(DomainModel):
    """作用：记录确定性计划冲突；输入：候选交叉校验；输出：人工审查风险项。"""

    code: str
    message: str
    symbol_ids: list[str] = Field(default_factory=list)
    blocking: bool = True


class RefactorOperation(DomainModel):
    """作用：描述尚未执行的单文件重命名或移动；输入：命名候选；输出：可预览操作。"""

    symbol_id: str
    module_id: str
    source_path: str
    target_path: str
    proposed_name: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class RefactorPlan(DomainModel):
    """作用：承载确定性合并后的只读重构计划；输入：两类候选；输出：人工审查对象。"""

    schema_version: str = "1.0"
    project_root: str
    modules: list[ProposedModule] = Field(default_factory=list)
    symbol_to_module: dict[str, str] = Field(default_factory=dict)
    operations: list[RefactorOperation] = Field(default_factory=list)
    unchanged_symbols: list[str] = Field(default_factory=list)
    directory_rules: list[str] = Field(default_factory=list)
    target_tree: list[str] = Field(default_factory=list)
    conflicts: list[PlanConflict] = Field(default_factory=list)
    minimum_confidence: float = Field(ge=0.0, le=1.0)
    requires_manual_review: bool = True


class ReviewDecision(DomainModel):
    """作用：记录人工对计划的决定；输入：审查动作和意见；输出：可恢复审批命令。"""

    action: Literal["approve", "reject", "request_changes"]
    comment: str = ""


class RefactorReviewOutcome(DomainModel):
    """作用：返回待审或已审计划；输入：Job、计划和决定；输出：应用层审查结果。"""

    job_id: str
    status: Literal[
        "waiting_approval",
        "waiting_repair_approval",
        "approved",
        "validated",
        "validation_failed",
        "rejected",
        "repair_rejected",
        "changes_requested",
    ]
    plan: RefactorPlan
    decision: ReviewDecision | None = None
    output_root: str | None = None
    change_set_ref: str | None = None
    validation_ref: str | None = None
    repair_proposal_ref: str | None = None
    validation_passed: bool | None = None
    failed_validation_checks: list[str] = Field(default_factory=list)
    repair_summary: str | None = None
    report_ref: str | None = None
    report_markdown_ref: str | None = None
    report_output_ref: str | None = None
    report_markdown_output_ref: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
