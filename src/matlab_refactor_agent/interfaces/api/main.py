"""
Description: 保留旧语义后端启动入口。
References: apps.semantic.backend.main。
Referenced By: matlab-refactor-web 兼容命令。
"""

from matlab_refactor_agent.apps.semantic.backend.main import main


if __name__ == "__main__":
    main()
