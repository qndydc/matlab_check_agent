"""
Description: 验证统一调用生命周期的参数、安全、重试和空结果策略。
References: CallLifecycle、ArtifactStore、CallObservation。
Referenced By: pytest 测试发现和调用生命周期回归验收。
"""

from pathlib import Path

import httpx
import pytest

from matlab_refactor_agent.domain.diagnostics import CallObservationLog
from matlab_refactor_agent.domain.enums import WorkerKind
from matlab_refactor_agent.domain.exceptions import CallLifecycleError, WorkerNotFoundError
from matlab_refactor_agent.domain.models import DomainModel
from matlab_refactor_agent.domain.orchestration import TaskEnvelope
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore
from matlab_refactor_agent.orchestration.call_lifecycle import (
    CallLifecycle,
    CallObservationRecorder,
    CallPolicy,
)
from matlab_refactor_agent.orchestration.worker_pool import WorkerPool
from matlab_refactor_agent.workers.base import WorkerContext


class _Query(DomainModel):
    query: str


def _lifecycle() -> CallLifecycle:
    return CallLifecycle(
        CallPolicy(transient_retries=2),
        sleep=lambda _delay: None,
        random_value=lambda: 0.0,
    )


def test_dirty_arguments_are_blocked_before_operation() -> None:
    called = False
    observations = []

    def operation(_arguments):
        nonlocal called
        called = True

    with pytest.raises(CallLifecycleError) as caught:
        _lifecycle().invoke(
            tool="search", arguments={"query": "x", "hidden": True},
            input_model=_Query, operation=operation, sink=observations.append,
        )

    assert not called
    assert caught.value.error_type == "invalid_arguments"
    assert observations[-1].status == "failure"
    assert observations[-1].retryable is False


def test_llm_arguments_can_be_repaired_from_exact_validation_error() -> None:
    observations = []
    errors = []

    result = _lifecycle().invoke(
        tool="search",
        arguments={},
        input_model=_Query,
        operation=lambda arguments: [arguments["query"]],
        argument_source="llm",
        repair_arguments=lambda _arguments, error: (
            errors.append(error) or {"query": "wider"}
        ),
        sink=observations.append,
    )

    assert result == ["wider"]
    assert "query" in errors[0]
    assert [item.next_action for item in observations] == [
        "repair_arguments", "finish"
    ]


def test_transient_error_retries_with_same_arguments() -> None:
    seen = []
    observations = []

    def operation(arguments):
        seen.append(dict(arguments))
        if len(seen) == 1:
            raise httpx.RemoteProtocolError("stream interrupted")
        return {"ok": True}

    result = _lifecycle().invoke(
        tool="remote", arguments={"query": "same"}, operation=operation,
        sink=observations.append,
    )

    assert result == {"ok": True}
    assert seen == [{"query": "same"}, {"query": "same"}]
    assert observations[0].error_type == "remote_protocol_error"
    assert observations[0].next_action == "retry_same_arguments"
    assert observations[-1].status == "success"


def test_debug_model_prints_readable_call_lifecycle(capsys) -> None:
    lifecycle = CallLifecycle(
        debug_model=True,
        sleep=lambda _delay: None,
        random_value=lambda: 0.0,
    )

    lifecycle.invoke(
        tool="llm.semantic.annotate.wcc-1",
        arguments={"model": "example", "response_model": "Draft"},
        operation=lambda _arguments: {"ok": True},
        summarize=lambda _result: "Draft 已通过结构化校验",
    )

    output = capsys.readouterr().out
    assert "[DEBUG_MODEL][CALL][START]" in output
    assert "tool=llm.semantic.annotate.wcc-1" in output
    assert "[DEBUG_MODEL][CALL][SUCCESS]" in output
    assert "Draft 已通过结构化校验" in output


def test_debug_model_false_keeps_call_lifecycle_quiet(capsys) -> None:
    _lifecycle().invoke(
        tool="quiet",
        arguments={"query": "x"},
        operation=lambda _arguments: "ok",
    )

    assert capsys.readouterr().out == ""


