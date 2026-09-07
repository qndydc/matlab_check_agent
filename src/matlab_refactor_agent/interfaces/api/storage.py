"""
Description: 保留旧语义 Web 存储导入路径的兼容转发层。
References: apps.semantic.backend.storage。
Referenced By: 旧测试和外部调用方。
"""

from matlab_refactor_agent.apps.semantic.backend.storage import (
    SQLiteWebProjectStore,
    StoredWebProject,
)

__all__ = ["SQLiteWebProjectStore", "StoredWebProject"]
