"""
Description: 验证迁移状态、组装、差分和诊断组件。
References: 迁移领域模型、Workers、ArtifactStore。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

import pytest

from matlab_refactor_agent.domain.contracts import BehaviorContract, FileSideEffect, ValueContract
from matlab_refactor_agent.domain.diagnostics import ExecutionResult
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import (
    GeneratedPythonFile, MatlabToPythonPlan, MigrationUnitState,
    MigrationWorkUnit, PythonArchitecture, TranslationResponse,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration.migration_state import MigrationScheduler, MigrationStateStore
from matlab_refactor_agent.workers.differential_validator import DifferentialValidator
from matlab_refactor_agent.workers.failure_classifier import FailureClassifier
from matlab_refactor_agent.workers.python_project_assembler import PythonProjectAssembler


def test_state_store_scheduler_and_affected_upstream(tmp_path: Path) -> None:
    plan = MatlabToPythonPlan(
        project_root="project", architecture=PythonArchitecture(package_name="p", source_directory="src/p"),
        units=[MigrationWorkUnit(unit_id="dep", symbol_ids=["dep"]),
               MigrationWorkUnit(unit_id="caller", symbol_ids=["caller"], depends_on_units=["dep"])],
    )
    store = MigrationStateStore(ArtifactStore(tmp_path / "artifacts"), "job1")
    store.initialize(plan)
    assert [item.unit_id for item in MigrationScheduler.ready(store.load())] == ["dep"]
    store.update("dep", status="frozen")
    assert [item.unit_id for item in MigrationScheduler.ready(store.load())] == ["caller"]
    assert MigrationScheduler.affected_upstream(store.load(), "dep") == {"caller"}


def test_assembler_writes_valid_files_and_rejects_escape(tmp_path: Path) -> None:
    assembler = PythonProjectAssembler(tmp_path / "generated")
    response = TranslationResponse(unit_id="u", confidence=1,
        files=[GeneratedPythonFile(path="src/pkg/a.py", content="def a():\n    return 1\n", symbol_ids=["a"])])
    paths = assembler.assemble(response)
    assert paths[0].read_text(encoding="utf-8").startswith("def a")
    unsafe = response.model_copy(update={"files": [GeneratedPythonFile(
        path="../a.py", content="x = 1", symbol_ids=["a"])]})
    with pytest.raises(OrchestrationError):
        assembler.assemble(unsafe)


def test_differential_validation_and_failure_classification() -> None:
    contract = BehaviorContract(unit_id="u", outputs=[ValueContract(
        name="y", shape=[2], dtype="float64", absolute_tolerance=1e-6)],
        file_side_effects=[FileSideEffect(path="result.txt", operation="create")])
    matlab = ExecutionResult(succeeded=True, outputs=[[1.0, 2.0]], output_shapes=[[2]],
                             output_dtypes=["float64"], file_side_effects=[{"path": "result.txt", "operation": "create"}])
    python = matlab.model_copy(update={"outputs": [[1.0, 3.0]]})
    observation = DifferentialValidator().validate(contract, matlab, python)
    assert not observation.passed
    diagnostic = FailureClassifier().classify(observation)
    assert diagnostic.category == "numeric"
    assert diagnostic.action == "repair"


def test_scheduler_requires_frozen_dependencies() -> None:
    states = [MigrationUnitState(unit_id="a", status="verified"),
              MigrationUnitState(unit_id="b", depends_on_units=["a"])]
    assert MigrationScheduler.ready(states) == []


def test_restart_unfinished_preserves_frozen_and_complete_review_wcc(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    store = MigrationStateStore(artifacts, "resumejob")
    store.save([
        MigrationUnitState(
            unit_id="done", status="frozen", translation_ref="done.json"
        ),
        MigrationUnitState(
            unit_id="partial",
            status="generated",
            translation_ref="partial.json",
            retry_count=1,
        ),
        MigrationUnitState(unit_id="todo", status="pending"),
        MigrationUnitState(
            unit_id="review", status="manual_review",
            translation_ref="review-translation.json",
            assembly_ref="review-assembly.json",
            observation_ref="review-observation.json",
            retry_count=1,
        ),
        MigrationUnitState(
            unit_id="incomplete-review", status="manual_review",
            translation_ref="incomplete.json",
        ),
    ])

    store.restart_unfinished()

    done, partial, todo, review, incomplete_review = store.load()
    assert done.status == "frozen"
    assert done.translation_ref == "done.json"
    assert partial.status == "pending"
    assert partial.translation_ref is None
    assert partial.retry_count == 0
    assert todo.status == "pending"
    assert review.status == "manual_review"
    assert review.translation_ref == "review-translation.json"
    assert review.assembly_ref == "review-assembly.json"
    assert review.observation_ref == "review-observation.json"
    assert review.retry_count == 1
    assert incomplete_review.status == "pending"
    assert incomplete_review.translation_ref is None