def test_rate_limit_respects_retry_after() -> None:
    class RateLimitError(Exception):
        status_code = 429
        response = type("Response", (), {"headers": {"Retry-After": "3"}})()

    delays = []
    calls = []
    lifecycle = CallLifecycle(
        CallPolicy(transient_retries=1),
        sleep=delays.append,
        random_value=lambda: 0.0,
    )

    def operation(arguments):
        calls.append(dict(arguments))
        if len(calls) == 1:
            raise RateLimitError("limited")
        return "ok"

    assert lifecycle.invoke(
        tool="remote", arguments={"query": "same"}, operation=operation
    ) == "ok"
    assert calls == [{"query": "same"}, {"query": "same"}]
    assert delays == [3.0]


def test_empty_result_gets_exactly_one_broader_query() -> None:
    seen = []

    result = _lifecycle().invoke(
        tool="search", arguments={"query": "exact"},
        operation=lambda arguments: (
            seen.append(arguments["query"]) or
            ([] if arguments["query"] == "exact" else ["found"])
        ),
        is_empty=lambda value: not value,
        broaden=lambda _arguments: {"query": "broad"},
    )

    assert result == ["found"]
    assert seen == ["exact", "broad"]


def test_second_empty_result_stops_without_more_generalization() -> None:
    seen = []
    with pytest.raises(CallLifecycleError) as caught:
        _lifecycle().invoke(
            tool="search", arguments={"query": "exact"},
            operation=lambda arguments: seen.append(arguments["query"]) or [],
            is_empty=lambda value: not value,
            broaden=lambda _arguments: {"query": "broad"},
        )

    assert seen == ["exact", "broad"]
    assert caught.value.error_type == "empty_result"
    assert caught.value.retryable is False


@pytest.mark.parametrize("status_code,error_type", [
    (400, "bad_request"), (401, "unauthorized"), (403, "forbidden"),
])
def test_permanent_http_error_is_not_retried(status_code, error_type) -> None:
    class PermanentError(Exception):
        pass

    PermanentError.status_code = status_code

    calls = []
    observations = []
    with pytest.raises(CallLifecycleError) as caught:
        _lifecycle().invoke(
            tool="remote", arguments={"query": "x"},
            operation=lambda _arguments: calls.append(1) or (_ for _ in ()).throw(
                PermanentError("forbidden")
            ),
            sink=observations.append,
        )

    assert len(calls) == 1
    assert caught.value.error_type == error_type
    assert observations[-1].retryable is False


def test_missing_worker_stops_and_records_observation(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path)
    task = TaskEnvelope(job_id="job1", worker_kind=WorkerKind.SCANNER)

    with pytest.raises(WorkerNotFoundError) as caught:
        WorkerPool().execute(task, WorkerContext(artifacts))

    assert caught.value.error_type == "tool_not_found"
    log = artifacts.read_model(
        artifacts.reference("job1", "call-observations.json"),
        CallObservationLog,
    )
    assert log.observations[-1].error_type == "tool_not_found"
    assert log.observations[-1].retryable is False


def test_sensitive_and_high_risk_calls_are_blocked() -> None:
    for arguments, risk in [
        ({"options": {"api_key": "secret"}}, "read_only"),
        ({"query": "x"}, "destructive"),
    ]:
        with pytest.raises(CallLifecycleError) as caught:
            _lifecycle().invoke(
                tool="unsafe", arguments=arguments,
                operation=lambda _arguments: "unused", risk=risk,
            )
        assert caught.value.error_type == "unsafe_action"


def test_observations_are_persisted_without_full_results(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path)
    recorder = CallObservationRecorder(artifacts, "job1")

    _lifecycle().invoke(
        tool="search", arguments={"query": "x"},
        operation=lambda _arguments: ["a"],
        summarize=lambda _result: "one result", sink=recorder,
    )

    log = artifacts.read_model(
        artifacts.reference("job1", "call-observations.json"),
        CallObservationLog,
    )
    assert log.observations[0].tool == "search"
    assert log.observations[0].result == "one result"
