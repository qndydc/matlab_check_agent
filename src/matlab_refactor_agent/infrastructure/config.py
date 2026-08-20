"""
Description: 从 `.env` 和进程环境变量加载并严格校验应用配置。
References: python-dotenv、Pydantic、domain.exceptions.ConfigurationError。
Referenced By: CLI、AnalysisService 和 Orchestrator 工厂。
"""

from __future__ import annotations

import os
import json
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from matlab_refactor_agent.domain.exceptions import ConfigurationError


class SettingsModel(BaseModel):
    """作用：提供严格配置模型基类；输入：环境变量映射；输出：校验配置；数据流：环境变量 -> Pydantic -> 应用服务。"""

    model_config = ConfigDict(extra="forbid")


class ProjectSettings(SettingsModel):
    """作用：保存扫描范围配置；输入：排除规则和入口点；输出：项目设置；数据流：配置文件 -> 扫描器/分析器。"""

    exclude_patterns: list[str] = Field(
        default_factory=lambda: [".git", "slprj", "build"]
    )
    entry_points: list[str] = Field(default_factory=list)


class LoggingSettings(SettingsModel):
    """作用：保存日志配置；输入：级别与格式；输出：日志设置；数据流：配置文件 -> 日志初始化。"""

    level: str = "INFO"
    format: str = "console"


class IOSettings(SettingsModel):
    """作用：集中定义用户输入和可交付输出目录；输入：`.env`/进程环境；输出：CLI 与报告发布路径。"""

    input_path: Path = Path("tests/fixtures/matlab_projects/basic")
    report_dir: Path = Path("var/reports")
    graph_dir: Path = Path("var/graphs")
    auto_export_graphs: bool = True


class OrchestratorSettings(SettingsModel):
    """作用：配置持久化、Worker 池和解析分片；输入：路径、并发数和分片大小；输出：调度设置；数据流：环境变量 -> Orchestrator 工厂。"""

    state_db: Path = Path("var/refactor-agent.db")
    web_db: Path = Path("var/web-projects.db")
    checkpoint_db: Path = Path("var/langgraph-checkpoints.db")
    artifact_dir: Path = Path("var/jobs")
    output_dir: Path = Path("var/refactored-projects")
    max_workers: int = Field(default=4, ge=1, le=64)
    parser_chunk_size: int = Field(default=100, ge=1, le=10_000)
    max_repair_attempts: int = Field(default=2, ge=0, le=5)


class LLMSettings(SettingsModel):
    """作用：配置 OpenAI-compatible LLM；输入：端点、模型和运行限制；输出：客户端工厂参数。"""

    api_key_env: str = Field(default="DEEPSEEK_API_KEY", pattern=r"^[A-Z][A-Z0-9_]+$")
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    sdk_max_retries: int = Field(default=2, ge=0, le=10)
    response_retries: int = Field(default=2, ge=0, le=5)
    max_output_tokens: int = Field(default=8192, ge=256, le=384_000)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    thinking_mode: str | None = Field(default="enabled", pattern="^(enabled|disabled)$")
    semantic_token_budget: int = Field(default=6000, ge=512)
    semantic_max_functions_per_unit: int = Field(default=8, ge=1, le=256)
    max_agents: int = Field(default=4, ge=1, le=64)


class AppSettings(SettingsModel):
    """作用：聚合应用配置；输入：各配置分组；输出：完整设置；数据流：配置加载器 -> 应用服务。"""

    project: ProjectSettings = Field(default_factory=ProjectSettings)
    io: IOSettings = Field(default_factory=IOSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)


