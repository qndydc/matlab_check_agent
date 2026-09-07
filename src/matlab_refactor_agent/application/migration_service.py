"""
Description: 为 MATLAB 到 Python 迁移子工程隔离实验性用例。
References: AppSettings、Orchestrator、MatlabToPythonOutcome。
Referenced By: Migration API、CLI 和迁移集成测试。
"""

from pathlib import Path

from matlab_refactor_agent.domain.migration import MatlabToPythonOutcome
from matlab_refactor_agent.domain.orchestration import new_job_id
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration import Orchestrator
from matlab_refactor_agent.migration.checkpoint import CHECKPOINT_NAME


class MigrationService:
    """CLI 和 Web 共用的迁移用例，不进入语义应用 API。"""

    def __init__(self, settings: AppSettings) -> None:
        self._orchestrator = Orchestrator.from_settings(settings)
        self._artifacts = ArtifactStore(settings.orchestrator.artifact_dir)
        self.last_job_id: str | None = None
        self.last_checkpoint_reference: str | None = None
        self.last_artifacts: dict[str, str] = {}

    def generate_translation_artifacts(
        self,
        project_root: Path,
        *,
        semantic_index_reference: str | None = None,
        scan_reference: str | None = None,
        analysis_reference: str | None = None,
        job_id: str | None = None,
    ) -> MatlabToPythonOutcome:
        """运行迁移 Agent；语义索引可选且不触发 Semantic Pipeline。"""

        root = project_root.expanduser().resolve()
        self.last_job_id = job_id or new_job_id()
        try:
            semantic_index_reference = self._snapshot_semantics(root, semantic_index_reference)
            if bool(scan_reference) != bool(analysis_reference):
                raise OrchestrationError("复用静态分析时必须同时提供 scan 和 analysis 引用")
            if scan_reference and analysis_reference:
                outcome = self._orchestrator.run_matlab_to_python_from_analysis(
                    root,
                    scan_reference=scan_reference,
                    analysis_reference=analysis_reference,
                    semantic_index_reference=semantic_index_reference,
                    job_id=self.last_job_id,
                )
            else:
                outcome = self._orchestrator.run_matlab_to_python(
                    root,
                    semantic_index_reference=semantic_index_reference,
                    job_id=self.last_job_id,
                )
        finally:
            self._remember_checkpoint()
        self.last_job_id = outcome.job_id
        self.last_artifacts = outcome.artifacts
        self.last_checkpoint_reference = outcome.artifacts.get(
            "migration_checkpoint"
        )
        return outcome

    def _snapshot_semantics(self, root: Path, reference: str | None) -> str | None:
        """CLI 的外部索引先校验并复制到本 Job，续跑不依赖原文件位置。"""
        if reference is None:
            return None
        try:
            index = SemanticIndex.model_validate_json(
                Path(reference).expanduser().read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise OrchestrationError("SemanticIndex 无效或不可读取") from exc
        if Path(index.project_root).expanduser().resolve() != root:
            raise OrchestrationError("SemanticIndex 所属项目与迁移目录不一致")
        return self._artifacts.write_model(self.last_job_id, "input-semantic-index.json", index)

    def resume_translation_artifacts(
        self,
        job_id: str,
        *,
        project_root: Path | None = None,
    ) -> MatlabToPythonOutcome:
        """从既有 Job 的首个未完成 WCC 继续迁移。"""

        self.last_job_id = job_id
        outcome = self._orchestrator.resume_matlab_to_python(
            job_id, project_root=project_root
        )
        self.last_artifacts = outcome.artifacts
        self.last_checkpoint_reference = outcome.artifacts.get(
            "migration_checkpoint"
        )
        return outcome

    def _remember_checkpoint(self) -> None:
        if self.last_job_id is None:
            return
        try:
            self.last_checkpoint_reference = self._artifacts.reference(
                self.last_job_id, CHECKPOINT_NAME
            )
        except Exception:
            self.last_checkpoint_reference = None
