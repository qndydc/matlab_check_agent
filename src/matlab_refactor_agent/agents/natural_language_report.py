"""
Description: 根据已执行 ChangeSet 与工具验证证据生成受约束的自然语言重构报告。
References: agents.base、StructuredLLMClient、domain.reporting、ArtifactStore。
Referenced By: LangGraphWorkflow 和报告 Agent 测试。
"""

from __future__ import annotations

import json

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.domain.agents import AgentProposal, AgentRequest, AgentResult
from matlab_refactor_agent.domain.changes import ChangeSet
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.planning import RefactorPlan
from matlab_refactor_agent.domain.reporting import (
    NaturalLanguageReport,
    NaturalLanguageReportDraft,
)
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.domain.validation import ValidationResult
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient

SYSTEM_PROMPT = (
    "你是 MATLAB 重构交付报告 Agent。只能解释给定的结构化事实，不得虚构测试、"
    "性能提升或业务结论。逐项验证解释必须完整覆盖输入 check_id，并保持原始状态。"
    "报告面向代码审查者，语言简洁、明确区分 passed、failed、skipped 和 unavailable。"
)


class NaturalLanguageReportAgent(BaseAgent):
    """作用：把计划、实际变更和验证证据转成可读报告；输入：四类 artifact；输出：JSON 与 Markdown。"""

    def __init__(self, client: StructuredLLMClient) -> None:
        self._client = client

    @property
    def kind(self) -> AgentKind:
        return AgentKind.REPORT

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        plan = context.artifact_store.read_model(
            request.artifact_refs["refactor_plan"], RefactorPlan
        )
        change_set = context.artifact_store.read_model(
            request.artifact_refs["change_set"], ChangeSet
        )
        validation = context.artifact_store.read_model(
            request.artifact_refs["validation"], ValidationResult
        )
        semantic = context.artifact_store.read_model(
            request.artifact_refs["semantic_index"], SemanticIndex
        )
        self._validate_inputs(request.job_id, plan, change_set, validation, semantic)
        prompt = self._build_prompt(plan, change_set, validation, semantic)
        draft = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            response_model=NaturalLanguageReportDraft,
        )
        self._validate_draft(draft, validation)
        report = NaturalLanguageReport(
            job_id=request.job_id,
            attempt=change_set.attempt,
            outcome="validated" if validation.passed else "validation_failed",
            source_root=change_set.source_root,
            output_root=change_set.output_root,
            project_purpose=semantic.project.purpose,
            copied_file_count=change_set.copied_file_count,
            changed_matlab_files=change_set.changed_matlab_files,
            source_tree_unchanged=(
                change_set.source_tree_hash_before
                == change_set.source_tree_hash_after
            ),
            validation_passed=validation.passed,
            **draft.model_dump(mode="python"),
        )
        suffix = f"{change_set.attempt:03d}"
        report_ref = context.artifact_store.write_model(
            request.job_id, f"natural-language-report-{suffix}.json", report
        )
        markdown_ref = context.artifact_store.write_text(
            request.job_id,
            f"natural-language-report-{suffix}.md",
            render_report_markdown(report),
        )
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[
                AgentProposal(
                    agent_kind=self.kind,
                    payload={
                        "report_ref": report_ref,
                        "markdown_ref": markdown_ref,
                    },
                    evidence=[item.check_id for item in validation.checks],
                    confidence=1.0,
                )
            ],
            artifacts={
                "natural_language_report": report_ref,
                "natural_language_report_markdown": markdown_ref,
            },
        )

    @staticmethod
    def _build_prompt(
        plan: RefactorPlan,
        change_set: ChangeSet,
        validation: ValidationResult,
        semantic: SemanticIndex,
    ) -> dict[str, object]:
        return {
            "project": {
                "purpose": semantic.project.purpose,
                "entry_points": semantic.project.entry_points,
                "semantic_risks": semantic.project.risks,
            },
            "plan": {
                "module_count": len(plan.modules),
                "operation_count": len(plan.operations),
                "modules": [
                    {
                        "name": item.name,
                        "responsibility": item.responsibility,
                        "symbol_count": len(item.symbol_ids),
                    }
                    for item in plan.modules
                ],
                "operations": [
                    {
                        "source_path": item.source_path,
                        "target_path": item.target_path,
                        "reason": item.reason,
                    }
                    for item in plan.operations
                ],
            },
            "change_set": {
                "attempt": change_set.attempt,
                "copied_file_count": change_set.copied_file_count,
                "changed_matlab_files": change_set.changed_matlab_files,
                "file_changes": [
                    item.model_dump(mode="json") for item in change_set.file_changes
                ],
                "source_tree_unchanged": (
                    change_set.source_tree_hash_before
                    == change_set.source_tree_hash_after
                ),
            },
            "validation": validation.model_dump(mode="json"),
        }

    @staticmethod
    def _validate_inputs(
        job_id: str,
        plan: RefactorPlan,
        change_set: ChangeSet,
        validation: ValidationResult,
        semantic: SemanticIndex,
    ) -> None:
        if change_set.job_id != job_id or validation.job_id != job_id:
            raise OrchestrationError("报告输入的 Job ID 不一致")
        if change_set.attempt != validation.attempt:
            raise OrchestrationError("报告输入的执行与验证 attempt 不一致")
        if plan.project_root != semantic.project_root:
            raise OrchestrationError("报告输入的计划与语义项目不一致")
        if change_set.source_root != plan.project_root:
            raise OrchestrationError("报告输入的 ChangeSet 与计划项目不一致")

    @staticmethod
    def _validate_draft(
        draft: NaturalLanguageReportDraft, validation: ValidationResult
    ) -> None:
        expected = {item.check_id: item.status for item in validation.checks}
        actual = {item.check_id: item.status for item in draft.check_findings}
        if len(actual) != len(draft.check_findings) or actual != expected:
            raise OrchestrationError("报告必须完整且原样覆盖全部验证检查状态")


def render_report_markdown(report: NaturalLanguageReport) -> str:
    """作用：确定性渲染 Markdown；输入：最终报告模型；输出：不含模型自由格式标记的文档。"""

    lines = [
        f"# {report.title}",
        "",
        report.executive_summary,
        "",
        "## 执行事实",
        "",
        f"- Job：`{report.job_id}`",
        f"- 结果：`{report.outcome}`",
        f"- 执行轮次：`{report.attempt}`",
        f"- 输入项目：`{report.source_root}`",
        f"- 隔离输出：`{report.output_root}`",
        f"- 复制文件数：`{report.copied_file_count}`",
        f"- 变更 MATLAB 文件数：`{len(report.changed_matlab_files)}`",
        f"- 原项目保持不变：`{str(report.source_tree_unchanged).lower()}`",
        "",
        "## 变更摘要",
        "",
    ]
    lines.extend(f"- {item}" for item in report.change_summary)
    lines.extend(["", "## 验证结果", "", report.validation_summary, ""])
    lines.extend(
        f"- `{item.check_id}` — **{item.status}**：{item.interpretation}"
        for item in report.check_findings
    )
    lines.extend(["", "## 风险", ""])
    lines.extend(f"- {item}" for item in report.risks)
    lines.extend(["", "## 后续建议", ""])
    lines.extend(f"- {item}" for item in report.next_steps)
    return "\n".join(lines).rstrip() + "\n"
