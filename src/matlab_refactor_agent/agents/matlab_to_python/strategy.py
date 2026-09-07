"""
Description: 根据 WCC 上下文和上一轮观察生成转换策略或结束决策。
References: StructuredLLMClient、domain.migration、domain.diagnostics。
Referenced By: MatlabToPythonMigrationAgent。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
from matlab_refactor_agent.domain.migration import (
    CallChainContext,
    ConversionStratagem,
)
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.infrastructure.llm.client import StreamProgressSink
from matlab_refactor_agent.orchestration.call_lifecycle import ObservationSink

SYSTEM_PROMPT = (
    "你是 MATLAB 到 Python 迁移的 Reason Agent。输入是一个完整 WCC，SCC 不可拆分。"
    "只规划转换，不生成 Python，也不规划文件结构（module_plan 留空，由系统分配）。"
    "默认 action=convert；代码错误时修订 conversion_steps；确实缺少项目内源码时才用 "
    "rebuild_context，并只请求具体符号或相对路径；调用图错误用 replan；无法可靠处理用 "
    "manual_review。external_dependencies 是缺失依赖的调用点证据：可选依赖保留替代分支，"
    "未知必需依赖标记显式适配接口。源码为空表示 Reason 只收到摘要，Act 仍会拿到完整源码。"
)


class ReasonDecision(BaseModel):
    """模型只输出会影响下一步的决策；系统字段不占输出 token。"""

    unit_id: str
    action: Literal[
        "convert", "finish", "rebuild_context", "replan", "manual_review"
    ] = "convert"
    conversion_steps: list[str] = Field(default_factory=list, max_length=12)
    matlab_semantic_risks: list[str] = Field(default_factory=list, max_length=12)
    validation_plan: list[str] = Field(default_factory=list, max_length=12)
    requested_context: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(default="", max_length=2000)


class ConversionReasonAgent:
    def __init__(self, client: StructuredLLMClient,
                 observation_callback: ObservationSink | None = None,
                 stream_progress_callback: StreamProgressSink | None = None) -> None:
        self._client = client
        self._observation_callback = observation_callback
        self._stream_progress_callback = stream_progress_callback

    def reason(
        self,
        context: CallChainContext,
        observation: DifferentialObservation | None = None,
    ) -> ConversionStratagem:
        if observation is not None and observation.passed:
            return ConversionStratagem(
                unit_id=context.unit.unit_id,
                action="finish",
                rationale="整条 WCC 调用链的观察项均已通过",
            )
        prompt = self.prompt(context, observation)
        decision = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=ReasonDecision,
            tool_name=f"llm.migration.reason.{context.unit.unit_id}",
            observation_callback=self._observation_callback,
            stream_progress_callback=self._stream_progress_callback,
        )
        result = ConversionStratagem(**decision.model_dump())
        if result.unit_id != context.unit.unit_id:
            raise ValueError("Reason 返回的 unit_id 与当前 WCC 不一致")
        return result

    @staticmethod
    def prompt(
        context: CallChainContext,
        observation: DifferentialObservation | None = None,
    ) -> str:
        """仅发送会改变转换决策的事实，避免重复结构、哈希和成功日志。"""

        def function(item: Any) -> dict[str, Any]:
            return {
                "symbol_id": item.symbol_id,
                "file_path": item.file_path,
                "source": item.source,
                "inputs": item.inputs,
                "outputs": item.outputs,
                "semantic_summary": item.semantic_summary,
                "risks": item.risks,
            }

        payload: dict[str, Any] = {
            "unit": {
                "unit_id": context.unit.unit_id,
                "symbol_ids": context.unit.symbol_ids,
                "entry_symbols": context.unit.entry_symbols,
                "sccs": context.unit.sccs,
            },
            "constraints": {
                "dependencies": context.architecture.dependencies,
                "rules": context.architecture.rules,
            },
            "functions": [function(item) for item in context.functions],
            "dependency_functions": [
                function(item) for item in context.dependency_functions
            ],
            "internal_dependencies": context.internal_dependencies,
            "unresolved_calls": context.unresolved_calls,
            "requested_context": context.requested_context,
            "external_dependencies": context.external_dependencies,
            "context_notes": context.context_notes,
        }
        if observation is not None:
            payload["previous_failures"] = [
                {
                    "kind": fact.kind,
                    "detail": fact.detail,
                    "expected": _compact(fact.expected),
                    "actual": _compact(fact.actual),
                }
                for fact in observation.facts if not fact.passed
            ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _compact(value: Any) -> Any:
    """限制 Observation 中可能非常大的 stderr、数组或嵌套结果。"""

    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, list):
        return [_compact(item) for item in value[:20]]
    if isinstance(value, tuple):
        return [_compact(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key): _compact(item)
            for key, item in list(value.items())[:20]
        }
    return value


__all__ = ["ConversionReasonAgent"]
