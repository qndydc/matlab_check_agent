"""
Description: 验证 OpenAI-compatible 客户端请求参数、结构化响应重试和安全密钥工厂。
References: SimpleNamespace、Pydantic、infrastructure.llm。
Referenced By: pytest 测试发现和 DeepSeek 接入验收。
"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from matlab_refactor_agent.domain.exceptions import ConfigurationError
from matlab_refactor_agent.infrastructure.config import LLMSettings
from matlab_refactor_agent.infrastructure.llm import (
    OpenAICompatibleLLMClient,
    create_llm_client,
)


class _Answer(BaseModel):
    """作用：提供客户端结构化响应测试模型。"""

    summary: str


class _Completions:
    """作用：模拟 OpenAI SDK Chat Completions；输入：响应内容队列；输出：SDK 形状对象。"""

    def __init__(self, contents: list[str | None]) -> None:
        self._contents = iter(contents)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self._contents)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=content),
                )
            ]
        )


def _sdk(contents: list[str | None]):
    completions = _Completions(contents)
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
    assert "JSON Schema" in call["messages"][0]["content"]


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
