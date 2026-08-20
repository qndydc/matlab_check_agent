"""
Description: 使用 LangGraph 编排扫描、解析、分析和语义注解的持久化项目工作流。
References: LangGraph StateGraph/Send、SQLite checkpointer、现有 Workers、Agents 和 ArtifactStore。
Referenced By: orchestration.orchestrator、AnalysisService 和工作流集成测试。
"""

from __future__ import annotations

import hashlib
import operator
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from matlab_refactor_agent.agents import (
    AgentContext,
    ModuleResponsibilityAgent,
    NamingDirectoryAgent,
    NaturalLanguageReportAgent,
    RepairAgent,
)
from matlab_refactor_agent.agents.semantic_annotation.agent import (
    SemanticAnnotationAgent,
)
from matlab_refactor_agent.agents.semantic_annotation.aggregator import (
    SemanticAnnotationAggregator,
)
from matlab_refactor_agent.agents.semantic_annotation.context_builder import (
    SemanticContextBuilder,
    SemanticWorkUnitBuilder,
    split_semantic_work_unit,
)
from matlab_refactor_agent.domain.agents import AgentRequest, AgentResult
from matlab_refactor_agent.domain.changes import ChangeSet
from matlab_refactor_agent.domain.enums import AgentKind, JobStatus, TaskStatus, WorkerKind
from matlab_refactor_agent.domain.exceptions import (
    ArtifactError,
    LLMOutputTruncatedError,
    OrchestrationError,
    QualityGateError,
)
from matlab_refactor_agent.domain.models import (
    AnalysisResult,
    MatlabFileManifest,
    ScanResult,
)
from matlab_refactor_agent.domain.orchestration import (
    AnalysisOutcome,
    JobRecord,
    PathClaim,
    ScanOutcome,
    TaskEnvelope,
    WorkerResult,
)
from matlab_refactor_agent.domain.planning import (
    ModuleResponsibilityResponse,
    NamingChange,
    NamingDirectoryResponse,
    RefactorPlan,
    RefactorPlanningCandidates,
    RefactorReviewOutcome,
    ReviewDecision,
)
from matlab_refactor_agent.domain.reporting import (
    NaturalLanguageReport,
    NaturalLanguageReportOutcome,
)
from matlab_refactor_agent.domain.semantics import (
    ClusterAnnotationResponse,
    SemanticAnnotationOutcome,
    SemanticClusterContext,
    SemanticConflicts,
    SemanticIndex,
    SemanticWorkUnit,
    SemanticWorkUnits,
)
from matlab_refactor_agent.domain.validation import (
    RepairProposal,
    ValidationResult,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.infrastructure.llm import (
    StructuredLLMClient,
    create_llm_client,
)
from matlab_refactor_agent.workers import (
    AnalyzerAgent,
    ParserAgent,
    ScannerAgent,
    WorkerContext,
)
from matlab_refactor_agent.workers.scanning import MatlabFileDiscovery

from .conflict_resolver import ConflictResolver
from .changeset_executor import ChangeSetExecutor
from .quality_gate import QualityGate
from .plan_reconciler import PlanReconciler
from .project_validator import ProjectValidator, RefactoredProjectValidator
from .state_manager import SQLiteStateManager
from .worker_pool import WorkerPool

WorkflowTarget = Literal["scan", "analyze", "annotate", "plan"]


def _merge_artifacts(
    current: dict[str, str], update: dict[str, str]
) -> dict[str, str]:
    """作用：合并并行节点的 artifact 引用；输入：两个映射；输出：无覆盖歧义的合并映射。"""

    merged = dict(current)
    for key, value in update.items():
        existing = merged.get(key)
        if existing is not None and existing != value:
            raise OrchestrationError(f"artifact 键冲突: {key}")
        merged[key] = value
    return merged


class WorkflowState(TypedDict, total=False):
    """作用：定义 LangGraph 的轻量可持久化状态；大型领域对象只通过 artifact 引用传递。"""

    job_id: str
    project_root: str
    target: WorkflowTarget
    source_fingerprint: str
    artifacts: Annotated[dict[str, str], _merge_artifacts]
    manifest_ref: str
    scan_task_id: str
    parse_chunk_refs: Annotated[list[str], operator.add]
    parse_task_ids: Annotated[list[str], operator.add]
    scan_result_ref: str
    aggregate_task_id: str
    analysis_result_ref: str
    analysis_task_id: str
    semantic_units: list[dict[str, Any]]
    semantic_work_units_ref: str
    annotation_refs: Annotated[list[str], operator.add]
    semantic_index_ref: str
    module_responsibility_ref: str
    naming_directory_ref: str
    planning_candidates_ref: str
    refactor_plan_ref: str
    review_decision_ref: str
    review_status: str
    change_set_ref: str
    output_root: str
    repair_attempt: int
    validation_ref: str
    validation_scan_ref: str
    validation_analysis_ref: str
    repair_proposal_ref: str
    repair_decision_ref: str
    repair_review_status: str
    report_ref: str
    report_markdown_ref: str
    report_output_ref: str
    report_markdown_output_ref: str
    workflow_status: str


class _StageExecutor:
    """作用：在 LangGraph 节点内保留 Worker 结果、质量门禁和 SQLite 审计契约。"""

    def __init__(
        self,
        pool: WorkerPool,
        state: SQLiteStateManager,
        conflicts: ConflictResolver,
        quality_gate: QualityGate,
        artifacts: ArtifactStore,
    ) -> None:
        self._pool = pool
        self._state = state
        self._conflicts = conflicts
        self._quality_gate = quality_gate
        self._context = WorkerContext(artifact_store=artifacts)

    def execute(self, task: TaskEnvelope, artifact_name: str) -> WorkerResult:
        claim = PathClaim(
            task_id=task.task_id,
            path=f"{task.job_id}/{artifact_name}",
            operation="write",
        )
        self._conflicts.claim(claim)
        self._state.save_task(task)
        running = self._state.set_task_status(task, TaskStatus.RUNNING)
        try:
            result = self._pool.execute(running, self._context)
            self._quality_gate.validate(running, result)
            self._state.set_task_status(running, TaskStatus.COMPLETED, result)
            return result
        except Exception as exc:
            failure = WorkerResult(
                task_id=running.task_id,
                success=False,
                diagnostics=[str(exc)],
            )
            self._state.set_task_status(running, TaskStatus.FAILED, failure)
            raise
        finally:
            self._conflicts.release(running.task_id)

    def validate_semantic_preflight(
        self,
        scan: ScanResult,
        analysis: AnalysisResult,
        work_units: SemanticWorkUnits,
        contexts: list[SemanticClusterContext],
    ) -> None:
        """作用：把非 Worker 的语义预检接入统一 QualityGate。"""

        self._quality_gate.validate_semantic_preflight(
            scan, analysis, work_units, contexts
        )


class LangGraphWorkflow:
    """作用：以 LangGraph 作为唯一跨阶段编排器；输入：项目与目标阶段；输出：领域 Outcome。"""

    def __init__(
        self,
        *,
        settings: AppSettings,
        artifacts: ArtifactStore,
        state: SQLiteStateManager,
        pool: WorkerPool,
        semantic_client: StructuredLLMClient | None = None,
        validator: ProjectValidator | None = None,
    ) -> None:
        self._settings = settings
        self._artifacts = artifacts
        self._state = state
        self._semantic_client = semantic_client
        self._client_lock = Lock()
        self._validator = validator or RefactoredProjectValidator(
            settings.project.exclude_patterns,
            settings.project.entry_points,
        )
        self._stage_executor = _StageExecutor(
            pool=pool,
            state=state,
            conflicts=ConflictResolver(),
            quality_gate=QualityGate(artifacts),
            artifacts=artifacts,
        )
        checkpoint_path = settings.orchestrator.checkpoint_db.expanduser().resolve()
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(
            checkpoint_path,
            check_same_thread=False,
        )
        self._checkpoint_connection.execute("PRAGMA journal_mode = WAL")
        serializer = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=[],
        )
        self._checkpointer = SqliteSaver(
            self._checkpoint_connection,
            serde=serializer,
        )
        self._graph = self._build_graph().compile(checkpointer=self._checkpointer)

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        semantic_client: StructuredLLMClient | None = None,
        validator: ProjectValidator | None = None,
    ) -> "LangGraphWorkflow":
        artifacts = ArtifactStore(settings.orchestrator.artifact_dir)
        pool = WorkerPool(settings.orchestrator.max_workers)
        for worker in (
            ScannerAgent(settings.project.exclude_patterns),
            ParserAgent(),
            AnalyzerAgent(settings.project.entry_points),
        ):
            pool.register(worker)
        return cls(
            settings=settings,
            artifacts=artifacts,
            state=SQLiteStateManager(settings.orchestrator.state_db),
            pool=pool,
            semantic_client=semantic_client,
            validator=validator,
        )

    @property
    def artifact_store(self) -> ArtifactStore:
        return self._artifacts

    def run_scan(self, project_root: Path) -> ScanOutcome:
        state = self._run(project_root, "scan")
        result = self._artifacts.read_model(state["scan_result_ref"], ScanResult)
        return ScanOutcome(
            job_id=state["job_id"],
            result=result,
            artifacts=state["artifacts"],
        )

    def run_analysis(self, project_root: Path) -> AnalysisOutcome:
        state = self._run(project_root, "analyze")
        result = self._artifacts.read_model(
            state["analysis_result_ref"], AnalysisResult
        )
        return AnalysisOutcome(
            job_id=state["job_id"],
            result=result,
            artifacts=state["artifacts"],
        )

    def run_annotation(self, project_root: Path) -> SemanticAnnotationOutcome:
        """作用：优先恢复同项目兼容 checkpoint；输入：项目目录；输出：三级语义结果。"""

        root = project_root.expanduser().resolve()
        fingerprint = self._source_fingerprint(root)
        state = self._recover_annotation(root, fingerprint)
        if state is None:
            state = self._run(root, "annotate", fingerprint)
        index = self._artifacts.read_model(
            state["semantic_index_ref"], SemanticIndex
        )
        return SemanticAnnotationOutcome(
            job_id=state["job_id"],
            index=index,
            artifacts=state["artifacts"],
        )

    def run_plan(self, project_root: Path) -> RefactorReviewOutcome:
        """作用：生成计划并停在人工审查节点；输入：项目目录；输出：待审计划。"""

        state = self._run(project_root, "plan")
        return self._review_outcome(state, "waiting_approval")

    def get_review(self, job_id: str) -> RefactorReviewOutcome:
        """作用：读取持久化审查状态；输入：Job ID；输出：待审或已审计划。"""

        snapshot = self._graph.get_state(self._graph_config(job_id))
        state = dict(snapshot.values)
        if "refactor_plan_ref" not in state:
            raise OrchestrationError(f"Job 尚未生成重构计划: {job_id}")
        status = state.get(
            "workflow_status",
            state.get("review_status", "waiting_approval"),
        )
        return self._review_outcome(state, status)

    def get_report(self, job_id: str) -> NaturalLanguageReportOutcome:
        """作用：从 checkpoint 查询最终报告；输入：Job ID；输出：JSON/Markdown artifact 及报告模型。"""

        if self._state.get_job(job_id) is None:
            raise OrchestrationError(f"Job 不存在: {job_id}")
        snapshot = self._graph.get_state(self._graph_config(job_id))
        state = dict(snapshot.values)
        report_ref = state.get("report_ref")
        markdown_ref = state.get("report_markdown_ref")
        if not report_ref or not markdown_ref:
            raise OrchestrationError(f"Job 尚未生成最终报告: {job_id}")
        return NaturalLanguageReportOutcome(
            job_id=job_id,
            report=self._artifacts.read_model(
                report_ref, NaturalLanguageReport
            ),
            report_ref=report_ref,
            markdown_ref=markdown_ref,
            output_ref=state.get("report_output_ref"),
            markdown_output_ref=state.get("report_markdown_output_ref"),
        )

    def submit_review(
        self, job_id: str, decision: ReviewDecision
    ) -> RefactorReviewOutcome:
        """作用：恢复 interrupt 并提交人工决定；输入：Job ID 与决定；输出：最终审查结果。"""

        job = self._state.get_job(job_id)
        if job is None:
            raise OrchestrationError(f"Job 不存在: {job_id}")
        if job.status != JobStatus.WAITING_APPROVAL:
            raise OrchestrationError(f"Job 不在待审状态: {job_id}")
        config = self._graph_config(job_id)
        try:
            result = self._graph.invoke(
                Command(resume=decision.model_dump(mode="json")), config
            )
            snapshot = self._graph.get_state(config)
            state = dict(snapshot.values) if snapshot.next else result
            status = state.get(
                "workflow_status", state.get("review_status", "approved")
            )
            if snapshot.next:
                self._state.set_job_status(job, JobStatus.WAITING_APPROVAL)
                return self._review_outcome(state, status)
            job_status = {
                "approved": JobStatus.COMPLETED,
                "validated": JobStatus.COMPLETED,
                "rejected": JobStatus.REJECTED,
                "repair_rejected": JobStatus.REJECTED,
                "changes_requested": JobStatus.CHANGES_REQUESTED,
                "validation_failed": JobStatus.VALIDATION_FAILED,
            }[status]
            self._state.set_job_status(job, job_status)
            return self._review_outcome(state, status)
        except OrchestrationError as exc:
            self._state.set_job_status(
                job, JobStatus.WAITING_APPROVAL, str(exc)
            )
            raise
        except Exception as exc:
            self._state.set_job_status(job, JobStatus.FAILED, str(exc))
            raise self._as_orchestration_error(exc) from exc

    def resume(self, job_id: str) -> WorkflowState:
        """作用：从最后成功 checkpoint 恢复失败或中断的工作流。"""

        job = self._state.get_job(job_id)
        if job is None:
            raise OrchestrationError(f"Job 不存在: {job_id}")
        if job.status == JobStatus.WAITING_APPROVAL:
            raise OrchestrationError("待审 Job 必须通过 submit_review 恢复")
        running = self._state.set_job_status(job, JobStatus.RUNNING)
        try:
            config = self._graph_config(job_id)
            state = self._graph.invoke(None, config)
            pending_state = self._set_post_invoke_status(running, config)
            return pending_state or state
        except Exception as exc:
            self._state.set_job_status(running, JobStatus.FAILED, str(exc))
            raise self._as_orchestration_error(exc) from exc

    def graph_mermaid(self) -> str:
        """作用：导出当前 LangGraph 结构；输入：已编译图；输出：Mermaid 文本。"""

        return self._graph.get_graph().draw_mermaid()

    def _run(
        self,
        project_root: Path,
        target: WorkflowTarget,
        source_fingerprint: str | None = None,
    ) -> WorkflowState:
        """作用：创建并执行新工作流；输入：项目、目标和可选源码指纹；输出：最终状态。"""

        job = JobRecord(project_root=str(project_root.expanduser().resolve()))
        self._state.save_job(job)
        running = self._state.set_job_status(job, JobStatus.RUNNING)
        initial: WorkflowState = {
            "job_id": job.job_id,
            "project_root": job.project_root,
            "target": target,
            "source_fingerprint": source_fingerprint or "",
            "artifacts": {},
            "parse_chunk_refs": [],
            "parse_task_ids": [],
            "annotation_refs": [],
            "repair_attempt": 0,
        }
        try:
            config = self._graph_config(job.job_id)
            result = self._graph.invoke(initial, config)
            pending_state = self._set_post_invoke_status(running, config)
            return pending_state or result
        except Exception as exc:
            self._state.set_job_status(running, JobStatus.FAILED, str(exc))
            raise self._as_orchestration_error(exc) from exc

    def _recover_annotation(
        self, project_root: Path, source_fingerprint: str
    ) -> WorkflowState | None:
        """作用：查找并恢复同项目注释进度；输入：规范路径和源码指纹；输出：恢复状态或空。"""

        for job in self._state.jobs_for_project(str(project_root)):
            snapshot = self._graph.get_state(self._graph_config(job.job_id))
            state = dict(snapshot.values)
            if state.get("target") != "annotate":
                continue
            checkpoint_fingerprint = state.get("source_fingerprint")
            if checkpoint_fingerprint and checkpoint_fingerprint != source_fingerprint:
                continue
            if state.get("semantic_index_ref"):
                try:
                    self._artifacts.read_model(
                        state["semantic_index_ref"], SemanticIndex
                    )
                except ArtifactError:
                    continue
                if job.status != JobStatus.COMPLETED:
                    self._state.set_job_status(job, JobStatus.COMPLETED)
                return state
            if job.status in {JobStatus.FAILED, JobStatus.RUNNING} and snapshot.next:
                return self.resume(job.job_id)
        return None

    def _source_fingerprint(self, project_root: Path) -> str:
        """作用：计算可恢复性指纹；输入：项目目录；输出：受扫描规则约束的 MATLAB 源码哈希。"""

        manifest = MatlabFileDiscovery(
            self._settings.project.exclude_patterns
        ).discover(project_root)
        digest = hashlib.sha256()
        for relative_path in manifest.files:
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update((project_root / relative_path).read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _graph_config(self, job_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": job_id},
            "max_concurrency": min(
                self._settings.orchestrator.max_workers,
                self._settings.llm.max_agents,
            ),
        }

    def _set_post_invoke_status(
        self, job: JobRecord, config: dict[str, Any]
    ) -> WorkflowState | None:
        snapshot = self._graph.get_state(config)
        pending = snapshot.next
        workflow_status = snapshot.values.get("workflow_status")
        status = (
            JobStatus.WAITING_APPROVAL
            if pending
            else {
                "validation_failed": JobStatus.VALIDATION_FAILED,
                "repair_rejected": JobStatus.REJECTED,
                "rejected": JobStatus.REJECTED,
                "changes_requested": JobStatus.CHANGES_REQUESTED,
            }.get(workflow_status, JobStatus.COMPLETED)
        )
        self._state.set_job_status(job, status)
        return dict(snapshot.values) if pending else None

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(WorkflowState)
        graph.add_node("discover", self._discover)
        graph.add_node("parse_chunk", self._parse_chunk)
        graph.add_node("aggregate_parse", self._aggregate_parse)
        graph.add_node("analyze", self._analyze)
        graph.add_node("build_semantic_units", self._build_semantic_units)
        graph.add_node("annotate_unit", self._annotate_unit)
        graph.add_node("aggregate_semantics", self._aggregate_semantics)
        graph.add_node(
            "module_responsibility", self._module_responsibility
        )
        graph.add_node("naming_directory", self._naming_directory)
        graph.add_node(
            "collect_planning_candidates",
            self._collect_planning_candidates,
        )
        graph.add_node("reconcile_plan", self._reconcile_plan)
        graph.add_node("human_review", self._human_review)
        graph.add_node("execute_changeset", self._execute_changeset)
        graph.add_node("validate_output", self._validate_output)
        graph.add_node("generate_report", self._generate_report)
        graph.add_node("propose_repair", self._propose_repair)
        graph.add_node("repair_review", self._repair_review)
        graph.add_node("finish", lambda state: {})
        graph.add_edge(START, "discover")
        graph.add_conditional_edges(
            "discover",
            self._dispatch_parse_chunks,
            ["parse_chunk", "aggregate_parse"],
        )
        graph.add_edge("parse_chunk", "aggregate_parse")
        graph.add_conditional_edges(
            "aggregate_parse",
            self._after_parse,
            ["analyze", "finish"],
        )
        graph.add_conditional_edges(
            "analyze",
            self._after_analysis,
            ["build_semantic_units", "finish"],
        )
        graph.add_conditional_edges(
            "build_semantic_units",
            self._dispatch_semantic_units,
            ["annotate_unit", "aggregate_semantics"],
        )
        graph.add_edge("annotate_unit", "aggregate_semantics")
        graph.add_conditional_edges(
            "aggregate_semantics",
            self._after_semantics,
            ["module_responsibility", "naming_directory", "finish"],
        )
        graph.add_edge(
            ["module_responsibility", "naming_directory"],
            "collect_planning_candidates",
        )
        graph.add_conditional_edges(
            "collect_planning_candidates",
            self._after_planning_candidates,
            ["reconcile_plan", "finish"],
        )
        graph.add_edge("reconcile_plan", "human_review")
        graph.add_conditional_edges(
            "human_review",
            self._after_review,
            ["execute_changeset", "finish"],
        )
        graph.add_edge("execute_changeset", "validate_output")
        graph.add_conditional_edges(
            "validate_output",
            self._after_validation,
            ["propose_repair", "generate_report"],
        )
        graph.add_edge("generate_report", "finish")
        graph.add_edge("propose_repair", "repair_review")
        graph.add_conditional_edges(
            "repair_review",
            self._after_repair_review,
            ["execute_changeset", "finish"],
        )
        graph.add_edge("finish", END)
        return graph

    def _discover(self, state: WorkflowState) -> dict[str, Any]:
        task = TaskEnvelope(
            job_id=state["job_id"],
            worker_kind=WorkerKind.SCANNER,
            payload={"project_root": state["project_root"]},
            priority=10,
        )
        result = self._stage_executor.execute(task, "file-manifest.json")
        reference = result.artifacts["file_manifest"]
        return {
            "manifest_ref": reference,
            "scan_task_id": task.task_id,
            "artifacts": result.artifacts,
        }

    def _dispatch_parse_chunks(
        self, state: WorkflowState
    ) -> str | list[Send]:
        manifest = self._artifacts.read_model(
            state["manifest_ref"], MatlabFileManifest
        )
        size = self._settings.orchestrator.parser_chunk_size
        chunks = [
            manifest.files[index : index + size]
            for index in range(0, len(manifest.files), size)
        ]
        if not chunks:
            return "aggregate_parse"
        return [
            Send(
                "parse_chunk",
                {
                    "job_id": state["job_id"],
                    "manifest_ref": state["manifest_ref"],
                    "scan_task_id": state["scan_task_id"],
                    "chunk_index": index,
                    "files": files,
                },
            )
            for index, files in enumerate(chunks)
        ]

    def _parse_chunk(self, state: WorkflowState) -> dict[str, Any]:
        index = int(state["chunk_index"])
        task = TaskEnvelope(
            job_id=state["job_id"],
            worker_kind=WorkerKind.PARSER,
            payload={
                "operation": "parse_chunk",
                "file_manifest": state["manifest_ref"],
                "chunk_index": index,
                "files": state["files"],
            },
            depends_on=[state["scan_task_id"]],
            priority=20,
        )
        result = self._stage_executor.execute(
            task, f"parse-chunk-{index:05d}.json"
        )
        reference = result.artifacts[f"parse_chunk_{index}"]
        return {
            "parse_chunk_refs": [reference],
            "parse_task_ids": [task.task_id],
            "artifacts": result.artifacts,
        }

    def _aggregate_parse(self, state: WorkflowState) -> dict[str, Any]:
        references = sorted(state.get("parse_chunk_refs", []))
        task = TaskEnvelope(
            job_id=state["job_id"],
            worker_kind=WorkerKind.PARSER,
            payload={
                "operation": "aggregate",
                "file_manifest": state["manifest_ref"],
                "chunk_results": references,
            },
            depends_on=state.get("parse_task_ids", [])
            or [state["scan_task_id"]],
            priority=30,
        )
        result = self._stage_executor.execute(task, "scan-result.json")
        return {
            "scan_result_ref": result.artifacts["scan_result"],
            "aggregate_task_id": task.task_id,
            "artifacts": result.artifacts,
        }

    @staticmethod
    def _after_parse(state: WorkflowState) -> str:
        return "finish" if state["target"] == "scan" else "analyze"

    def _analyze(self, state: WorkflowState) -> dict[str, Any]:
        task = TaskEnvelope(
            job_id=state["job_id"],
            worker_kind=WorkerKind.ANALYZER,
            payload={"scan_result": state["scan_result_ref"]},
            depends_on=[state["aggregate_task_id"]],
            priority=40,
        )
        result = self._stage_executor.execute(task, "analysis-result.json")
        return {
            "analysis_result_ref": result.artifacts["analysis_result"],
            "analysis_task_id": task.task_id,
            "artifacts": result.artifacts,
        }

    @staticmethod
    def _after_analysis(state: WorkflowState) -> str:
        return (
            "build_semantic_units"
            if state["target"] in {"annotate", "plan"}
            else "finish"
        )

    @staticmethod
    def _after_semantics(state: WorkflowState) -> str | list[str]:
        """作用：让纯注释任务及时结束，仅规划任务继续调用两个规划 Agent。"""

        if state["target"] == "plan":
            return ["module_responsibility", "naming_directory"]
        return "finish"

    def _build_semantic_units(self, state: WorkflowState) -> dict[str, Any]:
        """作用：使用最终提示的真实 token 口径生成不会在上下文阶段意外溢出的语义簇。"""

        scan = self._artifacts.read_model(state["scan_result_ref"], ScanResult)
        analysis = self._artifacts.read_model(
            state["analysis_result_ref"], AnalysisResult
        )
        context_builder = SemanticContextBuilder(self._artifacts)
        work_units = SemanticWorkUnitBuilder(
            self._settings.llm.semantic_token_budget,
            self._settings.llm.semantic_max_functions_per_unit,
        ).build(
            analysis,
            context_estimator=lambda unit: context_builder.estimate(
                scan_reference=state["scan_result_ref"],
                analysis_reference=state["analysis_result_ref"],
                unit=unit,
            ),
        )
        contexts: list[SemanticClusterContext] = []
        preflight_errors: list[str] = []
        for unit in work_units.units:
            try:
                contexts.append(
                    context_builder.build(
                        scan_reference=state["scan_result_ref"],
                        analysis_reference=state["analysis_result_ref"],
                        unit=unit,
                        token_budget=work_units.token_budget,
                    )
                )
            except OrchestrationError as error:
                preflight_errors.append(str(error))
        if preflight_errors:
            raise QualityGateError(
                "语义预检失败，尚未调用 LLM: " + "; ".join(preflight_errors)
            )
        self._stage_executor.validate_semantic_preflight(
            scan,
            analysis,
            work_units,
            contexts,
        )
        reference = self._artifacts.write_model(
            state["job_id"], "semantic-work-units.json", work_units
        )
        return {
            "semantic_units": [
                unit.model_dump(mode="json") for unit in work_units.units
            ],
            "semantic_work_units_ref": reference,
            "artifacts": {"semantic_work_units": reference},
        }

    @staticmethod
    def _dispatch_semantic_units(
        state: WorkflowState,
    ) -> str | list[Send]:
        units = state.get("semantic_units", [])
        if not units:
            return "aggregate_semantics"
        return [
            Send(
                "annotate_unit",
                {
                    "job_id": state["job_id"],
                    "scan_result_ref": state["scan_result_ref"],
                    "analysis_result_ref": state["analysis_result_ref"],
                    "work_unit": unit,
                },
            )
            for unit in units
        ]

    def _annotate_unit(self, state: WorkflowState) -> dict[str, Any]:
        """作用：执行函数簇注释，并在输出截断时按 SCC 边界自动二分重提。"""

        unit = SemanticWorkUnit.model_validate(state["work_unit"])
        references, artifacts = self._annotate_with_output_split(state, unit)
        return {
            "annotation_refs": references,
            "artifacts": artifacts,
        }

    def _annotate_with_output_split(
        self,
        state: WorkflowState,
        unit: SemanticWorkUnit,
    ) -> tuple[list[str], dict[str, str]]:
        """作用：递归执行语义 Agent；仅对输出截断且可沿 SCC 边界拆分的簇重试。"""

        request = AgentRequest(
            job_id=state["job_id"],
            agent_kind=AgentKind.SEMANTIC_ANNOTATION,
            artifact_refs={
                "scan_result": state["scan_result_ref"],
                "analysis_result": state["analysis_result_ref"],
            },
            inputs={
                "work_unit": unit.model_dump(mode="json"),
                "token_budget": self._settings.llm.semantic_token_budget,
            },
        )
        try:
            result = SemanticAnnotationAgent(self._get_semantic_client()).run(
                request,
                AgentContext(artifact_store=self._artifacts),
            )
        except LLMOutputTruncatedError as error:
            analysis = self._artifacts.read_model(
                state["analysis_result_ref"], AnalysisResult
            )
            children = split_semantic_work_unit(unit, analysis)
            if children is None:
                raise OrchestrationError(
                    f"函数簇 {unit.unit_id} 是不可拆 SCC，模型输出仍超过上限"
                ) from error
            references: list[str] = []
            artifacts: dict[str, str] = {}
            for child in children:
                child_refs, child_artifacts = self._annotate_with_output_split(
                    state, child
                )
                references.extend(child_refs)
                artifacts = _merge_artifacts(artifacts, child_artifacts)
            return references, artifacts
        if not result.success or not result.artifacts:
            raise OrchestrationError(
                f"SemanticAnnotationAgent 返回失败: {result.diagnostics}"
            )
        reference = next(iter(result.artifacts.values()))
        return [reference], result.artifacts

    def _aggregate_semantics(self, state: WorkflowState) -> dict[str, Any]:
        analysis = self._artifacts.read_model(
            state["analysis_result_ref"], AnalysisResult
        )
        responses = [
            self._artifacts.read_model(reference, ClusterAnnotationResponse)
            for reference in sorted(state.get("annotation_refs", []))
        ]
        index = SemanticAnnotationAggregator(
            self._get_semantic_client()
        ).aggregate(analysis, responses)
        artifacts = {
            "semantic_index": self._artifacts.write_model(
                state["job_id"], "semantic-index.json", index
            ),
            "project_annotation": self._artifacts.write_model(
                state["job_id"], "project-annotation.json", index.project
            ),
            "semantic_conflicts": self._artifacts.write_model(
                state["job_id"],
                "semantic-conflicts.json",
                SemanticConflicts(conflicts=index.conflicts),
            ),
        }
        return {
            "semantic_index_ref": artifacts["semantic_index"],
            "artifacts": artifacts,
        }

    def _module_responsibility(
        self, state: WorkflowState
    ) -> dict[str, Any]:
        result = ModuleResponsibilityAgent(self._get_semantic_client()).run(
            self._planning_request(
                state, AgentKind.MODULE_RESPONSIBILITY
            ),
            self._agent_context(),
        )
        reference = self._require_agent_artifact(
            result, "module_responsibility"
        )
        return {
            "module_responsibility_ref": reference,
            "artifacts": result.artifacts,
        }

    def _naming_directory(self, state: WorkflowState) -> dict[str, Any]:
        result = NamingDirectoryAgent(self._get_semantic_client()).run(
            self._planning_request(state, AgentKind.NAMING_DIRECTORY),
            self._agent_context(),
        )
        reference = self._require_agent_artifact(result, "naming_directory")
        return {
            "naming_directory_ref": reference,
            "artifacts": result.artifacts,
        }

    def _collect_planning_candidates(
        self, state: WorkflowState
    ) -> dict[str, Any]:
        module_response = self._artifacts.read_model(
            state["module_responsibility_ref"],
            ModuleResponsibilityResponse,
        )
        naming_response = self._artifacts.read_model(
            state["naming_directory_ref"], NamingDirectoryResponse
        )
        candidates = RefactorPlanningCandidates(
            project_root=state["project_root"],
            module_responsibility=module_response,
            naming_directory=naming_response,
        )
        reference = self._artifacts.write_model(
            state["job_id"], "refactor-planning-candidates.json", candidates
        )
        return {
            "planning_candidates_ref": reference,
            "artifacts": {"refactor_planning_candidates": reference},
        }

    @staticmethod
    def _after_planning_candidates(state: WorkflowState) -> str:
        return "reconcile_plan" if state["target"] == "plan" else "finish"

    def _reconcile_plan(self, state: WorkflowState) -> dict[str, Any]:
        candidates = self._artifacts.read_model(
            state["planning_candidates_ref"], RefactorPlanningCandidates
        )
        semantic = self._artifacts.read_model(
            state["semantic_index_ref"], SemanticIndex
        )
        plan = PlanReconciler().reconcile(candidates, semantic)
        reference = self._artifacts.write_model(
            state["job_id"], "refactor-plan.json", plan
        )
        return {
            "refactor_plan_ref": reference,
            "artifacts": {"refactor_plan": reference},
        }

    def _human_review(self, state: WorkflowState) -> dict[str, Any]:
        plan = self._artifacts.read_model(
            state["refactor_plan_ref"], RefactorPlan
        )
        raw = interrupt(
            {
                "job_id": state["job_id"],
                "plan_ref": state["refactor_plan_ref"],
                "operation_count": len(plan.operations),
                "blocking_conflicts": sum(
                    item.blocking for item in plan.conflicts
                ),
                "minimum_confidence": plan.minimum_confidence,
            }
        )
        decision = ReviewDecision.model_validate(raw)
        if decision.action == "approve" and any(
            item.blocking for item in plan.conflicts
        ):
            raise OrchestrationError("计划含阻断冲突，不能批准")
        status = {
            "approve": "approved",
            "reject": "rejected",
            "request_changes": "changes_requested",
        }[decision.action]
        reference = self._artifacts.write_model(
            state["job_id"], "review-decision.json", decision
        )
        return {
            "review_decision_ref": reference,
            "review_status": status,
            "workflow_status": status,
            "artifacts": {"review_decision": reference},
        }

    @staticmethod
    def _after_review(state: WorkflowState) -> str:
        return (
            "execute_changeset"
            if state["review_status"] == "approved"
            else "finish"
        )

    def _execute_changeset(self, state: WorkflowState) -> dict[str, Any]:
        decision = self._artifacts.read_model(
            state["review_decision_ref"], ReviewDecision
        )
        if decision.action != "approve":
            raise OrchestrationError("只有已批准计划可以执行")
        plan = self._artifacts.read_model(
            state["refactor_plan_ref"], RefactorPlan
        )
        attempt = state.get("repair_attempt", 0)
        change_set = ChangeSetExecutor(
            self._settings.orchestrator.output_dir,
            self._settings.project.exclude_patterns,
        ).execute(
            job_id=state["job_id"],
            source_root=Path(state["project_root"]),
            plan=plan,
            attempt=attempt,
        )
        reference = self._artifacts.write_model(
            state["job_id"], f"change-set-{attempt:03d}.json", change_set
        )
        artifact_key = "change_set" if attempt == 0 else f"change_set_repair_{attempt}"
        return {
            "change_set_ref": reference,
            "output_root": change_set.output_root,
            "artifacts": {artifact_key: reference},
        }

    def _validate_output(self, state: WorkflowState) -> dict[str, Any]:
        attempt = state.get("repair_attempt", 0)
        baseline = self._artifacts.read_model(
            state["analysis_result_ref"], AnalysisResult
        )
        plan = self._artifacts.read_model(
            state["refactor_plan_ref"], RefactorPlan
        )
        change_set = self._artifacts.read_model(
            state["change_set_ref"], ChangeSet
        )
        bundle = self._validator.validate(
            job_id=state["job_id"],
            attempt=attempt,
            baseline=baseline,
            plan=plan,
            change_set=change_set,
        )
        validation_ref = self._artifacts.write_model(
            state["job_id"],
            f"validation-{attempt:03d}.json",
            bundle.result,
        )
        scan_ref = self._artifacts.write_model(
            state["job_id"],
            f"validation-scan-{attempt:03d}.json",
            bundle.scan,
        )
        analysis_ref = self._artifacts.write_model(
            state["job_id"],
            f"validation-analysis-{attempt:03d}.json",
            bundle.analysis,
        )
        return {
            "validation_ref": validation_ref,
            "validation_scan_ref": scan_ref,
            "validation_analysis_ref": analysis_ref,
            "workflow_status": (
                "validated" if bundle.result.passed else "validation_failed"
            ),
            "artifacts": {
                f"validation_{attempt}": validation_ref,
                f"validation_scan_{attempt}": scan_ref,
                f"validation_analysis_{attempt}": analysis_ref,
            },
        }

    def _after_validation(self, state: WorkflowState) -> str:
        if state["workflow_status"] == "validated":
            return "generate_report"
        if state.get("repair_attempt", 0) >= self._settings.orchestrator.max_repair_attempts:
            return "generate_report"
        return "propose_repair"

    def _generate_report(self, state: WorkflowState) -> dict[str, Any]:
        request = AgentRequest(
            job_id=state["job_id"],
            agent_kind=AgentKind.REPORT,
            artifact_refs={
                "refactor_plan": state["refactor_plan_ref"],
                "change_set": state["change_set_ref"],
                "validation": state["validation_ref"],
                "semantic_index": state["semantic_index_ref"],
            },
        )
        result = NaturalLanguageReportAgent(
            self._get_semantic_client()
        ).run(request, self._agent_context())
        report_ref = self._require_agent_artifact(
            result, "natural_language_report"
        )
        markdown_ref = self._require_agent_artifact(
            result, "natural_language_report_markdown"
        )
        report_output_ref, markdown_output_ref = self._publish_report(
            state, report_ref, markdown_ref
        )
        return {
            "report_ref": report_ref,
            "report_markdown_ref": markdown_ref,
            "report_output_ref": report_output_ref,
            "report_markdown_output_ref": markdown_output_ref,
            "artifacts": result.artifacts,
        }

    def _publish_report(
        self,
        state: WorkflowState,
        report_ref: str,
        markdown_ref: str,
    ) -> tuple[str, str]:
        """作用：把内部报告原子发布到用户输出目录；输入：两类 artifact；输出：公开路径。"""

        report_root = self._settings.io.report_dir.expanduser().resolve()
        project_root = Path(state["project_root"]).expanduser().resolve()
        try:
            report_root.relative_to(project_root)
        except ValueError:
            pass
        else:
            raise OrchestrationError(
                f"报告输出目录不能位于输入项目内: {report_root}"
            )
        attempt = state.get("repair_attempt", 0)
        destination = report_root / state["job_id"]
        destination.mkdir(parents=True, exist_ok=True)
        report_output = destination / f"report-{attempt:03d}.json"
        markdown_output = destination / f"report-{attempt:03d}.md"
        self._write_external_text(
            report_output, self._artifacts.read_text(report_ref)
        )
        self._write_external_text(
            markdown_output, self._artifacts.read_text(markdown_ref)
        )
        return str(report_output), str(markdown_output)

    @staticmethod
    def _write_external_text(path: Path, content: str) -> None:
        """作用：原子写入用户可见报告；输入：目标与内容；输出：稳定文件。"""

        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _propose_repair(self, state: WorkflowState) -> dict[str, Any]:
        attempt = state.get("repair_attempt", 0) + 1
        request = AgentRequest(
            job_id=state["job_id"],
            agent_kind=AgentKind.REPAIR,
            artifact_refs={
                "refactor_plan": state["refactor_plan_ref"],
                "validation": state["validation_ref"],
                "semantic_index": state["semantic_index_ref"],
            },
            inputs={"attempt": attempt},
        )
        result = RepairAgent(self._get_semantic_client()).run(
            request, self._agent_context()
        )
        reference = self._require_agent_artifact(
            result, f"repair_proposal_{attempt}"
        )
        return {
            "repair_proposal_ref": reference,
            "workflow_status": "waiting_repair_approval",
            "artifacts": result.artifacts,
        }

    def _repair_review(self, state: WorkflowState) -> dict[str, Any]:
        proposal = self._artifacts.read_model(
            state["repair_proposal_ref"], RepairProposal
        )
        repair_plan = self._build_repair_plan(state, proposal)
        plan_reference = self._artifacts.write_model(
            state["job_id"],
            f"refactor-plan-repair-{proposal.attempt:03d}.json",
            repair_plan,
        )
        raw = interrupt(
            {
                "job_id": state["job_id"],
                "repair_attempt": proposal.attempt,
                "repair_proposal_ref": state["repair_proposal_ref"],
                "repair_plan_ref": plan_reference,
                "summary": proposal.summary,
                "blocking_conflicts": sum(
                    item.blocking for item in repair_plan.conflicts
                ),
            }
        )
        decision = ReviewDecision.model_validate(raw)
        if decision.action == "approve" and any(
            item.blocking for item in repair_plan.conflicts
        ):
            raise OrchestrationError("修复计划含阻断冲突，不能批准")
        status = {
            "approve": "repair_approved",
            "reject": "repair_rejected",
            "request_changes": "changes_requested",
        }[decision.action]
        decision_ref = self._artifacts.write_model(
            state["job_id"],
            f"repair-decision-{proposal.attempt:03d}.json",
            decision,
        )
        return {
            "refactor_plan_ref": plan_reference,
            "repair_attempt": proposal.attempt,
            "repair_decision_ref": decision_ref,
            "repair_review_status": status,
            "workflow_status": status,
            "artifacts": {
                f"refactor_plan_repair_{proposal.attempt}": plan_reference,
                f"repair_decision_{proposal.attempt}": decision_ref,
            },
        }

    @staticmethod
    def _after_repair_review(state: WorkflowState) -> str:
        return (
            "execute_changeset"
            if state["repair_review_status"] == "repair_approved"
            else "finish"
        )

    def _build_repair_plan(
        self, state: WorkflowState, proposal: RepairProposal
    ) -> RefactorPlan:
        current = self._artifacts.read_model(
            state["refactor_plan_ref"], RefactorPlan
        )
        semantic = self._artifacts.read_model(
            state["semantic_index_ref"], SemanticIndex
        )
        changed = {item.symbol_id for item in proposal.operations}
        known_symbols = {item.symbol_id for item in semantic.functions}
        candidates = RefactorPlanningCandidates(
            project_root=current.project_root,
            module_responsibility=ModuleResponsibilityResponse(
                project_root=current.project_root,
                modules=current.modules,
                unassigned_symbols=sorted(
                    known_symbols - set(current.symbol_to_module)
                ),
            ),
            naming_directory=NamingDirectoryResponse(
                project_root=current.project_root,
                changes=[
                    NamingChange(
                        symbol_id=item.symbol_id,
                        current_file_path=item.source_path,
                        proposed_name=item.proposed_name,
                        proposed_file_path=item.target_path,
                        reason=item.reason,
                        confidence=item.confidence,
                    )
                    for item in proposal.operations
                ],
                directory_rules=current.directory_rules,
                unchanged_symbols=sorted(
                    known_symbols - changed
                ),
            ),
        )
        plan = PlanReconciler().reconcile(candidates, semantic)
        return plan.model_copy(
            update={
                "minimum_confidence": min(
                    plan.minimum_confidence, proposal.confidence
                )
            }
        )

    def _review_outcome(
        self, state: WorkflowState, status: str
    ) -> RefactorReviewOutcome:
        plan = self._artifacts.read_model(
            state["refactor_plan_ref"], RefactorPlan
        )
        decision_reference = state.get("repair_decision_ref") or state.get(
            "review_decision_ref"
        )
        decision = (
            self._artifacts.read_model(
                decision_reference, ReviewDecision
            )
            if decision_reference
            else None
        )
        change_set_ref = state.get("change_set_ref")
        validation = (
            self._artifacts.read_model(
                state["validation_ref"], ValidationResult
            )
            if state.get("validation_ref")
            else None
        )
        repair = (
            self._artifacts.read_model(
                state["repair_proposal_ref"], RepairProposal
            )
            if state.get("repair_proposal_ref")
            else None
        )
        return RefactorReviewOutcome(
            job_id=state["job_id"],
            status=status,
            plan=plan,
            decision=decision,
            output_root=state.get("output_root"),
            change_set_ref=change_set_ref,
            validation_ref=state.get("validation_ref"),
            repair_proposal_ref=state.get("repair_proposal_ref"),
            validation_passed=(validation.passed if validation else None),
            failed_validation_checks=(
                [
                    f"{item.check_id}: {item.summary}"
                    for item in validation.checks
                    if item.status == "failed"
                ]
                if validation
                else []
            ),
            repair_summary=repair.summary if repair else None,
            report_ref=state.get("report_ref"),
            report_markdown_ref=state.get("report_markdown_ref"),
            report_output_ref=state.get("report_output_ref"),
            report_markdown_output_ref=state.get(
                "report_markdown_output_ref"
            ),
            artifacts=state["artifacts"],
        )

    @staticmethod
    def _planning_request(
        state: WorkflowState, kind: AgentKind
    ) -> AgentRequest:
        return AgentRequest(
            job_id=state["job_id"],
            agent_kind=kind,
            artifact_refs={
                "analysis_result": state["analysis_result_ref"],
                "semantic_index": state["semantic_index_ref"],
            },
        )

    def _agent_context(self) -> AgentContext:
        return AgentContext(artifact_store=self._artifacts)

    @staticmethod
    def _require_agent_artifact(result: AgentResult, key: str) -> str:
        if not result.success or key not in result.artifacts:
            raise OrchestrationError(
                f"Agent 未返回 {key}: {result.diagnostics}"
            )
        return result.artifacts[key]

    def _get_semantic_client(self) -> StructuredLLMClient:
        if self._semantic_client is None:
            with self._client_lock:
                if self._semantic_client is None:
                    self._semantic_client = create_llm_client(self._settings.llm)
        return self._semantic_client

    @staticmethod
    def _as_orchestration_error(error: Exception) -> OrchestrationError:
        if isinstance(error, OrchestrationError):
            return error
        return OrchestrationError(str(error))
