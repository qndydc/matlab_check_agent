"""
Description: 将 CLI 用例适配到确定性前处理与语义 Multi-Agent 流水线。
References: domain.models、domain.semantics、LLM 工厂、Orchestrator。
Referenced By: application 包和 interfaces.cli.main。
"""

from pathlib import Path

from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.planning import (
    RefactorReviewOutcome,
    ReviewDecision,
)
from matlab_refactor_agent.domain.semantics import SemanticIndex
from matlab_refactor_agent.domain.reporting import NaturalLanguageReportOutcome
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

    def annotate(self, project_root: Path) -> SemanticIndex:
        """作用：运行语义与规划 Agent 图；输入：项目目录；输出：三级语义索引，并在 artifacts 中保存规划候选。"""

        outcome = self._orchestrator.run_annotation(project_root)
        self.last_job_id = outcome.job_id
        self.last_artifacts = outcome.artifacts
        return outcome.index

    def plan(self, project_root: Path) -> RefactorReviewOutcome:
        """作用：生成重构计划并等待人工审查；输入：项目目录；输出：待审计划。"""

        outcome = self._orchestrator.run_plan(project_root)
        self._remember(outcome)
        return outcome

    def review(
        self, job_id: str, decision: ReviewDecision | None = None
    ) -> RefactorReviewOutcome:
        """作用：查看或提交人工审查；输入：Job ID 和可选决定；输出：审查状态。"""

        outcome = (
            self._orchestrator.submit_review(job_id, decision)
            if decision is not None
            else self._orchestrator.get_review(job_id)
        )
        self._remember(outcome)
        return outcome

    def report(self, job_id: str) -> NaturalLanguageReportOutcome:
        """作用：查询已完成 Job 的最终报告；输入：Job ID；输出：报告及 artifact 引用。"""

        outcome = self._orchestrator.get_report(job_id)
        self.last_job_id = outcome.job_id
        self.last_artifacts = {
            "natural_language_report": outcome.report_ref,
            "natural_language_report_markdown": outcome.markdown_ref,
        }
        return outcome

    def _remember(self, outcome: RefactorReviewOutcome) -> None:
        self.last_job_id = outcome.job_id
        self.last_artifacts = outcome.artifacts
