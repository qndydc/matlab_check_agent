"""
Description: 使用真实迁移图和模拟模型验证 Web 启动、视图、重启续跑与边界。
References: MigrationJobManager、MigrationService、FastAPI TestClient。
Referenced By: pytest 迁移前后端契约回归测试。
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from fastapi.testclient import TestClient
import pytest

from matlab_refactor_agent.application.migration_service import MigrationService
from matlab_refactor_agent.application.semantic_service import SemanticService
from matlab_refactor_agent.apps.migration.backend.app import create_app
from matlab_refactor_agent.apps.migration.backend.jobs import MigrationJobManager
from matlab_refactor_agent.apps.semantic.backend.app import (
    MvpJobManager,
    create_app as create_semantic_app,
)
from matlab_refactor_agent.apps.semantic.backend.storage import SQLiteWebProjectStore
from matlab_refactor_agent.domain.migration import (
    ActChunkPlan,
    ConversionStratagem,
    MigrationCheckpoint,
    TranslationResponse,
)
from matlab_refactor_agent.domain.semantics import SemanticIndex, ProjectAnnotation
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.orchestration import Orchestrator


class FakeMigrationClient:
    """只替代模型响应；扫描、WCC、LangGraph、组装与检查均走真实代码。"""

    def __init__(self) -> None:
        self.converted: list[str] = []
        self.fail_at: int | None = None
        self.started = Event()
        self.release = Event()
        self.release.set()
        self.finish_without_checks = False

    def complete(self, *, user_prompt, response_model, **_kwargs):
        self.started.set()
        assert self.release.wait(5), "test model was not released"
        data = json.loads(user_prompt.split("\n上一轮 Observation:")[0])
        unit = data["unit"]
        if response_model.__name__ in {"ConversionStratagem", "ReasonDecision"}:
            return ConversionStratagem(
                unit_id=unit["unit_id"],
                action="finish" if self.finish_without_checks else "convert",
                rationale="模拟测试策略", conversion_steps=["转换完整 WCC"],
                module_plan={symbol: "converted.py" for symbol in unit["symbol_ids"]},
            )
        assert response_model is TranslationResponse
        self.converted.append(unit["unit_id"])
        if len(self.converted) == self.fail_at:
            raise RuntimeError("模拟模型中断\n这里的冗长详情不应出现在简短日志")
        return TranslationResponse(unit_id=unit["unit_id"], confidence=0.9, files=[{
            "path": "converted.py", "content": "def main(x):\n    return x + 1\n",
            "symbol_ids": unit["symbol_ids"],
        }])


@pytest.fixture
def environment(tmp_path):
    project = tmp_path / "matlab"
    project.mkdir()
    for name in ("alpha", "beta", "gamma", "omega"):
        (project / f"{name}.m").write_text(
            f"function y = {name}(x)\ny = x + 1;\nend\n", encoding="utf-8"
        )
    settings = AppSettings(orchestrator={
        "artifact_dir": tmp_path / "jobs", "state_db": tmp_path / "state.db",
        "web_db": tmp_path / "web-projects.db",
    })
    model = FakeMigrationClient()

    def service_factory():
        service = MigrationService(settings)
        service._orchestrator = Orchestrator.from_settings(settings, semantic_client=model)
        return service

    return project, settings, model, service_factory


def wait_finished(client, job_id):
    for _ in range(300):
        response = client.get(f"/api/migrations/{job_id}")
        assert response.status_code == 200
        result = response.json()
        if result["state"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    pytest.fail("migration did not finish")


def wait_analysis_finished(client, job_id):
    for _ in range(300):
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["state"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    pytest.fail("project analysis did not finish")


def test_migration_full_restart_rebuilds_graph_preserves_old_artifacts(environment):
    project, settings, model, factory = environment
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        original = client.post("/api/migrations", json={"project_path": str(project)}).json()["job_id"]
        wait_finished(client, original)
        old_checkpoint_path = manager.artifacts.reference(original, "migration-checkpoint.json")
        old_checkpoint = Path(old_checkpoint_path).read_bytes()
        (project / "added.m").write_text("function y = added(x)\ny = x;\nend\n", encoding="utf-8")
        response = client.post(f"/api/migrations/{original}/restart")
        assert response.status_code == 202
        restarted = response.json()["job_id"]
        assert restarted != original
        completed = wait_finished(client, restarted)
        assert completed["total_chains"] == 5
        assert completed["static_analysis_reused"] is False
        checkpoint = manager.artifacts.read_model(manager.artifacts.reference(restarted, "migration-checkpoint.json"), MigrationCheckpoint)
        assert Path(checkpoint.scan_reference).parent.name == restarted
        assert Path(checkpoint.analysis_reference).parent.name == restarted
        assert (settings.orchestrator.artifact_dir / restarted / "structural-code-tree.json").is_file()
        assert Path(old_checkpoint_path).read_bytes() == old_checkpoint


def test_unavailable_context_is_reported_without_blocking_other_wcc(environment):
    project, settings, model, factory = environment
    complete = model.complete
    reason_calls = []

    def request_missing(*, user_prompt, response_model, **kwargs):
        if response_model.__name__ in {"ConversionStratagem", "ReasonDecision"}:
            data = json.loads(user_prompt)
            reason_calls.append(data["unit"]["unit_id"])
            return ConversionStratagem(unit_id=data["unit"]["unit_id"],
                action="rebuild_context", requested_context=["unavailable.m"])
        return complete(user_prompt=user_prompt, response_model=response_model, **kwargs)

    model.complete = request_missing
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post("/api/migrations", json={"project_path": str(project)}).json()["job_id"]
        result = wait_finished(client, job_id)
        assert result["state"] == "completed"
        assert len(model.converted) == len(reason_calls) == 4
        assert result["completed_chains"] == 4
        assert result["can_resume"] is False
    states = json.loads(manager.artifacts.read_text(manager.artifacts.reference(job_id, "migration-state.json")))
    assert all(item["status"] == "frozen" and item["translation_ref"] for item in states)
    assert all(item["retry_count"] == 1 for item in states)
    for item in states:
        observation = json.loads(manager.artifacts.read_text(item["observation_ref"]))
        assert observation["passed"]
        assert any(fact["kind"] == "manual_review" for fact in observation["facts"])
    project_observation = json.loads(manager.artifacts.read_text(
        manager.artifacts.reference(job_id, "project-observation.json")
    ))
    assert project_observation["passed"]
    assert any(fact["kind"] == "manual_review" for fact in project_observation["facts"])


def test_web_reuses_completed_project_analysis_for_new_migration(environment):
    """5174 只接收 5173 的快照引用，不为新迁移 Job 再执行 Scanner/Parser/Analyzer。"""

    project, settings, model, factory = environment
    semantic_manager = MvpJobManager(
        service_factory=lambda: SemanticService(settings),
        executor=ThreadPoolExecutor(max_workers=1),
        store=SQLiteWebProjectStore(settings.orchestrator.web_db),
        settings=settings,
    )
    with TestClient(create_semantic_app(manager=semantic_manager)) as semantic_client:
        analysis_id = semantic_client.post(
            "/api/jobs/analyze", json={"project_path": str(project)}
        ).json()["job_id"]
        completed = wait_analysis_finished(semantic_client, analysis_id)
        assert completed["analysis_ready"] is True

    stored = SQLiteWebProjectStore(settings.orchestrator.web_db).get(analysis_id)
    assert stored is not None
    assert stored.scan_reference and stored.analysis_reference

    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        analyses = client.get("/api/project-analyses")
        assert analyses.status_code == 200
        assert analyses.json()[0]["analysis_job_id"] == analysis_id
        assert analyses.json()[0]["usable"] is True

        created = client.post("/api/migrations", json={"analysis_job_id": analysis_id})
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        result = wait_finished(client, job_id)
        assert result["static_analysis_reused"] is True
        assert result["analysis_job_id"] == analysis_id

    checkpoint = manager.artifacts.read_model(
        manager.artifacts.reference(job_id, "migration-checkpoint.json"),
        MigrationCheckpoint,
    )
    assert checkpoint.scan_reference == stored.scan_reference
    assert checkpoint.analysis_reference == stored.analysis_reference
    assert not (settings.orchestrator.artifact_dir / job_id / "scan-result.json").exists()


def test_web_runs_real_multi_wcc_pipeline_and_reads_artifacts(environment):
    project, settings, model, factory = environment
    original = {path.name: path.read_bytes() for path in project.iterdir()}
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        model.release.clear()
        response = client.post("/api/migrations", json={"project_path": str(project)})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        try:
            assert model.started.wait(3)
            assert client.post(f"/api/migrations/{job_id}/resume").status_code == 409
            running = client.get(f"/api/migrations/{job_id}").json()
            assert running["state"] == "running"
            assert running["active_chain_id"]
            assert running["message"] == "规划转换策略"
        finally:
            model.release.set()
        result = wait_finished(client, job_id)
        assert result["state"] == "completed"
        assert result["completed_chains"] == result["total_chains"] == 4
        assert result["progress"] == 1
        assert not result["can_resume"]
        assert "owner" not in result
        chains = client.get(f"/api/migrations/{job_id}/chains").json()
        assert len(chains) == 4
        for chain in chains:
            assert chain["status"] == "frozen"
            detail = client.get(f"/api/migrations/{job_id}/chains/{chain['chain_id']}").json()
            assert detail["nodes"]
            assert detail["files"][0]["content"].startswith("def main")
            assert detail["observation"]["passed"]
            assert detail["act_chunks"][0]["status"] == "frozen"
            assert {fact["kind"] for fact in detail["observation"]["facts"]} == {"syntax", "import"}
            assert detail["attempts"][0]["state"] == "accepted"
        assert client.get(f"/api/migrations/{job_id}/chains/missing").status_code == 404
        assert client.post(f"/api/migrations/{job_id}/resume").status_code == 409
        log = client.get(f"/api/migrations/{job_id}/events").json()
        assert log[-1]["stage"] == "freeze_chain"
        assert len(log) <= 200
        assert not any("def main" in event["message"] for event in log)
        with client.stream(
            "GET", f"/api/migrations/{job_id}/heartbeat"
        ) as heartbeat:
            assert heartbeat.headers["content-type"].startswith(
                "text/event-stream"
            )
            line = next(item for item in heartbeat.iter_lines() if item)
            streamed = json.loads(line.removeprefix("data: "))
            assert streamed["job_id"] == job_id
            assert streamed["state"] == "completed"
            assert streamed["server_time"]
    assert {path.name: path.read_bytes() for path in project.iterdir()} == original
    reopened = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=reopened)) as client:
        assert client.get("/api/migrations").json()[0]["job_id"] == job_id
        assert client.get(f"/api/migrations/{job_id}").json()["state"] == "completed"


def test_migration_wccs_run_serially(environment):
    project, settings, model, factory = environment
    original_complete = model.complete
    lock = Lock()
    active = 0
    peak_active = 0

    def tracked_complete(*, response_model, **kwargs):
        nonlocal active, peak_active
        if response_model is not TranslationResponse:
            return original_complete(response_model=response_model, **kwargs)
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            time.sleep(0.05)
            return original_complete(response_model=response_model, **kwargs)
        finally:
            with lock:
                active -= 1

    model.complete = tracked_complete
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post(
            "/api/migrations", json={"project_path": str(project)}
        ).json()["job_id"]
        assert wait_finished(client, job_id)["state"] == "completed"

    assert peak_active == 1


def _branched_wcc_environment(tmp_path):
    project = tmp_path / "branched-matlab"
    project.mkdir()
    calls = " + ".join(f"leaf{index}(x)" for index in range(14))
    (project / "root.m").write_text(
        f"function y = root(x)\ny = {calls};\nend\n", encoding="utf-8"
    )
    for index in range(14):
        (project / f"leaf{index}.m").write_text(
            f"function y = leaf{index}(x)\ny = x + {index};\nend\n",
            encoding="utf-8",
        )
    settings = AppSettings(orchestrator={
        "artifact_dir": tmp_path / "jobs",
        "state_db": tmp_path / "state.db",
        "web_db": tmp_path / "web-projects.db",
    })
    model = FakeMigrationClient()

    def factory():
        service = MigrationService(settings)
        service._orchestrator = Orchestrator.from_settings(
            settings, semantic_client=model
        )
        return service

    return project, settings, model, factory


def test_act_chunks_run_in_dependency_waves_concurrently(tmp_path):
    project, settings, model, factory = _branched_wcc_environment(tmp_path)
    original_complete = model.complete
    lock = Lock()
    active = 0
    peak_active = 0

    def tracked_complete(*, response_model, **kwargs):
        nonlocal active, peak_active
        if response_model is not TranslationResponse:
            return original_complete(response_model=response_model, **kwargs)
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            time.sleep(0.05)
            return original_complete(response_model=response_model, **kwargs)
        finally:
            with lock:
                active -= 1

    model.complete = tracked_complete
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post(
            "/api/migrations", json={"project_path": str(project)}
        ).json()["job_id"]
        assert wait_finished(client, job_id)["state"] == "completed"

    assert peak_active >= 2


def test_failed_act_chunk_is_split_without_redoing_frozen_peers(tmp_path):
    project, settings, model, factory = _branched_wcc_environment(tmp_path)
    model.fail_at = 1
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post(
            "/api/migrations", json={"project_path": str(project)}
        ).json()["job_id"]
        result = wait_finished(client, job_id)
        assert result["state"] == "completed"

    chunk_file = next(
        (settings.orchestrator.artifact_dir / job_id).glob("act-chunks-*.json")
    )
    plan = manager.artifacts.read_model(str(chunk_file), ActChunkPlan)
    assert any(item.status == "superseded" for item in plan.chunks)
    assert all(
        item.status == "frozen"
        for item in plan.chunks if item.status != "superseded"
    )
    frozen_ids = {
        item.chunk_id for item in plan.chunks
        if item.status == "frozen" and item.parent_chunk_id is None
    }
    assert frozen_ids
    assert all(model.converted.count(chunk_id) == 1 for chunk_id in frozen_ids)


def test_chunk_resume_reuses_frozen_chunks_and_reaches_single_function_fallback(
    tmp_path,
):
    project, settings, model, factory = _branched_wcc_environment(tmp_path)
    original_complete = model.complete
    calls: list[str] = []
    failed_single = False

    def split_then_interrupt(*, user_prompt, response_model, **kwargs):
        nonlocal failed_single
        if response_model is not TranslationResponse:
            return original_complete(
                user_prompt=user_prompt, response_model=response_model, **kwargs
            )
        unit = json.loads(user_prompt)["unit"]
        calls.append(unit["unit_id"])
        if len(unit["symbol_ids"]) > 1 and "leaf0" in unit["symbol_ids"]:
            raise RuntimeError("force chunk bisection")
        if "leaf0" in unit["symbol_ids"] and not failed_single:
            failed_single = True
            raise RuntimeError("interrupt one single-function chunk")
        return original_complete(
            user_prompt=user_prompt, response_model=response_model, **kwargs
        )

    model.complete = split_then_interrupt
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post(
            "/api/migrations", json={"project_path": str(project)}
        ).json()["job_id"]
        assert wait_finished(client, job_id)["state"] == "failed"

    chunk_file = next(
        (settings.orchestrator.artifact_dir / job_id).glob("act-chunks-*.json")
    )
    failed_plan = manager.artifacts.read_model(str(chunk_file), ActChunkPlan)
    frozen_before = {
        item.chunk_id: calls.count(item.chunk_id)
        for item in failed_plan.chunks if item.status == "frozen"
    }
    assert frozen_before
    assert any(
        item.status == "failed" and item.function_count == 1
        for item in failed_plan.chunks
    )
    failed_chunk_id = next(
        item.chunk_id for item in failed_plan.chunks
        if item.status == "failed" and item.function_count == 1
    )

    reopened = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=reopened)) as client:
        assert client.post(f"/api/migrations/{job_id}/resume").status_code == 202
        assert wait_finished(client, job_id)["state"] == "completed"

    completed_plan = reopened.artifacts.read_model(str(chunk_file), ActChunkPlan)
    assert all(item.status == "frozen" for item in completed_plan.chunks
               if item.status != "superseded")
    assert next(item for item in completed_plan.chunks
                if item.chunk_id == failed_chunk_id).status == "frozen"
    assert all(calls.count(chunk_id) == count for chunk_id, count in frozen_before.items())


def test_web_resume_preserves_frozen_chunks_and_attempt_history(environment):
    project, settings, model, factory = environment
    model.fail_at = 2
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post("/api/migrations", json={"project_path": str(project)}).json()["job_id"]
        failed = wait_finished(client, job_id)
        assert failed["state"] == "failed" and failed["can_resume"]
        # 迁移固定单 Agent：失败点之后的 WCC 留给断点续跑。
        assert failed["completed_chains"] == 1
        chain_statuses = client.get(f"/api/migrations/{job_id}/chains").json()
        failed_chain_id = next(
            item["chain_id"] for item in chain_statuses
            if item["status"] == "failed"
        )
        assert "冗长" not in failed["error"]
        chain_id = next(
            item["chain_id"] for item in chain_statuses
            if item["status"] == "frozen"
        )
    job_root = settings.orchestrator.artifact_dir / job_id
    frozen = {path: path.read_bytes() for path in (job_root / "generated-python" / chain_id).rglob("*.py")}
    history = {path: path.read_bytes() for path in job_root.glob("*-*.json")
               if path.name.startswith(("context-", "stratagem-", "act-context-", "translation-"))}
    model.fail_at = None
    reopened = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=reopened)) as client:
        response = client.post(f"/api/migrations/{job_id}/resume")
        assert response.status_code == 202
        assert response.json()["job_id"] == job_id
        assert wait_finished(client, job_id)["state"] == "completed"
        assert model.converted.count(chain_id) == 1
        retried = client.get(
            f"/api/migrations/{job_id}/chains/{failed_chain_id}"
        ).json()
        assert [item["number"] for item in retried["attempts"]] == [1, 2]
    assert all(path.read_bytes() == content for path, content in frozen.items())
    assert all(path.read_bytes() == content for path, content in history.items())


def test_resume_rejects_source_changes_and_handles_server_restart(environment):
    project, settings, model, factory = environment
    model.fail_at = 2
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post("/api/migrations", json={"project_path": str(project)}).json()["job_id"]
        wait_finished(client, job_id)
    record = settings.orchestrator.artifact_dir / job_id / "migration-web.json"
    saved = json.loads(record.read_text(encoding="utf-8"))
    saved["state"] = "running"  # 模拟进程在写入终态前被强制退出。
    record.write_text(json.dumps(saved), encoding="utf-8")
    reopened = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=reopened)) as client:
        result = client.get(f"/api/migrations/{job_id}").json()
        assert result["state"] == "interrupted" and result["can_resume"]
        (project / "alpha.m").write_text("% changed", encoding="utf-8")
        response = client.post(f"/api/migrations/{job_id}/resume")
        assert response.status_code == 409
        assert "源码" in response.json()["detail"]


def test_unverified_finish_is_manual_review_and_bad_inputs_are_rejected(environment):
    project, settings, model, factory = environment
    model.finish_without_checks = True
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        assert client.post("/api/migrations", json={"project_path": str(project.parent)}).status_code == 422
        assert client.post("/api/migrations", json={
            "project_path": str(project), "semantic_index_reference": str(project / "alpha.m"),
        }).status_code == 422
        job_id = client.post("/api/migrations", json={"project_path": str(project)}).json()["job_id"]
        result = wait_finished(client, job_id)
        assert result["state"] == "manual_review"
        assert result["completed_chains"] == 0
        assert model.converted == []


def test_optional_semantic_index_is_snapshotted_and_displayed(environment, tmp_path):
    project, settings, model, factory = environment
    exported = tmp_path / "exported-index.json"
    index = SemanticIndex(
        project_root=str(project),
        project=ProjectAnnotation(purpose="四个独立的计算入口", confidence=0.9),
    )
    exported.write_text(index.model_dump_json(), encoding="utf-8")
    # CLI 与 Web 共用 Service；支持读取 ArtifactStore 以外的显式导出文件。
    result = factory().generate_translation_artifacts(project, semantic_index_reference=str(exported))
    snapshot = settings.orchestrator.artifact_dir / result.job_id / "input-semantic-index.json"
    assert snapshot.is_file()
    manager = MigrationJobManager(settings, factory)
    with TestClient(create_app(manager=manager)) as client:
        job_id = client.post("/api/migrations", json={
            "project_path": str(project), "semantic_index_reference": str(exported),
        }).json()["job_id"]
        exported.unlink()  # 前端提交后不再依赖外部索引文件。
        assert wait_finished(client, job_id)["semantic_index_used"]
        chain_id = client.get(f"/api/migrations/{job_id}/chains").json()[0]["chain_id"]
        detail = client.get(f"/api/migrations/{job_id}/chains/{chain_id}").json()
        assert "四个独立的计算入口" in detail["semantic_summary"]
        assert detail["stratagem"]["conversion_steps"] == ["转换完整 WCC"]
