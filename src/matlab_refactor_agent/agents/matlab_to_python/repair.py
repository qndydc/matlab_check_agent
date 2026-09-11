"""
Description: 基于结构化验证证据修订既有 Python 生成结果。
References: StructuredLLMClient、domain.diagnostics、domain.migration。
Referenced By: LangGraph repair 节点扩展。
"""

from __future__ import annotations

from matlab_refactor_agent.domain.diagnostics import FailureDiagnostic
from matlab_refactor_agent.domain.migration import TranslationResponse
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.orchestration.execution_pool import global_heavy_pool
from matlab_refactor_agent.orchestration.call_lifecycle import ObservationSink

SYSTEM_PROMPT = (
    "你是 MATLAB 到 Python 迁移 Repair Agent。只能根据结构化失败事实修订已有 Python 文件；"
    "不得修改工作单元边界，不得用占位实现绕过验证，返回完整 TranslationResponse。"
)


class MatlabToPythonRepairAgent:
    def __init__(self, client: StructuredLLMClient,
                 observation_callback: ObservationSink | None = None) -> None:
        self._client = client
        self._observation_callback = observation_callback

    def repair(self, translation: TranslationResponse,
               diagnostic: FailureDiagnostic) -> TranslationResponse:
        response = global_heavy_pool.run(
            self._client.complete,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=(translation.model_dump_json(indent=2) + "\n失败事实:\n" +
                         diagnostic.model_dump_json(indent=2)),
            response_model=TranslationResponse,
            tool_name=f"llm.migration.repair.{translation.unit_id}",
            observation_callback=self._observation_callback,
        )
        if response.unit_id != translation.unit_id:
            raise ValueError("Repair Agent 不得改变 unit_id")
        return response
