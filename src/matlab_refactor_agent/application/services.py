"""
Description: 将 CLI 用例适配到 Orchestrator–Worker 流水线。
References: domain.models、infrastructure.config、orchestration.Orchestrator。
Referenced By: application 包和 interfaces.cli.main。
"""

from pathlib import Path

from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.infrastructure.config import AppSettings
from matlab_refactor_agent.orchestration import Orchestrator


class AnalysisService:
    """作用：向接口层提供多 Worker 扫描分析用例；输入：应用配置；输出：领域结果；数据流：CLI -> Orchestrator -> artifacts/Workers -> CLI。"""

    def __init__(self, settings: AppSettings) -> None:
        """作用：组装 Orchestrator；输入：AppSettings；输出：服务实例；数据流：配置 -> 状态库/artifacts/WorkerPool。"""

        self._settings = settings
        self._orchestrator = Orchestrator.from_settings(settings)
        self.last_job_id: str | None = None
        self.last_artifacts: dict[str, str] = {}

    def scan(self, project_root: Path) -> ScanResult:
        """作用：通过 Worker-1 扫描 MATLAB 项目；输入：项目根目录；输出：ScanResult；数据流：路径 -> Orchestrator/ScannerAgent -> scan artifact。"""

        outcome = self._orchestrator.run_scan(project_root)
        self.last_job_id = outcome.job_id
        self.last_artifacts = outcome.artifacts
        return outcome.result

    def analyze(self, project_root: Path) -> AnalysisResult:
        """作用：通过 Worker-1/3 完成依赖分析；输入：项目根目录；输出：AnalysisResult；数据流：Scanner artifact -> AnalyzerAgent -> analysis artifact。"""

        outcome = self._orchestrator.run_analysis(project_root)
        self.last_job_id = outcome.job_id
        self.last_artifacts = outcome.artifacts
        return outcome.result
