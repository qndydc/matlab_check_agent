"""
Description: 启动已完成的独立语义后端和语义前端。
References: apps.semantic.backend.app、uvicorn 和语义前端脚本。
Referenced By: README 语义应用启动说明。
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _ensure_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"端口 {port} 已被占用，请先停止已有服务后重试。")


def _wait_for_backend(process: subprocess.Popen, port: int = 8000,
                      timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise SystemExit(f"后端启动失败，进程退出码：{return_code}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
                if response.status == 200:
                    time.sleep(0.1)
                    if process.poll() is not None:
                        raise SystemExit(
                            f"后端启动失败，进程退出码：{process.returncode}"
                        )
                    return
        except (OSError, URLError):
            time.sleep(0.1)
    raise SystemExit("后端启动超时，30 秒内未通过健康检查。")


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def start_app(application: str, backend_port: int, frontend_port: int) -> None:
    """两个子项目共用启动、端口检查与退出清理，不引入额外启动框架。"""
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node is None:
        raise SystemExit("未找到 Node.js，请先安装。")
    frontend_root = root / "apps" / application / "frontend"
    if not (frontend_root / "node_modules").is_dir():
        raise SystemExit(
            f"请先在 apps/{application}/frontend 运行 pnpm install（或 npm install）。"
        )

    _ensure_port_available(backend_port)
    _ensure_port_available(frontend_port)
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            f"matlab_refactor_agent.apps.{application}.backend.main",
        ],
        cwd=root,
    )
    frontend: subprocess.Popen | None = None
    try:
        _wait_for_backend(backend, backend_port)
        print(f"后端：http://127.0.0.1:{backend_port}", flush=True)
        frontend = subprocess.Popen(
            [
                node,
                str(frontend_root / "node_modules" / "vite" / "bin" / "vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(frontend_port),
                "--strictPort",
                "--clearScreen", "false",
            ],
            cwd=frontend_root,
        )
        print(f"前端：http://127.0.0.1:{frontend_port}（Ctrl+C 停止）", flush=True)
        while True:
            backend_code = backend.poll()
            if backend_code is not None:
                raise SystemExit(f"后端进程已退出，退出码：{backend_code}")
            frontend_code = frontend.poll()
            if frontend_code is not None:
                raise SystemExit(f"前端进程已退出，退出码：{frontend_code}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_process(frontend)
        _stop_process(backend)


def main() -> None:
    start_app("semantic", 8000, 5173)


if __name__ == "__main__":
    sys.exit(main())
