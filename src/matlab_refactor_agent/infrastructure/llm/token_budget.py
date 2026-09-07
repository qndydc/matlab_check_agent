"""
Description: 为不同 OpenAI-compatible 模型提供无 tokenizer 依赖的统一 token 预算估算。
References: math。
Referenced By: LLM 客户端和语义上下文构建器。
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """估算混合代码/中文文本 token；ASCII 按四字符一个，非 ASCII 按一字符一个。"""

    ascii_characters = sum(character.isascii() for character in text)
    non_ascii_characters = len(text) - ascii_characters
    return math.ceil(ascii_characters / 4) + non_ascii_characters


def estimate_chat_input_tokens(system_prompt: str, user_prompt: str) -> int:
    """估算完整两消息请求，并为 ChatML 角色和消息边界保留固定开销。"""

    return estimate_tokens(system_prompt) + estimate_tokens(user_prompt) + 32


__all__ = ["estimate_chat_input_tokens", "estimate_tokens"]
