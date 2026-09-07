"""
Description: 为代码树和三级语义应用提供独立用例门面。
References: AppSettings、Orchestrator、分析与语义领域模型。
Referenced By: Semantic Web API、CLI 和兼容 AnalysisService。
"""

from pathlib import Path
from collections.abc import Callable

from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.orchestration import new_job_id
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.domain.semantics import SemanticProgressEvent
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.orchestration import Orchestrator


class SemanticService:
    """只公开确定性分析和三级语义用例，不暴露迁移操作。"""

    def __init__(self, settings: AppSettings) -> None:
        self._orchestrator = Orchestrator.from_settings(settings)
        self.last_job_id: str | None = None
        self.last_artifacts: dict[str, str] = {}

    def scan(self, project_root: Path) -> ScanResult:
        outcome = self._orchestrator.run_scan(project_root)
        self._remember(outcome.job_id, outcome.artifacts)
        return outcome.result

    def analyze(self, project_root: Path) -> AnalysisResult:
        outcome = self._orchestrator.run_analysis(project_root)
        self._remember(outcome.job_id, outcome.artifacts)
        return outcome.result

    def annotate(
        self,
        project_root: Path,
        progress_callback: Callable[[SemanticProgressEvent], None] | None = None,
        *,
        job_id: str | None = None,
    ) -> SemanticIndex:
        self.last_job_id = job_id or new_job_id()
        outcome = self._orchestrator.run_annotation(
            project_root, progress_callback=progress_callback, job_id=self.last_job_id
        )
        self._remember(outcome.job_id, outcome.artifacts)
        return outcome.index

    def resume_annotation(self, job_id: str, *, project_root: Path | None = None,
                          progress_callback: Callable[[SemanticProgressEvent], None] | None = None) -> SemanticIndex:
        self.last_job_id = job_id
        outcome = self._orchestrator.resume_annotation(
            job_id, project_root=project_root, progress_callback=progress_callback)
        self._remember(outcome.job_id, outcome.artifacts)
        return outcome.index

    def _remember(self, job_id: str, artifacts: dict[str, str]) -> None:
        self.last_job_id = job_id
        self.last_artifacts = artifacts
