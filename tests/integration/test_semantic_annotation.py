"""
Description: 验证真实前处理与假 LLM 组成的三级语义识别闭环。
References: Orchestrator、FakeStructuredLLMClient、MATLAB fixture。
Referenced By: pytest 测试发现。
"""

import json
import shutil
import pytest
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from matlab_refactor_agent.application.semantic_service import SemanticService
from matlab_refactor_agent.apps.semantic.backend.app import MvpJobManager, create_app
from pathlib import Path

from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.infrastructure.llm import FakeStructuredLLMClient
from matlab_refactor_agent.orchestration import Orchestrator
from matlab_refactor_agent.domain.exceptions import OrchestrationError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "matlab_projects" / "basic"


@pytest.mark.parametrize("failure_stage", ["ClusterAnnotationDraftResponse", "FileAnnotationDraft", "ProjectAnnotationDraft"])
def test_semantic_resume_after_process_restart(tmp_path, failure_stage):
    project = tmp_path / "matlab"
    project.mkdir()
    for index in range(10):
        name = f"func{index}"
        (project / f"{name}.m").write_text(f"function y = {name}(x)\ny = x + 1;\nend\n", encoding="utf-8")
    settings = AppSettings(orchestrator={"state_db": tmp_path / "state.db",
                                        "artifact_dir": tmp_path / "jobs"})
    seen = []

    def interrupt(system, prompt, model):
        if model.__name__ == failure_stage:
            seen.append(prompt)
            if len(seen) == (1 if failure_stage == "ProjectAnnotationDraft" else 2):
                raise KeyboardInterrupt()
        return _response(system, prompt, model)

    first_client = FakeStructuredLLMClient(interrupt)
    with pytest.raises(KeyboardInterrupt):
        Orchestrator.from_settings(settings, semantic_client=first_client).run_annotation(project, job_id="resume123")
    job_dir = settings.orchestrator.artifact_dir / "resume123"
    checkpoint = json.loads((job_dir / "semantic-checkpoint.json").read_text(encoding="utf-8"))
    accepted = [item for item in checkpoint["graph_state"]["quality_history"] if item["status"] == "accepted"]
    assert accepted
    saved = {item["annotation_reference"]: Path(item["annotation_reference"]).read_bytes() for item in accepted}
    old_events = json.loads((job_dir / "semantic-progress.json").read_text(encoding="utf-8"))["events"]
    resumed_client = FakeStructuredLLMClient(_response)
    result = Orchestrator.from_settings(settings, semantic_client=resumed_client).resume_annotation("resume123")
    assert len(result.index.functions) == 10
    assert all(Path(path).read_bytes() == content for path, content in saved.items())
    events = json.loads((job_dir / "semantic-progress.json").read_text(encoding="utf-8"))["events"]
    assert events[:len(old_events)] == old_events
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    if failure_stage != "ClusterAnnotationDraftResponse":
        assert not any(call[2] == "ClusterAnnotationDraftResponse" for call in resumed_client.calls)
    if failure_stage == "ProjectAnnotationDraft":
        assert [call[2] for call in resumed_client.calls] == ["ProjectAnnotationDraft"]
        if failure_stage == "FileAnnotationDraft":
            # Other in-flight files may complete before ThreadPoolExecutor exits.
            assert 0 < sum(call[2] == "FileAnnotationDraft" for call in resumed_client.calls) < 9
    # Completed resume does not spend additional model calls.
    completed_client = FakeStructuredLLMClient(_response)
    Orchestrator.from_settings(settings, semantic_client=completed_client).resume_annotation("resume123")
    assert completed_client.calls == []


@pytest.mark.parametrize("change", ["modify", "add", "delete"])
def test_semantic_resume_rejects_changed_source_and_full_run_rebuilds(tmp_path, change):
    project = tmp_path / "matlab"
    shutil.copytree(FIXTURE, project)
    settings = AppSettings(orchestrator={"state_db": tmp_path / "state.db", "artifact_dir": tmp_path / "jobs"})
    workflow = Orchestrator.from_settings(settings, semantic_client=FakeStructuredLLMClient(_response))
    original = workflow.run_annotation(project)
    path = next(project.rglob("*.m"))
    if change == "modify":
        path.write_text(path.read_text(encoding="utf-8") + "\n% changed\n", encoding="utf-8")
    elif change == "delete":
        path.unlink()
    else:
        (project / "added.m").write_text("function y = added(x)\ny = x;\nend\n", encoding="utf-8")
    with pytest.raises(OrchestrationError, match="源码已变化"):
        workflow.resume_annotation(original.job_id)
    restarted = workflow.run_annotation(project)
    assert restarted.job_id != original.job_id
    assert restarted.artifacts["structural_code_tree"] != original.artifacts["structural_code_tree"]
    assert Path(original.artifacts["structural_code_tree"]).is_file()


