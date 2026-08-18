"""
Description: Expose the local MATLAB analysis Web API package.
References: interfaces.api.app.
Referenced By: FastAPI/uvicorn entrypoints and API consumers.
"""

from .app import app, create_app

__all__ = ["app", "create_app"]
