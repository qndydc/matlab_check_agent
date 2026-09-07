"""
Description: Verify bounded R0/R1/R2 degradation without discarding Act source.
References: ReasonFallbackPolicy, CallChainContextBuilder and fake LLM clients.
Referenced By: pytest regression suite.
"""

import json

import pytest

from matlab_refactor_agent.agents.matlab_to_python.strategy import ConversionReasonAgent
from matlab_refactor_agent.domain.exceptions import LLMClientError, LLMOutputTruncatedError
from matlab_refactor_agent.domain.diagnostics import DifferentialObservation, ValidationFact
from matlab_refactor_agent.domain.migration import (
    CallChainContext, ConversionStratagem, MigrationWorkUnit, PythonArchitecture,
    TranslationFunctionContext,
)
from matlab_refactor_agent.infrastructure.llm import FakeStructuredLLMClient
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.migration.context import CallChainContextBuilder
from matlab_refactor_agent.migration.planning import MigrationPlanBuilder
from matlab_refactor_agent.migration.reason_policy import ReasonFallbackPolicy
from matlab_refactor_agent.orchestration import Orchestrator


def context():
    return CallChainContext(project_root="project",
        architecture=PythonArchitecture(package_name="p", source_directory="src/p"),
        unit=MigrationWorkUnit(unit_id="chain", symbol_ids=["main", "dep", "remote"],
            entry_symbols=["main"], sccs=[["main"], ["dep"], ["remote"]]),
        internal_dependencies=["main -> dep", "dep -> remote"],
        functions=[TranslationFunctionContext(symbol_id=name, file_path=f"{name}.m",
            source=f"function y = {name}(x)\ny = x;\nend", source_hash=name,
            inputs=["x"], outputs=["y"]) for name in ("main", "dep", "remote")])


def failed_observation():
    return DifferentialObservation(unit_id="chain", passed=False, facts=[
        ValidationFact(kind="import", passed=False, detail="repair needed")
    ])


def test_first_pass_uses_compact_reason_decision():
    client = FakeStructuredLLMClient(lambda *_: {
        "unit_id": "chain", "action": "convert",
        "conversion_steps": ["转换完整 WCC"],
    })

    result = ReasonFallbackPolicy(ConversionReasonAgent(client)).run(
        context(), None, lambda _: pytest.fail("unexpected R1"), lambda _: None
    )

    assert result.action == "convert"
    assert result.conversion_steps == ["转换完整 WCC"]
    assert [call[2] for call in client.calls] == ["ReasonDecision"]


def test_missing_dependency_degrades_after_one_request():
    original = context()
    client = FakeStructuredLLMClient(lambda *_: ConversionStratagem(
        unit_id="chain", action="rebuild_context", requested_context=["missing.m"],
        conversion_steps=["Wait forever for missing.m"]))
    requests = []

    def supplement(names):
        requests.append(names)
        return original.model_copy(update={"requested_context": names,
            "external_dependencies": {"missing.m": ["main: y = missing(x)"]}})

    result = ReasonFallbackPolicy(ConversionReasonAgent(client)).run(
        original, failed_observation(), supplement, lambda _: None
    )
    assert result.action == "convert"
    assert len(client.calls) == len(requests) == 1
    assert "missing.m" in result.external_dependencies
    assert not any("Wait forever" in step for step in result.conversion_steps)
    assert result.requested_context == []


def test_repeated_rebuild_stops_after_new_source_was_supplied():
    original = context()
    client = FakeStructuredLLMClient(lambda *_: ConversionStratagem(
        unit_id="chain", action="rebuild_context", requested_context=["extra.m"]))
    requests = []

    def supplement(names):
        requests.append(names)
        return original.model_copy(update={"requested_context": names,
            "dependency_functions": [original.functions[0].model_copy(update={"symbol_id": "extra"})]})

    result = ReasonFallbackPolicy(ConversionReasonAgent(client)).run(
        original, failed_observation(), supplement, lambda _: None
    )
    assert result.action == "convert"
    assert len(client.calls) == 2
    assert len(requests) == 1