def _response(_system_prompt: str, user_prompt: str, response_model):
    context = json.loads(user_prompt)
    if response_model.__name__ in {"ConversionStratagem", "ReasonDecision"}:
        return {
            "unit_id": context["unit"]["unit_id"],
            "action": "convert",
            "module_plan": {
                item["symbol_id"]: f"src/generated/{item['symbol_id']}.py"
                for item in context["functions"]
            },
            "conversion_steps": ["按 WCC 内调用顺序转换全部函数"],
            "validation_plan": ["执行静态检查"],
        }
    if response_model.__name__ == "FileAnnotationDraft":
        return {"role": "承担该文件内 MATLAB 函数的处理职责", "risks": [], "confidence": 0.9}
    if response_model.__name__ == "ProjectAnnotationDraft":
        return {"purpose": "演示 MATLAB 数据处理与调用关系", "usage": "从入口函数运行项目", "risks": [], "confidence": 0.9}
    if response_model.__name__ == "TranslationResponse":
        symbols = [item["symbol_id"] for item in context["functions"]]
        return {
            "unit_id": context["unit"]["unit_id"],
            "files": [{
                "path": f"src/generated/{context['unit']['unit_id']}.py",
                "content": "\"\"\"Generated test module.\"\"\"\n",
                "symbol_ids": symbols,
            }],
            "assumptions": [],
            "manual_review": [],
            "confidence": 0.8,
        }
    annotations = []
    for function in context["functions"]:
        annotations.append({
            "symbol_id": function["symbol_id"],
            "file_path": function["file_path"],
            "start_line": function["start_line"],
            "end_line": function["end_line"],
            "summary": f"分析 {function['symbol_id']} 的 MATLAB 逻辑",
            "inputs": function["inputs"],
            "outputs": function["outputs"],
            "data_flow": ["输入经计算形成输出"],
            "side_effects": [], "risks": [], "open_questions": [],
            "confidence": 0.9,
            "is_algorithm_core": function["symbol_id"] == "processData",
        })
    return {"unit_id": context["unit"]["unit_id"], "annotations": annotations}


def test_semantic_annotation_closed_loop(tmp_path: Path) -> None:
    settings = AppSettings(orchestrator={
        "state_db": tmp_path / "state.db",
        "artifact_dir": tmp_path / "jobs",
        "parser_chunk_size": 3,
    })
    client = FakeStructuredLLMClient(_response)

    outcome = Orchestrator.from_settings(settings, semantic_client=client).run_annotation(FIXTURE)

    assert len(outcome.index.functions) == 8
    assert outcome.index.project.entry_points
    assert outcome.index.core_functions == ["processData"]
    assert {"scan_result", "analysis_result", "semantic_index"} <= outcome.artifacts.keys()
    assert "call_observations" in outcome.artifacts
    assert outcome.artifacts["structural_code_tree"] != outcome.artifacts["semantic_code_tree"]
    response_types = {call[2] for call in client.calls}
    assert {"ClusterAnnotationDraftResponse", "FileAnnotationDraft", "ProjectAnnotationDraft"} <= response_types
    assert all(item.evidence for item in outcome.index.functions)


