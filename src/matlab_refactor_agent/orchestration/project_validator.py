"""
Description: 重新解析隔离代码库并校验源树、ChangeSet、路径、符号和调用图不变量。
References: MaxxMatlabParser、DependencyAnalyzer、domain.validation、ChangeSetExecutor。
Referenced By: LangGraphWorkflow 和自动验证测试。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from matlab_refactor_agent.workers.dependency_analysis import DependencyAnalyzer
from matlab_refactor_agent.workers.matlab_parser import MaxxMatlabParser
from matlab_refactor_agent.workers.scanning import MatlabProjectScanner
from matlab_refactor_agent.domain.changes import ChangeSet
from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.planning import RefactorPlan
from matlab_refactor_agent.domain.validation import (
    ValidationCheck,
    ValidationResult,
)

from .changeset_executor import CHANGESET_MARKER, ChangeSetExecutor


@dataclass(frozen=True, slots=True)
class ValidationBundle:
    """作用：携带报告及验证期扫描分析；输入：验证器；输出：artifact 写入数据。"""

    result: ValidationResult
    scan: ScanResult
    analysis: AnalysisResult


class ProjectValidator(Protocol):
    """作用：允许测试或生产替换验证后端；输入：验证上下文；输出：ValidationBundle。"""

    def validate(
        self,
        *,
        job_id: str,
        attempt: int,
        baseline: AnalysisResult,
        plan: RefactorPlan,
        change_set: ChangeSet,
    ) -> ValidationBundle: ...


class RefactoredProjectValidator:
    """作用：运行不修改输出的确定性验证；输入：基线、计划和 ChangeSet；输出：ValidationBundle。"""

    def __init__(
        self,
        exclude_patterns: list[str],
        entry_points: list[str],
    ) -> None:
        self._exclude_patterns = exclude_patterns
        self._entry_points = entry_points

    def validate(
        self,
        *,
        job_id: str,
        attempt: int,
        baseline: AnalysisResult,
        plan: RefactorPlan,
        change_set: ChangeSet,
    ) -> ValidationBundle:
        output = Path(change_set.output_root)
        scan = MatlabProjectScanner(
            MaxxMatlabParser(), self._exclude_patterns
        ).scan(output)
        analysis = DependencyAnalyzer().analyze(scan, self._entry_points)
        checks = [
            self._source_integrity(change_set),
            self._output_integrity(change_set),
            self._parse_check(scan),
            self._plan_check(plan, scan),
            self._graph_check(baseline, analysis),
            self._matlab_runtime_check(),
        ]
        passed = not any(
            item.blocking and item.status == "failed" for item in checks
        )
        return ValidationBundle(
            result=ValidationResult(
                job_id=job_id,
                attempt=attempt,
                output_root=str(output),
                passed=passed,
                checks=checks,
                diagnostics=analysis.diagnostics,
            ),
            scan=scan,
            analysis=analysis,
        )

    @staticmethod
    def _source_integrity(change_set: ChangeSet) -> ValidationCheck:
        current_hash, _ = ChangeSetExecutor.snapshot(
            Path(change_set.source_root)
        )
        passed = (
            change_set.source_tree_hash_before
            == change_set.source_tree_hash_after
            == current_hash
        )
        return ValidationCheck(
            check_id="source_integrity",
            status="passed" if passed else "failed",
            summary=(
                "源项目哈希保持不变"
                if passed
                else "源项目哈希与执行记录不一致"
            ),
        )

    @staticmethod
    def _output_integrity(change_set: ChangeSet) -> ValidationCheck:
        current_hash, _ = ChangeSetExecutor.snapshot(
            Path(change_set.output_root), excluded={CHANGESET_MARKER}
        )
        passed = current_hash == change_set.output_tree_hash
        return ValidationCheck(
            check_id="output_integrity",
            status="passed" if passed else "failed",
            summary=(
                "隔离输出与 ChangeSet 一致"
                if passed
                else "隔离输出在执行后发生变化"
            ),
        )

    @staticmethod
    def _parse_check(scan: ScanResult) -> ValidationCheck:
        incomplete = [
            item.path
            for item in scan.files
            if str(item.parse_status) != "parsed"
        ]
        return ValidationCheck(
            check_id="matlab_parse",
            status="failed" if incomplete else "passed",
            summary=(
                "全部 MATLAB 文件完整解析"
                if not incomplete
                else "存在未完整解析的 MATLAB 文件"
            ),
            details=incomplete,
        )

    @staticmethod
    def _plan_check(
        plan: RefactorPlan, scan: ScanResult
    ) -> ValidationCheck:
        actual_paths = {item.path for item in scan.files}
        actual_names = {
            function.name
            for item in scan.files
            for function in item.functions
        }
        missing_paths = sorted(set(plan.target_tree) - actual_paths)
        missing_symbols = sorted(
            operation.proposed_name
            for operation in plan.operations
            if operation.proposed_name not in actual_names
        )
        details = [
            *(f"缺少目标文件: {item}" for item in missing_paths),
            *(f"缺少目标符号: {item}" for item in missing_symbols),
        ]
        return ValidationCheck(
            check_id="plan_conformance",
            status="failed" if details else "passed",
            summary=(
                "输出符合已批准路径和符号映射"
                if not details
                else "输出未完整实现已批准计划"
            ),
            details=details,
        )

    @staticmethod
    def _graph_check(
        baseline: AnalysisResult, current: AnalysisResult
    ) -> ValidationCheck:
        details: list[str] = []
        if len(current.functions) != len(baseline.functions):
            details.append(
                f"函数数 {len(baseline.functions)} -> {len(current.functions)}"
            )
        if len(current.dependencies) != len(baseline.dependencies):
            details.append(
                "内部调用边数 "
                f"{len(baseline.dependencies)} -> {len(current.dependencies)}"
            )
        baseline_unresolved = sum(
            len(items) for items in baseline.unresolved_calls.values()
        )
        current_unresolved = sum(
            len(items) for items in current.unresolved_calls.values()
        )
        if current_unresolved > baseline_unresolved:
            details.append(
                f"未解析调用数 {baseline_unresolved} -> {current_unresolved}"
            )
        return ValidationCheck(
            check_id="dependency_graph",
            status="failed" if details else "passed",
            summary=(
                "调用图关键指标保持一致"
                if not details
                else "调用图出现结构回归"
            ),
            details=details,
        )

    @staticmethod
    def _matlab_runtime_check() -> ValidationCheck:
        try:
            available = importlib.util.find_spec("matlab.engine") is not None
        except ModuleNotFoundError:
            available = False
        if not available:
            return ValidationCheck(
                check_id="matlab_runtime",
                status="unavailable",
                summary="未安装 MATLAB Engine，未执行数值与性能验证",
                blocking=False,
            )
        return ValidationCheck(
            check_id="matlab_runtime",
            status="skipped",
            summary="已检测到 MATLAB Engine，但尚未配置可安全运行的测试入口",
            blocking=False,
        )
