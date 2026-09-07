"""
Description: 延迟导出跨模块兼容编排门面和基础运行设施，避免反向依赖业务模块。
References: orchestrator、workflow、state_manager、worker_pool、quality_gate。
Referenced By: 兼容应用服务、CLI 和集成测试。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "Orchestrator": ".orchestrator",
    "MainWorkflow": ".workflow",
    "WorkflowState": ".workflow",
    "SQLiteStateManager": ".state_manager",
    "WorkerPool": ".worker_pool",
    "QualityGate": ".quality_gate",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
