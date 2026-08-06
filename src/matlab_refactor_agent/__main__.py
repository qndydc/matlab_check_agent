"""
Description: 提供 `python -m matlab_refactor_agent` 模块入口。
References: interfaces.cli.main。
Referenced By: Python 模块执行器和命令行用户。
"""

from matlab_refactor_agent.interfaces.cli.main import main


if __name__ == "__main__":
    raise SystemExit(main())
