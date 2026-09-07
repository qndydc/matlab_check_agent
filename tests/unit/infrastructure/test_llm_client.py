"""
Description: 验证 OpenAI-compatible 客户端请求参数、结构化响应重试和安全密钥工厂。
References: SimpleNamespace、Pydantic、infrastructure.llm。
Referenced By: pytest 测试发现和 DeepSeek 接入验收。
"""

from types import SimpleNamespace
from threading import Event
from time import monotonic

import httpx
import pytest
from pydantic import BaseModel

from matlab_refactor_agent.domain.exceptions import (
    ConfigurationError,
    LLMClientError,
    LLMOutputTruncatedError,
)
from matlab_refactor_agent.infrastructure.config import LLMSettings
from matlab_refactor_agent.infrastructure.llm import (
    OpenAICompatibleLLMClient,
    create_llm_client,
)


class _Answer(BaseModel):
    """作用：提供客户端结构化响应测试模型。"""

    summary: str


class _Stream:
    """模拟可关闭的 SDK 分片流，也支持流中途传输失败。"""

    def __init__(self, chunks) -> None:
        self.chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def __iter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def _chunk(content=None, finish_reason=None, **extra):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=content, **extra),
        finish_reason=finish_reason,
    )])


class _Completions:
    """作用：模拟 OpenAI SDK Chat Completions；输入：响应内容队列；输出：SDK 形状对象。"""

    def __init__(
        self,
        contents: list[str | None],
        finish_reasons: list[str | None] | None = None,
    ) -> None:
        self._contents = iter(contents)
        self._finish_reasons = iter(finish_reasons or ["stop"] * len(contents))
        self.calls: list[dict] = []
        self.streams: list[_Stream] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self._contents)
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="思考内容不能拼进 JSON"),
            *[_chunk(character) for character in (content or "")],
            _chunk(finish_reason=next(self._finish_reasons)),
            SimpleNamespace(choices=[]),
        ]
        stream = _Stream(chunks)
        self.streams.append(stream)
        return stream


def _sdk(contents: list[str | None], finish_reasons: list[str | None] | None = None):
    completions = _Completions(contents, finish_reasons)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


def test_openai_compatible_client_retries_invalid_json() -> None:
    """作用：验证 DeepSeek JSON mode 和本地校验重试；输入：坏/好响应；输出：结构模型。"""

    sdk, completions = _sdk(["not-json", '{"summary":"ok"}'])
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        response_retries=1,
        thinking_mode="enabled",
        sdk_client=sdk,
    )

    answer = client.complete(
        system_prompt="分析 MATLAB",
        user_prompt="目标上下文",
        response_model=_Answer,
    )

    assert answer.summary == "ok"
    assert len(completions.calls) == 2
    call = completions.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["response_format"] == {"type": "json_object"}
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["max_tokens"] == 16_384
    assert call["stream"] is True
    assert all(stream.closed for stream in completions.streams)
    assert "JSON Schema" in call["messages"][0]["content"]
    assert "上一次输出未通过结构化校验" in completions.calls[1]["messages"][1]["content"]


def test_qwen_uses_chat_template_thinking_switch_and_records_stream_fields() -> None:
    sdk, completions = _sdk(['{"summary":"ok"}'])
    progress = []
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="http://localhost:8000/v1",
        model="Qwen3.5-122B-A10B-w4a8", thinking_mode="disabled",
        sdk_client=sdk,
    )

    answer = client.complete(
        system_prompt="分析", user_prompt="源码", response_model=_Answer,
        stream_progress_callback=progress.append,
    )

    assert answer.summary == "ok"
    assert completions.calls[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    terminal = progress[-1]
    assert terminal["reasoning_chars"] == len("思考内容不能拼进 JSON")
    assert terminal["content_chars"] == len('{"summary":"ok"}')
    assert terminal["first_token_ms"] is not None
    assert terminal["finish_reason"] == "stop"


def test_qwen_reasoning_only_response_retries_with_thinking_disabled() -> None:
    sdk, completions = _sdk([None, '{"summary":"ok"}'])
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="http://localhost:8000/v1",
        model="Qwen3.5", thinking_mode="enabled", response_retries=1,
        sdk_client=sdk,
    )

    answer = client.complete(
        system_prompt="分析", user_prompt="源码", response_model=_Answer,
    )

    assert answer.summary == "ok"
    assert completions.calls[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": True}
    }
    assert completions.calls[1]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_qwen_long_reasoning_is_cut_off_before_stream_finishes() -> None:
    class LongReasoningCompletions:
        def __init__(self) -> None:
            self.calls = []
            self.streams = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                chunks = [_chunk(reasoning_content="x" * 2_048)]
            else:
                chunks = [
                    _chunk(content='{"summary":"ok"}'),
                    _chunk(finish_reason="stop"),
                ]
            stream = _Stream(chunks)
            self.streams.append(stream)
            return stream

    completions = LongReasoningCompletions()
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    progress = []
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="http://localhost:8000/v1",
        model="Qwen3.5", thinking_mode="enabled", response_retries=1,
        sdk_client=sdk,
    )

    answer = client.complete(
        system_prompt="分析", user_prompt="源码", response_model=_Answer,
        stream_progress_callback=progress.append,
    )

    assert answer.summary == "ok"
    assert len(completions.calls) == 2
    assert all(stream.closed for stream in completions.streams)
    assert completions.calls[1]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    assert any(
        item["phase"] == "failed"
        and item["error_type"] == "invalid_response"
        and item["reasoning_chars"] == 2_048
        for item in progress
    )


