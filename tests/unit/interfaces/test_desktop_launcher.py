"""
Description: 验证 Windows 单文件版使用 exe 同目录的外部配置和数据。
References: matlab_refactor_agent.apps.desktop。
Referenced By: pytest 自动发现。
"""

from pathlib import Path

from matlab_refactor_agent.apps import desktop


def test_desktop_runtime_uses_external_files_next_to_executable(tmp_path, monkeypatch) -> None:
    resources = tmp_path / "bundle"
    for relative in (
        "apps/semantic/frontend/dist",
        "apps/migration/frontend/dist",
    ):
        directory = resources / relative
        directory.mkdir(parents=True)
        (directory / "index.html").write_text("ok", encoding="utf-8")
    (resources / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    home = tmp_path / "profile"
    monkeypatch.setattr(desktop, "resource_root", lambda: resources)
    monkeypatch.setenv("MATLAB_ATLAS_HOME", str(home))

    paths = desktop.configure_runtime()

    assert paths.home == home.resolve()
    assert Path(desktop.os.environ["MATLAB_REFACTOR_ENV_FILE"]) == home / ".env"
    assert (home / ".env").read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=\n"
    assert Path(desktop.os.environ["MATLAB_REFACTOR_ARTIFACT_DIR"]) == home / "data" / "jobs"
    assert Path(desktop.os.environ["MATLAB_REFACTOR_STATE_DB"]) == home / "data" / "refactor-agent.db"
    assert Path(desktop.os.environ["MATLAB_REFACTOR_WEB_DB"]) == home / "data" / "web-projects.db"
    assert Path(desktop.os.environ["MATLAB_REFACTOR_FRONTEND_DIR"]).is_dir()
    assert Path(desktop.os.environ["MATLAB_MIGRATION_FRONTEND_DIR"]).is_dir()


def test_frozen_desktop_defaults_to_executable_directory(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "portable" / "MATLAB-Atlas.exe"
    monkeypatch.delenv("MATLAB_ATLAS_HOME", raising=False)
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(executable))

    assert desktop.user_home() == executable.parent.resolve()


def test_desktop_rejects_missing_frontend_assets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(desktop, "resource_root", lambda: tmp_path / "empty-bundle")
    monkeypatch.setenv("MATLAB_ATLAS_HOME", str(tmp_path / "profile"))

    try:
        desktop.configure_runtime()
    except RuntimeError as error:
        assert "缺少前端资源" in str(error)
    else:
        raise AssertionError("缺失前端时必须停止启动")


def test_desktop_port_probe_detects_occupied_port() -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        assert desktop.port_available(server.getsockname()[1]) is False
