"""
Description: 验证隔离迁移应用公开真实能力且校验输入目录。
References: apps.migration.backend.app、FastAPI TestClient。
Referenced By: pytest 迁移应用边界回归套件。
"""

from fastapi.testclient import TestClient
from pathlib import Path

from matlab_refactor_agent.apps.migration.backend.app import create_app
from matlab_refactor_agent.apps.migration.backend.jobs import MigrationJobManager
from matlab_refactor_agent.infrastructure.config import AppSettings


def test_migration_app_is_independent_and_reports_ready_status(tmp_path: Path) -> None:
    manager = MigrationJobManager(AppSettings(orchestrator={
        "artifact_dir": tmp_path / "jobs", "state_db": tmp_path / "state.db",
    }))
    client = TestClient(create_app(manager=manager))

    assert client.get("/api/health").json() == {
        "status": "ok",
        "application": "migration",
    }
    capabilities = client.get("/api/capabilities").json()
    assert capabilities["status"] == "ready"
    assert "matlab_python_execution" in capabilities["pending"]
    assert "numerical_differential_validation" in capabilities["pending"]
    assert "wcc_reason_act_observation" in capabilities["implemented"]
    assert "scc_atomic_boundaries" in capabilities["implemented"]
    assert "checkpoint_resume" in capabilities["implemented"]
    assert "checkpoint_resume" not in capabilities["pending"]

    response = client.post(
        "/api/migrations", json={"project_path": "D:/example/matlab"}
    )
    assert response.status_code == 422
    assert client.get("/api/migrations").json() == []
    assert client.get("/api/migrations/unknown").status_code == 404
    manager.close()