def test_hard_timeout_closes_stream_and_stops_all_retries() -> None:
    released = Event()

    class BlockingStream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def __iter__(self):
            released.wait(2)
            return
            yield  # pragma: no cover

        def close(self):
            released.set()

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_kwargs: BlockingStream(),
    )))
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="http://localhost:8000/v1",
        model="Qwen3.5", hard_timeout_seconds=0.05,
        sdk_max_retries=2, response_retries=2, sleep=lambda _delay: None,
        sdk_client=sdk,
    )
    started = monotonic()

    with pytest.raises(LLMClientError, match="硬超时|请求超时") as raised:
        client.complete(system_prompt="分析", user_prompt="源码", response_model=_Answer)

    assert raised.value.error_type == "timeout"
    assert monotonic() - started < 0.5


def test_invalid_response_debug_log_names_schema_validation_error(capsys) -> None:
    sdk, _ = _sdk(["not-json"])
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="http://localhost:8000/v1",
        model="Qwen3.5", response_retries=0, debug_model=True,
        sdk_client=sdk,
    )

    with pytest.raises(LLMClientError, match="Schema validation error"):
        client.complete(system_prompt="分析", user_prompt="源码", response_model=_Answer)

    output = capsys.readouterr().out
    assert "[SCHEMA_ERROR]" in output
    assert "Schema validation error" in output


def test_openai_compatible_client_classifies_output_truncation() -> None:
    """作用：验证连续 length 响应转换成可触发工作单元二分的专用异常。"""

    sdk, completions = _sdk(["{}", "{}"], ["length", "length"])
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        response_retries=1,
        sdk_client=sdk,
    )

    with pytest.raises(LLMOutputTruncatedError, match="token 上限"):
        client.complete(
            system_prompt="分析 MATLAB",
            user_prompt="目标上下文",
            response_model=_Answer,
        )

    assert len(completions.calls) == 2
    assert all(stream.closed for stream in completions.streams)


@pytest.mark.parametrize("content,finish_reason", [
    ('{"summary":"不能接受未完成响应"}', None),
    (None, "stop"),
])
def test_stream_rejects_missing_finish_or_empty_content(content, finish_reason):
    sdk, completions = _sdk([content], [finish_reason])
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="https://api.deepseek.com",
        model="test-model", response_retries=0, sdk_client=sdk,
    )

    with pytest.raises(LLMClientError):
        client.complete(system_prompt="分析", user_prompt="源码", response_model=_Answer)

    assert completions.streams[0].closed


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_stream_closes_and_reports_midstream_transport_failure(error_type):
    stream = _Stream([
        _chunk('{"summary":"partial'),
        error_type("stream interrupted"),
    ])
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_kwargs: stream,
    )))
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="https://api.deepseek.com",
        model="test-model", sleep=lambda _delay: None, sdk_client=sdk,
    )

    with pytest.raises(LLMClientError, match=error_type.__name__):
        client.complete(system_prompt="分析", user_prompt="源码", response_model=_Answer)

    assert stream.closed


def test_stream_transport_failure_is_retried_and_observed() -> None:
    streams = iter([
        _Stream([_chunk('{"summary":"partial'), httpx.RemoteProtocolError("cut")]),
        _Stream([_chunk('{"summary":"ok"}'), _chunk(finish_reason="stop")]),
    ])
    calls = []
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: calls.append(kwargs) or next(streams),
    )))
    observations = []
    client = OpenAICompatibleLLMClient(
        api_key="test-key", base_url="https://api.deepseek.com",
        model="test-model", sdk_max_retries=1, sleep=lambda _delay: None,
        sdk_client=sdk,
    )

    answer = client.complete(
        system_prompt="分析", user_prompt="源码", response_model=_Answer,
        observation_callback=observations.append,
    )

    assert answer.summary == "ok"
    assert len(calls) == 2
    assert observations[0].error_type == "remote_protocol_error"
    assert observations[0].retryable is True
    assert observations[-1].status == "success"


def test_client_rejects_input_and_output_over_context_window() -> None:
    """完整提示、最大输出与余量必须共同满足模型上下文窗口。"""

    sdk, completions = _sdk(['{"summary":"unused"}'])
    client = OpenAICompatibleLLMClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        max_output_tokens=1000,
        model_context_window_tokens=2000,
        context_safety_margin_tokens=500,
        sdk_client=sdk,
    )

    with pytest.raises(LLMClientError, match="模型上下文安全上限"):
        client.complete(
            system_prompt="system",
            user_prompt="x" * 4000,
            response_model=_Answer,
        )

    assert completions.calls == []


def test_llm_factory_requires_configured_environment(monkeypatch) -> None:
    """作用：验证密钥只能来自环境；输入：缺失环境变量；输出：ConfigurationError。"""

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        create_llm_client(LLMSettings())


def test_deepseek_defaults_use_current_v4_model() -> None:
    """作用：防止恢复已停用模型别名；输入：默认配置；输出：V4 Pro 断言。"""

    settings = LLMSettings()

    assert settings.base_url == "https://api.deepseek.com"
    assert settings.model == "deepseek-v4-pro"
    assert settings.semantic_max_output_tokens == 16_384
    assert settings.migration_max_output_tokens == 32_768
    assert settings.semantic_token_budget == 32_768
    assert settings.semantic_hard_input_tokens == 975_424
