"""
Description: 定义结构化 LLM 协议、OpenAI-compatible 真实客户端和可编程假客户端。
References: OpenAI Python SDK、typing.Protocol、Pydantic。
Referenced By: ClusterSemanticAnnotator、迁移 Agent、客户端工厂和离线测试。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from threading import Event, Lock, Timer
from time import monotonic
from typing import Any, Protocol, TypeVar

import httpx
import openai
from openai import OpenAI
from pydantic import BaseModel
from pydantic import ValidationError

from matlab_refactor_agent.domain.exceptions import (
    CallLifecycleError,
    LLMClientError,
    LLMOutputTruncatedError,
)
from matlab_refactor_agent.orchestration.call_lifecycle import (
    CallLifecycle,
    CallPolicy,
    ObservationSink,
    classify_exception,
)

from .token_budget import estimate_chat_input_tokens

ResponseT = TypeVar("ResponseT", bound=BaseModel)
StreamProgressSink = Callable[[dict[str, object]], None]


class StructuredLLMClient(Protocol):
    """作用：抽象真实或本地模型的结构化调用；输入：提示和响应模型；输出：已校验 Pydantic 模型。"""

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
        tool_name: str | None = None,
        observation_callback: ObservationSink | None = None,
        stream_progress_callback: StreamProgressSink | None = None,
    ) -> ResponseT:
        """执行一次结构化模型调用。"""


FakeResponder = Callable[[str, str, type[BaseModel]], BaseModel | dict[str, Any]]


class FakeStructuredLLMClient:
    """作用：用确定性回调模拟 LLM；输入：responder；输出：严格校验响应；数据流：Agent prompt -> callback -> Pydantic。"""

    def __init__(self, responder: FakeResponder) -> None:
        self._responder = responder
        self._lock = Lock()
        self.calls: list[tuple[str, str, str]] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
        tool_name: str | None = None,
        observation_callback: ObservationSink | None = None,
        stream_progress_callback: StreamProgressSink | None = None,
    ) -> ResponseT:
        with self._lock:
            self.calls.append(
                (system_prompt, user_prompt, response_model.__name__)
            )
        lifecycle = CallLifecycle(CallPolicy(transient_retries=0))
        started = monotonic()
        name = tool_name or f"llm.complete.{response_model.__name__}"
        if stream_progress_callback is not None:
            stream_progress_callback({
                "tool": name, "phase": "completed", "attempt": 1,
                "elapsed_ms": 0, "first_token_ms": 0,
                "reasoning_chars": 0, "content_chars": 0,
                "chunks": 0, "finish_reason": "fake",
            })
        try:
            result = self._respond(system_prompt, user_prompt, response_model)
        except Exception as exc:
            error_type, retryable = classify_exception(exc)
            lifecycle.observe(
                tool=name, status="failure", result=exc,
                error_type=error_type, retryable=retryable, attempt=1,
                started_at=started, next_action="stop",
                sink=observation_callback,
            )
            raise
        lifecycle.observe(
            tool=name, status="success",
            result=f"{response_model.__name__} 已通过结构化校验",
            error_type=None, retryable=False, attempt=1,
            started_at=started, next_action="finish",
            sink=observation_callback,
        )
        return result

    def _respond(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
    ) -> ResponseT:
        raw = self._responder(system_prompt, user_prompt, response_model)
        if isinstance(raw, response_model):
            return raw
        if isinstance(raw, BaseModel):
            raw = raw.model_dump(mode="json")
        return response_model.model_validate(raw)


class OpenAICompatibleLLMClient:
    """作用：通过 OpenAI Chat Completions 调用 DeepSeek 等兼容端点并严格解析 JSON。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 120.0,
        hard_timeout_seconds: float = 180.0,
        sdk_max_retries: int = 2,
        response_retries: int = 2,
        max_output_tokens: int = 16_384,
        model_context_window_tokens: int = 1_000_000,
        context_safety_margin_tokens: int = 8192,
        temperature: float = 0.1,
        thinking_mode: str | None = None,
        max_concurrent_calls: int = 4,
        debug_model: bool = False,
        sleep: Callable[[float], None] | None = None,
        sdk_client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMClientError("LLM API Key 为空")
        self._model = model
        self._response_retries = response_retries
        self._max_output_tokens = max_output_tokens
        self._model_context_window_tokens = model_context_window_tokens
        self._context_safety_margin_tokens = context_safety_margin_tokens
        self._temperature = temperature
        self._thinking_mode = thinking_mode
        self._hard_timeout_seconds = hard_timeout_seconds
        self._debug_model = debug_model
        self._lifecycle = CallLifecycle(
            CallPolicy(transient_retries=sdk_max_retries),
            max_concurrent_calls=max_concurrent_calls,
            debug_model=debug_model,
            **({"sleep": sleep} if sleep is not None else {}),
        )
        self._client = sdk_client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=min(timeout_seconds, hard_timeout_seconds),
            # 重试由应用层统一处理，避免 SDK 与 Agent Loop 叠加重试且无法审计。
            max_retries=0,
        )

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
        tool_name: str | None = None,
        observation_callback: ObservationSink | None = None,
        stream_progress_callback: StreamProgressSink | None = None,
    ) -> ResponseT:
        schema = json.dumps(
            response_model.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        json_system_prompt = (
            f"{system_prompt}\n"
            "必须只输出一个有效 JSON 对象，不要输出 Markdown 代码块或额外说明。"
            f"输出必须符合以下 JSON Schema：{schema}"
        )
        estimated_input_tokens = estimate_chat_input_tokens(
            json_system_prompt, user_prompt
        )
        required_context_tokens = (
            estimated_input_tokens
            + self._max_output_tokens
            + self._context_safety_margin_tokens
        )
        call_name = tool_name or f"llm.complete.{response_model.__name__}"
        if required_context_tokens > self._model_context_window_tokens:
            message = (
                "LLM 请求超过模型上下文安全上限: "
                f"估算输入 {estimated_input_tokens} + 最大输出 "
                f"{self._max_output_tokens} + 安全余量 "
                f"{self._context_safety_margin_tokens} > 上下文窗口 "
                f"{self._model_context_window_tokens}"
            )
            observation = self._lifecycle.observe(
                tool=call_name, status="failure", result=message,
                error_type="invalid_arguments", retryable=False, attempt=1,
                started_at=monotonic(), next_action="stop",
                sink=observation_callback,
            )
            raise LLMClientError(
                message, error_type="invalid_arguments", retryable=False,
                observation=observation,
            )
        last_error = "模型未返回有效 JSON"
        last_observation = None
        output_truncated = False
        current_user_prompt = user_prompt
        hard_deadline = monotonic() + self._hard_timeout_seconds
        force_disable_thinking = False
        for response_attempt in range(self._response_retries + 1):
            try:
                return self._lifecycle.invoke(
                    tool=call_name,
                    arguments={
                        "model": self._model,
                        "response_model": response_model.__name__,
                        "estimated_input_tokens": estimated_input_tokens,
                        "response_attempt": response_attempt + 1,
                    },
                    operation=lambda _arguments: self._request_and_validate(
                        json_system_prompt=json_system_prompt,
                        user_prompt=current_user_prompt,
                        response_model=response_model,
                        call_name=call_name,
                        response_attempt=response_attempt + 1,
                        hard_deadline=hard_deadline,
                        progress_callback=stream_progress_callback,
                        force_disable_thinking=force_disable_thinking,
                    ),
                    summarize=lambda _result: (
                        f"{response_model.__name__} 已通过结构化校验"
                    ),
                    deferred_error_types=(
                        {"invalid_response", "output_truncated"}
                        if response_attempt < self._response_retries else set()
                    ),
                    sink=observation_callback,
                )
            except CallLifecycleError as exc:
                last_error = str(exc)
                last_observation = exc.observation
                if exc.error_type == "output_truncated":
                    output_truncated = True
                if exc.error_type not in {"invalid_response", "output_truncated"}:
                    raise LLMClientError(
                        self._friendly_error(exc),
                        error_type=exc.error_type,
                        retryable=exc.retryable,
                        observation=exc.observation,
                    ) from exc
                if response_attempt < self._response_retries:
                    if "Qwen reasoning-only response" in last_error:
                        force_disable_thinking = True
                    current_user_prompt = (
                        f"{user_prompt}\n\n上一次输出未通过结构化校验。"
                        "请保持任务和事实不变，只修正 JSON 输出：\n"
                        f"{last_error[:2000]}"
                    )
        if output_truncated and "token 上限" in last_error:
            raise LLMOutputTruncatedError(
                f"LLM 结构化响应在 {self._response_retries + 1} 次尝试后仍无效: "
                f"{last_error}", error_type="output_truncated", retryable=False,
                observation=last_observation,
            )
        raise LLMClientError(
            f"LLM 结构化响应在 {self._response_retries + 1} 次尝试后仍无效: "
            f"{last_error}", error_type="invalid_response", retryable=False,
            observation=last_observation,
        )

    def _request_and_validate(
        self,
        *,
        json_system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
        call_name: str,
        response_attempt: int,
        hard_deadline: float,
        progress_callback: StreamProgressSink | None,
        force_disable_thinking: bool,
    ) -> ResponseT:
        extra_body = self._thinking_extra_body(force_disable_thinking)
        started = monotonic()
        remaining = hard_deadline - started
        if remaining <= 0:
            raise LLMClientError(
                f"模型调用超过硬超时 {self._hard_timeout_seconds:g} 秒",
                error_type="timeout", retryable=False,
            )
        expired = Event()
        stream_holder: list[Any] = []

        def expire() -> None:
            expired.set()
            if stream_holder:
                close = getattr(stream_holder[0], "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        pass

        timer = Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        self._emit_progress(progress_callback, {
            "tool": call_name, "phase": "started",
            "attempt": response_attempt, "elapsed_ms": 0,
            "first_token_ms": None, "reasoning_chars": 0,
            "content_chars": 0, "chunks": 0, "finish_reason": None,
        })
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": json_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                stream=True,
                max_tokens=self._max_output_tokens,
                temperature=self._temperature,
                extra_body=extra_body,
                timeout=remaining,
            )
            stream_holder.append(stream)
            if expired.is_set():
                raise LLMClientError(
                    f"模型调用超过硬超时 {self._hard_timeout_seconds:g} 秒",
                    error_type="timeout", retryable=False,
                )
            with stream:
                parts: list[str] = []
                finish_reason = None
                first_token_ms: int | None = None
                reasoning_chars = 0
                content_chars = 0
                chunks = 0
                last_emit = started
                for chunk in stream:
                    if expired.is_set():
                        raise LLMClientError(
                            f"模型调用超过硬超时 {self._hard_timeout_seconds:g} 秒",
                            error_type="timeout", retryable=False,
                        )
                    if not chunk.choices:
                        continue
                    chunks += 1
                    choice = chunk.choices[0]
                    reasoning = (
                        getattr(choice.delta, "reasoning", None)
                        or getattr(choice.delta, "reasoning_content", None)
                        or ""
                    )
                    content_part = getattr(choice.delta, "content", None) or ""
                    reasoning_chars += len(reasoning)
                    if content_part:
                        parts.append(content_part)
                        content_chars += len(content_part)
                    if (
                        reasoning_chars >= 2_048
                        and content_chars == 0
                        and "qwen" in self._model.casefold()
                        and self._thinking_mode == "enabled"
                        and not force_disable_thinking
                    ):
                        raise LLMClientError(
                            "Qwen reasoning-only response; retry with thinking disabled",
                            error_type="invalid_response",
                            retryable=True,
                        )
                    if first_token_ms is None and (reasoning or content_part):
                        first_token_ms = int((monotonic() - started) * 1000)
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
                    now = monotonic()
                    if first_token_ms is not None and (
                        chunks == 1 or now - last_emit >= 0.4
                    ):
                        last_emit = now
                        self._emit_progress(progress_callback, {
                            "tool": call_name, "phase": "streaming",
                            "attempt": response_attempt,
                            "elapsed_ms": int((now - started) * 1000),
                            "first_token_ms": first_token_ms,
                            "reasoning_chars": reasoning_chars,
                            "content_chars": content_chars,
                            "chunks": chunks, "finish_reason": finish_reason,
                        })
                content = "".join(parts)
            if expired.is_set():
                raise LLMClientError(
                    f"模型调用超过硬超时 {self._hard_timeout_seconds:g} 秒",
                    error_type="timeout", retryable=False,
                )
        except Exception as exc:
            if expired.is_set():
                self._emit_progress(progress_callback, {
                    "tool": call_name, "phase": "failed",
                    "attempt": response_attempt,
                    "elapsed_ms": int((monotonic() - started) * 1000),
                    "first_token_ms": locals().get("first_token_ms"),
                    "reasoning_chars": locals().get("reasoning_chars", 0),
                    "content_chars": locals().get("content_chars", 0),
                    "chunks": locals().get("chunks", 0),
                    "finish_reason": locals().get("finish_reason"),
                    "error_type": "timeout",
                })
                raise LLMClientError(
                    f"模型调用超过硬超时 {self._hard_timeout_seconds:g} 秒",
                    error_type="timeout", retryable=False,
                ) from None
            self._emit_progress(progress_callback, {
                "tool": call_name, "phase": "failed",
                "attempt": response_attempt,
                "elapsed_ms": int((monotonic() - started) * 1000),
                "first_token_ms": locals().get("first_token_ms"),
                "reasoning_chars": locals().get("reasoning_chars", 0),
                "content_chars": locals().get("content_chars", 0),
                "chunks": locals().get("chunks", 0),
                "finish_reason": locals().get("finish_reason"),
                "error_type": getattr(exc, "error_type", type(exc).__name__),
            })
            raise
        finally:
            timer.cancel()

        telemetry = {
            "tool": call_name, "phase": "completed",
            "attempt": response_attempt,
            "elapsed_ms": int((monotonic() - started) * 1000),
            "first_token_ms": first_token_ms,
            "reasoning_chars": reasoning_chars,
            "content_chars": content_chars,
            "chunks": chunks, "finish_reason": finish_reason,
        }
        self._emit_progress(progress_callback, telemetry)
        DEBUG_MODEL = self._debug_model
        if DEBUG_MODEL:
            print(
                f"[DEBUG_MODEL][LLM][STREAM] tool={call_name} "
                f"attempt={response_attempt} first_token_ms={first_token_ms} "
                f"reasoning_chars={reasoning_chars} content_chars={content_chars} "
                f"chunks={chunks} finish_reason={finish_reason}",
                flush=True,
            )

        if finish_reason == "length":
            raise LLMOutputTruncatedError(
                "模型输出因 token 上限被截断",
                error_type="output_truncated", retryable=True,
            )
        if finish_reason is None:
            raise LLMClientError(
                "模型流式响应未正常结束，缺少 finish_reason",
                error_type="invalid_response", retryable=True,
            )
        if not content or not content.strip():
            detail = (
                "Qwen reasoning-only response; retry with thinking disabled"
                if reasoning_chars and "qwen" in self._model.casefold()
                else "模型返回空内容"
            )
            raise LLMClientError(
                detail, error_type="invalid_response", retryable=True,
            )
        try:
            return response_model.model_validate_json(content)
        except (IndexError, TypeError, ValueError, ValidationError) as exc:
            if DEBUG_MODEL:
                print(
                    f"[DEBUG_MODEL][CALL][SCHEMA_ERROR] tool={call_name} "
                    f"response_model={response_model.__name__} "
                    "error=Schema validation error: "
                    f"{ascii(str(exc).replace(chr(10), ' ')[:2000])}",
                    flush=True,
                )
            self._emit_progress(progress_callback, {
                **telemetry, "phase": "failed",
                "error_type": "invalid_response",
                "schema_validation_error": str(exc)[:2000],
            })
            raise LLMClientError(
                f"Schema validation error: {exc}",
                error_type="invalid_response", retryable=True,
            ) from exc

    def _thinking_extra_body(
        self, force_disable_thinking: bool = False
    ) -> dict[str, object] | None:
        if self._thinking_mode is None:
            return None
        if "qwen" in self._model.casefold():
            return {
                "chat_template_kwargs": {
                    "enable_thinking": (
                        self._thinking_mode == "enabled"
                        and not force_disable_thinking
                    )
                }
            }
        return {"thinking": {"type": self._thinking_mode}}

    @staticmethod
    def _emit_progress(
        callback: StreamProgressSink | None,
        event: dict[str, object],
    ) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            # UI 心跳不能反向打断模型调用。
            pass

    @staticmethod
    def _friendly_error(exc: CallLifecycleError) -> str:
        messages = {
            "timeout": "模型服务请求超时，已达到自动重试上限",
            "rate_limited": "模型服务当前限流，已达到自动重试上限，请稍后续跑",
            "service_unavailable": "模型服务暂时不可用，已达到自动重试上限，请稍后续跑",
            "remote_protocol_error": "模型流式连接中断，已达到自动重试上限，请稍后续跑",
            "bad_request": "模型服务拒绝了请求，请检查模型名称和请求配置",
            "unauthorized": "模型 API Key 无效或已失效，请更新模型设置",
            "forbidden": "当前 API Key 无权使用该模型或接口",
        }
        message = messages.get(exc.error_type, f"LLM API 请求失败: {exc}")
        cause = exc.__cause__
        return f"{message}（{type(cause).__name__}）" if cause is not None else message
