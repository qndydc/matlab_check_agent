"""
Description: Run both existing FastAPI products in one process.
References: Uvicorn and the Semantic/Migration FastAPI applications.
Referenced By: Docker production command and matlab-combined-web.
"""

from __future__ import annotations

import asyncio

import uvicorn

from matlab_refactor_agent.apps.migration.backend.app import app as migration_app
from matlab_refactor_agent.apps.semantic.backend.app import app as semantic_app


async def serve() -> None:
    servers = [
        uvicorn.Server(uvicorn.Config(
            semantic_app, host="0.0.0.0", port=8000, access_log=False,
        )),
        uvicorn.Server(uvicorn.Config(
            migration_app, host="0.0.0.0", port=8001, access_log=False,
        )),
    ]
    for server in servers:
        server.install_signal_handlers = lambda: None
    await asyncio.gather(*(server.serve() for server in servers))


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
