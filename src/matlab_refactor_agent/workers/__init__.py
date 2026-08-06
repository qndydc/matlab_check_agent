"""
Description: 通过延迟导入汇总七类 Worker、BaseWorker 和 WorkerContext。
References: importlib、workers 各 Agent 模块。
Referenced By: Orchestrator 工厂和外部 Worker 扩展。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .analyzer_agent import AnalyzerAgent
    from .base import BaseWorker, WorkerContext
    from .parser_agent import ParserAgent
    from .executor_agent import ExecutorAgent
    from .planner_agent import PlannerAgent
    from .reporter_agent import ReporterAgent
    from .scanner_agent import ScannerAgent
    from .validator_agent import ValidatorAgent

_EXPORT_MODULES = {
    "AnalyzerAgent": ".analyzer_agent",
    "BaseWorker": ".base",
    "ExecutorAgent": ".executor_agent",
    "ParserAgent": ".parser_agent",
    "PlannerAgent": ".planner_agent",
    "ReporterAgent": ".reporter_agent",
    "ScannerAgent": ".scanner_agent",
    "ValidatorAgent": ".validator_agent",
    "WorkerContext": ".base",
}

__all__ = [
    "AnalyzerAgent",
    "BaseWorker",
    "ExecutorAgent",
    "ParserAgent",
    "PlannerAgent",
    "ReporterAgent",
    "ScannerAgent",
    "ValidatorAgent",
    "WorkerContext",
]


def __getattr__(name: str) -> Any:
    """作用：按需加载 Worker 导出，避免独立模块运行前被包初始化；输入：导出名称；输出：对应类；数据流：属性访问 -> import_module -> 模块成员缓存。"""

    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
