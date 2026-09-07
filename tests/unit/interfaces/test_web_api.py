"""
Description: Verify the local Web API analysis and annotation lifecycle.
References: interfaces.api.app and domain graph/semantic models.
Referenced By: pytest Web MVP regression suite.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from matlab_refactor_agent.domain.models import AnalysisResult, DependencyEdge, FunctionInfo
from matlab_refactor_agent.domain.semantics import (
    FileAnnotation,
    FunctionAnnotation,
    ProjectAnnotation,
    SemanticIndex,
    SemanticProgressEvent,
    SourceEvidence,
)
from matlab_refactor_agent.interfaces.api.app import MvpJobManager, create_app
from matlab_refactor_agent.interfaces.api.storage import SQLiteWebProjectStore
from matlab_refactor_agent.apps.semantic.backend.app import resolve_project_path


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

    def annotate(self, project_root: Path, progress_callback=None) -> SemanticIndex:
        if progress_callback is not None:
            progress_callback(
                SemanticProgressEvent(
                    job_id="internal-job",
                    sequence=1,
                    node="initialize",
                    phase="completed",
                    message="语义图已初始化",
                )
            )
            progress_callback(
                SemanticProgressEvent(
                    job_id="internal-job",
                    sequence=2,
                    node="aggregate",
                    phase="completed",
                    message="三级语义聚合完成",
                )
            )
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


class _BlockingProgressService(_FakeService):
    """作用：暂停语义任务，以验证任务未结束时事件已经可以读取。"""

    started = Event()
    release = Event()

    def annotate(self, project_root: Path, progress_callback=None) -> SemanticIndex:
        """作用：先发布簇事件，等待测试放行后再完成注释。"""

        if progress_callback is not None:
            progress_callback(
                SemanticProgressEvent(
                    job_id="internal-job",
                    sequence=1,
                    node="select_unit",
                    phase="completed",
                    message="选择函数簇 cluster-live",
                    unit_id="cluster-live",
                )
            )
        self.started.set()
        if not self.release.wait(timeout=3):
            raise TimeoutError("测试未放行语义任务")
        return super().annotate(project_root, progress_callback=None)


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["state"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_project_path_accepts_native_and_windows_container_paths(tmp_path: Path) -> None:
    """作用：验证本机路径和 Windows 宿主机路径都能定位同一挂载项目。"""

    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)

    assert resolve_project_path(f'"{project}"') == project.resolve()
    assert resolve_project_path(
        r"D:\MATLAB\projects\demo",
        system_name="posix",
        projects_root=tmp_path / "projects",
        host_root_value="D:/MATLAB/projects",
    ) == project.resolve()


def test_windows_container_path_cannot_leave_mounted_root(tmp_path: Path) -> None:
    """作用：拒绝映射到宿主机挂载范围之外或包含父目录跳转的路径。"""

    mounted = tmp_path / "projects"
    mounted.mkdir()
    with pytest.raises(HTTPException) as outside:
        resolve_project_path(
            r"C:\other\demo",
            system_name="posix",
            projects_root=mounted,
            host_root_value="D:/MATLAB/projects",
        )
    assert outside.value.status_code == 422

    with pytest.raises(HTTPException) as traversal:
        resolve_project_path(
            r"D:\MATLAB\projects\..\secret",
            system_name="posix",
            projects_root=mounted,
            host_root_value="D:/MATLAB/projects",
        )
    assert traversal.value.status_code == 422


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
    assert re.fullmatch(r"\d{20}", job_id)
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
    events = client.get(f"/api/jobs/{job_id}/semantic-events").json()
    assert [item["sequence"] for item in events] == [1, 2]
    assert all(item["job_id"] == job_id for item in events)
    assert client.get(
        f"/api/jobs/{job_id}/semantic-events?after_sequence=1"
    ).json()[0]["sequence"] == 2
    with client.stream(
        "GET", f"/api/jobs/{job_id}/semantic-events/stream"
    ) as stream:
        assert stream.headers["cache-control"] == "no-cache, no-transform"
        assert stream.headers["x-accel-buffering"] == "no"
        body = "".join(stream.iter_text())
    assert "event: semantic_progress" in body
    assert "event: terminal" in body


def test_semantic_progress_is_readable_before_job_finishes(tmp_path: Path) -> None:
    """作用：验证前端可在长时间 LLM 调用期间增量读取当前函数簇。"""

    _BlockingProgressService.started.clear()
    _BlockingProgressService.release.clear()
    manager = MvpJobManager(
        service_factory=_BlockingProgressService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(tmp_path / "web-projects.db"),
    )
    client = TestClient(create_app(manager))
    created = client.post(
        "/api/jobs/analyze", json={"project_path": str(tmp_path)}
    ).json()
    job_id = created["job_id"]
    assert _wait(client, job_id)["graph_ready"] is True

    client.post(f"/api/jobs/{job_id}/annotate")
    assert _BlockingProgressService.started.wait(timeout=1)
    running = client.get(f"/api/jobs/{job_id}").json()
    events = client.get(f"/api/jobs/{job_id}/semantic-events").json()

    assert running["state"] == "running"
    assert events[0]["unit_id"] == "cluster-live"
    assert events[0]["node"] == "select_unit"
    _BlockingProgressService.release.set()
    assert _wait(client, job_id)["state"] == "completed"


def test_function_source_is_limited_to_analyzed_node(tmp_path: Path) -> None:
    """作用：验证源码接口只读取图中函数的已解析行范围。"""

    (tmp_path / "main.m").write_text(
        "function y = main(x)\ny = x + 1;\nend\n", encoding="utf-8"
    )
    manager = MvpJobManager(
        service_factory=_FakeService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(tmp_path / "web-projects.db"),
    )
    client = TestClient(create_app(manager))
    created = client.post(
        "/api/jobs/analyze", json={"project_path": str(tmp_path)}
    ).json()
    _wait(client, created["job_id"])

    source = client.get(f"/api/jobs/{created['job_id']}/functions/main/source")

    assert source.status_code == 200
    assert source.json()["source"] == "function y = main(x)"
    assert client.get(
        f"/api/jobs/{created['job_id']}/functions/unknown/source"
    ).status_code == 404


def test_model_setting_updates_only_selected_env_field(
    tmp_path: Path, monkeypatch,
) -> None:
    """作用：验证模型设置 API 保留 env 其他字段且不暴露密钥。"""

    monkeypatch.delenv("MATLAB_REFACTOR_LLM_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=secret\nMATLAB_REFACTOR_LLM_MODEL=old-model\n",
        encoding="utf-8",
    )
    manager = MvpJobManager(
        service_factory=_FakeService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(tmp_path / "web-projects.db"),
    )
    client = TestClient(create_app(manager, env_file=env_file))

    assert client.get("/api/settings/model").json()["model"] == "old-model"
    updated = client.put("/api/settings/model", json={"model": "deepseek-chat"})

    assert updated.status_code == 200
    assert updated.json()["model"] == "deepseek-chat"
    content = env_file.read_text(encoding="utf-8")
    assert "MATLAB_REFACTOR_LLM_MODEL=deepseek-chat" in content
    assert "DEEPSEEK_API_KEY=secret" in content
    assert "secret" not in str(updated.json())


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


def test_production_app_serves_built_frontend_without_shadowing_api(
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        "<!doctype html><title>MATLAB Atlas</title>", encoding="utf-8"
    )
    manager = MvpJobManager(
        service_factory=_FakeService,
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(tmp_path / "web-projects.db"),
    )
    client = TestClient(create_app(manager, frontend_dir=frontend))

    assert client.get("/").status_code == 200
    assert "MATLAB Atlas" in client.get("/").text
    assert client.get("/api/health").json() == {"status": "ok"}
