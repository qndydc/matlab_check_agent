"""
Description: 渲染扫描、分析、语义索引、迁移结果和 Job 的 Rich 终端输出。
References: Rich、domain.models、domain.semantics、domain.migration、domain.orchestration。
Referenced By: interfaces.cli.main。
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from matlab_refactor_agent.domain.models import AnalysisResult, ScanResult
from matlab_refactor_agent.domain.migration import MatlabToPythonOutcome
from matlab_refactor_agent.domain.orchestration import JobRecord
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


def render_migration(result: MatlabToPythonOutcome, console: Console) -> None:
    """渲染 WCC 迁移摘要，不把完整生成源码打印到终端。"""

    summary = Table(title="MATLAB → Python WCC 迁移")
    summary.add_column("指标")
    summary.add_column("结果", justify="right")
    summary.add_row("WCC 调用链", str(len(result.plan.units)))
    summary.add_row(
        "内部 SCC",
        str(sum(len(unit.sccs) for unit in result.plan.units)),
    )
    summary.add_row("已生成调用链", str(len(result.translations)))
    summary.add_row(
        "Python 文件",
        str(sum(len(item.files) for item in result.translations)),
    )
    summary.add_row(
        "人工复核项",
        str(sum(len(item.manual_review) for item in result.translations)),
    )
    console.print(summary)
    for unit in result.plan.units:
        entries = ", ".join(unit.entry_symbols) or "无明确入口"
        console.print(
            f"[bold]{unit.unit_id}[/bold]：入口 {entries}；"
            f"{len(unit.symbol_ids)} 个 symbol；{len(unit.sccs)} 个 SCC"
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
