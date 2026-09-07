"""
Description: 验证 WCC checkpoint 持久化和 MATLAB 源码变更保护。
References: migration.checkpoint、MigrationCheckpointStore。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

import pytest

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import (
    MigrationCheckpoint,
    MigrationUnitState,
)
from matlab_refactor_agent.domain.models import MatlabFileInfo, ScanResult
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.migration.checkpoint import (
    MigrationCheckpointStore,
    source_fingerprint,
    validate_resume_source,
)


def test_checkpoint_records_frozen_wcc_and_rejects_changed_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.m"
    source.write_text("function y = main(x)\ny = x;\nend\n", encoding="utf-8")
    scan = ScanResult(
        project_root=str(project), files=[MatlabFileInfo(path="main.m")]
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    checkpoint = MigrationCheckpoint(
        job_id="resumejob",
        project_root=str(project),
        source_fingerprint=source_fingerprint(project, scan),
        scan_reference="scan.json",
        analysis_reference="analysis.json",
        plan_reference="plan.json",
        migration_state_reference="state.json",
    )
    checkpoints = MigrationCheckpointStore(artifacts)

    reference = checkpoints.save(
        checkpoint,
        [
            MigrationUnitState(unit_id="done", status="frozen"),
            MigrationUnitState(unit_id="todo", status="pending"),
        ],
        status="interrupted",
    )
    loaded_reference, loaded = checkpoints.load("resumejob")

    assert loaded_reference == reference
    assert loaded.status == "interrupted"
    assert loaded.completed_unit_ids == ["done"]
    validate_resume_source(loaded, project, scan)

    source.write_text("function y = main(x)\ny = x + 1;\nend\n", encoding="utf-8")
    with pytest.raises(OrchestrationError, match="源码.*变化"):
        validate_resume_source(loaded, project, scan)
