"""
Description: Expose the local MATLAB analysis Web API package.
References: interfaces.api.app.
Referenced By: FastAPI/uvicorn entrypoints and API consumers.
"""

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    """按需加载旧入口，导入共享设置时不提前创建语义应用。"""
    if name in __all__:
        from .app import app, create_app
        globals().update(app=app, create_app=create_app)
        return globals()[name]
    raise AttributeError(name)
