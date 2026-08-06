"""
Description: 定义 Worker-5 ExecutorAgent 预留接口及独立演示入口。
References: workers.reserved_agents、domain.enums。
Referenced By: workers 延迟导出、Orchestrator 工厂和入口测试。
"""

from __future__ import annotations

from typing import Sequence

from matlab_refactor_agent.domain.enums import WorkerKind

from .reserved_agents import _ReservedAgent, run_reserved_demo


class ExecutorAgent(_ReservedAgent):
    """作用：预留 Worker-5 安全执行；输入：获批计划；输出：变更与快照 artifacts；数据流：RefactorPlan -> 冲突检查/文件操作 -> ChangeSet。"""

    worker_kind = WorkerKind.EXECUTOR
    description = "ExecutorAgent"


def main(argv: Sequence[str] | None = None) -> int:
    """作用：展示 Worker-5 标准响应；输入：artifact 目录参数；输出：演示退出码；数据流：CLI -> ExecutorAgent -> WorkerResult JSON。"""

    return run_reserved_demo(ExecutorAgent, "Worker-5 ExecutorAgent", argv)


if __name__ == "__main__":
    raise SystemExit(main())
