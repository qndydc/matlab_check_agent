"""
Description: 渲染扫描、分析、语义索引、重构计划和 Job 的 Rich 终端输出。
References: Rich、domain.models、domain.semantics、domain.planning、domain.reporting、domain.orchestration。
Referenced By: interfaces.cli.main。
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.orchestration import JobRecord
from matlab_refactor_agent.domain.planning import RefactorReviewOutcome
from matlab_refactor_agent.domain.reporting import NaturalLanguageReportOutcome
from matlab_refactor_agent.domain.semantics import SemanticIndex


def render_scan(result: ScanResult, console: Console) -> None:
    """作用：渲染扫描报告；输入：ScanResult 和控制台；输出：无；数据流：文件模型 -> Rich 表格 -> 终端。"""

    table = Table(title="MATLAB 项目扫描")
    table.add_column("文件")
    table.add_column("类型")
    table.add_column("函数", justify="right")
    table.add_column("行数", justify="right")
    table.add_column("状态")
    for item in result.files:
        table.add_row(
            item.path,
            str(item.kind),
            str(len(item.functions)),
            str(item.line_count),
            str(item.parse_status),
        )
    console.print(table)
    console.print(
        f"扫描完成：{len(result.files)} 个 MATLAB 文件，"
        f"{result.function_count} 个可分析对象，排除 {result.excluded_count} 个文件。"
    )


def render_analysis(result: AnalysisResult, console: Console) -> None:
    """作用：渲染依赖分析报告；输入：AnalysisResult 和控制台；输出：无；数据流：分析指标 -> Rich 表格/列表 -> 终端。"""

    summary = Table(title="MATLAB 依赖分析")
    summary.add_column("指标")
    summary.add_column("结果", justify="right")
    summary.add_row("函数/脚本", str(len(result.functions)))
    summary.add_row("内部调用边", str(len(result.dependencies)))
    summary.add_row("循环簇", str(len(result.cycle_clusters)))
    summary.add_row("孤立对象", str(len(result.orphans)))
    summary.add_row("核心函数", str(len(result.core_functions)))
    summary.add_row("入口点", str(len(result.entry_points)))
    summary.add_row("含未解析调用的对象", str(len(result.unresolved_calls)))
    console.print(summary)

    _render_items(console, "入口点", result.entry_points)
    _render_items(console, "语义识别的算法核心", result.core_functions)
    _render_items(console, "孤立对象", result.orphans)
    if result.cycle_clusters:
        console.print("[bold red]循环簇（SCC）[/bold red]")
        for cluster in result.cycle_clusters:
            console.print("  " + ", ".join(cluster))
    if result.cycles:
        console.print("[bold red]代表性环[/bold red]")
        for cycle in result.cycles:
            console.print("  " + " -> ".join([*cycle, cycle[0]]))
    if result.diagnostics:
        console.print("[bold yellow]诊断信息[/bold yellow]")
        for message in result.diagnostics:
            console.print(f"  - {message}")


def render_semantic_index(result: SemanticIndex, console: Console) -> None:
    """作用：渲染三级语义索引摘要；输入：SemanticIndex；输出：终端统计和项目说明。"""

    summary = Table(title="MATLAB 语义注解")
    summary.add_column("指标")
    summary.add_column("结果", justify="right")
    summary.add_row("函数注解", str(len(result.functions)))
    summary.add_row("算法核心", str(len(result.core_functions)))
    summary.add_row("文件注解", str(len(result.files)))
    summary.add_row("待复核项", str(len(result.conflicts)))
    summary.add_row("项目置信度", f"{result.project.confidence:.2f}")
    console.print(summary)
    console.print(f"[bold]项目概述[/bold] {result.project.purpose}")


def render_refactor_review(
    result: RefactorReviewOutcome, console: Console
) -> None:
    """作用：渲染待审或已审计划；输入：审查结果；输出：计划摘要和操作表。"""

    plan = result.plan
    summary = Table(title="重构计划审查")
    summary.add_column("项目")
    summary.add_column("结果", justify="right")
    summary.add_row("状态", result.status)
    summary.add_row("模块", str(len(plan.modules)))
    summary.add_row("文件操作", str(len(plan.operations)))
    summary.add_row("阻断冲突", str(sum(item.blocking for item in plan.conflicts)))
    summary.add_row("最低置信度", f"{plan.minimum_confidence:.2f}")
    console.print(summary)
    if plan.operations:
        operations = Table(title="候选文件操作")
        operations.add_column("符号")
        operations.add_column("源路径")
        operations.add_column("目标路径")
        for item in plan.operations:
            operations.add_row(item.symbol_id, item.source_path, item.target_path)
        console.print(operations)
    for conflict in plan.conflicts:
        style = "red" if conflict.blocking else "yellow"
        console.print(f"[{style}]{conflict.code}: {conflict.message}[/{style}]")
    if result.decision is not None:
        console.print(
            f"审查决定：{result.decision.action} {result.decision.comment}".rstrip()
        )
    if result.output_root is not None:
        console.print(f"[bold green]隔离代码库[/bold green] {result.output_root}")
    for failure in result.failed_validation_checks:
        console.print(f"[bold red]验证失败[/bold red] {failure}")
    if result.repair_summary is not None:
        console.print(f"[bold yellow]修复提议[/bold yellow] {result.repair_summary}")
    report_path = result.report_markdown_output_ref or result.report_markdown_ref
    if report_path is not None:
        console.print(f"[bold green]最终报告[/bold green] {report_path}")


def render_natural_language_report(
    outcome: NaturalLanguageReportOutcome, console: Console
) -> None:
    """作用：渲染最终自然语言报告；输入：报告查询结果；输出：事实摘要、检查和建议。"""

    report = outcome.report
    summary = Table(title=report.title)
    summary.add_column("项目")
    summary.add_column("结果")
    summary.add_row("Job", report.job_id)
    summary.add_row("状态", report.outcome)
    summary.add_row("执行轮次", str(report.attempt))
    summary.add_row("复制文件", str(report.copied_file_count))
    summary.add_row("变更 MATLAB 文件", str(len(report.changed_matlab_files)))
    summary.add_row("原项目只读", "是" if report.source_tree_unchanged else "否")
    console.print(summary)
    console.print(report.executive_summary)
    checks = Table(title="验证解释")
    checks.add_column("检查")
    checks.add_column("状态")
    checks.add_column("解释")
    for item in report.check_findings:
        checks.add_row(item.check_id, item.status, item.interpretation)
    console.print(checks)
    _render_items(console, "风险", report.risks)
    _render_items(console, "后续建议", report.next_steps)
    console.print(
        f"Markdown：{outcome.markdown_output_ref or outcome.markdown_ref}"
    )


def _render_items(console: Console, title: str, items: list[str]) -> None:
    """作用：渲染命名列表；输入：控制台、标题和项目；输出：无；数据流：字符串列表 -> Rich 文本。"""

    if not items:
        return
    console.print(f"[bold]{title}[/bold]")
    for item in items:
        console.print(f"  - {item}")


def render_job_status(
    job: JobRecord, tasks: list[tuple[str, str, str]], console: Console
) -> None:
    """作用：渲染 Orchestrator Job 状态；输入：JobRecord、任务状态和控制台；输出：无；数据流：SQLite 状态 -> Rich 表格 -> 终端。"""

    console.print(f"[bold]Job[/bold] {job.job_id}")
    console.print(f"项目：{job.project_root}")
    console.print(f"状态：{job.status}")
    if job.error:
        console.print(f"[red]错误：{job.error}[/red]")
    table = Table(title="Worker 任务")
    table.add_column("Task ID")
    table.add_column("Worker")
    table.add_column("状态")
    for task_id, worker_kind, status in tasks:
        table.add_row(task_id, worker_kind, status)
    console.print(table)
