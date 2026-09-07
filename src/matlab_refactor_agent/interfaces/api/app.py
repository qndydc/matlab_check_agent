"""
Description: 保留旧语义 Web API 导入路径的兼容转发层。
References: apps.semantic.backend.app。
Referenced By: 旧测试、旧部署配置和外部调用方。
"""

from matlab_refactor_agent.apps.semantic.backend.app import (
    MvpJobManager,
    app,
    create_app,
)

__all__ = ["MvpJobManager", "app", "create_app"]
