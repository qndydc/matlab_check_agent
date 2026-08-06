"""
Description: 定义 Worker-7 ReporterAgent 预留接口及独立演示入口。
References: workers.reserved_agents、domain.enums。
Referenced By: workers 延迟导出、Orchestrator 工厂和入口测试。
"""

from __future__ import annotations

from typing import Sequence

from matlab_refactor_agent.domain.enums import WorkerKind

from .reserved_agents import _ReservedAgent, run_reserved_demo


class ReporterAgent(_ReservedAgent):
    """作用：预留 Worker-7 报告生成；输入：全阶段 artifacts；输出：HTML/JSON 报告；数据流：状态与结果 -> 模板/可视化 -> 报告。"""

    worker_kind = WorkerKind.REPORTER
    description = "ReporterAgent"


def main(argv: Sequence[str] | None = None) -> int:
    """作用：展示 Worker-7 标准响应；输入：artifact 目录参数；输出：演示退出码；数据流：CLI -> ReporterAgent -> WorkerResult JSON。"""

    return run_reserved_demo(ReporterAgent, "Worker-7 ReporterAgent", argv)


if __name__ == "__main__":
    raise SystemExit(main())
