"""
Description: 依据有限源码、三级语义和全局架构生成单个 MATLAB 工作单元的 Python 提议。
References: BaseAgent、TranslationContextBuilder、StructuredLLMClient、domain.migration。
Referenced By: AnalysisWorkflow 和迁移 Agent 测试。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from matlab_refactor_agent.agents.base import AgentContext, BaseAgent
from matlab_refactor_agent.domain.agents import AgentProposal, AgentRequest, AgentResult
from matlab_refactor_agent.domain.enums import AgentKind
from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.migration import (
    ActContext,
    MatlabToPythonPlan,
    MigrationWorkUnit,
    TranslationResponse,
)
from matlab_refactor_agent.infrastructure.llm import StructuredLLMClient
from matlab_refactor_agent.infrastructure.llm.client import StreamProgressSink
from matlab_refactor_agent.orchestration.call_lifecycle import ObservationSink

from .context_builder import TranslationContextBuilder

SYSTEM_PROMPT = (
    "你是 MATLAB 到 Python 科学计算迁移 Act Agent。只能依据给定 Stratagem 和当前 ActChunk 源码转换，"
    "不得重新规划项目结构，不得扩大 ActChunk 的 symbol_ids，不得臆造未提供的实现。"
    "优先使用 NumPy/SciPy，显式处理 MATLAB 一基索引、列主序、多返回值、"
    "矩阵运算与逐元素运算差异。返回完整、可写入文件的结构化结果；"
    "无法可靠转换的行为写入 manual_review，不得用占位实现伪装完成。"
    "dependency_functions 是参考依赖源码；dependency_interfaces 是已冻结依赖 chunk 的接口摘要；"
    "二者都不改变 unit.symbol_ids 的输出覆盖范围，也不得重复生成依赖实现。"
    "外部依赖按调用点保留适配接口；原代码已有可选依赖替代分支时使用该分支。"
    "无法实现的必需依赖明确抛出 NotImplementedError 并列入 manual_review；不得返回假数据。"
)


class MatlabToPythonAgent(BaseAgent):
    """为单个依赖原子单元生成结构化 Python 文件提议。"""

    def __init__(self, client: StructuredLLMClient,
                 observation_callback: ObservationSink | None = None,
                 stream_progress_callback: StreamProgressSink | None = None) -> None:
        self._client = client
        self._observation_callback = observation_callback
        self._stream_progress_callback = stream_progress_callback

    @property
    def kind(self) -> AgentKind:
        return AgentKind.MATLAB_TO_PYTHON

    def run(self, request: AgentRequest, context: AgentContext) -> AgentResult:
        if "act_context" in request.artifact_refs:
            act_context = context.artifact_store.read_model(
                request.artifact_refs["act_context"], ActContext
            )
            unit = act_context.unit
            prompt = act_context.model_dump_json(indent=2)
            evidence = [item.source_hash for item in act_context.functions]
        else:
            # 兼容旧调用方；新迁移闭环始终使用上面的最小 ActContext。
            unit = MigrationWorkUnit.model_validate(request.inputs["work_unit"])
            plan = context.artifact_store.read_model(
                request.artifact_refs["migration_plan"], MatlabToPythonPlan
            )
            translation_context = TranslationContextBuilder(context.artifact_store).build(
                scan_reference=request.artifact_refs["scan_result"],
                analysis_reference=request.artifact_refs["analysis_result"],
                semantic_reference=request.artifact_refs.get("semantic_index"),
                plan=plan,
                unit=unit,
            )
            prompt = translation_context.model_dump_json(indent=2)
            evidence = [item.source_hash for item in translation_context.functions]
        response = self._client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=TranslationResponse,
            tool_name=f"llm.migration.act.{unit.unit_id}",
            observation_callback=self._observation_callback,
            stream_progress_callback=self._stream_progress_callback,
        )
        self._validate(response, unit)
        reference = context.artifact_store.write_model(
            request.job_id, f"translation-{unit.unit_id}-{request.request_id}.json", response
        )
        return AgentResult(
            request_id=request.request_id,
            success=True,
            proposals=[
                AgentProposal(
                    agent_kind=self.kind,
                    payload={"unit_id": unit.unit_id, "translation_ref": reference},
                    evidence=evidence,
                    confidence=response.confidence,
                )
            ],
            artifacts={f"translation_{unit.unit_id}": reference},
        )

    @staticmethod
    def _validate(response: TranslationResponse, unit: MigrationWorkUnit) -> None:
        if response.unit_id != unit.unit_id:
            raise OrchestrationError("转换响应 unit_id 与请求不一致")
        covered = [symbol for item in response.files for symbol in item.symbol_ids]
        if set(covered) != set(unit.symbol_ids) or len(covered) != len(set(covered)):
            raise OrchestrationError("转换响应必须恰好覆盖工作单元中的全部 symbol")
        paths = [item.path.replace("\\", "/") for item in response.files]
        if len(paths) != len(set(paths)):
            raise OrchestrationError("转换响应包含重复目标路径")
        for path in paths:
            candidate = PurePosixPath(path)
            if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix != ".py":
                raise OrchestrationError(f"转换响应包含不安全 Python 路径: {path}")
