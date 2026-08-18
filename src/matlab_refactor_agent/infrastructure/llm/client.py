"""
Description: 定义结构化 LLM 协议、OpenAI-compatible 真实客户端和可编程假客户端。
References: OpenAI Python SDK、typing.Protocol、Pydantic。
Referenced By: SemanticAnnotationAgent、客户端工厂和离线测试。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from threading import Lock
from typing import Any, Protocol, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel
from pydantic import ValidationError

from matlab_refactor_agent.domain.exceptions import LLMClientError

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class StructuredLLMClient(Protocol):
    """作用：抽象真实或本地模型的结构化调用；输入：提示和响应模型；输出：已校验 Pydantic 模型。"""

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
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
    ) -> ResponseT:
        with self._lock:
            self.calls.append(
                (system_prompt, user_prompt, response_model.__name__)
            )
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
        sdk_max_retries: int = 2,
        response_retries: int = 2,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
        thinking_mode: str | None = None,
        sdk_client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMClientError("LLM API Key 为空")
        self._model = model
        self._response_retries = response_retries
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._thinking_mode = thinking_mode
        self._client = sdk_client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=sdk_max_retries,
        )

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseT],
    ) -> ResponseT:
        schema = json.dumps(
            response_model.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        json_system_prompt = (
            f"{system_prompt}\n"
            "必须只输出一个有效 JSON 对象，不要输出 Markdown 代码块或额外说明。"
            f"输出必须符合以下 JSON Schema：{schema}"
        )
        last_error = "模型未返回有效 JSON"
        for _ in range(self._response_retries + 1):
            try:
                extra_body = (
                    {"thinking": {"type": self._thinking_mode}}
                    if self._thinking_mode is not None
                    else None
                )
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": json_system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=self._max_output_tokens,
                    temperature=self._temperature,
                    extra_body=extra_body,
                )
            except openai.APIError as exc:
                request_id = getattr(exc, "request_id", None)
                suffix = f"，request_id={request_id}" if request_id else ""
                raise LLMClientError(
                    f"LLM API 请求失败: {type(exc).__name__}{suffix}"
                ) from exc
            try:
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    raise ValueError("模型输出因 token 上限被截断")
                content = choice.message.content
                if not content or not content.strip():
                    raise ValueError("模型返回空内容")
                return response_model.model_validate_json(content)
            except (IndexError, TypeError, ValueError, ValidationError) as exc:
                last_error = str(exc)
        raise LLMClientError(
            f"LLM 结构化响应在 {self._response_retries + 1} 次尝试后仍无效: "
            f"{last_error}"
        )