_ENV_FIELDS: dict[str, tuple[str, str]] = {
    "MATLAB_REFACTOR_INPUT_PATH": ("io", "input_path"),
    "MATLAB_REFACTOR_REPORT_OUTPUT_DIR": ("io", "report_dir"),
    "MATLAB_REFACTOR_GRAPH_OUTPUT_DIR": ("io", "graph_dir"),
    "MATLAB_REFACTOR_AUTO_EXPORT_GRAPHS": ("io", "auto_export_graphs"),
    "MATLAB_REFACTOR_EXCLUDE_PATTERNS": ("project", "exclude_patterns"),
    "MATLAB_REFACTOR_ENTRY_POINTS": ("project", "entry_points"),
    "MATLAB_REFACTOR_LOG_LEVEL": ("logging", "level"),
    "MATLAB_REFACTOR_LOG_FORMAT": ("logging", "format"),
    "MATLAB_REFACTOR_STATE_DB": ("orchestrator", "state_db"),
    "MATLAB_REFACTOR_WEB_DB": ("orchestrator", "web_db"),
    "MATLAB_REFACTOR_CHECKPOINT_DB": ("orchestrator", "checkpoint_db"),
    "MATLAB_REFACTOR_ARTIFACT_DIR": ("orchestrator", "artifact_dir"),
    "MATLAB_REFACTOR_CODE_OUTPUT_DIR": ("orchestrator", "output_dir"),
    "MATLAB_REFACTOR_MAX_WORKERS": ("orchestrator", "max_workers"),
    "MATLAB_REFACTOR_PARSER_CHUNK_SIZE": ("orchestrator", "parser_chunk_size"),
    "MATLAB_REFACTOR_MAX_REPAIR_ATTEMPTS": (
        "orchestrator",
        "max_repair_attempts",
    ),
    "MATLAB_REFACTOR_LLM_API_KEY_ENV": ("llm", "api_key_env"),
    "MATLAB_REFACTOR_LLM_BASE_URL": ("llm", "base_url"),
    "MATLAB_REFACTOR_LLM_MODEL": ("llm", "model"),
    "MATLAB_REFACTOR_LLM_TIMEOUT_SECONDS": ("llm", "timeout_seconds"),
    "MATLAB_REFACTOR_LLM_SDK_MAX_RETRIES": ("llm", "sdk_max_retries"),
    "MATLAB_REFACTOR_LLM_RESPONSE_RETRIES": ("llm", "response_retries"),
    "MATLAB_REFACTOR_LLM_MAX_OUTPUT_TOKENS": ("llm", "max_output_tokens"),
    "MATLAB_REFACTOR_LLM_TEMPERATURE": ("llm", "temperature"),
    "MATLAB_REFACTOR_LLM_THINKING_MODE": ("llm", "thinking_mode"),
    "MATLAB_REFACTOR_SEMANTIC_TOKEN_BUDGET": (
        "llm",
        "semantic_token_budget",
    ),
    "MATLAB_REFACTOR_SEMANTIC_MAX_FUNCTIONS_PER_UNIT": (
        "llm",
        "semantic_max_functions_per_unit",
    ),
    "MATLAB_REFACTOR_MAX_AGENTS": ("llm", "max_agents"),
}


def load_settings() -> AppSettings:
    """作用：合并默认值、`.env` 和进程环境；输入：无；输出：严格配置。"""

    process_environment = dict(os.environ)
    env_file = process_environment.get("MATLAB_REFACTOR_ENV_FILE", ".env")
    dotenv_environment = dotenv_values(env_file)
    try:
        raw: dict[str, dict[str, object]] = {}
        for env_name, (section, field) in _ENV_FIELDS.items():
            value = process_environment.get(env_name)
            if value is None:
                value = dotenv_environment.get(env_name)
            if value is None or not value.strip():
                continue
            section_values = raw.setdefault(section, {})
            try:
                section_values[field] = json.loads(value)
            except json.JSONDecodeError:
                section_values[field] = value
        settings = AppSettings.model_validate(raw)
        secret_name = settings.llm.api_key_env
        dotenv_secret = dotenv_environment.get(secret_name)
        if secret_name not in process_environment and dotenv_secret:
            os.environ[secret_name] = dotenv_secret
        return settings
    except ValidationError as exc:
        raise ConfigurationError(f"环境配置无效: {exc}") from exc
