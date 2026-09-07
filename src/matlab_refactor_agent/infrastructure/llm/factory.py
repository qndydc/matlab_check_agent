"""
Description: 从安全环境变量和应用配置创建 OpenAI-compatible LLM Client。
References: os、infrastructure.config、llm.client。
Referenced By: AnalysisService 和客户端工厂测试。
"""

from __future__ import annotations

import os

from matlab_refactor_agent.domain.exceptions import ConfigurationError
from matlab_refactor_agent.infrastructure.config import LLMSettings

from .client import OpenAICompatibleLLMClient


def create_llm_client(
    settings: LLMSettings,
    *,
    max_output_tokens: int | None = None,
    thinking_mode: str | None = None,
    debug_model: bool = False,
) -> OpenAICompatibleLLMClient:
    """作用：按任务输出预算创建客户端；输入：LLMSettings 和可选上限；输出：真实客户端。"""

    api_key = (
        settings.api_key.get_secret_value()
        if settings.api_key is not None else os.getenv(settings.api_key_env, "")
    ).strip()
    if not api_key:
        raise ConfigurationError(
            f"未设置环境变量 {settings.api_key_env}；请在环境或 .env 中配置 DeepSeek API Key"
        )
    return OpenAICompatibleLLMClient(
        api_key=api_key,
        base_url=settings.base_url,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        hard_timeout_seconds=settings.hard_timeout_seconds,
        sdk_max_retries=settings.sdk_max_retries,
        response_retries=settings.response_retries,
        max_output_tokens=(
            settings.semantic_max_output_tokens
            if max_output_tokens is None
            else max_output_tokens
        ),
        model_context_window_tokens=settings.model_context_window_tokens,
        context_safety_margin_tokens=settings.context_safety_margin_tokens,
        temperature=settings.temperature,
        thinking_mode=(settings.thinking_mode if thinking_mode is None else thinking_mode),
        max_concurrent_calls=settings.max_agents,
        debug_model=debug_model,
    )
