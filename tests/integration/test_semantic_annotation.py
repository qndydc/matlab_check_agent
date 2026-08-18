"""
Description: 使用 LangGraph、真实前处理 artifacts 和假 LLM Client 验证语义注解完整闭环。
References: Orchestrator、LangGraph SQLite checkpoint、FakeStructuredLLMClient。
Referenced By: pytest 测试发现和 P1 语义注解验收。
"""

import json
import sqlite3
from pathlib import Path

from matlab_refactor_agent.agents.semantic_annotation import (
    SemanticWorkUnitBuilder,
)
from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.changes import ChangeSet
from matlab_refactor_agent.domain.planning import (
    RefactorPlanningCandidates,
    ReviewDecision,
)
from matlab_refactor_agent.domain.reporting import NaturalLanguageReport
from matlab_refactor_agent.domain.validation import ValidationResult
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.infrastructure.llm import FakeStructuredLLMClient
from matlab_refactor_agent.interfaces.cli.main import main as cli_main
from matlab_refactor_agent.orchestration import Orchestrator, SQLiteStateManager
from matlab_refactor_agent.orchestration.project_validator import (
    RefactoredProjectValidator,
    ValidationBundle,
)
from matlab_refactor_agent.domain.validation import ValidationCheck


FIXTURE = Path(__file__).parents[1] / "fixtures" / "matlab_projects" / "basic"


def _semantic_response(system_prompt: str, user_prompt: str, response_model):
    """作用：从 Agent 的真实上下文生成确定性结构化响应；输入：提示；输出：假 LLM JSON。"""

    context = json.loads(user_prompt)
    if response_model.__name__ == "FileAnnotation":
        functions = context["functions"]
        return {
            "file_path": context["file_path"],
            "role": "汇总该文件内函数并承担对应的 MATLAB 处理职责",
            "function_symbols": [item["symbol_id"] for item in functions],
            "risks": sorted({risk for item in functions for risk in item["risks"]}),
            "confidence": sum(item["confidence"] for item in functions) / len(functions),
        }
    if response_model.__name__ == "ProjectAnnotation":
        return {
            "purpose": "该项目用于演示 MATLAB 数据加载、处理与依赖分析流程",
            "usage": "从入口函数开始运行，并根据文件职责准备所需输入数据",
            "entry_points": context["entry_points"],
            "files": [item["file_path"] for item in context["files"]],
            "risks": sorted({risk for item in context["files"] for risk in item["risks"]}),
            "confidence": (
                sum(item["confidence"] for item in context["files"])
                / len(context["files"])
                if context["files"]
                else 0.0
            ),
        }
    if response_model.__name__ == "ModuleResponsibilityResponse":
        symbols = [item["symbol_id"] for item in context["functions"]]
        return {
            "project_root": context["project_root"],
            "modules": [
                {
                    "module_id": "core",
                    "name": "Core",
                    "responsibility": "承载当前项目的核心 MATLAB 计算流程",
                    "symbol_ids": symbols,
                    "depends_on_modules": [],
                    "rationale": ["根据当前语义摘要形成最小无冲突边界"],
                    "risks": [],
                    "confidence": 0.8,
                }
            ],
            "unassigned_symbols": [],
            "assumptions": [],
        }
    if response_model.__name__ == "NamingDirectoryResponse":
        return {
            "project_root": context["project_root"],
            "changes": [],
            "directory_rules": ["保持现有 MATLAB package 目录语义"],
            "unchanged_symbols": [
                item["symbol_id"] for item in context["functions"]
            ],
            "assumptions": [],
        }
    if response_model.__name__ == "RepairProposal":
        return {
            "attempt": context["attempt"],
            "summary": "根据失败证据重放已审核的安全操作",
            "addressed_checks": ["forced_failure"],
            "operations": context["plan"]["operations"],
            "confidence": 0.8,
        }
    if response_model.__name__ == "NaturalLanguageReportDraft":
        checks = context["validation"]["checks"]
        return {
            "title": "MATLAB 隔离重构交付报告",
            "executive_summary": "重构已在隔离目录执行，并由确定性工具完成验证。",
            "change_summary": [
                f"复制 {context['change_set']['copied_file_count']} 个项目文件",
                f"变更 {len(context['change_set']['changed_matlab_files'])} 个 MATLAB 文件",
            ],
            "validation_summary": "验证状态严格来自工具检查，不将跳过项视为通过。",
            "check_findings": [
                {
                    "check_id": item["check_id"],
                    "status": item["status"],
                    "interpretation": item["summary"],
                }
                for item in checks
            ],
            "risks": ["未执行的运行时检查仍需在目标 MATLAB 环境补充"],
            "next_steps": ["审阅隔离输出并执行项目级 MATLAB 测试"],
        }
    annotations = []
    for function in context["functions"]:
        confidence = 0.45 if function["symbol_id"] == "orphan" else 0.9
        annotations.append(
            {
                "symbol_id": function["symbol_id"],
                "file_path": function["file_path"],
                "start_line": function["start_line"],
                "end_line": function["end_line"],
                "summary": f"分析 {function['symbol_id']} 的 MATLAB 逻辑",
                "inputs": function["inputs"],
                "outputs": function["outputs"],
                "data_flow": ["输入经过函数计算后形成输出"],
                "side_effects": [],
                "risks": ["需补充边界测试"] if confidence < 0.6 else [],
                "open_questions": [],
                "evidence": [
                    {
                        "file_path": function["file_path"],
                        "start_line": function["start_line"],
                        "end_line": function["end_line"],
                        "source_hash": function["source_hash"],
                    }
                ],
                "confidence": confidence,
            }
        )
    return {"unit_id": context["unit"]["unit_id"], "annotations": annotations}


