"""
Description: Verify employee ownership, ZIP ingestion, export and admin boundaries.
References: Semantic FastAPI application and the multi-user control store.
Referenced By: Pytest Web security regression suite.
"""

from __future__ import annotations

import io
import sqlite3
import stat
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from matlab_refactor_agent.apps.migration.backend.jobs import MigrationJobManager
from matlab_refactor_agent.apps.semantic.backend.app import MvpJobManager, create_app
from matlab_refactor_agent.apps.semantic.backend.storage import SQLiteWebProjectStore
from matlab_refactor_agent.domain.models import AnalysisResult, FunctionInfo
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.interfaces.api.multiuser import (
    ControlStore,
    WebRuntimeSettings,
)


class _AnalysisService:
    def analyze(self, root: Path) -> AnalysisResult:
        return AnalysisResult(
            project_root=str(root),
            functions=[FunctionInfo(
                name="main", qualified_name="main", file_path="main.m",
            )],
            entry_points=["main"],
        )


def _zip(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return output.getvalue()


def _zip_entries(files: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files:
            bundle.writestr(name, content)
    return output.getvalue()


def _client(tmp_path: Path) -> tuple[TestClient, MvpJobManager]:
    settings = AppSettings(orchestrator={
        "artifact_dir": tmp_path / "jobs",
        "state_db": tmp_path / "state.db",
        "web_db": tmp_path / "control.db",
    })
    runtime = WebRuntimeSettings(
        data_root=tmp_path / "data",
        require_employee=True,
        administrators=frozenset({"A001"}),
        max_upload_bytes=1024 * 1024,
        max_unpacked_bytes=2 * 1024 * 1024,
        max_project_files=100,
        user_quota_bytes=4 * 1024 * 1024,
    )
    control = ControlStore(settings.orchestrator.web_db, runtime)
    manager = MvpJobManager(
        service_factory=_AnalysisService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(settings.orchestrator.web_db),
        settings=settings,
        control_store=control,
    )
    return TestClient(create_app(manager)), manager


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/jobs/{job_id}")
        if response.status_code == 200 and response.json()["state"] not in {"queued", "running"}:
            return response.json()
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_employee_upload_job_and_result_are_isolated(tmp_path: Path):
    client, manager = _client(tmp_path)
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/session", json={"employee_id": "A001"}).status_code == 200
    uploaded = client.post(
        "/api/source-projects/upload",
        files={"file": ("demo.zip", _zip({"main.m": "function y=main(x)\ny=x;\nend"}), "application/zip")},
    )
    assert uploaded.status_code == 201
    project = uploaded.json()
    assert project["state"] == "ready"
    assert Path(project["storage_path"], "main.m").is_file()

    created = client.post(
        "/api/jobs/analyze", json={"project_id": project["project_id"]}
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert len(job_id) == 28 and job_id.isalnum()
    assert _wait(client, job_id)["state"] == "completed"

    job_dir = manager._artifacts.root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "report.json").write_text("{}", encoding="utf-8")
    (job_dir / ".env").write_text("SECRET=value", encoding="utf-8")
    assert client.post(f"/api/jobs/{job_id}/export").status_code == 202
    archive = client.get(f"/api/jobs/{job_id}/download")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert "report.json" in bundle.namelist()
        assert ".env" not in bundle.namelist()

    assert client.post("/api/session", json={"employee_id": "B002"}).status_code == 200
    assert client.get("/api/source-projects").json() == []
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/semantic-events").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/semantic-events/stream").status_code == 404
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 404
    assert client.post(f"/api/jobs/{job_id}/restart").status_code == 404
    assert client.delete(f"/api/projects/{job_id}").status_code == 404
    assert client.delete(f"/api/source-projects/{project['project_id']}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 404
    assert client.put("/api/settings/model", json={"model": "x"}).status_code == 403
    assert client.post(
        "/api/jobs/analyze", json={"project_path": str(tmp_path)}
    ).status_code == 422


def test_malicious_zip_is_rejected_without_path_escape(tmp_path: Path):
    client, _manager = _client(tmp_path)
    client.post("/api/session", json={"employee_id": "A001"})
    response = client.post(
        "/api/source-projects/upload",
        files={"file": ("bad.zip", _zip({"../escaped.m": "secret"}), "application/zip")},
    )
    assert response.status_code == 422
    assert not (tmp_path / "escaped.m").exists()


def test_oversized_and_duplicate_zip_inputs_are_rejected(tmp_path: Path):
    client, _manager = _client(tmp_path)
    client.post("/api/session", json={"employee_id": "A001"})

    oversized = client.post(
        "/api/source-projects/upload",
        files={"file": ("large.zip", b"x" * (1024 * 1024 + 1), "application/zip")},
    )
    assert oversized.status_code == 413

    duplicate = client.post(
        "/api/source-projects/upload",
        files={"file": (
            "duplicate.zip",
            _zip_entries([("main.m", b"one"), ("MAIN.m", b"two")]),
            "application/zip",
        )},
    )
    assert duplicate.status_code == 422
    for failed in client.get("/api/source-projects").json():
        project_root = Path(failed["storage_path"]).parent
        assert not (project_root / "upload.zip.part").exists()
        assert not (project_root / "upload.zip").exists()
        assert not (project_root / "source").exists()


def test_zip_links_are_rejected(tmp_path: Path):
    client, _manager = _client(tmp_path)
    client.post("/api/session", json={"employee_id": "A001"})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        link = zipfile.ZipInfo("linked.m")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "target.m")
    response = client.post(
        "/api/source-projects/upload",
        files={"file": ("link.zip", output.getvalue(), "application/zip")},
    )
    assert response.status_code == 422


def test_control_store_marks_unfinished_jobs_interrupted_after_restart(tmp_path: Path):
    runtime = WebRuntimeSettings(
        data_root=tmp_path / "data",
        require_employee=True,
        administrators=frozenset({"A001"}),
        max_upload_bytes=100,
        max_unpacked_bytes=100,
        max_project_files=10,
        user_quota_bytes=1000,
    )
    database = tmp_path / "control.db"
    first = ControlStore(database, runtime)
    first.register_job("job1", "A001", "semantic", state="queued")
    first.register_job("job2", "A001", "migration", state="running")
    second = ControlStore(database, runtime)
    with second.transaction() as connection:
        states = dict(connection.execute(
            "SELECT job_id, state FROM execution_jobs ORDER BY job_id"
        ).fetchall())
    assert states == {"job1": "interrupted", "job2": "interrupted"}
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert not (tmp_path / "data" / "users" / "A001" / "projects" / "escaped.m").exists()


def test_server_mode_rejects_arbitrary_migration_server_paths(tmp_path: Path):
    settings = AppSettings(orchestrator={
        "artifact_dir": tmp_path / "jobs",
        "state_db": tmp_path / "state.db",
        "web_db": tmp_path / "control.db",
    })
    runtime = WebRuntimeSettings(
        data_root=tmp_path / "data",
        require_employee=True,
        administrators=frozenset({"A001"}),
        max_upload_bytes=100,
        max_unpacked_bytes=100,
        max_project_files=10,
        user_quota_bytes=1000,
    )
    control = ControlStore(settings.orchestrator.web_db, runtime)
    manager = MigrationJobManager(
        settings=settings,
        control_store=control,
    )
    with pytest.raises(HTTPException) as captured:
        manager.submit(project_path=str(tmp_path), employee_id="A001")
    assert captured.value.status_code == 422
