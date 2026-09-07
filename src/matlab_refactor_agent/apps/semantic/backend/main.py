"""
Description: 启动独立 MATLAB 语义分析后端。
References: uvicorn、apps.semantic.backend.app。
Referenced By: matlab-semantic-web 控制台命令。
"""

import uvicorn

from matlab_refactor_agent.infrastructure.config import load_settings
from matlab_refactor_agent.infrastructure.logging import configure_logging


def main() -> None:
    configure_logging(load_settings().logging.level)
    uvicorn.run(
        "matlab_refactor_agent.apps.semantic.backend.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