def test_semantic_web_restart_resume_and_new_graph(tmp_path):
    project = tmp_path / "matlab"
    shutil.copytree(FIXTURE, project)
    settings = AppSettings(orchestrator={"state_db": tmp_path / "state.db",
        "web_db": tmp_path / "web.db", "artifact_dir": tmp_path / "jobs"})

    def fail_project(system, prompt, model):
        if model.__name__ == "ProjectAnnotationDraft":
            raise RuntimeError("simulated connection failure")
        return _response(system, prompt, model)

    model = FakeStructuredLLMClient(fail_project)

    def factory():
        service = SemanticService(settings)
        service._orchestrator = Orchestrator.from_settings(settings, semantic_client=model)
        return service

    def wait(client, job_id):
        for _ in range(300):
            current = client.get(f"/api/jobs/{job_id}").json()
            if current["state"] not in {"queued", "running"}:
                return current
            time.sleep(0.01)
        pytest.fail("semantic web job did not finish")

    manager = MvpJobManager(service_factory=factory, settings=settings, executor=ThreadPoolExecutor(1))
    with TestClient(create_app(manager)) as client:
        original = client.post("/api/jobs/analyze", json={"project_path": str(project)}).json()["job_id"]
        assert wait(client, original)["state"] == "completed"
        assert client.post(f"/api/jobs/{original}/resume").status_code == 409
        assert client.post(f"/api/jobs/{original}/annotate").status_code == 202
        failed = wait(client, original)
        assert failed["state"] == "failed" and failed["can_resume"]
    manager._executor.shutdown()
    # Recreate the web manager, SQLite store, service and model.
    model = FakeStructuredLLMClient(_response)
    manager = MvpJobManager(service_factory=factory, settings=settings, executor=ThreadPoolExecutor(1))
    with TestClient(create_app(manager)) as client:
        assert client.get(f"/api/jobs/{original}").json()["can_resume"]
        assert client.post(f"/api/jobs/{original}/resume").status_code == 202
        assert wait(client, original)["state"] == "completed"
        assert [call[2] for call in model.calls] == ["ProjectAnnotationDraft"]
        old_graph = client.get(f"/api/jobs/{original}/graph").json()
        (project / "added.m").write_text("function y = added(x)\ny = x;\nend\n", encoding="utf-8")
        assert client.post(f"/api/jobs/{original}/resume").status_code == 409
        response = client.post(f"/api/jobs/{original}/restart")
        assert response.status_code == 202
        restarted = response.json()["job_id"]
        assert restarted != original
        assert wait(client, restarted)["state"] == "completed"
        new_graph = client.get(f"/api/jobs/{restarted}/graph").json()
        assert len(new_graph["nodes"]) == len(old_graph["nodes"]) + 1
        assert client.get(f"/api/jobs/{original}/graph").json() == old_graph
    manager._executor.shutdown()


def test_semantic_resume_retries_units_after_quality_retry_limit(tmp_path):
    settings = AppSettings(orchestrator={"state_db": tmp_path / "state.db", "artifact_dir": tmp_path / "jobs"})

    def unavailable(system, prompt, model):
        if model.__name__ == "ClusterAnnotationDraftResponse":
            raise RuntimeError("model unavailable")
        return _response(system, prompt, model)

    first = Orchestrator.from_settings(settings, semantic_client=FakeStructuredLLMClient(unavailable)).run_annotation(FIXTURE)
    assert first.index.conflicts
    resumed = Orchestrator.from_settings(settings, semantic_client=FakeStructuredLLMClient(_response)).resume_annotation(first.job_id)
    assert len(resumed.index.functions) == 8
    assert not resumed.index.conflicts


def test_main_workflow_controls_matlab_to_python_agents(tmp_path: Path) -> None:
    settings = AppSettings(orchestrator={
        "state_db": tmp_path / "state.db",
        "artifact_dir": tmp_path / "jobs",
        "parser_chunk_size": 3,
    })
    client = FakeStructuredLLMClient(_response)

    outcome = Orchestrator.from_settings(
        settings, semantic_client=client
    ).run_matlab_to_python(FIXTURE)

    assert outcome.plan.units
    assert len(outcome.translations) == len(outcome.plan.units)
    assert "migration_plan" in outcome.artifacts
    assert "call_observations" in outcome.artifacts
    assert "semantic_index" not in outcome.artifacts
    assert all(item.files for item in outcome.translations)
    response_types = {call[2] for call in client.calls}
    assert "TranslationResponse" in response_types
    assert "ReasonDecision" in response_types
    assert "ClusterAnnotationDraftResponse" not in response_types
    assert "migration_checkpoint" in outcome.artifacts

    resumed_client = FakeStructuredLLMClient(_response)
    resumed = Orchestrator.from_settings(
        settings, semantic_client=resumed_client
    ).resume_matlab_to_python(outcome.job_id)

    assert resumed.job_id == outcome.job_id
    assert len(resumed.translations) == len(outcome.translations)
    assert resumed_client.calls == []
