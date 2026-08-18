"""
Description: 验证 `.env`、进程环境变量与外部路径配置的合并规则。
References: infrastructure.config、pytest monkeypatch。
Referenced By: pytest 测试发现和统一外部输入配置验收。
"""

from pathlib import Path

from matlab_refactor_agent.infrastructure.config import load_settings


def test_dotenv_defines_input_outputs_and_runtime_limits(
    tmp_path: Path, monkeypatch
) -> None:
    """作用：验证全部外部输入可来自 `.env`；输入：临时 env；输出：Pydantic 配置。"""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "MATLAB_REFACTOR_INPUT_PATH=fixtures/project\n"
        "MATLAB_REFACTOR_CODE_OUTPUT_DIR=outputs/code\n"
        "MATLAB_REFACTOR_REPORT_OUTPUT_DIR=outputs/reports\n"
        "MATLAB_REFACTOR_GRAPH_OUTPUT_DIR=outputs/graphs\n"
        "MATLAB_REFACTOR_ARTIFACT_DIR=runtime/jobs\n"
        "MATLAB_REFACTOR_EXCLUDE_PATTERNS=[\"build\", \"cache\"]\n"
        "MATLAB_REFACTOR_MAX_WORKERS=7\n"
        "MATLAB_REFACTOR_LLM_MODEL=deepseek-test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.io.input_path == Path("fixtures/project")
    assert settings.orchestrator.output_dir == Path("outputs/code")
    assert settings.io.report_dir == Path("outputs/reports")
    assert settings.io.graph_dir == Path("outputs/graphs")
    assert settings.orchestrator.artifact_dir == Path("runtime/jobs")
    assert settings.project.exclude_patterns == ["build", "cache"]
    assert settings.orchestrator.max_workers == 7
    assert settings.llm.model == "deepseek-test"


def test_process_environment_overrides_dotenv(
    tmp_path: Path, monkeypatch
) -> None:
    """作用：验证覆盖顺序；输入：进程与 dotenv 不同值；输出：进程环境胜出。"""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "MATLAB_REFACTOR_INPUT_PATH=from-dotenv\n"
        "MATLAB_REFACTOR_MAX_WORKERS=2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv("MATLAB_REFACTOR_MAX_WORKERS", "4")

    settings = load_settings()

    assert settings.io.input_path == Path("from-dotenv")
    assert settings.orchestrator.max_workers == 4
