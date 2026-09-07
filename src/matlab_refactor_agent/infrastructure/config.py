"""
Description: 从 `.env` 和进程环境变量加载并严格校验应用配置。
References: python-dotenv、Pydantic、domain.exceptions.ConfigurationError。
Referenced By: CLI、AnalysisService 和 Orchestrator 工厂。
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

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
    debug_model: bool = False


class IOSettings(SettingsModel):
    """作用：集中定义用户输入和可交付输出目录；输入：`.env`/进程环境；输出：CLI 与报告发布路径。"""

    input_path: Path = Path("tests/fixtures/matlab_projects/basic")
    graph_dir: Path = Path("var/graphs")
    auto_export_graphs: bool = True


class OrchestratorSettings(SettingsModel):
    """作用：配置持久化、Worker 池和解析分片；输入：路径、并发数和分片大小；输出：调度设置；数据流：环境变量 -> Orchestrator 工厂。"""

    state_db: Path = Path("var/refactor-agent.db")
    web_db: Path = Path("var/web-projects.db")
    artifact_dir: Path = Path("var/jobs")
    max_workers: int = Field(default=4, ge=1, le=64)
    parser_chunk_size: int = Field(default=100, ge=1, le=10_000)


class LLMSettings(SettingsModel):
    """作用：配置 OpenAI-compatible LLM；输入：端点、模型和运行限制；输出：客户端工厂参数。"""

    api_key_env: str = Field(default="DEEPSEEK_API_KEY", pattern=r"^[A-Z][A-Z0-9_]+$")
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    hard_timeout_seconds: float = Field(default=180.0, gt=0, le=1800)
    sdk_max_retries: int = Field(default=2, ge=0, le=10)
    response_retries: int = Field(default=2, ge=0, le=5)
    semantic_max_output_tokens: int = Field(
        default=16_384, ge=256, le=384_000
    )
    migration_max_output_tokens: int = Field(
        default=32_768, ge=256, le=384_000
    )
    migration_reason_max_output_tokens: int = Field(
        default=4096, ge=256, le=65_536
    )
    model_context_window_tokens: int = Field(
        default=1_000_000, ge=4096, le=10_000_000
    )
    context_safety_margin_tokens: int = Field(
        default=8192, ge=256, le=1_000_000
    )
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    thinking_mode: str | None = Field(default="enabled", pattern="^(enabled|disabled)$")
    semantic_token_budget: int = Field(default=32_768, ge=512)
    semantic_max_functions_per_unit: int = Field(default=8, ge=1, le=256)
    semantic_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    semantic_max_attempts: int = Field(default=2, ge=1, le=10)
    max_agents: int = Field(default=4, ge=1, le=64)
    semantic_max_agents: int = Field(default=4, ge=1, le=16)
    migration_max_agents: int = Field(default=1, ge=1, le=16)
    migration_chunk_max_agents: int = Field(default=4, ge=1, le=16)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip()
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError as exc:
            raise ValueError("URL 地址或端口无效") from exc
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or any(char.isspace() for char in value)
                or (port is not None and port < 1)):
            raise ValueError("URL 须为 http(s) 地址，不能包含账号、密码、查询参数或片段")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
            raise ValueError("模型名称不能为空、过长或包含控制字符")
        return value

    @property
    def semantic_hard_input_tokens(self) -> int:
        """语义请求在预留完整输出和安全余量后可使用的最大输入。"""

        return (
            self.model_context_window_tokens
            - self.semantic_max_output_tokens
            - self.context_safety_margin_tokens
        )

    @model_validator(mode="after")
    def validate_context_budgets(self) -> "LLMSettings":
        """确保输入目标与两类输出预算能够同时装入模型上下文窗口。"""

        for name, output_tokens in (
            ("semantic_max_output_tokens", self.semantic_max_output_tokens),
            ("migration_max_output_tokens", self.migration_max_output_tokens),
            ("migration_reason_max_output_tokens", self.migration_reason_max_output_tokens),
        ):
            if output_tokens + self.context_safety_margin_tokens >= (
                self.model_context_window_tokens
            ):
                raise ValueError(
                    f"{name} 与 context_safety_margin_tokens 之和必须小于 "
                    "model_context_window_tokens"
                )
        if self.semantic_token_budget > self.semantic_hard_input_tokens:
            raise ValueError(
                "semantic_token_budget 超过语义请求的最大安全输入"
            )
        return self


class AppSettings(SettingsModel):
    """作用：聚合应用配置；输入：各配置分组；输出：完整设置；数据流：配置加载器 -> 应用服务。"""

    project: ProjectSettings = Field(default_factory=ProjectSettings)
    io: IOSettings = Field(default_factory=IOSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)


_ENV_FIELDS: dict[str, tuple[str, str]] = {
    "MATLAB_REFACTOR_INPUT_PATH": ("io", "input_path"),
    "MATLAB_REFACTOR_GRAPH_OUTPUT_DIR": ("io", "graph_dir"),
    "MATLAB_REFACTOR_AUTO_EXPORT_GRAPHS": ("io", "auto_export_graphs"),
    "MATLAB_REFACTOR_EXCLUDE_PATTERNS": ("project", "exclude_patterns"),
    "MATLAB_REFACTOR_ENTRY_POINTS": ("project", "entry_points"),
    "MATLAB_REFACTOR_LOG_LEVEL": ("logging", "level"),
    "MATLAB_REFACTOR_LOG_FORMAT": ("logging", "format"),
    "DEBUG_MODEL": ("logging", "debug_model"),
    "MATLAB_REFACTOR_STATE_DB": ("orchestrator", "state_db"),
    "MATLAB_REFACTOR_WEB_DB": ("orchestrator", "web_db"),
    "MATLAB_REFACTOR_ARTIFACT_DIR": ("orchestrator", "artifact_dir"),
    "MATLAB_REFACTOR_MAX_WORKERS": ("orchestrator", "max_workers"),
    "MATLAB_REFACTOR_PARSER_CHUNK_SIZE": ("orchestrator", "parser_chunk_size"),
    "MATLAB_REFACTOR_LLM_API_KEY_ENV": ("llm", "api_key_env"),
    "MATLAB_REFACTOR_LLM_BASE_URL": ("llm", "base_url"),
    "MATLAB_REFACTOR_LLM_MODEL": ("llm", "model"),
    "MATLAB_REFACTOR_LLM_TIMEOUT_SECONDS": ("llm", "timeout_seconds"),
    "MATLAB_REFACTOR_LLM_HARD_TIMEOUT_SECONDS": (
        "llm", "hard_timeout_seconds",
    ),
    "MATLAB_REFACTOR_LLM_SDK_MAX_RETRIES": ("llm", "sdk_max_retries"),
    "MATLAB_REFACTOR_LLM_RESPONSE_RETRIES": ("llm", "response_retries"),
    "MATLAB_REFACTOR_SEMANTIC_MAX_OUTPUT_TOKENS": (
        "llm",
        "semantic_max_output_tokens",
    ),
    "MATLAB_REFACTOR_MIGRATION_MAX_OUTPUT_TOKENS": (
        "llm",
        "migration_max_output_tokens",
    ),
    "MATLAB_REFACTOR_MIGRATION_REASON_MAX_OUTPUT_TOKENS": (
        "llm",
        "migration_reason_max_output_tokens",
    ),
    "MATLAB_REFACTOR_LLM_CONTEXT_WINDOW_TOKENS": (
        "llm",
        "model_context_window_tokens",
    ),
    "MATLAB_REFACTOR_LLM_CONTEXT_SAFETY_MARGIN_TOKENS": (
        "llm",
        "context_safety_margin_tokens",
    ),
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
    "MATLAB_REFACTOR_SEMANTIC_CONFIDENCE_THRESHOLD": (
        "llm",
        "semantic_confidence_threshold",
    ),
    "MATLAB_REFACTOR_SEMANTIC_MAX_ATTEMPTS": (
        "llm",
        "semantic_max_attempts",
    ),
    "MATLAB_REFACTOR_MAX_AGENTS": ("llm", "max_agents"),
    "MATLAB_REFACTOR_SEMANTIC_MAX_AGENTS": (
        "llm", "semantic_max_agents",
    ),
    "MATLAB_REFACTOR_MIGRATION_MAX_AGENTS": (
        "llm", "migration_max_agents",
    ),
    "MATLAB_REFACTOR_MIGRATION_CHUNK_MAX_AGENTS": (
        "llm", "migration_chunk_max_agents",
    ),
}


def load_settings(env_file: Path | str | None = None) -> AppSettings:
    """作用：合并默认值、`.env` 和进程环境；输入：无；输出：严格配置。"""

    process_environment = dict(os.environ)
    env_file = env_file or process_environment.get("MATLAB_REFACTOR_ENV_FILE", ".env")
    dotenv_environment = dotenv_values(env_file, interpolate=False)
    try:
        raw: dict[str, dict[str, object]] = {}
        for env_name, (section, field) in _ENV_FIELDS.items():
            value = process_environment.get(env_name)
            if value is None:
                value = dotenv_environment.get(env_name)
            if value is None or not value.strip():
                continue
            section_values = raw.setdefault(section, {})
            if field in {"model", "base_url", "api_key_env", "input_path", "graph_dir",
                         "state_db", "web_db", "artifact_dir", "level", "format"}:
                section_values[field] = value
                continue
            try:
                section_values[field] = json.loads(value)
            except json.JSONDecodeError:
                section_values[field] = value
        settings = AppSettings.model_validate(raw)
        secret_name = settings.llm.api_key_env
        secret = process_environment.get(secret_name, dotenv_environment.get(secret_name) or "")
        # 密钥随本次任务快照保存，不污染 os.environ；修改 .env 后下个任务可立即生效。
        settings.llm.api_key = SecretStr(secret)
        return settings
    except ValidationError as exc:
        raise ConfigurationError(f"环境配置无效: {exc}") from exc
