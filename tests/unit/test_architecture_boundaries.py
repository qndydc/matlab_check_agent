"""
Description: 锁定 Analysis、Semantics 与 Migration 三模块的依赖边界。
References: pathlib、三个顶层业务包。
Referenced By: pytest 测试发现。
"""

from pathlib import Path

import matlab_refactor_agent


def test_stable_pipelines_do_not_import_agent_runtime() -> None:
    package_root = Path(matlab_refactor_agent.__file__).parent
    for module_name in ("analysis", "semantics"):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (package_root / module_name).glob("*.py")
        )
        assert "matlab_refactor_agent.agents" not in sources


def test_migration_module_exposes_agent_entrypoint() -> None:
    package_root = Path(matlab_refactor_agent.__file__).parent
    source = (package_root / "migration" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert "class MatlabToPythonMigrationAgent" in source
    assert "semantic_index_reference: str | None = None" in source


def test_cli_main_only_parses_and_dispatches() -> None:
    package_root = Path(matlab_refactor_agent.__file__).parent
    source = (package_root / "interfaces" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "return dispatch(args, settings, console)" in source
    assert "AnalysisService" not in source
    assert "MigrationService" not in source
    assert "SQLiteStateManager" not in source


def test_migration_agent_only_composes_session_runtime_and_graph() -> None:
    package_root = Path(matlab_refactor_agent.__file__).parent
    source = (package_root / "migration" / "agent.py").read_text(
        encoding="utf-8"
    )
    assert "MigrationSessionManager" in source
    assert "MigrationRuntime" in source
    assert "MigrationGraph" in source
    assert "CallChainContextBuilder" not in source
    assert "PythonProjectAssembler" not in source
    assert "CallChainObserver" not in source


def test_all_main_modules_remain_thin_entrypoints() -> None:
    package_root = Path(matlab_refactor_agent.__file__).parent
    entrypoints = [*package_root.rglob("main.py"), package_root / "__main__.py"]
    oversized = [
        path.relative_to(package_root).as_posix()
        for path in entrypoints
        if len(path.read_text(encoding="utf-8").splitlines()) > 130
    ]
    assert oversized == []
