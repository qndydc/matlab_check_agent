"""
Description: 验证 NaturalLanguageReportAgent 的事实封装和验证状态防篡改边界。
References: FakeStructuredLLMClient、ArtifactStore、domain.reporting。
Referenced By: pytest 测试发现和报告 Agent 验收。
"""

from pathlib import Path

import pytest

from matlab_refactor_agent.agents import AgentContext, NaturalLanguageReportAgent
from matlab_refactor_agent.domain.agents import AgentRequest
from matlab_refactor_agent.domain.changes import ChangeSet
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.planning import RefactorPlan
from matlab_refactor_agent.domain.reporting import NaturalLanguageReport
from matlab_refactor_agent.domain.semantics import ProjectAnnotation, SemanticIndex
from matlab_refactor_agent.domain.validation import ValidationCheck, ValidationResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm import FakeStructuredLLMClient


def _request(
    tmp_path: Path, responder
) -> tuple[NaturalLanguageReportAgent, AgentRequest, AgentContext]:
    store = ArtifactStore(tmp_path / "jobs")
    job_id = "job123"
    source = str((tmp_path / "source").resolve())
    output = str((tmp_path / "output").resolve())
    refs = {
        "refactor_plan": store.write_model(
            job_id,
            "plan.json",
            RefactorPlan(project_root=source, minimum_confidence=1.0),
        ),
        "change_set": store.write_model(
            job_id,
            "change.json",
            ChangeSet(
                job_id=job_id,
                source_root=source,
                output_root=output,
                plan_hash="plan",
                source_tree_hash_before="same",
                source_tree_hash_after="same",
                output_tree_hash="output",
                copied_file_count=1,
            ),
        ),
        "validation": store.write_model(
            job_id,
            "validation.json",
            ValidationResult(
                job_id=job_id,
                attempt=0,
                output_root=output,
                passed=True,
                checks=[
                    ValidationCheck(
                        check_id="source_immutability",
                        status="passed",
                        summary="源项目未改变",
                    )
                ],
            ),
        ),
        "semantic_index": store.write_model(
            job_id,
            "semantic.json",
            SemanticIndex(
                project_root=source,
                project=ProjectAnnotation(
                    purpose="测试项目",
                    usage="运行测试入口",
                    confidence=1.0,
                ),
            ),
        ),
    }
    return (
        NaturalLanguageReportAgent(FakeStructuredLLMClient(responder)),
        AgentRequest(job_id=job_id, agent_kind=AgentKind.REPORT, artifact_refs=refs),
        AgentContext(artifact_store=store),
    )


def test_report_agent_persists_json_and_markdown(tmp_path: Path) -> None:
    """作用：验证报告双 artifact 与只读事实；输入：通过检查；输出：JSON/Markdown 断言。"""

    def responder(system_prompt, user_prompt, response_model):
        return {
            "title": "交付报告",
            "executive_summary": "隔离重构已完成。",
            "validation_summary": "源项目检查通过。",
            "check_findings": [
                {
                    "check_id": "source_immutability",
                    "status": "passed",
                    "interpretation": "源项目哈希一致。",
                }
            ],
        }

    agent, request, context = _request(tmp_path, responder)

    result = agent.run(request, context)

    report = context.artifact_store.read_model(
        result.artifacts["natural_language_report"], NaturalLanguageReport
    )
    assert report.source_tree_unchanged
    assert Path(result.artifacts["natural_language_report_markdown"]).is_file()


def test_report_agent_rejects_changed_validation_status(tmp_path: Path) -> None:
    """作用：验证 LLM 不能篡改工具状态；输入：把 passed 改为 failed 的草稿；输出：编排异常。"""

    def responder(system_prompt, user_prompt, response_model):
        return {
            "title": "错误报告",
            "executive_summary": "错误地改写状态。",
            "validation_summary": "错误。",
            "check_findings": [
                {
                    "check_id": "source_immutability",
                    "status": "failed",
                    "interpretation": "错误状态。",
                }
            ],
        }

    agent, request, context = _request(tmp_path, responder)

    with pytest.raises(OrchestrationError, match="完整且原样覆盖"):
        agent.run(request, context)
