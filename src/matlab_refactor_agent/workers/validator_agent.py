"""
Description: 定义 Worker-6 ValidatorAgent 预留接口及独立演示入口。
References: workers.reserved_agents、domain.enums。
Referenced By: workers 延迟导出、Orchestrator 工厂和入口测试。
"""

from __future__ import annotations

from typing import Sequence

from matlab_refactor_agent.domain.enums import WorkerKind

from .reserved_agents import _ReservedAgent, run_reserved_demo


class ValidatorAgent(_ReservedAgent):
    """作用：预留 Worker-6 回归验证；输入：基线和变更 artifacts；输出：ValidationResult；数据流：重构工程 -> MATLAB/测试 -> 对比结果。"""

    worker_kind = WorkerKind.VALIDATOR
    description = "ValidatorAgent"


def main(argv: Sequence[str] | None = None) -> int:
    """作用：展示 Worker-6 标准响应；输入：artifact 目录参数；输出：演示退出码；数据流：CLI -> ValidatorAgent -> WorkerResult JSON。"""

    return run_reserved_demo(ValidatorAgent, "Worker-6 ValidatorAgent", argv)


if __name__ == "__main__":
    raise SystemExit(main())
