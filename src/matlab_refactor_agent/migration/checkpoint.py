"""
Description: 提供 WCC 迁移断点文件、源码指纹和恢复校验。
References: MigrationCheckpoint、MigrationStateStore、ArtifactStore。
Referenced By: MatlabToPythonMigrationAgent、MainWorkflow 和 CLI。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import (
    MigrationCheckpoint,
    MigrationCheckpointStatus,
    MigrationUnitState,
    MatlabToPythonPlan,
)
from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration.migration_state import (
    MigrationStateStore,
)

from .planning import MigrationPlanBuilder

CHECKPOINT_NAME = "migration-checkpoint.json"


class MigrationCheckpointStore:
    """读写同一 Job 的迁移恢复入口。"""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    def load(self, job_id: str) -> tuple[str, MigrationCheckpoint]:
        reference = self._artifacts.reference(job_id, CHECKPOINT_NAME)
        return reference, self._artifacts.read_model(
            reference, MigrationCheckpoint
        )

    def save(
        self,
        checkpoint: MigrationCheckpoint,
        states: list[MigrationUnitState],
        *,
        status: MigrationCheckpointStatus | None = None,
    ) -> str:
        updated = checkpoint.model_copy(
            update={
                "status": status or checkpoint.status,
                "completed_unit_ids": [
                    state.unit_id
                    for state in states
                    if state.status == "frozen"
                ],
            }
        )
        return self._artifacts.write_model(
            checkpoint.job_id, CHECKPOINT_NAME, updated
        )


def source_fingerprint(project_root: Path, scan: ScanResult) -> str:
    """对扫描快照内的 MATLAB 源码计算稳定 SHA-256 指纹。"""

    root = project_root.expanduser().resolve()
    digest = hashlib.sha256()
    for item in sorted(scan.files, key=lambda value: value.path.casefold()):
        relative = Path(item.path)
        source = (relative if relative.is_absolute() else root / relative).resolve()
        try:
            normalized = source.relative_to(root).as_posix()
        except ValueError as exc:
            raise OrchestrationError(
                f"扫描文件越出 MATLAB 项目目录: {source}"
            ) from exc
        if not source.is_file():
            raise OrchestrationError(f"迁移源码不存在: {source}")
        digest.update(normalized.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_resume_source(
    checkpoint: MigrationCheckpoint,
    project_root: Path,
    scan: ScanResult,
) -> None:
    """拒绝用旧 WCC 产物续跑已经改变的 MATLAB 源码。"""

    root = project_root.expanduser().resolve()
    expected_root = Path(checkpoint.project_root).expanduser().resolve()
    if root != expected_root:
        raise OrchestrationError(
            f"恢复项目不匹配: checkpoint={expected_root}, current={root}"
        )
    current = source_fingerprint(root, scan)
    if current != checkpoint.source_fingerprint:
        raise OrchestrationError(
            "MATLAB 源码自断点创建后已变化；请新建迁移任务，不能复用旧 WCC"
        )


@dataclass(frozen=True)
class MigrationSession:
    """Agent Loop 所需的稳定会话，不承载源码或模型响应正文。"""

    job_id: str
    plan: MatlabToPythonPlan
    plan_reference: str
    state_reference: str
    checkpoint: MigrationCheckpoint

    def graph_input(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "plan_ref": self.plan_reference,
            "current_chain_id": "",
            "migration_state_ref": self.state_reference,
            "retry_count": 0,
        }


@dataclass(frozen=True)
class MigrationSessionResult:
    """完成会话后交还 Agent 门面的轻量引用。"""

    plan_reference: str
    state_reference: str
    translation_references: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


class MigrationSessionManager:
    """集中处理新建、恢复、中断和完成，避免 Agent 承担持久化细节。"""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts
        self._checkpoints = MigrationCheckpointStore(artifacts)

    def open(
        self,
        *,
        job_id: str,
        scan_reference: str,
        analysis_reference: str,
        semantic_index_reference: str | None,
        plan_reference: str | None,
        state_reference: str | None,
        resume: bool,
    ) -> MigrationSession:
        analysis = self._artifacts.read_model(
            analysis_reference, AnalysisResult
        )
        if plan_reference is None:
            plan = MigrationPlanBuilder().build(analysis)
            plan_reference = self._artifacts.write_model(
                job_id, "matlab-to-python-plan.json", plan
            )
        else:
            plan = self._artifacts.read_model(
                plan_reference, MatlabToPythonPlan
            )
        scan = self._artifacts.read_model(scan_reference, ScanResult)
        if resume:
            _, previous = self._checkpoints.load(job_id)
            validate_resume_source(previous, Path(plan.project_root), scan)
        states = MigrationStateStore(
            self._artifacts, job_id, state_reference
        )
        if state_reference is None:
            state_reference = states.initialize(plan)
        elif resume:
            state_reference = states.restart_unfinished()
        self._validate_units(plan, states.load())
        checkpoint = MigrationCheckpoint(
            job_id=job_id,
            project_root=plan.project_root,
            source_fingerprint=source_fingerprint(
                Path(plan.project_root), scan
            ),
            scan_reference=scan_reference,
            analysis_reference=analysis_reference,
            semantic_index_reference=semantic_index_reference,
            plan_reference=plan_reference,
            migration_state_reference=state_reference,
        )
        self._checkpoints.save(checkpoint, states.load(), status="running")
        return MigrationSession(
            job_id=job_id,
            plan=plan,
            plan_reference=plan_reference,
            state_reference=state_reference,
            checkpoint=checkpoint,
        )

    def interrupt(self, session: MigrationSession, *, current_chain_id: str | None = None) -> None:
        store = MigrationStateStore(
            self._artifacts, session.job_id, session.state_reference
        )
        states = store.load()
        if current_chain_id and any(
            item.unit_id == current_chain_id and item.status != "frozen" for item in states
        ):
            store.update(current_chain_id, status="failed")
            states = store.load()
        self._checkpoints.save(
            session.checkpoint, states, status="interrupted"
        )

    def complete(
        self,
        session: MigrationSession,
        state_reference: str,
        runtime_artifacts: dict[str, str],
    ) -> MigrationSessionResult:
        states = MigrationStateStore(
            self._artifacts, session.job_id, state_reference
        ).load()
        checkpoint = session.checkpoint.model_copy(
            update={"migration_state_reference": state_reference}
        )
        checkpoint_reference = self._checkpoints.save(
            checkpoint, states, status=self._status(states)
        )
        translation_references = [
            state.translation_ref for state in states if state.translation_ref
        ]
        artifacts = {
            "migration_plan": session.plan_reference,
            "migration_state": state_reference,
            "migration_checkpoint": checkpoint_reference,
            **runtime_artifacts,
            **{
                f"translation_{state.unit_id}": state.translation_ref
                for state in states
                if state.translation_ref
            },
        }
        return MigrationSessionResult(
            plan_reference=session.plan_reference,
            state_reference=state_reference,
            translation_references=translation_references,
            artifacts=artifacts,
        )

    @staticmethod
    def _validate_units(
        plan: MatlabToPythonPlan, states: list[MigrationUnitState]
    ) -> None:
        if {unit.unit_id for unit in plan.units} != {
            state.unit_id for state in states
        }:
            raise OrchestrationError("迁移计划与 WCC checkpoint 不匹配")

    @staticmethod
    def _status(
        states: list[MigrationUnitState],
    ) -> MigrationCheckpointStatus:
        if states and all(state.status == "frozen" for state in states):
            return "completed"
        if any(state.status == "manual_review" for state in states):
            return "manual_review"
        return "interrupted"


__all__ = [
    "CHECKPOINT_NAME",
    "MigrationCheckpointStore",
    "MigrationSession",
    "MigrationSessionManager",
    "MigrationSessionResult",
    "source_fingerprint",
    "validate_resume_source",
]
