"""
Description: 编排扫描、解析、调用图分析和结构代码树生成，不包含任何 LLM 或 Agent 决策。
References: WorkerPool、QualityGate、ArtifactStore、确定性 Workers。
Referenced By: MainWorkflow、SemanticService 和 MigrationService。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from matlab_refactor_agent.domain.code_tree import CodeTreeDocument
from matlab_refactor_agent.domain.enums import TaskStatus, WorkerKind
from matlab_refactor_agent.domain.models import (
    AnalysisResult,
    MatlabFileManifest,
    ScanResult,
)
from matlab_refactor_agent.domain.orchestration import TaskEnvelope, WorkerResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration.quality_gate import QualityGate
from matlab_refactor_agent.orchestration.state_manager import SQLiteStateManager
from matlab_refactor_agent.orchestration.worker_pool import WorkerPool
from matlab_refactor_agent.workers import CodeTreeBuilder, WorkerContext


@dataclass(frozen=True)
class AnalysisPipelineResult:
    """保存确定性分析产物引用；大型结果仍由 ArtifactStore 承载。"""

    job_id: str
    project_root: str
    scan_result_ref: str
    analysis_result_ref: str | None = None
    structural_code_tree_ref: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


class AnalysisPipeline:
    """稳定执行 Scanner → Parser → Dependency Analyzer → Code Tree。"""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        state: SQLiteStateManager,
        pool: WorkerPool,
        parser_chunk_size: int,
    ) -> None:
        self._artifacts = artifacts
        self._state = state
        self._pool = pool
        self._chunk_size = parser_chunk_size
        self._context = WorkerContext(artifact_store=artifacts)
        self._quality_gate = QualityGate(artifacts)

    def run(
        self,
        *,
        job_id: str,
        project_root: Path,
        include_dependency_analysis: bool,
    ) -> AnalysisPipelineResult:
        """生成扫描解析结果，并按需继续生成调用图和纯结构代码树。"""

        artifacts: dict[str, str] = {}
        scan_reference = self._scan_and_parse(job_id, project_root, artifacts)
        if not include_dependency_analysis:
            self._include_call_log(job_id, artifacts)
            return AnalysisPipelineResult(
                job_id=job_id,
                project_root=str(project_root),
                scan_result_ref=scan_reference,
                artifacts=artifacts,
            )

        analysis_reference, tree_reference = self._analyze(
            job_id, scan_reference
        )
        artifacts.update(
            {
                "analysis_result": analysis_reference,
                "structural_code_tree": tree_reference,
                "code_tree": tree_reference,
            }
        )
        self._include_call_log(job_id, artifacts)
        return AnalysisPipelineResult(
            job_id=job_id,
            project_root=str(project_root),
            scan_result_ref=scan_reference,
            analysis_result_ref=analysis_reference,
            structural_code_tree_ref=tree_reference,
            artifacts=artifacts,
        )

    def _execute(self, task: TaskEnvelope) -> WorkerResult:
        self._state.save_task(task)
        running = self._state.set_task_status(task, TaskStatus.RUNNING)
        try:
            result = self._pool.execute(running, self._context)
            self._quality_gate.validate(running, result)
            self._state.set_task_status(running, TaskStatus.COMPLETED, result)
            return result
        except Exception:
            self._state.set_task_status(
                running,
                TaskStatus.FAILED,
                WorkerResult(task_id=running.task_id, success=False),
            )
            raise

    def _scan_and_parse(
        self, job_id: str, project_root: Path, artifacts: dict[str, str]
    ) -> str:
        scan_task = TaskEnvelope(
            job_id=job_id,
            worker_kind=WorkerKind.SCANNER,
            payload={"project_root": str(project_root)},
        )
        scanned = self._execute(scan_task)
        manifest_reference = scanned.artifacts["file_manifest"]
        manifest = self._artifacts.read_model(
            manifest_reference, MatlabFileManifest
        )
        tasks = [
            TaskEnvelope(
                job_id=job_id,
                worker_kind=WorkerKind.PARSER,
                payload={
                    "operation": "parse_chunk",
                    "file_manifest": manifest_reference,
                    "chunk_index": index,
                    "files": manifest.files[offset : offset + self._chunk_size],
                },
                depends_on=[scan_task.task_id],
            )
            for index, offset in enumerate(
                range(0, len(manifest.files), self._chunk_size)
            )
        ]
        with ThreadPoolExecutor(max_workers=self._pool.max_workers) as executor:
            results = list(executor.map(self._execute, tasks))
        chunk_references = [
            result.artifacts[f"parse_chunk_{index}"]
            for index, result in enumerate(results)
        ]
        artifacts.update(
            {
                f"parse_chunk_{index}": reference
                for index, reference in enumerate(chunk_references)
            }
        )
        aggregated = self._execute(
            TaskEnvelope(
                job_id=job_id,
                worker_kind=WorkerKind.PARSER,
                payload={
                    "operation": "aggregate",
                    "file_manifest": manifest_reference,
                    "chunk_results": chunk_references,
                },
                depends_on=[task.task_id for task in tasks],
            )
        )
        scan_reference = aggregated.artifacts["scan_result"]
        artifacts.update(
            {
                "file_manifest": manifest_reference,
                "scan_result": scan_reference,
            }
        )
        return scan_reference

    def _analyze(self, job_id: str, scan_reference: str) -> tuple[str, str]:
        result = self._execute(
            TaskEnvelope(
                job_id=job_id,
                worker_kind=WorkerKind.ANALYZER,
                payload={"scan_result": scan_reference},
            )
        )
        analysis_reference = result.artifacts["analysis_result"]
        analysis = self._artifacts.read_model(
            analysis_reference, AnalysisResult
        )
        tree = CodeTreeBuilder().build(analysis)
        tree_reference = self._artifacts.write_model(
            job_id, "structural-code-tree.json", tree
        )
        return analysis_reference, tree_reference

    def read_scan(self, reference: str) -> ScanResult:
        return self._artifacts.read_model(reference, ScanResult)

    def read_analysis(self, reference: str) -> AnalysisResult:
        return self._artifacts.read_model(reference, AnalysisResult)

    def read_code_tree(self, reference: str) -> CodeTreeDocument:
        return self._artifacts.read_model(reference, CodeTreeDocument)

    def _include_call_log(self, job_id: str, artifacts: dict[str, str]) -> None:
        path = self._artifacts.root / job_id / "call-observations.json"
        if path.is_file():
            artifacts["call_observations"] = str(path)


__all__ = ["AnalysisPipeline", "AnalysisPipelineResult"]
