"""
Description: 通过延迟导入汇总三个确定性前处理 Worker、BaseWorker 和 WorkerContext。
References: importlib、workers 各 Agent 模块。
Referenced By: Orchestrator 工厂和外部 Worker 扩展。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .analyzer_agent import AnalyzerAgent, AnalyzerWorker
    from .base import BaseWorker, WorkerContext
    from .code_tree import CodeTreeBuilder
    from .dependency_analysis import DependencyAnalyzer
    from .graph_output import (
        build_graph_document,
        render_dependency_tree,
        write_graph_json,
        write_mermaid,
    )
    from .matlab_parser import MaxxMatlabParser
    from .parser_agent import ParserAgent, ParserWorker
    from .scanning import MatlabFileDiscovery, MatlabProjectScanner
    from .scanner_agent import ScannerAgent, ScannerWorker

_EXPORT_MODULES = {
    "AnalyzerAgent": ".analyzer_agent",
    "AnalyzerWorker": ".analyzer_agent",
    "BaseWorker": ".base",
    "CodeTreeBuilder": ".code_tree",
    "DependencyAnalyzer": ".dependency_analysis",
    "MatlabFileDiscovery": ".scanning",
    "MatlabProjectScanner": ".scanning",
    "MaxxMatlabParser": ".matlab_parser",
    "ParserAgent": ".parser_agent",
    "ParserWorker": ".parser_agent",
    "ScannerAgent": ".scanner_agent",
    "ScannerWorker": ".scanner_agent",
    "WorkerContext": ".base",
    "build_graph_document": ".graph_output",
    "render_dependency_tree": ".graph_output",
    "write_graph_json": ".graph_output",
    "write_mermaid": ".graph_output",
}

__all__ = [
    "AnalyzerAgent",
    "AnalyzerWorker",
    "BaseWorker",
    "CodeTreeBuilder",
    "DependencyAnalyzer",
    "MatlabFileDiscovery",
    "MatlabProjectScanner",
    "MaxxMatlabParser",
    "ParserAgent",
    "ParserWorker",
    "ScannerAgent",
    "ScannerWorker",
    "WorkerContext",
    "build_graph_document",
    "render_dependency_tree",
    "write_graph_json",
    "write_mermaid",
]


def __getattr__(name: str) -> Any:
    """作用：按需加载 Worker 导出，避免独立模块运行前被包初始化；输入：导出名称；输出：对应类；数据流：属性访问 -> import_module -> 模块成员缓存。"""

    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
