"""
Description: 验证 migrate CLI 参数、可选 SemanticIndex 与 JSON 输出。
References: interfaces.cli.main、MigrationService、domain.migration。
Referenced By: pytest 测试发现。
"""

import importlib
import json
from pathlib import Path

from matlab_refactor_agent.domain.migration import (
    GeneratedPythonFile,
    MatlabToPythonOutcome,
    MatlabToPythonPlan,
    MigrationWorkUnit,
    PythonArchitecture,
    TranslationResponse,
)
from matlab_refactor_agent.infrastructure.config import AppSettings

cli_module = importlib.import_module("matlab_refactor_agent.interfaces.cli.main")
commands_module = importlib.import_module(
    "matlab_refactor_agent.interfaces.cli.commands"
)


class _FakeMigrationService:
    semantic_reference: str | None = None
    resume_job_id: str | None = None
    resume_project: Path | None = None

    def __init__(self, _settings: AppSettings) -> None:
        self.last_job_id = "jobcli"
        self.last_checkpoint_reference = "checkpoint.json"

    def generate_translation_artifacts(
        self, project_root: Path, *, semantic_index_reference: str | None = None
    ) -> MatlabToPythonOutcome:
        del project_root
        type(self).semantic_reference = semantic_index_reference
        unit = MigrationWorkUnit(
            unit_id="chain1", symbol_ids=["main", "helper"],
            entry_symbols=["main"], sccs=[["helper"], ["main"]],
        )
        return MatlabToPythonOutcome(
            job_id="jobcli",
            plan=MatlabToPythonPlan(
                project_root="project",
                architecture=PythonArchitecture(
                    package_name="project", source_directory="src/project"
                ),
                units=[unit],
            ),
            translations=[TranslationResponse(
                unit_id="chain1", confidence=0.9,
                files=[GeneratedPythonFile(
                    path="src/project/main.py", content="VALUE = 1\n",
                    symbol_ids=["main", "helper"],
                )],
            )],
            artifacts={"migration_state": "state.json"},
        )

    def resume_translation_artifacts(
        self, job_id: str, *, project_root: Path | None = None
    ) -> MatlabToPythonOutcome:
        type(self).resume_job_id = job_id
        type(self).resume_project = project_root
        return self.generate_translation_artifacts(
            project_root or Path("project")
        )


def test_migrate_cli_writes_json_and_forwards_semantic_index(
    tmp_path: Path, monkeypatch
) -> None:
    settings = AppSettings(orchestrator={
        "state_db": tmp_path / "state.db",
        "artifact_dir": tmp_path / "jobs",
    })
    semantic = tmp_path / "semantic-index.json"
    semantic.write_text("{}", encoding="utf-8")
    output = tmp_path / "migration.json"
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        commands_module, "MigrationService", _FakeMigrationService
    )

    exit_code = cli_module.main([
        "migrate", str(tmp_path), "--semantic-index", str(semantic),
        "--json", str(output),
    ])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["plan"]["units"][0]["entry_symbols"] == ["main"]
    assert _FakeMigrationService.semantic_reference == str(semantic.resolve())


def test_migrate_cli_rejects_missing_semantic_index(
    tmp_path: Path, monkeypatch
) -> None:
    settings = AppSettings(orchestrator={
        "state_db": tmp_path / "state.db",
        "artifact_dir": tmp_path / "jobs",
    })
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        commands_module, "MigrationService", _FakeMigrationService
    )

    assert cli_module.main([
        "migrate", str(tmp_path), "--semantic-index", str(tmp_path / "missing.json")
    ]) == 2


def test_migrate_cli_resumes_same_job_without_project(
    tmp_path: Path, monkeypatch
) -> None:
    settings = AppSettings(orchestrator={
        "state_db": tmp_path / "state.db",
        "artifact_dir": tmp_path / "jobs",
    })
    output = tmp_path / "resumed.json"
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        commands_module, "MigrationService", _FakeMigrationService
    )

    exit_code = cli_module.main([
        "migrate", "--resume", "jobcli", "--json", str(output)
    ])

    assert exit_code == 0
    assert _FakeMigrationService.resume_job_id == "jobcli"
    assert _FakeMigrationService.resume_project is None
    assert json.loads(output.read_text(encoding="utf-8"))["job_id"] == "jobcli"
