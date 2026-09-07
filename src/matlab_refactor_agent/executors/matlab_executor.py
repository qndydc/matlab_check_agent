"""
Description: 提供可注入 MATLAB runner 的执行适配器。
References: domain.diagnostics。
Referenced By: 双端差分迁移工作流。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from matlab_refactor_agent.domain.diagnostics import ExecutionResult
from matlab_refactor_agent.orchestration.call_lifecycle import (
    CallLifecycle,
    ObservationSink,
)


class MatlabExecutor:
    def __init__(self, runner: Callable[[str, list[Any]], ExecutionResult] | None = None,
                 lifecycle: CallLifecycle | None = None) -> None:
        self._runner = runner
        self._lifecycle = lifecycle or CallLifecycle()

    def execute(self, symbol: str, inputs: list[Any], *,
                observation_callback: ObservationSink | None = None) -> ExecutionResult:
        if self._runner is None:
            return ExecutionResult(succeeded=False, exception_type="EnvironmentUnavailable",
                                   exception_message="未配置 MATLAB runner")
        return self._lifecycle.invoke(
            tool="runner.matlab",
            arguments={"symbol": symbol, "inputs": inputs},
            operation=lambda arguments: self._runner(
                str(arguments["symbol"]), list(arguments["inputs"])
            ),
            risk="execute_external",
            # 显式注入 runner 即表示调用方已允许外部执行；默认不把它视为幂等动作。
            allow_high_risk=True,
            idempotent=False,
            summarize=lambda result: (
                f"symbol={symbol}, succeeded={result.succeeded}, outputs={len(result.outputs)}"
            ),
            sink=observation_callback,
        )
