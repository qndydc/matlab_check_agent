"""
Description: 组合语义准备、簇注释、自检重试和函数/文件/项目三级聚合。
References: ClusterSemanticAnnotator、SemanticQualityReviewer、SemanticAnnotationGraph。
Referenced By: MainWorkflow 和 SemanticService。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from matlab_refactor_agent.domain.models import AnalysisResult
from matlab_refactor_agent.domain.semantics import (
    SemanticIndex,
    SemanticPreparationBundle,
    SemanticProgressEvent,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.orchestration.quality_gate import QualityGate
from matlab_refactor_agent.orchestration.semantic_graph import (
    SemanticAnnotationGraph,
)
from matlab_refactor_agent.orchestration.semantic_preparation import (
    SemanticPreparation,
)
from matlab_refactor_agent.workers.code_tree import CodeTreeBuilder

from .aggregator import SemanticAggregator
from .annotator import ClusterSemanticAnnotator
from .quality import SemanticQualityReviewer
from .checkpoint import SemanticCheckpoint, source_snapshot, validate_source


@dataclass(frozen=True)
class SemanticPipelineResult:
    """三级语义流水线的独立产物集合。"""

    semantic_index_ref: str
    semantic_code_tree_ref: str
    preparation_ref: str
    work_units_ref: str
    conflicts_ref: str
    quality_report_ref: str
    progress_log_ref: str
    artifacts: dict[str, str] = field(default_factory=dict)


class SemanticAnnotationPipeline:
    """固定执行三级语义生成与有限自检，不承担开放式任务决策。"""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        client: StructuredLLMClient,
        token_budget: int,
        hard_token_limit: int,
        max_functions_per_unit: int,
        confidence_threshold: float,
        max_attempts: int,
        exclude_patterns: list[str] | None = None,
        debug_model: bool = False,
        max_concurrency: int = 1,
    ) -> None:
        self._artifacts = artifacts
        self._client = client
        self._token_budget = token_budget
        self._hard_token_limit = hard_token_limit
        self._max_functions_per_unit = max_functions_per_unit
        self._confidence_threshold = confidence_threshold
        self._max_attempts = max_attempts
        self._exclude_patterns = exclude_patterns or [".git", "slprj", "build"]
        self._debug_model = debug_model
        self._max_concurrency = max(1, max_concurrency)

    def run(
        self,
        *,
        job_id: str,
        scan_reference: str,
        analysis_reference: str,
        structural_code_tree_reference: str,
        progress_callback: Callable[[SemanticProgressEvent], None] | None = None,
        resume: bool = False,
    ) -> SemanticPipelineResult:
        """执行准备、注释、自检、重试和三级聚合。"""

        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][PIPELINE] job={job_id} "
                f"resume={resume} token_budget={self._token_budget} "
                f"max_functions_per_unit={self._max_functions_per_unit}",
                flush=True,
            )

        checkpoint_reference = self._artifacts.root / job_id / "semantic-checkpoint.json"
        if resume:
            checkpoint = self._artifacts.read_model(str(checkpoint_reference), SemanticCheckpoint)
            preparation_reference = checkpoint.preparation_reference
            bundle = self._artifacts.read_model(preparation_reference, SemanticPreparationBundle)
            validate_source(checkpoint, Path(bundle.project_root))
        else:
            preparation_reference = self._prepare(job_id, scan_reference, analysis_reference,
                                                  structural_code_tree_reference)
            bundle = self._artifacts.read_model(preparation_reference, SemanticPreparationBundle)
            self._artifacts.write_model(job_id, "semantic-checkpoint.json", SemanticCheckpoint(
                preparation_reference=preparation_reference,
                source_fingerprint=source_snapshot(Path(bundle.project_root), self._exclude_patterns),
                exclude_patterns=self._exclude_patterns,
            ))
        graph_state = SemanticAnnotationGraph(
            self._artifacts,
            ClusterSemanticAnnotator(
                self._client, self._artifacts, debug_model=self._debug_model
            ),
            SemanticAggregator(
                self._client,
                low_confidence_threshold=self._confidence_threshold,
                artifacts=self._artifacts,
                job_id=job_id,
                debug_model=self._debug_model,
                max_concurrency=self._max_concurrency,
            ),
            confidence_threshold=self._confidence_threshold,
            max_attempts=self._max_attempts,
            quality_reviewer=SemanticQualityReviewer(self._confidence_threshold),
            debug_model=self._debug_model,
        ).invoke(preparation_reference, event_handler=progress_callback, resume=resume)

        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][SEMANTIC][PIPELINE] job={job_id} "
                "LangGraph 已结束，正在组装最终语义产物",
                flush=True,
            )

        return self._result(job_id, preparation_reference, analysis_reference, graph_state)

    def _prepare(self, job_id: str, scan_reference: str, analysis_reference: str,
                 structural_code_tree_reference: str) -> str:
        return SemanticPreparation(
            self._artifacts, QualityGate(self._artifacts)
        ).prepare(
            job_id=job_id,
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            code_tree_reference=structural_code_tree_reference,
            token_budget=self._token_budget,
            hard_token_limit=self._hard_token_limit,
            max_functions_per_unit=self._max_functions_per_unit,
        )

    def _result(self, job_id: str, preparation_reference: str,
                analysis_reference: str, graph_state: dict) -> SemanticPipelineResult:
        index_reference = graph_state["semantic_index_ref"]
        index = self._artifacts.read_model(index_reference, SemanticIndex)
        analysis = self._artifacts.read_model(
            analysis_reference, AnalysisResult
        )
        semantic_tree = CodeTreeBuilder().build(
            analysis,
            summaries={item.symbol_id: item.summary for item in index.functions},
            file_summaries={item.file_path: item.role for item in index.files},
            project_summary=index.project.purpose,
        )
        semantic_tree_reference = self._artifacts.write_model(
            job_id, "semantic-code-tree.json", semantic_tree
        )
        preparation = self._artifacts.read_model(
            preparation_reference, SemanticPreparationBundle
        )
        artifacts = {
            "semantic_checkpoint": self._artifacts.reference(job_id, "semantic-checkpoint.json"),
            "semantic_preparation": preparation_reference,
            "semantic_work_units": preparation.work_units_reference,
            "semantic_index": index_reference,
            "semantic_code_tree": semantic_tree_reference,
            "semantic_conflicts": graph_state["conflicts_ref"],
            "semantic_quality_report": graph_state["quality_report_ref"],
            "semantic_progress": graph_state["progress_log_ref"],
        }
        call_log = self._artifacts.root / job_id / "call-observations.json"
        if call_log.is_file():
            artifacts["call_observations"] = str(call_log)
        return SemanticPipelineResult(
            semantic_index_ref=index_reference,
            semantic_code_tree_ref=semantic_tree_reference,
            preparation_ref=preparation_reference,
            work_units_ref=preparation.work_units_reference,
            conflicts_ref=graph_state["conflicts_ref"],
            quality_report_ref=graph_state["quality_report_ref"],
            progress_log_ref=graph_state["progress_log_ref"],
            artifacts=artifacts,
        )


__all__ = ["SemanticAnnotationPipeline", "SemanticPipelineResult"]
