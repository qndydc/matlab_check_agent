"""
Description: 导出已完成的语义应用 FastAPI 工厂。
References: apps.semantic.backend.app。
Referenced By: 语义后端启动器和测试。
"""

__all__ = ["MvpJobManager", "app", "create_app"]


def __getattr__(name: str):
    """按需加载 API，读取共享 SQLite 存储时不意外启动另一套 Web 服务。"""

    if name in __all__:
        from .app import MvpJobManager, app, create_app

        globals().update(
            MvpJobManager=MvpJobManager,
            app=app,
            create_app=create_app,
        )
        return globals()[name]
    raise AttributeError(name)