def test_fake_llm_semantic_annotation_closed_loop(tmp_path: Path) -> None:
    """作用：验证前处理到三级语义索引闭环；输入：MATLAB fixture；输出：artifacts、调用和冲突断言。"""

    artifact_dir = tmp_path / "jobs"
    checkpoint_db = tmp_path / "checkpoints.db"
    settings = AppSettings(
        io={"report_dir": tmp_path / "reports"},
        orchestrator={
            "state_db": tmp_path / "state.db",
            "checkpoint_db": checkpoint_db,
            "artifact_dir": artifact_dir,
            "parser_chunk_size": 3,
            "max_workers": 3,
        }
    )
    client = FakeStructuredLLMClient(_semantic_response)
    orchestrator = Orchestrator.from_settings(
        settings, semantic_client=client
    )

    outcome = orchestrator.run_annotation(FIXTURE)

    assert len(outcome.index.functions) == 8
    assert {item.file_path for item in outcome.index.files} == {
        item.file_path for item in outcome.index.functions
    }
    assert outcome.index.project.entry_points
    assert any(item.symbol_id == "orphan" for item in outcome.index.conflicts)
    assert len(client.calls) >= 2
    response_names = {item[2] for item in client.calls}
    assert {
        "ClusterAnnotationResponse",
        "FileAnnotation",
        "ProjectAnnotation",
        "ModuleResponsibilityResponse",
        "NamingDirectoryResponse",
    } <= response_names
    for _, prompt, response_name in client.calls:
        context = json.loads(prompt)
        if response_name == "ClusterAnnotationResponse":
            assert context["functions"]
            assert all("source" in item for item in context["functions"])
            assert all("source" not in item for item in context["neighbors"])
            assert context["estimated_tokens"] <= 6000
        elif "functions" in context:
            assert context["functions"]
            assert all("source" not in item for item in context["functions"])
    assert outcome.index.project.usage
    assert "用于" in outcome.index.project.purpose
    for reference in outcome.artifacts.values():
        assert Path(reference).is_file()
    with sqlite3.connect(checkpoint_db) as connection:
        checkpoint_count = connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            (outcome.job_id,),
        ).fetchone()[0]
    assert checkpoint_count > 0
    mermaid = orchestrator.graph_mermaid()
    assert "parse_chunk" in mermaid
    assert "annotate_unit" in mermaid
    assert "module_responsibility" in mermaid
    assert "naming_directory" in mermaid
    assert "collect_planning_candidates" in mermaid
    assert "execute_changeset" in mermaid
    assert "validate_output" in mermaid
    assert "propose_repair" in mermaid
    assert "repair_review" in mermaid
    assert "generate_report" in mermaid
    assert "module_responsibility" in outcome.artifacts
    assert "naming_directory" in outcome.artifacts
    assert "refactor_planning_candidates" in outcome.artifacts
    candidates = orchestrator.artifact_store.read_model(
        outcome.artifacts["refactor_planning_candidates"],
        RefactorPlanningCandidates,
    )
    assigned = {
        symbol
        for module in candidates.module_responsibility.modules
        for symbol in module.symbol_ids
    }
    assert assigned == {item.symbol_id for item in outcome.index.functions}


def test_work_units_keep_cycles_in_one_cluster(tmp_path: Path) -> None:
    """作用：验证 SCC 不被 token 分片拆散；输入：真实分析结果；输出：cycleA/cycleB 同簇断言。"""

    settings = AppSettings(
        orchestrator={
            "state_db": tmp_path / "state.db",
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": tmp_path / "jobs",
        }
    )
    analysis_outcome = Orchestrator.from_settings(settings).run_analysis(FIXTURE)
    analysis = AnalysisResult.model_validate(analysis_outcome.result)

    units = SemanticWorkUnitBuilder(token_budget=64).build(analysis)

    cycle_unit = next(unit for unit in units.units if "cycleA" in unit.symbol_ids)
    assert {"cycleA", "cycleB"}.issubset(cycle_unit.symbol_ids)


