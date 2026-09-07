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
        "MATLAB_REFACTOR_GRAPH_OUTPUT_DIR=outputs/graphs\n"
        "MATLAB_REFACTOR_ARTIFACT_DIR=runtime/jobs\n"
        "MATLAB_REFACTOR_EXCLUDE_PATTERNS=[\"build\", \"cache\"]\n"
        "MATLAB_REFACTOR_MAX_WORKERS=7\n"
        "MATLAB_REFACTOR_LLM_MODEL=deepseek-test\n"
        "DEBUG_MODEL=True\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.io.input_path == Path("fixtures/project")
    assert settings.io.graph_dir == Path("outputs/graphs")
    assert settings.orchestrator.artifact_dir == Path("runtime/jobs")
    assert settings.project.exclude_patterns == ["build", "cache"]
    assert settings.orchestrator.max_workers == 7
    assert settings.llm.model == "deepseek-test"
    assert settings.llm.semantic_max_output_tokens == 16_384
    assert settings.llm.migration_max_output_tokens == 32_768
    assert settings.llm.migration_reason_max_output_tokens == 4096
    assert settings.llm.migration_max_agents == 1
    assert settings.llm.semantic_max_agents == 4
    assert settings.llm.migration_chunk_max_agents == 4
    assert settings.llm.hard_timeout_seconds == 180
    assert settings.llm.semantic_token_budget == 32_768
    assert settings.llm.model_context_window_tokens == 1_000_000
    assert settings.llm.context_safety_margin_tokens == 8192
    assert settings.llm.semantic_hard_input_tokens == 975_424
    assert settings.logging.debug_model is True


def test_dotenv_overrides_task_specific_output_limits(
    tmp_path: Path, monkeypatch
) -> None:
    """作用：验证注释与代码转换使用相互独立的输出 token 上限。"""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "MATLAB_REFACTOR_SEMANTIC_MAX_OUTPUT_TOKENS=12000\n"
        "MATLAB_REFACTOR_MIGRATION_MAX_OUTPUT_TOKENS=24000\n"
        "MATLAB_REFACTOR_MIGRATION_REASON_MAX_OUTPUT_TOKENS=3000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATLAB_REFACTOR_ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.llm.semantic_max_output_tokens == 12_000
    assert settings.llm.migration_max_output_tokens == 24_000
    assert settings.llm.migration_reason_max_output_tokens == 3000


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
