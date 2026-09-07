"""
Description: 导出独立迁移应用 FastAPI 工厂。
References: apps.migration.backend.app。
Referenced By: 迁移后端启动器和测试。
"""

from .app import app, create_app

__all__ = ["app", "create_app"]
