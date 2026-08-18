"""
Description: 定义最终自然语言重构报告、LLM 草稿和应用层查询结果契约。
References: Pydantic、domain.models、domain.validation。
Referenced By: NaturalLanguageReportAgent、LangGraphWorkflow 和 CLI。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import DomainModel


class ReportCheckNarrative(DomainModel):
    """作用：解释一个确定性验证检查；输入：检查 ID、状态和模型解释；输出：可审计报告条目。"""

    check_id: str
    status: Literal["passed", "failed", "skipped", "unavailable"]
    interpretation: str = Field(min_length=1)


class NaturalLanguageReportDraft(DomainModel):
    """作用：约束 LLM 只生成叙述字段；输入：受控项目事实；输出：待确定性封装的报告草稿。"""

    title: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    change_summary: list[str] = Field(default_factory=list)
    validation_summary: str = Field(min_length=1)
    check_findings: list[ReportCheckNarrative] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class NaturalLanguageReport(DomainModel):
    """作用：合并工具事实与自然语言叙述；输入：草稿、ChangeSet 和验证结果；输出：最终报告事实源。"""

    schema_version: str = "1.0"
    job_id: str
    attempt: int = Field(ge=0)
    outcome: Literal["validated", "validation_failed"]
    source_root: str
    output_root: str
    project_purpose: str
    copied_file_count: int = Field(ge=0)
    changed_matlab_files: list[str] = Field(default_factory=list)
    source_tree_unchanged: bool
    validation_passed: bool
    title: str
    executive_summary: str
    change_summary: list[str] = Field(default_factory=list)
    validation_summary: str
    check_findings: list[ReportCheckNarrative] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class NaturalLanguageReportOutcome(DomainModel):
    """作用：向应用层返回报告及其两种 artifact；输入：图状态；输出：CLI 可查询结果。"""

    job_id: str
    report: NaturalLanguageReport
    report_ref: str
    markdown_ref: str
    output_ref: str | None = None
    markdown_output_ref: str | None = None
