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


def create_llm_client(settings: LLMSettings) -> OpenAICompatibleLLMClient:
    """作用：避免在源码保存密钥；输入：LLMSettings；输出：真实客户端；数据流：环境变量 -> SDK。"""

    api_key = os.getenv(settings.api_key_env, "").strip()
    if not api_key:
        raise ConfigurationError(
            f"未设置环境变量 {settings.api_key_env}；请在环境或 .env 中配置 DeepSeek API Key"
        )
    return OpenAICompatibleLLMClient(
        api_key=api_key,
        base_url=settings.base_url,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        sdk_max_retries=settings.sdk_max_retries,
        response_retries=settings.response_retries,
        max_output_tokens=settings.max_output_tokens,
        temperature=settings.temperature,
        thinking_mode=settings.thinking_mode,
    )
