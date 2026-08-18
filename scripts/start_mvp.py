"""
Description: Start the local FastAPI service and Vite frontend with one command.
References: interfaces.api.app, uvicorn, and frontend package scripts.
Referenced By: README Web MVP startup instructions.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    package_runner = (
        shutil.which("pnpm")
        or shutil.which("pnpm.cmd")
        or shutil.which("npm")
        or shutil.which("npm.cmd")
    )
    if package_runner is None:
        raise SystemExit("未找到 pnpm 或 npm，请先安装 Node.js。")
    if not (root / "frontend" / "node_modules").is_dir():
        raise SystemExit("请先在 frontend 目录运行 pnpm install（或 npm install）。")

    frontend = subprocess.Popen(
        [package_runner, "run", "dev"], cwd=root / "frontend"
    )
    try:
        print("前端：http://127.0.0.1:5173")
        print("后端：http://127.0.0.1:8000")
        uvicorn.run(
            "matlab_refactor_agent.interfaces.api.app:app",
            host="127.0.0.1",
            port=8000,
        )
    finally:
        frontend.terminate()
        try:
            frontend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frontend.kill()


if __name__ == "__main__":
    sys.exit(main())
