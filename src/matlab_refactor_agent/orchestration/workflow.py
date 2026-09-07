"""
Description: 兼容公共门面；将确定性分析、语义流水线和迁移 Agent 委派给三个独立模块。
References: analysis、semantics、migration、ArtifactStore、SQLiteStateManager。
Referenced By: Orchestrator、应用服务、CLI 和 Web API。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict, TypeVar

from matlab_refactor_agent.analysis import (
    AnalysisPipeline,
    AnalyzerWorker,
    ParserWorker,
    ScannerWorker,
)
from matlab_refactor_agent.domain.code_tree import CodeTreeDocument
from matlab_refactor_agent.domain.enums import JobStatus
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import (
    MatlabToPythonOutcome,
    MatlabToPythonPlan,
    TranslationResponse,
)
from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.orchestration import (
    AnalysisOutcome,
    JobRecord,
    ScanOutcome,
)
from matlab_refactor_agent.domain.semantics import (
    SemanticAnnotationOutcome,
    SemanticIndex,
    SemanticProgressEvent,
    SemanticPreparationBundle,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.infrastructure.llm import (
    StructuredLLMClient,
    create_llm_client,
)
from matlab_refactor_agent.migration import (
    MatlabToPythonMigrationAgent,
    MigrationAgentResult,
)
from matlab_refactor_agent.migration.checkpoint import (
    MigrationCheckpointStore,
)
from matlab_refactor_agent.semantics import SemanticAnnotationPipeline
from matlab_refactor_agent.semantics.checkpoint import SemanticCheckpoint, validate_source
from matlab_refactor_agent.workers.scanning import validate_scan_inventory

from .state_manager import SQLiteStateManager
from .worker_pool import WorkerPool


WorkflowTarget = Literal["scan", "analyze", "annotate", "convert"]
ResultT = TypeVar("ResultT")


class WorkflowState(TypedDict, total=False):
    """跨模块只传递轻量 artifact 引用。"""

    job_id: str
    project_root: str
    target: WorkflowTarget
    artifacts: dict[str, str]
    scan_result_ref: str
    analysis_result_ref: str
    structural_code_tree_ref: str
    semantic_code_tree_ref: str
    semantic_index_ref: str
    semantic_preparation_ref: str
    semantic_quality_report_ref: str
    semantic_progress_ref: str
    migration_plan_ref: str
    migration_state_ref: str
    translation_refs: list[str]


class MainWorkflow:
    """兼容旧 API；自身不再实现三类业务流程。"""

    def __init__(
        self,
        *,
        settings: AppSettings,
        artifacts: ArtifactStore,
        state: SQLiteStateManager,
        pool: WorkerPool,
        semantic_client: StructuredLLMClient | None = None,
    ) -> None:
        self._settings = settings
        self._artifacts = artifacts
        self._state = state
        self._pool = pool
        self._provided_llm_client = semantic_client
        self._llm_clients: dict[tuple[int, str | None], StructuredLLMClient] = {}
        self._analysis_pipeline = AnalysisPipeline(
            artifacts=artifacts,
            state=state,
            pool=pool,
            parser_chunk_size=settings.orchestrator.parser_chunk_size,
        )

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        semantic_client: StructuredLLMClient | None = None,
    ) -> "MainWorkflow":
        artifacts = ArtifactStore(settings.orchestrator.artifact_dir)
        pool = WorkerPool(settings.orchestrator.max_workers)
        for worker in (
            ScannerWorker(settings.project.exclude_patterns),
            ParserWorker(),
            AnalyzerWorker(settings.project.entry_points),
        ):
            pool.register(worker)
        return cls(
            settings=settings,
            artifacts=artifacts,
            state=SQLiteStateManager(settings.orchestrator.state_db),
            pool=pool,
            semantic_client=semantic_client,
        )

    @property
    def artifact_store(self) -> ArtifactStore:
        return self._artifacts

    def run_scan(self, project_root: Path) -> ScanOutcome:
        state = self._run(project_root, "scan")
        return ScanOutcome(
            job_id=state["job_id"],
            result=self._artifacts.read_model(
                state["scan_result_ref"], ScanResult
            ),
            artifacts=state["artifacts"],
        )

    def run_analysis(self, project_root: Path) -> AnalysisOutcome:
        state = self._run(project_root, "analyze")
        return AnalysisOutcome(
            job_id=state["job_id"],
            result=self._artifacts.read_model(
                state["analysis_result_ref"], AnalysisResult
            ),
            artifacts=state["artifacts"],
        )

    def run_annotation(
        self,
        project_root: Path,
        progress_callback: Callable[[SemanticProgressEvent], None] | None = None,
        *,
        job_id: str | None = None,
    ) -> SemanticAnnotationOutcome:
        state = self._run(
            project_root,
            "annotate",
            semantic_progress=progress_callback,
            job_id=job_id,
        )
        return SemanticAnnotationOutcome(
            job_id=state["job_id"],
            index=self._artifacts.read_model(
                state["semantic_index_ref"], SemanticIndex
            ),
            artifacts=state["artifacts"],
        )

    def resume_annotation(
        self, job_id: str, *, project_root: Path | None = None,
        progress_callback: Callable[[SemanticProgressEvent], None] | None = None,
    ) -> SemanticAnnotationOutcome:
        job = self._state.get_job(job_id)
        if job is None:
            raise OrchestrationError(f"Job 不存在: {job_id}")
        checkpoint = self._artifacts.read_model(
            self._artifacts.reference(job_id, "semantic-checkpoint.json"), SemanticCheckpoint)
        bundle = self._artifacts.read_model(checkpoint.preparation_reference, SemanticPreparationBundle)
        root = Path(bundle.project_root).resolve()
        if project_root is not None and project_root.resolve() != root:
            raise OrchestrationError("恢复项目与断点不一致")
        validate_source(checkpoint, root)
        state: WorkflowState = {
            "job_id": job_id, "project_root": str(root), "target": "annotate",
            "scan_result_ref": bundle.scan_reference,
            "analysis_result_ref": bundle.analysis_reference,
            "structural_code_tree_ref": bundle.code_tree_reference,
            "artifacts": {"scan_result": bundle.scan_reference,
                          "analysis_result": bundle.analysis_reference,
                          "structural_code_tree": bundle.code_tree_reference},
        }
        self._execute_job(job, lambda: self._annotate(state, progress_callback, resume=True),
                          interrupt_message="语义任务已中断，可从断点恢复")
        return SemanticAnnotationOutcome(job_id=job_id, artifacts=state["artifacts"],
            index=self._artifacts.read_model(state["semantic_index_ref"], SemanticIndex))

    def run_code_tree(self, project_root: Path) -> CodeTreeDocument:
        """始终返回不含 LLM 结论的结构代码树。"""

        state = self._run(project_root, "analyze")
        return self._artifacts.read_model(
            state["structural_code_tree_ref"], CodeTreeDocument
        )

    def run_matlab_to_python(
        self,
        project_root: Path,
        *,
        semantic_index_reference: str | None = None,
        job_id: str | None = None,
    ) -> MatlabToPythonOutcome:
        """运行迁移 Agent；三级语义索引是可选增强输入。"""

        state = self._run(
            project_root,
            "convert",
            semantic_index_reference=semantic_index_reference,
            job_id=job_id,
        )
        return self._migration_outcome(
            job_id=state["job_id"],
            plan_reference=state["migration_plan_ref"],
            translation_references=state.get("translation_refs", []),
            artifacts=state["artifacts"],
        )

    def run_matlab_to_python_from_analysis(
        self,
        project_root: Path,
        *,
        scan_reference: str,
        analysis_reference: str,
        semantic_index_reference: str | None = None,
        job_id: str | None = None,
    ) -> MatlabToPythonOutcome:
        """从已完成的项目静态分析快照创建独立迁移 Job，不重复扫描源码。"""

        root = project_root.expanduser().resolve()
        scan = self._artifacts.read_model(scan_reference, ScanResult)
        analysis = self._artifacts.read_model(analysis_reference, AnalysisResult)
        if Path(scan.project_root).expanduser().resolve() != root or (
            Path(analysis.project_root).expanduser().resolve() != root
        ):
            raise OrchestrationError("项目静态分析快照与迁移目录不一致")
        job = (
            JobRecord(job_id=job_id, project_root=str(root))
            if job_id else JobRecord(project_root=str(root))
        )
        if not job.job_id.isalnum() or self._state.get_job(job.job_id):
            raise OrchestrationError("新任务 ID 无效或已存在；续跑请使用 resume")
        self._state.save_job(job)
        state: WorkflowState = {
            "job_id": job.job_id,
            "project_root": str(root),
            "target": "convert",
            "artifacts": {
                "scan_result": scan_reference,
                "analysis_result": analysis_reference,
            },
            "scan_result_ref": scan_reference,
            "analysis_result_ref": analysis_reference,
        }
        self._execute_job(
            job,
            lambda: self._convert(state, semantic_index_reference),
            interrupt_message="迁移任务已中断，可检查对应断点后恢复",
        )
        return self._migration_outcome(
            job_id=state["job_id"],
            plan_reference=state["migration_plan_ref"],
            translation_references=state.get("translation_refs", []),
            artifacts=state["artifacts"],
        )

    def resume_matlab_to_python(
        self,
        job_id: str,
        *,
        project_root: Path | None = None,
    ) -> MatlabToPythonOutcome:
        """恢复同一迁移 Job，跳过已经冻结的 WCC。"""

        job = self._state.get_job(job_id)
        if job is None:
            raise OrchestrationError(f"Job 不存在: {job_id}")
        _, checkpoint = MigrationCheckpointStore(self._artifacts).load(
            job_id
        )
        validate_scan_inventory(self._artifacts.read_model(checkpoint.scan_reference, ScanResult),
                                self._settings.project.exclude_patterns)
        root = (
            project_root.expanduser().resolve()
            if project_root is not None
            else Path(checkpoint.project_root).expanduser().resolve()
        )
        if root != Path(checkpoint.project_root).expanduser().resolve():
            raise OrchestrationError(
                f"恢复项目不匹配: checkpoint={checkpoint.project_root}, "
                f"current={root}"
            )
        result = self._execute_job(
            job,
            lambda: self._migration_agent().run(
                job_id=job_id,
                scan_reference=checkpoint.scan_reference,
                analysis_reference=checkpoint.analysis_reference,
                semantic_index_reference=checkpoint.semantic_index_reference,
                plan_reference=checkpoint.plan_reference,
                migration_state_reference=checkpoint.migration_state_reference,
                resume=True,
            ),
            interrupt_message="迁移任务已中断，可从 WCC 断点恢复",
        )
        return self._migration_outcome(
            job_id=job_id,
            plan_reference=result.plan_ref,
            translation_references=result.translation_refs,
            artifacts=result.artifacts,
        )

    def _run(
        self,
        project_root: Path,
        target: WorkflowTarget,
        semantic_progress: Callable[[SemanticProgressEvent], None] | None = None,
        semantic_index_reference: str | None = None,
        job_id: str | None = None,
    ) -> WorkflowState:
        root = project_root.expanduser().resolve()
        job = (
            JobRecord(job_id=job_id, project_root=str(root))
            if job_id else JobRecord(project_root=str(root))
        )
        if not job.job_id.isalnum() or self._state.get_job(job.job_id):
            raise OrchestrationError("新任务 ID 无效或已存在；续跑请使用 resume")
        self._state.save_job(job)
        return self._execute_job(
            job,
            lambda: self._run_pipeline(
                job,
                root,
                target,
                semantic_progress,
                semantic_index_reference,
            ),
            interrupt_message="任务已中断，可检查对应断点后恢复",
        )

    def _run_pipeline(
        self,
        job: JobRecord,
        root: Path,
        target: WorkflowTarget,
        semantic_progress: Callable[[SemanticProgressEvent], None] | None,
        semantic_index_reference: str | None,
    ) -> WorkflowState:
        """顺序连接 Analysis、Semantic 和 Migration 三条独立流水线。"""

        state: WorkflowState = {
            "job_id": job.job_id,
            "project_root": str(root),
            "target": target,
            "artifacts": {},
        }
        analysis = self._analysis_pipeline.run(
            job_id=job.job_id,
            project_root=root,
            include_dependency_analysis=target != "scan",
        )
        state["scan_result_ref"] = analysis.scan_result_ref
        state["artifacts"].update(analysis.artifacts)
        if analysis.analysis_result_ref:
            state["analysis_result_ref"] = analysis.analysis_result_ref
        if analysis.structural_code_tree_ref:
            state["structural_code_tree_ref"] = (
                analysis.structural_code_tree_ref
            )
        if target == "annotate":
            self._annotate(state, semantic_progress)
        elif target == "convert":
            self._convert(state, semantic_index_reference)
        return state

    def _execute_job(
        self,
        job: JobRecord,
        operation: Callable[[], ResultT],
        *,
        interrupt_message: str,
    ) -> ResultT:
        """统一 Job 状态迁移和异常包装。"""

        running = self._state.set_job_status(
            job, JobStatus.RUNNING, error=None
        )
        try:
            result = operation()
            self._state.set_job_status(running, JobStatus.COMPLETED)
            return result
        except KeyboardInterrupt:
            self._state.set_job_status(
                running, JobStatus.FAILED, interrupt_message
            )
            raise
        except Exception as exc:
            self._state.set_job_status(running, JobStatus.FAILED, str(exc))
            if isinstance(exc, OrchestrationError):
                raise
            raise OrchestrationError(str(exc)) from exc

    def _migration_agent(self) -> MatlabToPythonMigrationAgent:
        settings = self._settings.llm
        return MatlabToPythonMigrationAgent(
            artifacts=self._artifacts,
            client=self._client(
                self._settings.llm.migration_max_output_tokens
            ),
            reason_client=self._client(
                settings.migration_reason_max_output_tokens,
                thinking_mode="disabled",
            ),
            # Reserve output, configured safety margin and Reason schema/instruction overhead.
            reason_input_budget=max(1, min(32_768, settings.model_context_window_tokens
                - settings.migration_reason_max_output_tokens
                - settings.context_safety_margin_tokens - 4096)),
            debug_model=self._settings.logging.debug_model,
            max_concurrency=settings.migration_max_agents,
            chunk_concurrency=settings.migration_chunk_max_agents,
        )

    def _migration_outcome(
        self,
        *,
        job_id: str,
        plan_reference: str,
        translation_references: list[str],
        artifacts: dict[str, str],
    ) -> MatlabToPythonOutcome:
        return MatlabToPythonOutcome(
            job_id=job_id,
            plan=self._artifacts.read_model(
                plan_reference, MatlabToPythonPlan
            ),
            translations=[
                self._artifacts.read_model(reference, TranslationResponse)
                for reference in translation_references
            ],
            artifacts=artifacts,
        )

    def _client(
        self,
        max_output_tokens: int,
        *,
        thinking_mode: str | None = None,
    ) -> StructuredLLMClient:
        """按任务预算复用真实客户端；测试注入客户端继续覆盖全部 LLM 调用。"""

        if self._provided_llm_client is not None:
            return self._provided_llm_client
        key = (max_output_tokens, thinking_mode)
        if key not in self._llm_clients:
            self._llm_clients[key] = create_llm_client(
                self._settings.llm,
                max_output_tokens=max_output_tokens,
                thinking_mode=thinking_mode,
                debug_model=self._settings.logging.debug_model,
            )
        return self._llm_clients[key]

    def _annotate(
        self,
        state: WorkflowState,
        progress_callback: Callable[[SemanticProgressEvent], None] | None,
        *,
        resume: bool = False,
    ) -> None:
        settings = self._settings.llm
        result = SemanticAnnotationPipeline(
            artifacts=self._artifacts,
            client=self._client(settings.semantic_max_output_tokens),
            token_budget=settings.semantic_token_budget,
            hard_token_limit=settings.semantic_hard_input_tokens,
            max_functions_per_unit=settings.semantic_max_functions_per_unit,
            confidence_threshold=settings.semantic_confidence_threshold,
            max_attempts=settings.semantic_max_attempts,
            exclude_patterns=self._settings.project.exclude_patterns,
            debug_model=self._settings.logging.debug_model,
            max_concurrency=settings.semantic_max_agents,
        ).run(
            job_id=state["job_id"],
            scan_reference=state["scan_result_ref"],
            analysis_reference=state["analysis_result_ref"],
            structural_code_tree_reference=state[
                "structural_code_tree_ref"
            ],
            progress_callback=progress_callback,
            resume=resume,
        )
        state["semantic_index_ref"] = result.semantic_index_ref
        state["semantic_code_tree_ref"] = result.semantic_code_tree_ref
        state["semantic_preparation_ref"] = result.preparation_ref
        state["semantic_quality_report_ref"] = result.quality_report_ref
        state["semantic_progress_ref"] = result.progress_log_ref
        state["artifacts"].update(result.artifacts)

    def _convert(
        self,
        state: WorkflowState,
        semantic_index_reference: str | None,
    ) -> None:
        result: MigrationAgentResult = self._migration_agent().run(
            job_id=state["job_id"],
            scan_reference=state["scan_result_ref"],
            analysis_reference=state["analysis_result_ref"],
            semantic_index_reference=semantic_index_reference,
        )
        state["migration_plan_ref"] = result.plan_ref
        state["migration_state_ref"] = result.migration_state_ref
        state["translation_refs"] = result.translation_refs
        state["artifacts"].update(result.artifacts)


__all__ = ["MainWorkflow", "WorkflowState", "WorkflowTarget"]