def test_work_units_follow_stable_topological_order() -> None:
    """作用：验证 SCC 缩点后按依赖拓扑顺序分簇；输入：名称顺序相反的调用边；输出：调用者簇先于被调用者簇。"""

    analysis = AnalysisResult.model_validate(
        {
            "project_root": ".",
            "functions": [
                {
                    "name": "leaf",
                    "qualified_name": "a_leaf",
                    "file_path": "leaf.m",
                    "line_count": 1,
                },
                {
                    "name": "entry",
                    "qualified_name": "z_entry",
                    "file_path": "entry.m",
                    "line_count": 1,
                },
            ],
            "dependencies": [{"source": "z_entry", "target": "a_leaf"}],
        }
    )

    units = SemanticWorkUnitBuilder(token_budget=32).build(analysis).units

    assert [unit.symbol_ids for unit in units] == [["z_entry"], ["a_leaf"]]


def test_langgraph_resumes_failed_semantic_node_from_checkpoint(
    tmp_path: Path,
) -> None:
    """作用：验证 LLM 节点失败后从 checkpoint 恢复；输入：首次失败客户端；输出：完成索引且不重跑前处理。"""

    project = tmp_path / "project"
    project.mkdir()
    (project / "identity.m").write_text(
        "function y = identity(x)\ny = x;\nend\n", encoding="utf-8"
    )
    state_db = tmp_path / "state.db"
    settings = AppSettings(
        io={"report_dir": tmp_path / "reports"},
        orchestrator={
            "state_db": state_db,
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": tmp_path / "jobs",
            "parser_chunk_size": 1,
        }
    )
    semantic_attempts = 0

    def flaky_response(system_prompt: str, user_prompt: str, response_model):
        nonlocal semantic_attempts
        if response_model.__name__ == "ClusterAnnotationResponse":
            semantic_attempts += 1
            if semantic_attempts == 1:
                raise RuntimeError("temporary model failure")
        return _semantic_response(system_prompt, user_prompt, response_model)

    orchestrator = Orchestrator.from_settings(
        settings,
        semantic_client=FakeStructuredLLMClient(flaky_response),
    )

    try:
        orchestrator.run_annotation(project)
    except Exception:
        pass
    else:
        raise AssertionError("首次语义调用应失败")

    with sqlite3.connect(state_db) as connection:
        job_id, status = connection.execute(
            "SELECT job_id, status FROM jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert status == "failed"
    tasks_before = SQLiteStateManager(state_db).task_statuses(job_id)

    resumed = orchestrator.resume(job_id)

    assert Path(resumed["semantic_index_ref"]).is_file()
    assert str(SQLiteStateManager(state_db).get_job(job_id).status) == "completed"
    assert SQLiteStateManager(state_db).task_statuses(job_id) == tasks_before
    assert semantic_attempts == 2


def test_refactor_plan_waits_for_and_records_human_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """作用：验证计划仲裁和 interrupt 审批闭环；输入：假 LLM 候选；输出：待审、恢复和决定 artifact。"""

    state_db = tmp_path / "state.db"
    settings = AppSettings(
        io={"report_dir": tmp_path / "reports"},
        orchestrator={
            "state_db": state_db,
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": tmp_path / "jobs",
            "output_dir": tmp_path / "outputs",
            "parser_chunk_size": 3,
        }
    )
    orchestrator = Orchestrator.from_settings(
        settings,
        semantic_client=FakeStructuredLLMClient(_semantic_response),
    )

    pending = orchestrator.run_plan(FIXTURE)

    assert pending.status == "waiting_approval"
    assert pending.plan.symbol_to_module
    assert not pending.plan.conflicts
    assert "refactor_plan" in pending.artifacts
    assert str(
        SQLiteStateManager(state_db).get_job(pending.job_id).status
    ) == "waiting_approval"
    loaded = orchestrator.get_review(pending.job_id)
    assert loaded.plan == pending.plan

    approved = orchestrator.submit_review(
        pending.job_id,
        ReviewDecision(action="approve", comment="人工确认方案可执行"),
    )

    assert approved.status == "validated"
    assert approved.decision is not None
    assert approved.decision.comment == "人工确认方案可执行"
    assert Path(approved.artifacts["review_decision"]).is_file()
    assert approved.output_root is not None
    assert Path(approved.output_root, "main.m").is_file()
    assert approved.change_set_ref is not None
    change_set = orchestrator.artifact_store.read_model(
        approved.change_set_ref, ChangeSet
    )
    assert change_set.source_tree_hash_before == change_set.source_tree_hash_after
    assert change_set.output_root == approved.output_root
    assert approved.validation_ref is not None
    validation = orchestrator.artifact_store.read_model(
        approved.validation_ref, ValidationResult
    )
    assert validation.passed
    assert approved.report_ref is not None
    assert approved.report_markdown_ref is not None
    assert approved.report_output_ref is not None
    assert approved.report_markdown_output_ref is not None
    assert Path(approved.report_output_ref).is_file()
    assert Path(approved.report_markdown_output_ref).is_file()
    assert Path(approved.report_markdown_ref).read_text(encoding="utf-8").startswith(
        "# MATLAB 隔离重构交付报告"
    )
    report_outcome = orchestrator.get_report(pending.job_id)
    assert report_outcome.report.validation_passed
    report = orchestrator.artifact_store.read_model(
        report_outcome.report_ref, NaturalLanguageReport
    )
    assert report.source_tree_unchanged
    assert {item.check_id for item in report.check_findings} == {
        item.check_id for item in validation.checks
    }
    monkeypatch.setenv("MATLAB_REFACTOR_STATE_DB", str(state_db))
    monkeypatch.setenv(
        "MATLAB_REFACTOR_CHECKPOINT_DB", str(tmp_path / "checkpoints.db")
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ARTIFACT_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("MATLAB_REFACTOR_CODE_OUTPUT_DIR", str(tmp_path / "outputs"))
    cli_output = tmp_path / "final-report.json"
    assert cli_main(
        [
            "report",
            pending.job_id,
            "--json",
            str(cli_output),
        ]
    ) == 0
    assert json.loads(cli_output.read_text(encoding="utf-8"))["report"][
        "job_id"
    ] == pending.job_id
    assert any(
        item.check_id == "matlab_runtime"
        and item.status in {"skipped", "unavailable"}
        for item in validation.checks
    )
    assert str(
        SQLiteStateManager(state_db).get_job(pending.job_id).status
    ) == "completed"


def test_failed_validation_enters_reviewed_repair_loop(
    tmp_path: Path,
) -> None:
    """作用：验证失败证据生成修复、再次审批和新隔离输出；输入：首次失败验证器；输出：第二次验证通过。"""

    class FailOnceValidator:
        def __init__(self) -> None:
            self.delegate = RefactoredProjectValidator([], [])
            self.calls = 0

        def validate(self, **kwargs) -> ValidationBundle:
            self.calls += 1
            bundle = self.delegate.validate(**kwargs)
            if self.calls > 1:
                return bundle
            failure = ValidationCheck(
                check_id="forced_failure",
                status="failed",
                summary="测试注入的首次失败",
            )
            return ValidationBundle(
                result=bundle.result.model_copy(
                    update={
                        "passed": False,
                        "checks": [*bundle.result.checks, failure],
                    }
                ),
                scan=bundle.scan,
                analysis=bundle.analysis,
            )

    validator = FailOnceValidator()
    settings = AppSettings(
        io={"report_dir": tmp_path / "reports"},
        orchestrator={
            "state_db": tmp_path / "state.db",
            "checkpoint_db": tmp_path / "checkpoints.db",
            "artifact_dir": tmp_path / "jobs",
            "output_dir": tmp_path / "outputs",
            "max_repair_attempts": 1,
        }
    )
    orchestrator = Orchestrator.from_settings(
        settings,
        semantic_client=FakeStructuredLLMClient(_semantic_response),
        validator=validator,
    )
    pending = orchestrator.run_plan(FIXTURE)

    repair_pending = orchestrator.submit_review(
        pending.job_id, ReviewDecision(action="approve")
    )

    assert repair_pending.status == "waiting_repair_approval"
    assert repair_pending.repair_proposal_ref is not None
    assert repair_pending.validation_passed is False
    assert repair_pending.failed_validation_checks
    assert repair_pending.repair_summary
    assert Path(repair_pending.output_root).is_dir()
    assert str(
        SQLiteStateManager(settings.orchestrator.state_db)
        .get_job(pending.job_id)
        .status
    ) == "waiting_approval"

    repaired = orchestrator.submit_review(
        pending.job_id,
        ReviewDecision(action="approve", comment="批准受限修复"),
    )

    assert repaired.status == "validated"
    assert validator.calls == 2
    assert repaired.output_root is not None
    assert repaired.output_root.endswith("-repair-1")
    repaired_change_set = orchestrator.artifact_store.read_model(
        repaired.change_set_ref, ChangeSet
    )
    assert repaired_change_set.attempt == 1
    assert repaired.report_ref is not None
    assert orchestrator.get_report(pending.job_id).report.attempt == 1
