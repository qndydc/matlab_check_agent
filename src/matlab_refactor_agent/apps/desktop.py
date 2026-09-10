"""
Description: Windows 单文件版入口，启动两个本地 Web 应用并提供控制台生命周期。
References: semantic/migration FastAPI app、uvicorn。
Referenced By: PyInstaller Windows 打包配置。
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import logging
import os
from pathlib import Path
import socket
import sys
from threading import Thread
import time
import traceback
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


APP_NAME = "MATLAB Atlas"
APP_VERSION = "0.1.0"
MUTEX_NAME = "MATLABAtlasDesktop"


@dataclass(frozen=True)
class RuntimePaths:
    """安装版只读资源和用户可写数据的位置。"""

    resources: Path
    home: Path
    config: Path
    data: Path
    logs: Path
    semantic_frontend: Path
    migration_frontend: Path


def resource_root() -> Path:
    """开发时指向仓库根目录，冻结后指向 PyInstaller 资源目录。"""

    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen).resolve()
    return Path(__file__).resolve().parents[3]


def user_home() -> Path:
    """冻结版使用 exe 同目录；环境变量仍可覆盖以便测试和高级部署。"""

    override = os.environ.get("MATLAB_ATLAS_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    local = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return (base / APP_NAME).resolve()


def configure_runtime() -> RuntimePaths:
    """创建可写目录，并让现有配置系统使用稳定的单机路径。"""

    resources = resource_root()
    home = user_home()
    config = home
    data = home / "data"
    logs = home / "logs"
    for directory in (data, logs, data / "jobs", data / "graphs"):
        directory.mkdir(parents=True, exist_ok=True)

    env_file = home / ".env"
    env_template = resources / ".env.example"
    if not env_file.exists() and env_template.is_file():
        env_file.write_bytes(env_template.read_bytes())

    semantic_frontend = resources / "apps" / "semantic" / "frontend" / "dist"
    migration_frontend = resources / "apps" / "migration" / "frontend" / "dist"
    for frontend in (semantic_frontend, migration_frontend):
        if not (frontend / "index.html").is_file():
            raise RuntimeError(f"安装包缺少前端资源：{frontend}")

    runtime = {
        "MATLAB_REFACTOR_ENV_FILE": env_file,
        "MATLAB_REFACTOR_ARTIFACT_DIR": data / "jobs",
        "MATLAB_REFACTOR_STATE_DB": data / "refactor-agent.db",
        "MATLAB_REFACTOR_WEB_DB": data / "web-projects.db",
        "MATLAB_REFACTOR_GRAPH_OUTPUT_DIR": data / "graphs",
        "MATLAB_REFACTOR_FRONTEND_DIR": semantic_frontend,
        "MATLAB_MIGRATION_FRONTEND_DIR": migration_frontend,
    }
    for name, value in runtime.items():
        os.environ[name] = str(value)
    return RuntimePaths(
        resources=resources,
        home=home,
        config=config,
        data=data,
        logs=logs,
        semantic_frontend=semantic_frontend,
        migration_frontend=migration_frontend,
    )


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def wait_until_healthy(urls: list[str], timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    pending = set(urls)
    while pending and time.monotonic() < deadline:
        for url in list(pending):
            try:
                with urlopen(url, timeout=0.5) as response:
                    if response.status == 200:
                        pending.remove(url)
            except (OSError, URLError):
                pass
        if pending:
            time.sleep(0.1)
    if pending:
        raise RuntimeError("本地服务启动超时，请查看日志：" + ", ".join(sorted(pending)))


def acquire_single_instance():
    """Windows 命名互斥量避免重复启动；非 Windows 开发环境不启用。"""

    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise OSError("无法创建单实例互斥量")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    return handle


class DesktopApplication:
    def __init__(self, paths: RuntimePaths, semantic_port: int, migration_port: int) -> None:
        self.paths = paths
        self.semantic_port = semantic_port
        self.migration_port = migration_port
        self.semantic_url = f"http://127.0.0.1:{semantic_port}"
        self.migration_url = f"http://127.0.0.1:{migration_port}"
        self.servers = []
        self.threads: list[Thread] = []

    def _start_servers(self) -> None:
        import uvicorn
        from matlab_refactor_agent.apps.migration.backend.app import app as migration_app
        from matlab_refactor_agent.apps.semantic.backend.app import app as semantic_app

        for application, port, name in (
            (semantic_app, self.semantic_port, "semantic"),
            (migration_app, self.migration_port, "migration"),
        ):
            server = uvicorn.Server(uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_config=None,
            ))
            thread = Thread(
                target=server.run,
                name=f"matlab-atlas-{name}",
                daemon=True,
            )
            self.servers.append(server)
            self.threads.append(thread)
            thread.start()

        wait_until_healthy([
            f"{self.semantic_url}/api/health",
            f"{self.migration_url}/api/health",
        ])

    def stop_background(self) -> None:
        for server in self.servers:
            server.should_exit = True
        for thread in self.threads:
            thread.join(timeout=15)

    def run_console(self) -> None:
        self._start_servers()
        print(f"{APP_NAME} {APP_VERSION} 已启动。")
        print(f"语义分析：{self.semantic_url}")
        print(f"迁移工具：{self.migration_url}")
        print(f"配置与数据：{self.paths.home}")
        print("请保持此窗口开启；按 Ctrl+C 或关闭窗口即可停止服务。")
        webbrowser.open(self.semantic_url)
        try:
            while all(thread.is_alive() for thread in self.threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n正在停止服务……")
        finally:
            self.stop_background()


def main() -> int:
    mutex = acquire_single_instance()
    if mutex is False:
        port = int(os.environ.get("MATLAB_ATLAS_SEMANTIC_PORT", "8000"))
        webbrowser.open(f"http://127.0.0.1:{port}")
        return 0
    paths = configure_runtime()
    logging.basicConfig(
        filename=paths.logs / "matlab-atlas.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )
    semantic_port = int(os.environ.get("MATLAB_ATLAS_SEMANTIC_PORT", "8000"))
    migration_port = int(os.environ.get("MATLAB_ATLAS_MIGRATION_PORT", "8001"))
    if semantic_port == migration_port:
        raise SystemExit("语义分析和迁移服务不能使用同一个端口")
    occupied = [port for port in (semantic_port, migration_port) if not port_available(port)]
    if occupied:
        raise SystemExit("端口已被占用：" + ", ".join(map(str, occupied)))
    try:
        DesktopApplication(paths, semantic_port, migration_port).run_console()
    finally:
        if os.name == "nt" and mutex not in (None, False):
            ctypes.windll.kernel32.CloseHandle(mutex)
    return 0


def run() -> int:
    """为无控制台安装版保留可诊断的启动错误。"""

    try:
        return main()
    except BaseException as error:
        try:
            home = user_home()
            logs = home / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "startup-error.log").write_text(
                "".join(traceback.format_exception(error)),
                encoding="utf-8",
            )
        except BaseException:
            pass
        if os.name == "nt" and sys.stdout is None:
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"启动失败：{error}\n\n请查看数据目录 logs/startup-error.log。",
                    APP_NAME,
                    0x10,
                )
            except BaseException:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
