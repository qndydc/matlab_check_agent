"""
Description: 加载并严格校验项目、日志和 Orchestrator YAML 配置。
References: PyYAML、Pydantic、domain.exceptions.ConfigurationError。
Referenced By: CLI、AnalysisService 和 Orchestrator 工厂。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from matlab_refactor_agent.domain.exceptions import ConfigurationError


class SettingsModel(BaseModel):
    """作用：提供严格配置模型基类；输入：配置字典；输出：校验配置；数据流：YAML -> Pydantic -> 应用服务。"""

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


class OrchestratorSettings(SettingsModel):
    """作用：配置持久化、Worker 池和解析分片；输入：路径、并发数和分片大小；输出：调度设置；数据流：YAML -> Orchestrator 工厂。"""

    state_db: Path = Path("var/refactor-agent.db")
    artifact_dir: Path = Path("var/jobs")
    max_workers: int = Field(default=4, ge=1, le=64)
    parser_chunk_size: int = Field(default=100, ge=1, le=10_000)


class AppSettings(SettingsModel):
    """作用：聚合应用配置；输入：各配置分组；输出：完整设置；数据流：配置加载器 -> 应用服务。"""

    project: ProjectSettings = Field(default_factory=ProjectSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)


def load_settings(path: Path | None = None) -> AppSettings:
    """作用：读取并校验 YAML；输入：可选配置路径；输出：AppSettings；数据流：文件/默认值 -> YAML -> Pydantic。"""

    if path is None:
        return AppSettings()
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AppSettings.model_validate(raw)
    except OSError as exc:
        raise ConfigurationError(f"无法读取配置文件 {path}: {exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"配置文件无效 {path}: {exc}") from exc