def test_r2_keeps_core_direct_source_and_act_restores_remote_source():
    original = context()
    original.functions[-1].source += "\ny = x + 1;" * 4000
    client = FakeStructuredLLMClient(lambda *_: ConversionStratagem(unit_id="chain"))
    result = ReasonFallbackPolicy(ConversionReasonAgent(client), input_budget=2500).run(
        original, failed_observation(), lambda _: pytest.fail("unexpected R1"), lambda _: None)
    received = json.loads(client.calls[0][1])
    assert all(item["source"] for item in received["functions"][:2])
    assert received["functions"][-1]["source"] == ""
    assert received["functions"][-1]["semantic_summary"]
    act = CallChainContextBuilder.for_act(original, result)
    assert act.functions[-1].source == original.functions[-1].source


def test_reason_prompt_omits_non_decision_metadata_and_success_facts():
    original = context().model_copy(update={
        "project_structure": ["main -> p.main"],
        "semantic_index_used": True,
    })
    observation = DifferentialObservation(
        unit_id="chain", passed=False,
        matlab_result_ref="large-matlab-result.json",
        python_result_ref="large-python-result.json",
        facts=[
            ValidationFact(kind="syntax", passed=True, detail="ok"),
            ValidationFact(kind="import", passed=False, detail="missing scipy"),
        ],
    )

    payload = json.loads(ConversionReasonAgent.prompt(original, observation))

    assert "project_root" not in payload
    assert "project_structure" not in payload
    assert "semantic_index_used" not in payload
    assert "source_hash" not in payload["functions"][0]
    assert "depends_on_units" not in payload["unit"]
    assert "status" not in payload["unit"]
    assert payload["previous_failures"] == [{
        "kind": "import", "detail": "missing scipy",
        "expected": None, "actual": None,
    }]


def test_core_over_budget_uses_local_strategy_without_truncation():
    original = context()
    original.functions[0].source += "\ny = x + 1;" * 4000
    client = FakeStructuredLLMClient(lambda *_: pytest.fail("oversized Reason request"))
    result = ReasonFallbackPolicy(ConversionReasonAgent(client), input_budget=2500).run(
        original, failed_observation(), lambda _: pytest.fail("unexpected R1"), lambda _: None)
    assert result.action == "convert"
    assert "R2" in result.rationale
    assert len(original.functions[0].source) > 40_000


def test_invalid_response_retries_reduced_view_once():
    original = context()
    count = 0

    def respond(*_):
        nonlocal count
        count += 1
        raise LLMOutputTruncatedError("输出被截断")

    client = FakeStructuredLLMClient(respond)
    result = ReasonFallbackPolicy(ConversionReasonAgent(client)).run(
        original, failed_observation(), lambda _: pytest.fail("unexpected R1"), lambda _: None)
    assert count == 2
    assert result.action == "convert"
    assert json.loads(client.calls[-1][1])["functions"][-1]["source"] == ""


def test_transport_error_is_not_disguised_as_context_failure():
    def respond(*_):
        raise LLMClientError(
            "LLM API 请求失败: RemoteProtocolError",
            error_type="remote_protocol_error",
            retryable=True,
        )
    with pytest.raises(LLMClientError, match="RemoteProtocolError"):
        ReasonFallbackPolicy(ConversionReasonAgent(FakeStructuredLLMClient(respond))).run(
            context(), failed_observation(), lambda _: pytest.fail("unexpected R1"), lambda _: None)


def test_r1_resolves_only_scanned_project_and_marks_external_dependencies(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("main", "extra"):
        (project / f"{name}.m").write_text(f"function y = {name}(x)\ny = x;\nend", encoding="utf-8")
    (tmp_path / "outside.m").write_text("function outside\nend", encoding="utf-8")
    settings = AppSettings(orchestrator={"artifact_dir": tmp_path / "jobs", "state_db": tmp_path / "state.db"})
    workflow = Orchestrator.from_settings(settings)
    analysis = workflow.run_analysis(project)
    plan = MigrationPlanBuilder().build(analysis.result)
    unit = next(unit for unit in plan.units if "main" in unit.symbol_ids)
    result = CallChainContextBuilder(workflow.artifact_store).build(
        scan_reference=analysis.artifacts["scan_result"], analysis_reference=analysis.artifacts["analysis_result"],
        semantic_reference=None, plan=plan, unit=unit,
        requested_context=["extra.m", "../outside.m", "missing.m", "main.m"])
    assert [item.symbol_id for item in result.dependency_functions] == ["extra"]
    assert result.dependency_functions[0].source.startswith("function")
    assert set(result.external_dependencies) == {"../outside.m", "missing.m"}
    assert result.unit.symbol_ids == ["main"]
