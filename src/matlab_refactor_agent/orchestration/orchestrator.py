"""
Description: 创建 Job、安排阶段任务并控制 Worker、状态、冲突和质量门禁。
References: TaskQueue、WorkerPool、SQLiteStateManager、ArtifactStore、Workers。
Referenced By: application.services 和 Orchestrator 集成测试。
"""

from __future__ import annotations

from pathlib import Path

from matlab_refactor_agent.domain.enums import JobStatus, TaskStatus, WorkerKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
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
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.workers import (
    AnalyzerAgent,
    ExecutorAgent,
    ParserAgent,
    PlannerAgent,
    ReporterAgent,
    ScannerAgent,
    ValidatorAgent,
    WorkerContext,
)

from .conflict_resolver import ConflictResolver
from .quality_gate import QualityGate
from .state_manager import SQLiteStateManager
from .task_queue import TaskQueue
from .worker_pool import WorkerPool


class Orchestrator:
    """作用：控制多 Worker 流水线和全局状态；输入：任务组件与项目请求；输出：Scan/AnalysisOutcome；数据流：TaskQueue -> WorkerPool -> QualityGate -> StateManager。"""

    def __init__(
        self,
        queue: TaskQueue,
        pool: WorkerPool,
        state: SQLiteStateManager,
        conflicts: ConflictResolver,
        quality_gate: QualityGate,
        artifacts: ArtifactStore,
        parser_chunk_size: int,
    ) -> None:
        """作用：注入调度组件；输入：队列、Worker 池、状态、冲突、门禁和 artifacts；输出：Orchestrator；数据流：应用工厂 -> 可运行调度器。"""

        self._queue = queue
        self._pool = pool
        self._state = state
        self._conflicts = conflicts
        self._quality_gate = quality_gate
        self._artifacts = artifacts
        self._parser_chunk_size = parser_chunk_size
        self._context = WorkerContext(artifact_store=artifacts)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "Orchestrator":
        """作用：按配置组装默认调度器；输入：AppSettings；输出：Orchestrator；数据流：配置 -> State/Artifacts/七类 Worker -> 组件实例。"""

        artifacts = ArtifactStore(settings.orchestrator.artifact_dir)
        pool = WorkerPool(settings.orchestrator.max_workers)
        for worker in (
            ScannerAgent(settings.project.exclude_patterns),
            ParserAgent(),
            AnalyzerAgent(settings.project.entry_points),
            PlannerAgent(),
            ExecutorAgent(),
            ValidatorAgent(),
            ReporterAgent(),
        ):
            pool.register(worker)
        return cls(
            queue=TaskQueue(),
            pool=pool,
            state=SQLiteStateManager(settings.orchestrator.state_db),
            conflicts=ConflictResolver(),
            quality_gate=QualityGate(artifacts),
            artifacts=artifacts,
            parser_chunk_size=settings.orchestrator.parser_chunk_size,
        )

    def run_scan(self, project_root: Path) -> ScanOutcome:
        """作用：运行扫描、解析 fan-out 和聚合 Job；输入：项目根目录；输出：ScanOutcome；数据流：Worker-1 manifest -> Worker-2 chunks/fan-in -> ScanResult。"""

        job = self._start_job(project_root)
        try:
            scan, artifacts, _ = self._run_ingestion(job, project_root)
            self._state.set_job_status(job, JobStatus.COMPLETED)
            return ScanOutcome(
                job_id=job.job_id,
                result=scan,
                artifacts=artifacts,
            )
        except Exception as exc:
            self._fail_job(job, exc)
            raise self._as_orchestration_error(exc) from exc

    def run_analysis(self, project_root: Path) -> AnalysisOutcome:
        """作用：运行扫描、分片解析、聚合和图分析；输入：项目根目录；输出：AnalysisOutcome；数据流：manifest -> parser fan-out/fan-in -> Analyzer。"""

        job = self._start_job(project_root)
        try:
            _, ingestion_artifacts, ingestion_task_id = self._run_ingestion(
                job, project_root
            )
            analyze_task = TaskEnvelope(
                job_id=job.job_id,
                worker_kind=WorkerKind.ANALYZER,
                payload={"scan_result": ingestion_artifacts["scan_result"]},
                depends_on=[ingestion_task_id],
                priority=40,
            )
            analysis_result = self._execute_task(
                analyze_task, "analysis-result.json"
            )
            analysis = self._artifacts.read_model(
                analysis_result.artifacts["analysis_result"], AnalysisResult
            )
            self._state.set_job_status(job, JobStatus.COMPLETED)
            artifacts = {**ingestion_artifacts, **analysis_result.artifacts}
            return AnalysisOutcome(
                job_id=job.job_id,
                result=analysis,
                artifacts=artifacts,
            )
        except Exception as exc:
            self._fail_job(job, exc)
            raise self._as_orchestration_error(exc) from exc

    def _run_ingestion(
        self, job: JobRecord, project_root: Path
    ) -> tuple[ScanResult, dict[str, str], str]:
        """作用：执行 Scanner -> Parser fan-out -> Parser aggregate；输入：Job 和项目路径；输出：ScanResult、artifacts、聚合 Task ID；数据流：manifest -> chunks -> fan-in。"""

        scan_task = TaskEnvelope(
            job_id=job.job_id,
            worker_kind=WorkerKind.SCANNER,
            payload={"project_root": str(project_root.expanduser().resolve())},
            priority=10,
        )
        scan_worker_result = self._execute_task(scan_task, "file-manifest.json")
        manifest_reference = scan_worker_result.artifacts["file_manifest"]
        manifest = self._artifacts.read_model(
            manifest_reference, MatlabFileManifest
        )
        file_chunks = [
            manifest.files[index : index + self._parser_chunk_size]
            for index in range(0, len(manifest.files), self._parser_chunk_size)
        ]
        parse_tasks = [
            TaskEnvelope(
                job_id=job.job_id,
                worker_kind=WorkerKind.PARSER,
                payload={
                    "operation": "parse_chunk",
                    "file_manifest": manifest_reference,
                    "chunk_index": index,
                    "files": files,
                },
                depends_on=[scan_task.task_id],
                priority=20,
            )
            for index, files in enumerate(file_chunks)
        ]
        parse_results = self._execute_batch(
            [
                (task, f"parse-chunk-{index:05d}.json")
                for index, task in enumerate(parse_tasks)
            ]
        )
        chunk_references = [
            result.artifacts[f"parse_chunk_{index}"]
            for index, result in enumerate(parse_results)
        ]
        aggregate_task = TaskEnvelope(
            job_id=job.job_id,
            worker_kind=WorkerKind.PARSER,
            payload={
                "operation": "aggregate",
                "file_manifest": manifest_reference,
                "chunk_results": chunk_references,
            },
            depends_on=[task.task_id for task in parse_tasks]
            or [scan_task.task_id],
            priority=30,
        )
        aggregate_result = self._execute_task(aggregate_task, "scan-result.json")
        scan = self._artifacts.read_model(
            aggregate_result.artifacts["scan_result"], ScanResult
        )
        artifacts = {
            **scan_worker_result.artifacts,
            **{
                key: value
                for result in parse_results
                for key, value in result.artifacts.items()
            },
            **aggregate_result.artifacts,
        }
        return scan, artifacts, aggregate_task.task_id

    def _start_job(self, project_root: Path) -> JobRecord:
        """作用：创建并启动 Job；输入：项目根目录；输出：RUNNING JobRecord；数据流：请求 -> SQLite created/running 状态。"""

        job = JobRecord(project_root=str(project_root.expanduser().resolve()))
        self._state.save_job(job)
        return self._state.set_job_status(job, JobStatus.RUNNING)

    def _execute_task(self, task: TaskEnvelope, artifact_name: str) -> WorkerResult:
        """作用：执行受状态、冲突和质量控制的任务；输入：任务和目标 artifact 名；输出：WorkerResult；数据流：入队 -> Worker -> QualityGate -> 持久化。"""

        return self._execute_batch([(task, artifact_name)])[0]

    def _execute_batch(
        self, tasks: list[tuple[TaskEnvelope, str]]
    ) -> list[WorkerResult]:
        """作用：先批量入队再执行独立任务；输入：任务与输出名称列表；输出：按输入顺序的 WorkerResult；数据流：fan-out submit -> WorkerPool -> result fan-in。"""

        for task, artifact_name in tasks:
            self._submit_task(task, artifact_name)
        running_tasks = [
            self._state.set_task_status(self._queue.get(), TaskStatus.RUNNING)
            for _ in tasks
        ]
        outcomes = self._pool.execute_many(running_tasks, self._context)
        results: dict[str, WorkerResult] = {}
        errors: list[Exception] = []
        for running in running_tasks:
            outcome = outcomes[running.task_id]
            try:
                if isinstance(outcome, Exception):
                    raise outcome
                self._quality_gate.validate(running, outcome)
                self._state.set_task_status(
                    running, TaskStatus.COMPLETED, outcome
                )
                results[running.task_id] = outcome
            except Exception as exc:
                failure = WorkerResult(
                    task_id=running.task_id,
                    success=False,
                    diagnostics=[str(exc)],
                )
                self._state.set_task_status(running, TaskStatus.FAILED, failure)
                errors.append(exc)
            finally:
                self._conflicts.release(running.task_id)
        if errors:
            raise errors[0]
        return [results[task.task_id] for task, _ in tasks]

    def _submit_task(self, task: TaskEnvelope, artifact_name: str) -> None:
        """作用：声明输出并持久化入队任务；输入：TaskEnvelope 和 artifact 名；输出：无；数据流：冲突声明 -> StateManager -> TaskQueue。"""

        claim = PathClaim(
            task_id=task.task_id,
            path=f"{task.job_id}/{artifact_name}",
            operation="write",
        )
        self._conflicts.claim(claim)
        self._queue.put(task)
        self._state.save_task(task)

    def _fail_job(self, job: JobRecord, error: Exception) -> None:
        """作用：持久化 Job 失败；输入：Job 和异常；输出：无；数据流：异常 -> error 文本 -> SQLite failed 状态。"""

        self._state.set_job_status(job, JobStatus.FAILED, str(error))

    def _as_orchestration_error(self, error: Exception) -> OrchestrationError:
        """作用：统一调度错误边界；输入：任意异常；输出：OrchestrationError；数据流：Worker/基础设施异常 -> 应用层可处理异常。"""

        if isinstance(error, OrchestrationError):
            return error
        return OrchestrationError(str(error))
