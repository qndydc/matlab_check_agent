"""
Description: 统一模型和工具调用的前置校验、错误分类、退避及 Observation 记录。
References: domain.diagnostics、domain.exceptions、ArtifactStore。
Referenced By: LLM Client、WorkerPool 和外部执行适配器。
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import BoundedSemaphore, RLock
from time import monotonic
from typing import Any, Literal, TypeVar

import httpx
import openai
from pydantic import BaseModel, ValidationError

from matlab_refactor_agent.domain.diagnostics import (
    CallErrorType,
    CallObservation,
    CallObservationLog,
)
from matlab_refactor_agent.domain.exceptions import (
    ArtifactError,
    CallLifecycleError,
    WorkerNotFoundError,
)
from matlab_refactor_agent.infrastructure.artifacts import ArtifactStore

ResultT = TypeVar("ResultT")
ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)
RiskLevel = Literal["read_only", "write_artifact", "execute_external", "destructive"]
ObservationSink = Callable[[CallObservation], None]

_OBSERVATION_LOCK = RLock()
_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "password", "secret",
    "access_token", "refresh_token",
}
_TRANSIENT_TYPES: set[CallErrorType] = {
    "timeout", "rate_limited", "service_unavailable", "remote_protocol_error",
}


def _sensitive_argument_path(value: object, prefix: str = "") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            normalized = name.lower().replace("-", "_")
            path = f"{prefix}.{name}" if prefix else name
            if normalized in _SENSITIVE_KEYS:
                return path
            found = _sensitive_argument_path(child, path)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _sensitive_argument_path(child, f"{prefix}[{index}]")
            if found:
                return found
    return None


@dataclass(frozen=True)
class CallPolicy:
    """保持生命周期配置集中且足够简单。"""

    transient_retries: int = 2
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    allowed_risks: tuple[RiskLevel, ...] = ("read_only", "write_artifact")


def sanitize_summary(value: object, *, limit: int = 500) -> str:
    """生成不会泄露秘密或完整大对象的一行摘要。"""

    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    lowered = text.lower()
    for marker in ("bearer ", "sk-", "api_key=", "apikey="):
        position = lowered.find(marker)
        if position >= 0:
            text = text[:position] + "[REDACTED]"
            break
    return text[:limit] or "无可用摘要"


def classify_exception(exc: BaseException) -> tuple[CallErrorType, bool]:
    """把 SDK、HTTP 和本地边界错误映射成稳定分类。"""

    if isinstance(exc, WorkerNotFoundError):
        return "tool_not_found", False
    if isinstance(exc, CallLifecycleError):
        return exc.error_type, exc.retryable  # type: ignore[return-value]
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "invalid_arguments", True
    if isinstance(exc, (httpx.TimeoutException, TimeoutError, openai.APITimeoutError)):
        return "timeout", True
    if isinstance(exc, httpx.RemoteProtocolError):
        return "remote_protocol_error", True

    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limited", True
    if status_code in {408, 500, 502, 503, 504}:
        return "service_unavailable", True
    if status_code == 400:
        return "bad_request", False
    if status_code == 401:
        return "unauthorized", False
    if status_code == 403:
        return "forbidden", False
    if isinstance(exc, (openai.APIConnectionError, ConnectionError)):
        return "service_unavailable", True
    return "unknown", False


class CallObservationRecorder:
    """复用 ArtifactStore 保存一个 Job 的有界调用日志。"""

    def __init__(self, artifacts: ArtifactStore, job_id: str, *, limit: int = 500) -> None:
        self._artifacts = artifacts
        self._job_id = job_id
        self._limit = limit

    def __call__(self, observation: CallObservation) -> None:
        path = self._artifacts.root / self._job_id / "call-observations.json"
        try:
            with _OBSERVATION_LOCK:
                if path.is_file():
                    log = self._artifacts.read_model(str(path), CallObservationLog)
                else:
                    log = CallObservationLog(job_id=self._job_id)
                log.observations = [*log.observations, observation][-self._limit:]
                self._artifacts.write_model(self._job_id, path.name, log)
        except (ArtifactError, OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("写入调用 Observation 失败：%s", exc)


class CallLifecycle:
    """一个小型同步调用边界；业务图只处理业务质量，不重复处理传输故障。"""

    def __init__(
        self,
        policy: CallPolicy | None = None,
        *,
        max_concurrent_calls: int = 4,
        debug_model: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy or CallPolicy()
        self._limiter = BoundedSemaphore(max(1, max_concurrent_calls))
        self._debug_model = debug_model
        self._sleep = sleep
        self._random = random_value

    @staticmethod
    def validate_arguments(
        arguments: Mapping[str, Any],
        input_model: type[ArgumentsT] | None = None,
    ) -> dict[str, Any]:
        sensitive_path = _sensitive_argument_path(arguments)
        if sensitive_path:
            raise CallLifecycleError(
                f"工具参数不允许携带敏感字段: {sensitive_path}",
                error_type="unsafe_action",
                retryable=False,
            )
        if input_model is None:
            return dict(arguments)
        return input_model.model_validate(dict(arguments)).model_dump(mode="python")

    def observe(
        self,
        *,
        tool: str,
        status: Literal["success", "failure"],
        result: object,
        error_type: CallErrorType | None,
        retryable: bool,
        attempt: int,
        started_at: float,
        next_action: str,
        sink: ObservationSink | None,
    ) -> CallObservation:
        observation = CallObservation(
            tool=tool,
            status=status,
            result=sanitize_summary(result),
            error_type=error_type,
            retryable=retryable,
            attempt=attempt,
            duration_ms=max(0, int((monotonic() - started_at) * 1000)),
            next_action=next_action,
        )
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            message = (
                f"[DEBUG_MODEL][CALL][{observation.status.upper()}] "
                f"tool={observation.tool} attempt={observation.attempt} "
                f"error_type={observation.error_type or '-'} "
                f"retryable={observation.retryable} next={observation.next_action} "
                f"duration_ms={observation.duration_ms} result={observation.result}"
            )
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(
                message.encode(encoding, errors="backslashreplace").decode(encoding),
                flush=True,
            )
        if sink is not None:
            try:
                sink(observation)
            except Exception:
                logging.getLogger(__name__).exception("调用 Observation 回调失败")
        return observation

    def backoff(self, attempt: int, exc: BaseException | None = None) -> None:
        retry_after = self._retry_after(exc)
        delay = retry_after if retry_after is not None else min(
            self.policy.max_backoff_seconds,
            self.policy.base_backoff_seconds * (2 ** max(0, attempt - 1))
            + self._random() * 0.25,
        )
        self._sleep(max(0.0, delay))

    def invoke(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any],
        operation: Callable[[dict[str, Any]], ResultT],
        input_model: type[ArgumentsT] | None = None,
        argument_validator: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
        risk: RiskLevel = "read_only",
        allow_high_risk: bool = False,
        idempotent: bool = True,
        summarize: Callable[[ResultT], object] | None = None,
        is_empty: Callable[[ResultT], bool] | None = None,
        broaden: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
        repair_arguments: Callable[[dict[str, Any], str], Mapping[str, Any]] | None = None,
        argument_source: Literal["code", "user", "llm"] = "code",
        deferred_error_types: set[CallErrorType] | None = None,
        sink: ObservationSink | None = None,
    ) -> ResultT:
        """执行注册工具；参数修正与查询泛化都只发生在明确提供回调时。"""

        current = dict(arguments)
        argument_repairs = 0
        broadened = False
        attempt = 0
        transient_failures = 0
        while True:
            attempt += 1
            started = monotonic()
            DEBUG_MODEL = self._debug_model
            if DEBUG_MODEL:
                print(
                    f"[DEBUG_MODEL][CALL][START] tool={tool} attempt={attempt} "
                    f"risk={risk} argument_fields={sorted(current)}",
                    flush=True,
                )
            try:
                if risk not in self.policy.allowed_risks and not allow_high_risk:
                    raise CallLifecycleError(
                        f"调用 {tool} 被风险策略阻止: {risk}",
                        error_type="unsafe_action",
                        retryable=False,
                    )
                validated = self.validate_arguments(current, input_model)
                if argument_validator is not None:
                    validated = dict(argument_validator(validated))
            except (ValidationError, json.JSONDecodeError, TypeError, CallLifecycleError) as exc:
                error_type, retryable = (
                    classify_exception(exc)
                    if isinstance(exc, CallLifecycleError)
                    else ("invalid_arguments", True)
                )
                can_repair = (
                    error_type == "invalid_arguments"
                    and argument_source == "llm"
                    and repair_arguments is not None
                    and argument_repairs < 2
                )
                observation = self.observe(
                    tool=tool, status="failure", result=exc,
                    error_type=error_type, retryable=can_repair,
                    attempt=attempt, started_at=started,
                    next_action="repair_arguments" if can_repair else "stop", sink=sink,
                )
                if can_repair:
                    argument_repairs += 1
                    current = dict(repair_arguments(current, str(exc)))
                    continue
                raise CallLifecycleError(
                    sanitize_summary(exc), error_type=error_type,
                    retryable=False, observation=observation,
                ) from exc

            try:
                with self._limiter:
                    value = operation(validated)
            except Exception as exc:
                error_type, retryable = classify_exception(exc)
                if error_type == "invalid_arguments" and argument_source != "llm":
                    retryable = False
                if error_type in _TRANSIENT_TYPES:
                    transient_failures += 1
                can_retry = (
                    retryable and error_type in _TRANSIENT_TYPES and idempotent
                    and transient_failures <= self.policy.transient_retries
                )
                deferred = error_type in (deferred_error_types or set())
                observation = self.observe(
                    tool=tool, status="failure", result=exc,
                    error_type=error_type, retryable=can_retry or (deferred and retryable),
                    attempt=attempt, started_at=started,
                    next_action=(
                        "retry_same_arguments" if can_retry
                        else "repair_response" if deferred and retryable
                        else "stop"
                    ),
                    sink=sink,
                )
                if can_retry:
                    self.backoff(transient_failures, exc)
                    continue
                raise CallLifecycleError(
                    sanitize_summary(exc), error_type=error_type,
                    retryable=retryable, observation=observation,
                ) from exc

            if is_empty is not None and is_empty(value):
                can_broaden = broaden is not None and not broadened
                observation = self.observe(
                    tool=tool, status="failure", result="工具成功但未返回可用结果",
                    error_type="empty_result", retryable=can_broaden,
                    attempt=attempt, started_at=started,
                    next_action="broaden_query" if can_broaden else "stop", sink=sink,
                )
                if can_broaden:
                    broadened = True
                    current = dict(broaden(validated))
                    continue
                raise CallLifecycleError(
                    "工具未返回可用结果", error_type="empty_result",
                    retryable=False, observation=observation,
                )

            self.observe(
                tool=tool, status="success",
                result=summarize(value) if summarize else type(value).__name__,
                error_type=None, retryable=False, attempt=attempt,
                started_at=started, next_action="finish", sink=sink,
            )
            return value

    @staticmethod
    def _retry_after(exc: BaseException | None) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get("retry-after") or headers.get("Retry-After")
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None


__all__ = [
    "CallLifecycle", "CallObservationRecorder", "CallPolicy", "ObservationSink",
    "RiskLevel", "classify_exception", "sanitize_summary",
]
