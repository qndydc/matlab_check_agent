"""
Description: 定义 Worker-4 PlannerAgent 预留接口及独立演示入口。
References: workers.reserved_agents、domain.enums。
Referenced By: workers 延迟导出、Orchestrator 工厂和入口测试。
"""

from __future__ import annotations

from typing import Sequence

from matlab_refactor_agent.domain.enums import WorkerKind

from .reserved_agents import _ReservedAgent, run_reserved_demo


class PlannerAgent(_ReservedAgent):
    """作用：预留 Worker-4 LLM 重构规划；输入：分析摘要 artifacts；输出：RefactorPlan；数据流：图摘要/代码片段 -> LLM -> 结构化计划。"""

    worker_kind = WorkerKind.PLANNER
    description = "PlannerAgent"


def main(argv: Sequence[str] | None = None) -> int:
    """作用：展示 Worker-4 标准响应；输入：artifact 目录参数；输出：演示退出码；数据流：CLI -> PlannerAgent -> WorkerResult JSON。"""

    return run_reserved_demo(PlannerAgent, "Worker-4 PlannerAgent", argv)


if __name__ == "__main__":
    raise SystemExit(main())
