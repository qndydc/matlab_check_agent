"""
Description: Bound Reason to R0/R1/R2 and emit explicit conversion fallbacks.
References: ConversionReasonAgent, CallChainContext and token estimates.
Referenced By: MigrationRuntime and reason policy regression tests.
"""

from collections.abc import Callable

from matlab_refactor_agent.agents.matlab_to_python.strategy import ConversionReasonAgent
from matlab_refactor_agent.domain.diagnostics import DifferentialObservation
from matlab_refactor_agent.domain.exceptions import LLMClientError
from matlab_refactor_agent.domain.migration import CallChainContext, ConversionStratagem
from matlab_refactor_agent.infrastructure.llm.token_budget import estimate_tokens


class ReasonFallbackPolicy:
    """At most two model calls; no unchanged-context rebuild loop."""

    def __init__(self, agent: ConversionReasonAgent, input_budget: int = 32_768) -> None:
        self.agent = agent
        self.input_budget = input_budget

    def run(self, context: CallChainContext, observation: DifferentialObservation | None,
            supplement: Callable[[list[str]], CallChainContext],
            record: Callable[[CallChainContext], None]) -> ConversionStratagem:
        if observation is not None:
            if any(fact.kind == "dependency_adaptation" and not fact.passed for fact in observation.facts):
                return ConversionStratagem(unit_id=context.unit.unit_id, action="manual_review",
                    rationale="转换产物已保存，外部依赖需人工适配；不重复请求不可获得的源码")
            if observation.passed:
                return self.agent.reason(context, observation)
        view = self._view(context)
        previous = None
        for attempt in range(2):
            record(view)
            prompt_size = estimate_tokens(self.agent.prompt(view, observation))
            if prompt_size > self.input_budget:
                return self._fallback(context, "R2 后核心与直接依赖仍超过 Reason 预算；使用确定性策略", previous)
            try:
                result = self.agent.reason(view, observation)
            except LLMClientError as exc:
                # Transport failures cannot be solved by context degradation.
                if exc.error_type in {
                    "timeout", "rate_limited", "service_unavailable",
                    "remote_protocol_error", "bad_request", "unauthorized",
                    "forbidden",
                }:
                    raise
                reduced = self._view(context, force=True)
                if attempt == 0 and reduced != view:
                    view = reduced
                    continue
                return self._fallback(context, f"Reason 结构化响应失败：{exc}", previous)
            previous = result
            if result.action != "rebuild_context":
                return result.model_copy(update={"external_dependencies": context.external_dependencies})
            if attempt == 1:
                if set(result.requested_context) - set(context.requested_context):
                    context = supplement(list(dict.fromkeys([*context.requested_context, *result.requested_context])))
                    record(context)
                return self._fallback(context, "R1 已执行，不再重复补充上下文", result)
            expanded = supplement(list(dict.fromkeys([*context.requested_context, *result.requested_context])))
            record(expanded)
            new_symbols = {item.symbol_id for item in expanded.dependency_functions} - {
                item.symbol_id for item in context.dependency_functions}
            context = expanded
            if not new_symbols:
                return self._fallback(context, "R1 没有新增源码；缺失或已提供的依赖不再触发循环", result)
            view = self._view(context)
        raise AssertionError("unreachable")

    def _view(self, context: CallChainContext, *, force: bool = False) -> CallChainContext:
        if not force and estimate_tokens(self.agent.prompt(context)) <= self.input_budget:
            return context
        core = set(context.unit.entry_symbols or context.unit.symbol_ids[:1]) | set(context.core_symbols)
        keep = core | {edge.split(" -> ", 1)[1] for edge in context.internal_dependencies
                       if edge.split(" -> ", 1)[0] in core}
        for scc in context.unit.sccs:
            if keep.intersection(scc):
                keep.update(scc)
        functions = []
        for function in context.functions:
            if function.symbol_id in keep:
                functions.append(function)
                continue
            comments = "\n".join(line.strip() for line in function.source.splitlines()
                                 if line.lstrip().startswith("%"))[:800]
            summary = function.semantic_summary or (
                f"接口：{function.symbol_id}({', '.join(function.inputs)}) -> {', '.join(function.outputs)}。"
                f"未提供已验证语义摘要；源码注释摘录：{comments or '无'}")
            functions.append(function.model_copy(update={"source": "", "semantic_summary": summary}))
        return context.model_copy(update={"functions": functions,
            "context_notes": [*context.context_notes,
                "R2：核心、直接依赖及关联 SCC 保留完整源码；远端函数仅保留摘要。Act 将恢复完整源码。"]})

    @staticmethod
    def _fallback(context: CallChainContext, reason: str,
                  previous: ConversionStratagem | None) -> ConversionStratagem:
        return ConversionStratagem(
            unit_id=context.unit.unit_id, action="convert",
            # Do not reuse a rebuild plan whose steps merely request more context.
            conversion_steps=[
                "依据完整目标 WCC 源码执行转换，保持既有函数调用关系和 SCC 边界",
                "dependency_functions 仅作依赖实现参考，不改变本工作单元的 symbol 覆盖范围",
                "外部依赖仅按调用点声明适配接口；可选依赖保留原源码已有的替代分支",
                "未知且必需的依赖使用显式 NotImplementedError，记录 manual_review；不得返回假数据或编造算法",
            ],
            matlab_semantic_risks=list(dict.fromkeys([
                *(previous.matlab_semantic_risks if previous else []), *context.context_notes])),
            validation_plan=["验证语法及模块连接；存在外部适配缺口时不得视为功能完整"],
            external_dependencies=context.external_dependencies,
            rationale=f"降级进入 Act：{reason}",
        )
