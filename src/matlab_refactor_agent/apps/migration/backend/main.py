"""
Description: 启动独立 MATLAB 到 Python 迁移后端。
References: uvicorn、apps.migration.backend.app。
Referenced By: matlab-migration-web 控制台命令。
"""

import uvicorn

from matlab_refactor_agent.infrastructure.config import load_settings
from matlab_refactor_agent.infrastructure.logging import configure_logging


def main() -> None:
    configure_logging(load_settings().logging.level)
    uvicorn.run(
        "matlab_refactor_agent.apps.migration.backend.app:app",
        host="127.0.0.1",
        port=8001,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
