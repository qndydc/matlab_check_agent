"""
Description: 提供真正基于观察反馈决策的 MATLAB 到 Python 迁移 Agent 公共边界。
References: migration.agent、planning、loop。
Referenced By: MainWorkflow、MigrationService 和外部扩展。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "MatlabToPythonMigrationAgent": ".agent",
    "MigrationAgentResult": ".agent",
    "MigrationPlanBuilder": ".planning",
    "MigrationAgentLoop": ".loop",
    "CallChainContextBuilder": ".context",
    "MigrationGraph": ".loop",
    "MigrationScheduler": ".loop",
    "MigrationStateStore": ".loop",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
