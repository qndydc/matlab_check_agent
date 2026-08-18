"""
Description: Verify the local Web API analysis and annotation lifecycle.
References: interfaces.api.app and domain graph/semantic models.
Referenced By: pytest Web MVP regression suite.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from matlab_refactor_agent.domain.models import AnalysisResult, DependencyEdge, FunctionInfo
from matlab_refactor_agent.domain.semantics import (
    FileAnnotation,
    FunctionAnnotation,
    ProjectAnnotation,
    SemanticIndex,
    SourceEvidence,
)
from matlab_refactor_agent.interfaces.api.app import MvpJobManager, create_app
from matlab_refactor_agent.interfaces.api.storage import SQLiteWebProjectStore


class _FakeService:
    def analyze(self, project_root: Path) -> AnalysisResult:
        return AnalysisResult(
            project_root=str(project_root),
            functions=[
                FunctionInfo(name="main", qualified_name="main", file_path="main.m"),
                FunctionInfo(name="load", qualified_name="load", file_path="load.m"),
            ],
            dependencies=[DependencyEdge(source="main", target="load")],
            entry_points=["main"],
        )

    def annotate(self, project_root: Path) -> SemanticIndex:
        evidence = SourceEvidence(
            file_path="main.m", start_line=1, end_line=1, source_hash="hash"
        )
        return SemanticIndex(
            project_root=str(project_root),
            functions=[
                FunctionAnnotation(
                    symbol_id="main",
                    file_path="main.m",
                    start_line=1,
                    end_line=1,
                    summary="项目入口",
                    evidence=[evidence],
                    confidence=0.9,
                )
            ],
            files=[
                FileAnnotation(
                    file_path="main.m",
                    role="入口文件",
                    function_symbols=["main"],
                    confidence=0.9,
                )
            ],
            project=ProjectAnnotation(
                purpose="测试项目",
                usage="运行项目入口函数",
                confidence=0.9,
            ),
        )


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["state"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_analysis_then_annotation_keeps_graph_available(tmp_path: Path) -> None:
    manager = MvpJobManager(
        service_factory=_FakeService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(tmp_path / "web-projects.db"),
    )
    client = TestClient(create_app(manager))

    assert client.get("/api/health").json() == {"status": "ok"}
    created = client.post(
        "/api/jobs/analyze", json={"project_path": str(tmp_path)}
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert _wait(client, job_id)["graph_ready"] is True

    graph = client.get(f"/api/jobs/{job_id}/graph")
    assert graph.status_code == 200
    assert len(graph.json()["nodes"]) == 2
    assert len(graph.json()["edges"]) == 1

    view = client.get(f"/api/jobs/{job_id}/graph/view?scope=project")
    assert view.status_code == 200
    assert view.json()["visible_nodes"] == 2
    assert {node["node_type"] for node in view.json()["nodes"]} == {"file"}

    annotate = client.post(f"/api/jobs/{job_id}/annotate")
    assert annotate.status_code == 202
    complete = _wait(client, job_id)
    assert complete["graph_ready"] is True
    assert complete["semantics_ready"] is True
    assert client.get(f"/api/jobs/{job_id}/semantics").json()["project"]["purpose"] == "测试项目"


def test_missing_results_return_clear_errors(tmp_path: Path) -> None:
    manager = MvpJobManager(
        service_factory=_FakeService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(tmp_path / "web-projects.db"),
    )
    client = TestClient(create_app(manager))
    assert client.get("/api/jobs/unknown").status_code == 404

    created = client.post(
        "/api/jobs/analyze", json={"project_path": str(tmp_path)}
    ).json()
    _wait(client, created["job_id"])
    assert client.get(f"/api/jobs/{created['job_id']}/semantics").status_code == 409


def test_projects_survive_manager_restart_and_can_be_deleted(tmp_path: Path) -> None:
    """作用：验证图和三级注释跨进程管理器恢复，并能通过项目 API 删除。"""

    database = tmp_path / "web-projects.db"
    first = TestClient(
        create_app(
            MvpJobManager(
                service_factory=_FakeService,
                executor=ThreadPoolExecutor(max_workers=1),
                store=SQLiteWebProjectStore(database),
            )
        )
    )
    created = first.post(
        "/api/jobs/analyze", json={"project_path": str(tmp_path)}
    ).json()
    job_id = created["job_id"]
    _wait(first, job_id)
    first.post(f"/api/jobs/{job_id}/annotate")
    assert _wait(first, job_id)["semantics_ready"] is True

    restarted = TestClient(
        create_app(
            MvpJobManager(
                service_factory=_FakeService,
                executor=ThreadPoolExecutor(max_workers=1),
                store=SQLiteWebProjectStore(database),
            )
        )
    )
    projects = restarted.get("/api/projects")
    assert projects.status_code == 200
    assert [item["job_id"] for item in projects.json()] == [job_id]
    assert restarted.get(f"/api/jobs/{job_id}/graph").status_code == 200
    assert restarted.get(f"/api/jobs/{job_id}/semantics").status_code == 200

    assert restarted.delete(f"/api/projects/{job_id}").status_code == 204
    assert restarted.get("/api/projects").json() == []
    assert restarted.get(f"/api/jobs/{job_id}").status_code == 404
