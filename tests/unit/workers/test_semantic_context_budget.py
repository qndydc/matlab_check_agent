"""
Description: 验证语义工作单元的目标预算与模型上下文硬上限。
References: SemanticWorkUnitBuilder、AnalysisResult。
Referenced By: pytest 测试发现。
"""

import pytest

from matlab_refactor_agent.domain.exceptions import OrchestrationError
from matlab_refactor_agent.domain.models import (
    AnalysisResult,
    DependencyEdge,
    FunctionInfo,
)
from matlab_refactor_agent.workers.semantic_context import SemanticWorkUnitBuilder


def _analysis(*symbols: str, connected: bool = False) -> AnalysisResult:
    functions = [
        FunctionInfo(name=symbol, qualified_name=symbol, file_path=f"{symbol}.m")
        for symbol in symbols
    ]
    dependencies = (
        [
            DependencyEdge(source=left, target=right)
            for left, right in zip(symbols, symbols[1:])
        ]
        if connected
        else []
    )
    return AnalysisResult(
        project_root="project",
        functions=functions,
        dependencies=dependencies,
    )


def test_normal_call_chain_is_split_at_target_budget() -> None:
    """普通链尽量留在目标预算内，并在可拆 SCC 边界切分。"""

    units = SemanticWorkUnitBuilder(
        token_budget=32_768,
        hard_token_limit=100_000,
    ).build(
        _analysis("caller", "callee", connected=True),
        context_estimator=lambda unit: len(unit.symbol_ids) * 20_000,
    )

    assert [unit.symbol_ids for unit in units.units] == [
        ["caller"],
        ["callee"],
    ]
    assert all(unit.estimated_tokens <= units.token_budget for unit in units.units)


def test_single_oversized_function_is_allowed_below_hard_limit() -> None:
    """单函数超过目标预算时保留完整源码，不做截断。"""

    units = SemanticWorkUnitBuilder(
        token_budget=32_768,
        hard_token_limit=50_000,
    ).build(
        _analysis("large_function"),
        context_estimator=lambda _unit: 40_000,
    )

    assert units.units[0].symbol_ids == ["large_function"]
    assert units.units[0].estimated_tokens == 40_000


def test_single_function_over_hard_limit_is_rejected() -> None:
    """即使不可拆，超过模型安全输入上限也必须在调用 LLM 前失败。"""

    builder = SemanticWorkUnitBuilder(
        token_budget=32_768,
        hard_token_limit=50_000,
    )

    with pytest.raises(OrchestrationError, match="模型上下文安全输入上限"):
        builder.build(
            _analysis("too_large"),
            context_estimator=lambda _unit: 50_001,
        )
